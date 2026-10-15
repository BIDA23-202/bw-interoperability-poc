"""
test_recon.py  |  Tests for recon.py (tasks 5.1 to 5.4). Run from the src folder:  python -m pytest -q
Builds its own stand-in matched pool, so it does not need the real file.
"""
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import loader   # noqa: E402
import recon    # noqa: E402


def bank(key="K1", amount=10000, msisdn="26771111111", settled="2026-08-02", status="DISPATCHED", txn_id=1):
    return {"txn_id": txn_id, "key": key, "amount_minor": amount, "currency": "BWP", "settled_at": settled, "initiated_at": "2026-10-15T08:00:00Z",
            "status": status, "debtor_participant_id": 1, "creditor_participant_id": 8, "msisdn": msisdn}


def wallet(key="K1", amount=10000, msisdn="26771111111", created="2026-08-01T10:00:00Z", status="COMPLETED", txn_id=100):
    return {"txn_id": txn_id, "key": key, "amount_minor": amount, "currency": "BWP", "settled_at": None, "initiated_at": created,
            "status": status, "debtor_participant_id": 1, "creditor_participant_id": 8, "msisdn": msisdn}


def test_clean_pair_matches():
    outcome, diff, gap, flags = recon.evaluate_pair(bank(), wallet())
    assert (outcome, diff, gap, flags) == ("MATCHED", 0, 1, [])


def test_amount_difference_is_discrepant():
    outcome, diff, gap, flags = recon.evaluate_pair(bank(), wallet(amount=10500))
    assert outcome == "DISCREPANT" and diff == 500 and flags == ["AMOUNT_DIFF"]


def test_tolerance_absorbs_small_difference():
    assert recon.evaluate_pair(bank(), wallet(amount=10050), tol_minor=100)[0] == "MATCHED"


def test_failed_wallet_is_status_mismatch():
    outcome, _, _, flags = recon.evaluate_pair(bank(), wallet(status="FAILED"))
    assert outcome == "DISCREPANT" and "STATUS_MISMATCH" in flags


def test_lag_outlier_is_flagged_but_matched():
    outcome, _, gap, flags = recon.evaluate_pair(bank(settled="2026-08-09"), wallet())
    assert outcome == "MATCHED" and gap == 8 and flags == ["LAG_OUTLIER"]


def test_party_mismatch():
    assert "PARTY_MISMATCH" in recon.evaluate_pair(bank(), wallet(msisdn="26772222222"))[3]


def test_key_match_then_tolerant_fallback():
    banks = [bank("A", txn_id=1), bank("B", amount=20000, msisdn="26773333333", txn_id=2)]
    wallets = [wallet("A", txn_id=10), wallet("OTHER", amount=20100, msisdn="26773333333", txn_id=11)]
    pairs = {(b["key"] if b else None, w["key"] if w else None, m) for b, w, m in recon.match(banks, wallets)}
    assert ("A", "A", "KEY") in pairs and ("B", "OTHER", "TOLERANT") in pairs


def test_ambiguous_tolerant_candidates_are_not_matched():
    banks = [bank("B", txn_id=2)]
    wallets = [wallet("X", txn_id=11), wallet("Y", txn_id=12)]
    methods = [m for _, _, m in recon.match(banks, wallets)]
    assert "TOLERANT" not in methods


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("recon")
    rng = np.random.default_rng(1)
    n = 3000
    pd.DataFrame({"step": rng.integers(1, 744, n), "type": "TRANSFER", "amount": np.round(rng.lognormal(7, 1.3, n), 2),
                  "nameOrig": ["C%d" % x for x in rng.integers(10**8, 10**9, n)], "oldbalanceOrg": 0.0, "newbalanceOrig": 0.0,
                  "nameDest": ["M%d" % x for x in rng.integers(10**8, 10**9, n)], "oldbalanceDest": 0.0, "newbalanceDest": 0.0,
                  "isFraud": 0, "isFlaggedFraud": 0}).to_csv(tmp / "pool.csv", index=False)
    subprocess.run([sys.executable, os.path.join(HERE, "make_poc_pairs.py"), "--matched-pool", str(tmp / "pool.csv"),
                    "--participants", os.path.join(HERE, "participants.yaml"), "--n", "400", "--seed", "42", "--out", str(tmp / "poc")],
                   check=True, capture_output=True)
    db = loader.connect(str(tmp / "poc.db"))
    loader.create_schema(db, os.path.join(HERE, "schema.sql"))
    loader.seed_participants(db, os.path.join(HERE, "participants.yaml"))
    loader.load_pacs008(db, str(tmp / "poc_pacs008.xml"), batch_id="B1", now="2026-10-15T09:00:00Z")
    loader.load_orange(db, str(tmp / "poc_orange.json"), batch_id="B2", now="2026-10-15T09:00:00Z")
    outcomes = recon.reconcile(db, now="2026-10-15T10:00:00Z")
    return db, tmp, outcomes


def test_outcome_counts(pipeline):
    _, _, outcomes = pipeline
    assert outcomes == {"MATCHED": 352, "DISCREPANT": 24, "UNMATCHED": 24}


def test_scores_against_manifest(pipeline):
    db, tmp, _ = pipeline
    res, summary = recon.score(db, str(tmp / "poc_manifest.csv"))
    assert summary["rows"] == 408 and summary["correct"] == 408
    assert summary["false_matches"] == 0 and summary["missed_matches"] == 0
    assert res["lag_matches_manifest"].all()


def test_journal_is_balanced_and_only_for_matched(pipeline):
    db, *_ = pipeline
    assert db.execute("SELECT COUNT(*) FROM settlement_journal").fetchone()[0] == 352 * 2
    assert recon._imbalances(db) == 0
    posted = db.execute("""SELECT COUNT(DISTINCT j.txn_id) FROM settlement_journal j JOIN reconciliation_records r ON r.bank_txn_id=j.txn_id
                           WHERE r.outcome='MATCHED'""").fetchone()[0]
    assert posted == 352
    d, c = db.execute("SELECT SUM(CASE WHEN dc='D' THEN amount_minor END), SUM(CASE WHEN dc='C' THEN amount_minor END) FROM settlement_journal").fetchone()
    assert d == c


def test_status_manager(pipeline):
    db, *_ = pipeline
    got = dict(db.execute("SELECT status, COUNT(*) FROM transactions WHERE source_system='PACS008' GROUP BY status").fetchall())
    assert got["COMPLETED"] == 352 and got["FAILED"] == 12
    assert got["DISPATCHED"] == 382 - 352 - 12      # amount differences and bank-only items stay pending
    reports = db.execute("SELECT COUNT(*) FROM status_reports WHERE source='DERIVED_LEDGER'").fetchone()[0]
    assert reports == 382


def test_exception_queue(pipeline):
    db, *_ = pipeline
    got = dict(db.execute("SELECT reason_code, COUNT(*) FROM exceptions WHERE source_system='RECON' GROUP BY reason_code").fetchall())
    assert got == {"AMOUNT_DIFF": 12, "STATUS_MISMATCH": 12, "LAG_OUTLIER": 8, "NO_WALLET_LEG": 6, "NO_BANK_LEG": 18}


def test_rerun_rules(pipeline):
    db, *_ = pipeline
    with pytest.raises(RuntimeError):
        recon.reconcile(db)
    again = recon.reconcile(db, rerun=True, now="2026-10-15T11:00:00Z")
    assert again == {"MATCHED": 352, "DISCREPANT": 24, "UNMATCHED": 24}
    assert db.execute("SELECT COUNT(*) FROM settlement_journal").fetchone()[0] == 704
    assert db.execute("SELECT COUNT(*) FROM reconciliation_records").fetchone()[0] == 400
