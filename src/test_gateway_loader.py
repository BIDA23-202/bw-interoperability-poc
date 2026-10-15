"""
test_gateway_loader.py  |  Unit and integration tests for gateway.py and loader.py (tasks 3.1, 3.4, part of 3.6).
Run from the src folder:  python -m pytest -q
The tests build their own small stand-in matched pool, so they do not need the real 954K-row file.
"""
import copy
import json
import os
import sqlite3
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gateway   # noqa: E402
import loader    # noqa: E402

ROSTER = gateway.build_roster(os.path.join(HERE, "participants.yaml"))
PACS_OK = dict(msg_id="MSG-1", instr_id="INSTR-1", end_to_end_id="E2E-1", tx_id="TX-1", uetr="3f8a1c2e-9b4d-4e7a-8c5f-1d2e3f4a5b6c",
               clr_sys_ref="CLR-1", amount="1250.50", currency="BWP", settlement_date="2026-08-08", charge_bearer="SHAR",
               debtor_name="D", debtor_account="1155104816", debtor_bic="MOCKBWBRXXX", creditor_name="C",
               creditor_account="26779399039", creditor_bic="MOCKBWOMXXX", remittance_info="Invoice 1")
OM_OK = {"transactionId": "OM-1", "order_id": "E2E-1", "reference": "REF-1", "amount": {"value": 1250.5, "unit": "BWP"},
         "customer": {"id": "26779399039", "idType": "MSISDN"}, "partner": {"id": "BARODA", "idType": "CODE"},
         "status": "SUCCESS", "createdAt": "2026-08-07T19:02:00Z", "pay_token": "abc"}


def test_pacs_valid():
    model, reasons = gateway.validate_pacs(PACS_OK, ROSTER)
    assert reasons == [] and model.amount == 1250.50


@pytest.mark.parametrize("patch,code", [
    ({"currency": ""}, "MISSING_CURRENCY"), ({"currency": None}, "MISSING_CURRENCY"), ({"currency": "USD"}, "INVALID_CURRENCY"),
    ({"amount": "-5"}, "INVALID_AMOUNT"), ({"amount": "0"}, "INVALID_AMOUNT"), ({"amount": "1.234"}, "INVALID_AMOUNT"),
    ({"amount": "abc"}, "INVALID_AMOUNT"), ({"creditor_bic": "MOCKBWZZXXX"}, "UNKNOWN_BIC"), ({"creditor_bic": "bad"}, "INVALID_BIC"),
    ({"creditor_bic": "MOCKBWMZXXX"}, "INACTIVE_PARTICIPANT"), ({"creditor_account": "123"}, "INVALID_MSISDN"),
    ({"debtor_account": "12"}, "INVALID_ACCOUNT"), ({"settlement_date": "2026-13-45"}, "INVALID_DATE"),
    ({"charge_bearer": "XXXX"}, "INVALID_CHARGE_BEARER"), ({"uetr": "not-a-uuid"}, "INVALID_UETR"),
    ({"end_to_end_id": "x" * 36}, "INVALID_KEY"), ({"tx_id": None}, "MISSING_FIELD")])
def test_pacs_rejections(patch, code):
    model, reasons = gateway.validate_pacs({**PACS_OK, **patch}, ROSTER)
    assert model is None and reasons[0][0] == code


def test_orange_valid():
    model, reasons = gateway.validate_orange(OM_OK, ROSTER)
    assert reasons == [] and model.partner_id == "BARODA"


def _om(**changes):
    raw = copy.deepcopy(OM_OK)
    for path, value in changes.items():
        cur = raw
        keys = path.split("__")
        for k in keys[:-1]:
            cur = cur[k]
        cur[keys[-1]] = value
    return raw


@pytest.mark.parametrize("raw,code", [
    (_om(amount__unit="USD"), "INVALID_CURRENCY"), (_om(amount__unit=None), "MISSING_CURRENCY"), (_om(amount__value=-1), "INVALID_AMOUNT"),
    (_om(customer__id="123"), "INVALID_MSISDN"), (_om(customer__idType="CODE"), "INVALID_ID_TYPE"),
    (_om(partner__id="NOBODY"), "UNKNOWN_PARTICIPANT"), (_om(partner__id="MYZAKA"), "INACTIVE_PARTICIPANT"),
    (_om(status="PAID"), "INVALID_STATUS"), (_om(order_id="E" * 31), "INVALID_KEY"), (_om(createdAt="not a date"), "INVALID_DATE")])
def test_orange_rejections(raw, code):
    model, reasons = gateway.validate_orange(raw, ROSTER)
    assert model is None and reasons[0][0] == code


@pytest.fixture(scope="module")
def loaded(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("poc")
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
    s1 = loader.load_pacs008(db, str(tmp / "poc_pacs008.xml"), batch_id="B-PACS", now="2026-10-15T09:00:00Z")
    s2 = loader.load_orange(db, str(tmp / "poc_orange.json"), batch_id="B-OM", now="2026-10-15T09:00:00Z")
    return db, tmp, s1, s2


def test_counts_and_reasons(loaded):
    db, tmp, s1, s2 = loaded
    assert (s1["rows_in"], s1["rows_mapped"], s1["rows_rejected"]) == (402, 382, 20)
    assert s1["reasons"] == {"MISSING_CURRENCY": 6, "DUPLICATE_KEY": 8, "UNKNOWN_BIC": 6}
    assert (s2["rows_in"], s2["rows_mapped"], s2["rows_rejected"]) == (394, 394, 0)


def test_batch_invariant_and_exceptions(loaded):
    db, *_ = loaded
    for rin, mapped, rej in db.execute("SELECT rows_in, rows_mapped, rows_rejected FROM load_audit"):
        assert rin == mapped + rej
    assert db.execute("SELECT COUNT(*) FROM exceptions").fetchone()[0] == 20


def test_identifiers_kept_separate(loaded):
    db, *_ = loaded
    assert db.execute("SELECT COUNT(*) FROM transaction_source_ids WHERE source_system='PACS008'").fetchone()[0] == 382 * 6
    assert db.execute("SELECT COUNT(*) FROM transaction_source_ids WHERE source_system='ORANGE'").fetchone()[0] == 394 * 3
    kinds = {r[0] for r in db.execute("SELECT DISTINCT identifier_kind FROM transaction_source_ids WHERE source_system='PACS008'")}
    assert kinds == {"MSG_ID", "INSTR_ID", "END_TO_END_ID", "TX_ID", "UETR", "CLR_SYS_REF"}


def test_every_transaction_has_status_report(loaded):
    db, *_ = loaded
    missing = db.execute("SELECT COUNT(*) FROM transactions t LEFT JOIN status_reports s ON s.txn_id=t.txn_id WHERE s.report_id IS NULL").fetchone()[0]
    assert missing == 0


def test_amounts_are_exact_integers(loaded):
    db, tmp, *_ = loaded
    assert {r[0] for r in db.execute("SELECT DISTINCT typeof(amount_minor) FROM transactions")} == {"integer"}
    man = pd.read_csv(tmp / "poc_manifest.csv", dtype=str).fillna("")
    clean = man[(man.anomaly_type == "") & (man.iso_present == "1")].iloc[0]
    row = db.execute("SELECT amount_minor FROM transactions WHERE source_system='PACS008' AND end_to_end_key=?", (clean.end_to_end_id,)).fetchone()
    assert row[0] == int(round(float(clean.amount_iso) * 100))


def test_status_derivation(loaded):
    db, *_ = loaded
    assert {r for r in db.execute("SELECT DISTINCT status, status_source FROM transactions WHERE source_system='PACS008'")} == {("DISPATCHED", "DERIVED_ACK")}
    sources = {r[0] for r in db.execute("SELECT DISTINCT status_source FROM transactions WHERE source_system='ORANGE'")}
    assert sources == {"SOURCE_ECHO"}
    assert db.execute("SELECT COUNT(*) FROM transactions WHERE source_system='ORANGE' AND status='FAILED'").fetchone()[0] == 12


def test_reload_is_rejected_as_duplicates(loaded):
    db, tmp, *_ = loaded
    again = loader.load_pacs008(db, str(tmp / "poc_pacs008.xml"), batch_id="B-PACS-2", now="2026-10-15T10:00:00Z")
    assert again["rows_mapped"] == 0 and again["reasons"].get("DUPLICATE_KEY", 0) >= 382
    assert db.execute("SELECT COUNT(*) FROM transactions WHERE source_system='PACS008'").fetchone()[0] == 382
