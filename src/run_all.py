"""
run_all.py  |  One command that reproduces every PoC result (task 3.8).

  python src/run_all.py --base . --pool data/MatchedPoolReference.csv

--base is the folder that contains src/ and data/. Steps: generate data, load through the gateway, reconcile and score,
reverse-map and check fidelity, then print a PASS/FAIL table. Exit code 1 if any check fails.
Add --n and --seed to change the dataset size and seed (count checks then become consistency checks).
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys


def run(title, cmd):
    print(f"\n=== {title} ===")
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout.rstrip())
    if r.returncode != 0:
        print(r.stderr.rstrip()); print(f"\nFAILED at step: {title}"); sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="."); ap.add_argument("--pool", default=None)
    ap.add_argument("--n", type=int, default=400); ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    src, data = os.path.join(a.base, "src"), os.path.join(a.base, "data")
    pool = a.pool or os.path.join(data, "MatchedPoolReference.csv")
    py = sys.executable
    p = lambda *x: os.path.join(*x)
    run("1. Generate synthetic pairs", [py, p(src, "make_poc_pairs.py"), "--matched-pool", pool, "--participants", p(src, "participants.yaml"),
                                        "--n", str(a.n), "--seed", str(a.seed), "--out", p(data, "poc")])
    run("2. Validate and load", [py, p(src, "loader.py"), "--reset", "--db", p(data, "poc.db"), "--schema", p(src, "schema.sql"),
                                 "--participants", p(src, "participants.yaml"), "--xml", p(data, "poc_pacs008.xml"), "--orange", p(data, "poc_orange.json")])
    run("3. Reconcile, post ledger, score", [py, p(src, "recon.py"), "--rerun", "--db", p(data, "poc.db"), "--manifest", p(data, "poc_manifest.csv"), "--out", p(data, "recon")])
    run("4. Reverse-map and check fidelity", [py, p(src, "reverse_mappers.py"), "--db", p(data, "poc.db"), "--xml", p(data, "poc_pacs008.xml"),
                                              "--orange", p(data, "poc_orange.json"), "--out", p(data, "out")])

    db = sqlite3.connect(p(data, "poc.db"))
    audit = {r[0]: r[1:] for r in db.execute("SELECT source_system, rows_in, rows_mapped, rows_rejected FROM load_audit")}
    rc = json.load(open(p(data, "recon_summary.json"))); fid = json.load(open(p(data, "out_summary.json")))
    tr = fid["translation"]
    checks = [
        ("Batch invariant holds (rows in = mapped + rejected)", all(i == m + j for i, m, j in audit.values())),
        ("Ledger balances (no unbalanced transaction)", db.execute("""SELECT COUNT(*) FROM (SELECT SUM(CASE WHEN dc='D' THEN amount_minor ELSE -amount_minor END) x
                                                                  FROM settlement_journal GROUP BY txn_id) WHERE x <> 0""").fetchone()[0] == 0),
        ("Reconciliation agrees with the manifest on every row", rc["correct"] == rc["rows"]),
        ("Round trip: every record identical, no lossy field", fid["records_identical"] == fid["records_checked"] and not fid["lossy_fields"]),
        ("Translation bank -> wallet reproduces the real wallet leg", tr["bank_to_wallet_identical"] == tr["matched_pairs"]),
        ("Translation wallet -> bank reproduces the derivable bank fields", tr["wallet_to_bank_identical"] == tr["matched_pairs"]),
    ]
    if a.n == 400:
        checks.insert(0, ("Counts for n=400: pacs.008 402/382/20 and Orange 394/394/0", audit.get("PACS008") == (402, 382, 20) and audit.get("ORANGE") == (394, 394, 0)))
    print("\n=== RESULT ===")
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print("\nALL CHECKS PASSED" if all(ok for _, ok in checks) else "\nSOME CHECKS FAILED")
    sys.exit(0 if all(ok for _, ok in checks) else 1)


if __name__ == "__main__":
    main()
