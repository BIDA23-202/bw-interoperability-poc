"""
recon.py  |  Reconciliation engine, settlement journal, exception queue, status manager and scoring (tasks 5.1 to 5.4).

Run (after loader.py):
  python src/recon.py --db data/poc.db --manifest data/poc_manifest.csv --out data/recon [--rerun]   (writes data/recon_results.csv and data/recon_summary.json)

What it does
 1. Match bank-leg (PACS008) and wallet-leg (ORANGE) transactions: pass 1 on end_to_end_key (KEY); pass 2 on creditor MSISDN +
    amount within tolerance + date window, only when exactly one candidate exists (TOLERANT).
 2. Outcome per pair: MATCHED, DISCREPANT (AMOUNT_DIFF, STATUS_MISMATCH, PARTY_MISMATCH, CURRENCY_MISMATCH, STATUS_PENDING),
    or UNMATCHED (NO_WALLET_LEG, NO_BANK_LEG). A day gap outside the policy window adds the LAG_OUTLIER flag but stays MATCHED.
 3. Post a double-entry settlement journal for MATCHED pairs only; verify debits = credits.
 4. Send every flagged item to the exception queue (`exceptions`, source_system 'RECON').
 5. Status manager: derive the bank leg's status from the ledger and write pacs.002-style status reports.
 6. Score the outcome against the manifest (ground truth) and export results.csv and summary.json.
"""
import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import date, datetime, timezone

import pandas as pd

DISCREPANCY_FLAGS = {"AMOUNT_DIFF", "STATUS_MISMATCH", "PARTY_MISMATCH", "CURRENCY_MISMATCH", "STATUS_PENDING"}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(db, source):
    db.row_factory = sqlite3.Row
    sql = """SELECT t.txn_id, t.end_to_end_key AS key, t.amount_minor, t.currency, t.settled_at, t.initiated_at, t.status,
                    t.debtor_participant_id, t.creditor_participant_id, cp.identifier_value AS msisdn
             FROM transactions t LEFT JOIN parties cp ON cp.party_id = t.creditor_party_id WHERE t.source_system = ?"""
    rows = [dict(r) for r in db.execute(sql, (source,))]
    db.row_factory = None
    return rows


def _day_gap(bank, wallet):
    return (date.fromisoformat(bank["settled_at"][:10]) - date.fromisoformat(wallet["initiated_at"][:10])).days


def evaluate_pair(bank, wallet, tol_minor=0, lag_min=0, lag_max=3):
    flags = []
    diff = wallet["amount_minor"] - bank["amount_minor"]
    if abs(diff) > tol_minor:
        flags.append("AMOUNT_DIFF")
    if bank["currency"] != wallet["currency"]:
        flags.append("CURRENCY_MISMATCH")
    if bank["msisdn"] != wallet["msisdn"]:
        flags.append("PARTY_MISMATCH")
    if wallet["status"] == "FAILED":
        flags.append("STATUS_MISMATCH")
    elif wallet["status"] != "COMPLETED":
        flags.append("STATUS_PENDING")
    gap = _day_gap(bank, wallet)
    if gap < lag_min or gap > lag_max:
        flags.append("LAG_OUTLIER")
    outcome = "DISCREPANT" if DISCREPANCY_FLAGS & set(flags) else "MATCHED"
    return outcome, diff, gap, flags


def match(banks, wallets, tolerant_pct=0.05, window_days=10):
    """Return [(bank|None, wallet|None, method)]. Each transaction is used at most once."""
    wallets_by_key = {w["key"]: w for w in wallets}
    pairs, used_w, left_b = [], set(), []
    for b in banks:
        w = wallets_by_key.get(b["key"])
        if w is not None and w["txn_id"] not in used_w:
            pairs.append((b, w, "KEY")); used_w.add(w["txn_id"])
        else:
            left_b.append(b)
    left_w = [w for w in wallets if w["txn_id"] not in used_w]
    by_msisdn = {}
    for w in left_w:
        by_msisdn.setdefault(w["msisdn"], []).append(w)

    def candidates(b):
        out = []
        for w in by_msisdn.get(b["msisdn"], []):
            if w["txn_id"] in used_w:
                continue
            if abs(w["amount_minor"] - b["amount_minor"]) <= max(1, int(b["amount_minor"] * tolerant_pct)) \
                    and abs(_day_gap(b, w)) <= window_days:
                out.append(w)
        return out

    still_b = []
    for b in left_b:
        c = candidates(b)
        rival = [x for x in left_b if x is not b and any(w in c for w in candidates(x))] if len(c) == 1 else []
        if len(c) == 1 and not rival:
            pairs.append((b, c[0], "TOLERANT")); used_w.add(c[0]["txn_id"])
        else:
            still_b.append(b)
    pairs += [(b, None, "NONE") for b in still_b]
    pairs += [(None, w, "NONE") for w in left_w if w["txn_id"] not in used_w]
    return pairs


def clear_previous(db):
    with db:
        db.execute("DELETE FROM reconciliation_records")
        db.execute("DELETE FROM settlement_journal")
        db.execute("DELETE FROM status_reports WHERE source = 'DERIVED_LEDGER'")
        db.execute("DELETE FROM exceptions WHERE source_system = 'RECON'")
        db.execute("""UPDATE transactions SET status='DISPATCHED', status_source='DERIVED_ACK',
                      status_detail='ack received, awaiting reconciliation' WHERE source_system='PACS008'""")


def _imbalances(db):
    return db.execute("""SELECT COUNT(*) FROM (SELECT txn_id, SUM(CASE WHEN dc='D' THEN amount_minor ELSE -amount_minor END) AS x
                         FROM settlement_journal GROUP BY txn_id) WHERE x <> 0""").fetchone()[0]


def reconcile(db, tol_minor=0, lag_min=0, lag_max=3, rerun=False, now=None):
    now = now or now_iso()
    if db.execute("SELECT COUNT(*) FROM reconciliation_records").fetchone()[0]:
        if not rerun:
            raise RuntimeError("Reconciliation already done. Use --rerun to clear it and run again.")
        clear_previous(db)
    banks, wallets = _rows(db, "PACS008"), _rows(db, "ORANGE")
    pairs = match(banks, wallets)
    summary = Counter()
    with db:
        for b, w, method in pairs:
            if b and w:
                outcome, diff, gap, flags = evaluate_pair(b, w, tol_minor, lag_min, lag_max)
            elif b:
                outcome, diff, gap, flags = "UNMATCHED", None, None, ["NO_WALLET_LEG"]
            else:
                outcome, diff, gap, flags = "UNMATCHED", None, None, ["NO_BANK_LEG"]
            db.execute("""INSERT INTO reconciliation_records(bank_txn_id,wallet_txn_id,match_method,outcome,amount_diff_minor,day_gap,flags,reconciled_at)
                          VALUES (?,?,?,?,?,?,?,?)""", (b["txn_id"] if b else None, w["txn_id"] if w else None, method, outcome, diff, gap, json.dumps(flags), now))
            summary[outcome] += 1
            key = (b or w)["key"]
            for f in flags:
                if outcome != "MATCHED" or f == "LAG_OUTLIER":
                    db.execute("INSERT INTO exceptions(batch_id,source_system,record_ref,reason_code,reason_detail,payload_json,created_at) VALUES (NULL,'RECON',?,?,?,NULL,?)",
                               (key, f, f"outcome={outcome}; amount_diff_minor={diff}; day_gap={gap}", now))
            if b:   # the status manager and the ledger act on the bank leg
                if outcome == "MATCHED":
                    status, iso, reason = "COMPLETED", "ACSC", None
                    for dc, acct in (("D", f"BANK:{b['debtor_participant_id']}:SETTLEMENT"), ("C", f"WALLET:{b['creditor_participant_id']}:SETTLEMENT")):
                        db.execute("INSERT INTO settlement_journal(txn_id,dc,account,amount_minor,currency,end_to_end_key,posted_at) VALUES (?,?,?,?,?,?,?)",
                                   (b["txn_id"], dc, acct, b["amount_minor"], b["currency"], b["key"], now))
                elif "STATUS_MISMATCH" in flags:
                    status, iso, reason = "FAILED", "RJCT", "WALLET_FAILED"
                else:
                    status, iso, reason = "DISPATCHED", "PDNG", next((f for f in flags if f in DISCREPANCY_FLAGS or f.startswith("NO_")), "REVIEW")
                db.execute("UPDATE transactions SET status=?, status_source='DERIVED_LEDGER', status_detail=? WHERE txn_id=?",
                           (status, reason or "settled and reconciled", b["txn_id"]))
                db.execute("INSERT INTO status_reports(txn_id,iso_status,canonical_status,reason_code,reason_text,source,created_at) VALUES (?,?,?,?,?,'DERIVED_LEDGER',?)",
                           (b["txn_id"], iso, status, reason, "derived from ledger and reconciliation outcome", now))
    if _imbalances(db):
        raise AssertionError("some transactions have an unbalanced journal")
    return dict(summary)


def score(db, manifest_path):
    """Compare actual outcomes with the manifest's expected outcomes. Returns (results DataFrame, summary dict)."""
    man = pd.read_csv(manifest_path, dtype=str).fillna("")
    db.row_factory = sqlite3.Row
    exc = {}
    for r in db.execute("SELECT record_ref, reason_code FROM exceptions WHERE source_system='PACS008'"):
        exc.setdefault(r["record_ref"], set()).add(r["reason_code"])
    rec = {}
    q = """SELECT COALESCE(b.end_to_end_key, w.end_to_end_key) AS key, r.outcome, r.flags, r.amount_diff_minor, r.day_gap, r.match_method
           FROM reconciliation_records r LEFT JOIN transactions b ON b.txn_id=r.bank_txn_id LEFT JOIN transactions w ON w.txn_id=r.wallet_txn_id"""
    for r in db.execute(q):
        rec[r["key"]] = dict(r)
    db.row_factory = None
    out = []
    for m in man.to_dict("records"):
        key, is_dup = m["end_to_end_id"], m["pair_id"].endswith("D")
        codes = exc.get(key, set())
        diff = gap = method = None
        if is_dup:
            actual = "REJECTED" if "DUPLICATE_KEY" in codes else "ACCEPTED"; flags = sorted(codes)
        elif codes - {"DUPLICATE_KEY"}:
            actual, flags = "REJECTED", sorted(codes - {"DUPLICATE_KEY"})
        elif key in rec:
            r = rec[key]; actual, flags = r["outcome"], json.loads(r["flags"]); diff, gap, method = r["amount_diff_minor"], r["day_gap"], r["match_method"]
        else:
            actual, flags = "MISSING", []
        exp_flag = m["expected_flag"]
        if exp_flag:
            flag_ok = exp_flag in flags
        elif actual == "MATCHED":
            flag_ok = not flags
        else:
            flag_ok = True
        lag_ok = True if gap is None or m["lag_days"] == "" else gap == int(m["lag_days"])
        out.append({"pair_id": m["pair_id"], "end_to_end_id": key, "anomaly_type": m["anomaly_type"] or "(clean)",
                    "expected_outcome": m["expected_outcome"], "actual_outcome": actual, "expected_flag": exp_flag,
                    "actual_flags": ";".join(flags), "outcome_correct": actual == m["expected_outcome"], "flag_correct": flag_ok,
                    "lag_matches_manifest": lag_ok, "correct": (actual == m["expected_outcome"]) and flag_ok and lag_ok,
                    "amount_diff_minor": diff, "day_gap": gap, "match_method": method})
    res = pd.DataFrame(out)
    by_type = res.groupby("anomaly_type")["correct"].agg(["sum", "count"]).rename(columns={"sum": "correct", "count": "n"})
    summary = {"rows": int(len(res)), "correct": int(res["correct"].sum()), "accuracy": round(float(res["correct"].mean()), 4),
               "outcome_accuracy": round(float(res["outcome_correct"].mean()), 4),
               "false_matches": int(((res.actual_outcome == "MATCHED") & (res.expected_outcome != "MATCHED")).sum()),
               "missed_matches": int(((res.actual_outcome != "MATCHED") & (res.expected_outcome == "MATCHED")).sum()),
               "by_anomaly_type": {k: {"correct": int(v["correct"]), "n": int(v["n"])} for k, v in by_type.iterrows()},
               "confusion": {e: {a: int(n) for a, n in row.items()} for e, row in pd.crosstab(res.expected_outcome, res.actual_outcome).iterrows()}}
    return res, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/poc.db"); ap.add_argument("--manifest", default="data/poc_manifest.csv")
    ap.add_argument("--out", default="data/recon"); ap.add_argument("--rerun", action="store_true")
    ap.add_argument("--tolerance-minor", type=int, default=0); ap.add_argument("--lag-min", type=int, default=0); ap.add_argument("--lag-max", type=int, default=3)
    a = ap.parse_args()
    db = sqlite3.connect(a.db); db.execute("PRAGMA foreign_keys = ON")
    outcomes = reconcile(db, a.tolerance_minor, a.lag_min, a.lag_max, a.rerun)
    print("Reconciliation outcomes:", outcomes)
    print("Journal entries:", db.execute("SELECT COUNT(*) FROM settlement_journal").fetchone()[0], "| unbalanced transactions:", _imbalances(db))
    res, summary = score(db, a.manifest)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    res.to_csv(f"{a.out}_results.csv", index=False)
    json.dump(summary, open(f"{a.out}_summary.json", "w"), indent=2)
    print(f"Accuracy vs manifest: {summary['correct']}/{summary['rows']} = {summary['accuracy']:.2%} "
          f"(false matches {summary['false_matches']}, missed matches {summary['missed_matches']})")
    print(pd.DataFrame(summary["by_anomaly_type"]).T.to_string())
    if summary["correct"] != summary["rows"]:
        print("\nRows that are wrong:"); print(res[~res["correct"]].to_string(index=False))


if __name__ == "__main__":
    main()
