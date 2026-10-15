"""
test_reverse.py  |  Tests for reverse_mappers.py (tasks 3.5 and 3.7). Run from the src folder:  python -m pytest -q
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
import gateway          # noqa: E402
import loader           # noqa: E402
import recon            # noqa: E402
import reverse_mappers as rm   # noqa: E402


def test_money_formatting_is_exact():
    assert rm._money(125050) == "1250.50" and rm._money(5) == "0.05" and rm._money(100) == "1.00" and rm._money(1) == "0.01"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rev")
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
    recon.reconcile(db, now="2026-10-15T10:00:00Z")
    return db, tmp


def test_round_trip_has_no_loss(world):
    db, tmp = world
    df, summary = rm.roundtrip_fidelity(db, str(tmp / "poc_pacs008.xml"), str(tmp / "poc_orange.json"))
    assert summary["records_checked"] == 776 and summary["records_identical"] == 776
    assert summary["lossy_fields"] == []
    assert summary["by_source"]["PACS008"] == {"records": 382, "identical": 382}
    assert summary["by_source"]["ORANGE"] == {"records": 394, "identical": 394}


def test_service_level_charge_bearer_is_kept(world):
    db, tmp = world
    values = {r[0] for r in db.execute("SELECT DISTINCT charge_bearer FROM transactions WHERE source_system='PACS008'")}
    assert "SERVICE_LEVEL" in values and "SHARED" in values
    bank = rm.load_canonical(db, "PACS008")
    back = {rm.canonical_to_pacs_record(c)["charge_bearer"] for c in bank.values()}
    assert back == {"DEBT", "CRED", "SHAR", "SLEV"}


def test_orange_native_status_is_restored(world):
    db, tmp = world
    original = pd.Series([r["status"] for r in json.load(open(tmp / "poc_orange.json"))]).value_counts().to_dict()
    wallet = rm.load_canonical(db, "ORANGE")
    restored = pd.Series([rm.canonical_to_orange_record(c)["status"] for c in wallet.values()]).value_counts().to_dict()
    assert original == restored


def test_written_xml_parses_back(world, tmp_path):
    db, _ = world
    bank = rm.load_canonical(db, "PACS008")
    records = [rm.canonical_to_pacs_record(c) for c in bank.values()]
    path = tmp_path / "out.xml"
    rm.build_pacs008_xml(records, "2026-10-15T08:00:00Z", "MSG-TEST").write(str(path), encoding="utf-8", xml_declaration=True)
    header, parsed = loader.parse_pacs008(str(path))
    assert len(parsed) == 382 and header["msg_id"] == "MSG-TEST"
    assert [p["end_to_end_id"] for p in parsed] == [r["end_to_end_id"] for r in records]
    from xml.etree import ElementTree as ET
    root = ET.parse(path).getroot()
    nb = next(e for e in root.iter() if e.tag.endswith("NbOfTxs")).text
    assert nb == "382"


def test_translation_bank_to_wallet_matches_real_wallet_leg(world):
    db, _ = world
    tr = rm.translation_fidelity(db)
    assert tr["matched_pairs"] == 352
    assert tr["bank_to_wallet_identical"] == 352


def test_translation_wallet_to_bank_matches_derivable_fields(world):
    db, _ = world
    assert rm.translation_fidelity(db)["wallet_to_bank_identical"] == 352


def test_wallet_to_bank_needs_extra_data_to_pass_the_gateway(world):
    db, _ = world
    wallet = rm.load_canonical(db, "ORANGE")
    bic = db.execute("SELECT bic FROM participants WHERE code='ORANGE_MONEY'").fetchone()[0]
    roster = gateway.build_roster(os.path.join(HERE, "participants.yaml"))
    c = next(c for c in wallet.values() if c["status"] == "COMPLETED")
    raw = {**rm.translate_wallet_to_bank(c, bic), "msg_id": "MSG-X", "charge_bearer": "SHAR", "creditor_name": "CDTR", "clr_sys_ref": None}
    model, reasons = gateway.validate_pacs(raw, roster)
    assert model is None
    assert {r[0] for r in reasons} == {"MISSING_FIELD"} and len(reasons) == 2     # debtor name and debtor account only
    raw.update(debtor_name="UNKNOWN", debtor_account="0000000000")
    model, reasons = gateway.validate_pacs(raw, roster)
    assert reasons == [] and model is not None
