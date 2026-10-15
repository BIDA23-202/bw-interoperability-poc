"""
test_mock_core.py  |  Tests for mock_core.py (tasks 4.1 to 4.3). Run from the src folder:  python -m pytest -q
Needs no web framework. Builds its own stand-in matched pool to generate bank records.
"""
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import loader                                        # noqa: E402
from mock_core import Hub, MockWallet, WalletError   # noqa: E402

PARTICIPANTS = os.path.join(HERE, "participants.yaml")


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


def make_payment(wallet, **over):
    token = wallet.issue_token(wallet.basic_header())["access_token"]
    body = {"merchant_key": wallet.merchant_key, "currency": "BWP", "order_id": "E2E-1", "amount": "125.50", "return_url": "https://x.invalid/r",
            "cancel_url": "https://x.invalid/c", "notif_url": "https://x.invalid/n", "lang": "en", "reference": "REF"}
    body.update(over)
    return f"Bearer {token}", body


def test_token_needs_valid_basic_credentials():
    w = MockWallet()
    assert w.issue_token(w.basic_header())["token_type"] == "Bearer"
    for bad in (None, "", "Basic wrong"):
        with pytest.raises(WalletError) as e:
            w.issue_token(bad)
        assert e.value.http_status == 401 and e.value.code == "INVALID_CLIENT"


def test_create_payment_needs_a_bearer_token():
    w = MockWallet()
    _, body = make_payment(w)
    for bad in (None, "Bearer nope", "Basic abc"):
        with pytest.raises(WalletError) as e:
            w.create_payment(bad, body)
        assert e.value.http_status == 401


def test_create_payment_returns_tokens_and_a_mock_url():
    w = MockWallet()
    auth, body = make_payment(w)
    out = w.create_payment(auth, body)
    assert out["status"] == 201 and out["pay_token"] and out["notif_token"]
    assert out["payment_url"].startswith("https://mock-wallet.invalid/")


@pytest.mark.parametrize("over,code,http", [
    ({"merchant_key": "wrong"}, "INVALID_MERCHANT_KEY", 403), ({"currency": "USD"}, "INVALID_CURRENCY", 400),
    ({"order_id": ""}, "INVALID_ORDER_ID", 400), ({"order_id": "E" * 31}, "INVALID_ORDER_ID", 400),
    ({"amount": "0"}, "INVALID_AMOUNT", 400), ({"amount": "-5"}, "INVALID_AMOUNT", 400), ({"amount": "1.234"}, "INVALID_AMOUNT", 400),
    ({"amount": "abc"}, "INVALID_AMOUNT", 400), ({"notif_url": ""}, "MISSING_URL", 400), ({"return_url": None}, "MISSING_URL", 400)])
def test_create_payment_rejections(over, code, http):
    w = MockWallet()
    auth, body = make_payment(w, **over)
    with pytest.raises(WalletError) as e:
        w.create_payment(auth, body)
    assert (e.value.code, e.value.http_status) == (code, http)


def test_duplicate_order_id_is_rejected():
    w = MockWallet()
    auth, body = make_payment(w)
    w.create_payment(auth, body)
    with pytest.raises(WalletError) as e:
        w.create_payment(auth, body)
    assert e.value.code == "DUPLICATE_ORDER_ID" and e.value.http_status == 409


def test_success_flow_with_notification_and_status():
    w = MockWallet()
    auth, body = make_payment(w)
    created = w.create_payment(auth, body)
    note = w.complete(created["pay_token"], "SUCCESS")
    assert note["status"] == "SUCCESS" and note["notif_token"] == created["notif_token"] and note["txnid"]
    got = w.status(auth, {"order_id": "E2E-1", "amount": "125.50", "pay_token": created["pay_token"]})
    assert got["status"] == "SUCCESS" and got["txnid"] == note["txnid"]
    assert w.notifications == [note]


@pytest.mark.parametrize("outcome", ["FAILED", "CANCELLED"])
def test_other_customer_outcomes(outcome):
    w = MockWallet()
    auth, body = make_payment(w)
    created = w.create_payment(auth, body)
    assert w.complete(created["pay_token"], outcome)["status"] == outcome
    assert w.status(auth, {"order_id": "E2E-1", "amount": "125.50", "pay_token": created["pay_token"]})["status"] == outcome


def test_a_final_order_cannot_be_completed_again():
    w = MockWallet()
    auth, body = make_payment(w)
    created = w.create_payment(auth, body)
    w.complete(created["pay_token"], "SUCCESS")
    with pytest.raises(WalletError) as e:
        w.complete(created["pay_token"], "FAILED")
    assert e.value.code == "ALREADY_FINAL" and e.value.http_status == 409


def test_invalid_outcome_and_unknown_token():
    w = MockWallet()
    auth, body = make_payment(w)
    created = w.create_payment(auth, body)
    with pytest.raises(WalletError) as e:
        w.complete(created["pay_token"], "PAID")
    assert e.value.code == "INVALID_OUTCOME"
    with pytest.raises(WalletError) as e:
        w.complete("nope", "SUCCESS")
    assert e.value.code == "ORDER_NOT_FOUND"


def test_session_expires_after_ten_minutes():
    clock = Clock()
    w = MockWallet(clock=clock)
    auth, body = make_payment(w)
    created = w.create_payment(auth, body)
    clock.advance(11)
    assert w.status(auth, {"order_id": "E2E-1", "amount": "125.50", "pay_token": created["pay_token"]})["status"] == "EXPIRED"
    with pytest.raises(WalletError) as e:
        w.complete(created["pay_token"], "SUCCESS")
    assert e.value.code == "EXPIRED" and e.value.http_status == 410


def test_token_expires_after_an_hour():
    clock = Clock()
    w = MockWallet(clock=clock)
    auth, body = make_payment(w)
    clock.advance(61)
    with pytest.raises(WalletError) as e:
        w.create_payment(auth, body)
    assert e.value.code == "INVALID_TOKEN"


def test_status_needs_matching_order_amount_and_token():
    w = MockWallet()
    auth, body = make_payment(w)
    created = w.create_payment(auth, body)
    for bad in ({"order_id": "OTHER", "amount": "125.50"}, {"order_id": "E2E-1", "amount": "99.00"}):
        with pytest.raises(WalletError) as e:
            w.status(auth, {**bad, "pay_token": created["pay_token"]})
        assert e.value.code == "ORDER_NOT_FOUND"


# ---------- the hub, fed with the generated bank records ----------
@pytest.fixture(scope="module")
def bank_records(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mock")
    rng = np.random.default_rng(1)
    n = 3000
    pd.DataFrame({"step": rng.integers(1, 744, n), "type": "TRANSFER", "amount": np.round(rng.lognormal(7, 1.3, n), 2),
                  "nameOrig": ["C%d" % x for x in rng.integers(10**8, 10**9, n)], "oldbalanceOrg": 0.0, "newbalanceOrig": 0.0,
                  "nameDest": ["M%d" % x for x in rng.integers(10**8, 10**9, n)], "oldbalanceDest": 0.0, "newbalanceDest": 0.0,
                  "isFraud": 0, "isFlaggedFraud": 0}).to_csv(tmp / "pool.csv", index=False)
    subprocess.run([sys.executable, os.path.join(HERE, "make_poc_pairs.py"), "--matched-pool", str(tmp / "pool.csv"), "--participants", PARTICIPANTS,
                    "--n", "400", "--seed", "42", "--out", str(tmp / "poc")], check=True, capture_output=True)
    return loader.parse_pacs008(str(tmp / "poc_pacs008.xml"))[1]


def first_valid(hub, records):
    probe = Hub(PARTICIPANTS)
    for r in records:
        if probe.bank_to_wallet(r)["accepted"]:
            return r
    raise AssertionError("no valid record")


def test_hub_success_returns_a_status_report(bank_records):
    hub = Hub(PARTICIPANTS)
    rec = first_valid(hub, bank_records)
    out = hub.bank_to_wallet(rec)
    assert out["accepted"] and out["canonical_status"] == "COMPLETED" and out["iso_status"] == "ACSC"
    assert out["notification_verified"] is True and out["wallet_order"]["status"] == "SUCCESS" and out["wallet_order"]["txnid"]
    req = out["wallet_request"]
    assert req["order_id"] == rec["end_to_end_id"] and req["reference"] == rec["remittance_info"]
    assert req["customer"]["id"] == rec["creditor_account"] and req["customer"]["idType"] == "MSISDN"
    assert req["partner"]["idType"] == "CODE" and req["amount"]["unit"] == "BWP"
    assert str(req["amount"]["value"]) == str(float(rec["amount"]))
    assert hub.get_payment(rec["end_to_end_id"]) == out


@pytest.mark.parametrize("sim,canonical,iso,wallet_status", [("FAILED", "FAILED", "RJCT", "FAILED"), ("CANCELLED", "FAILED", "RJCT", "CANCELLED"),
                                                            ("PENDING", "DISPATCHED", "PDNG", "INITIATED")])
def test_hub_simulated_outcomes(bank_records, sim, canonical, iso, wallet_status):
    hub = Hub(PARTICIPANTS)
    out = hub.bank_to_wallet(first_valid(hub, bank_records), simulate=sim)
    assert (out["canonical_status"], out["iso_status"], out["wallet_order"]["status"]) == (canonical, iso, wallet_status)
    assert out["notification_verified"] is (None if sim == "PENDING" else True)


def test_hub_rejects_duplicates_and_bad_input(bank_records):
    hub = Hub(PARTICIPANTS)
    rec = first_valid(hub, bank_records)
    assert hub.bank_to_wallet(rec)["accepted"]
    dup = hub.bank_to_wallet(rec)
    assert not dup["accepted"] and dup["http_status"] == 409 and dup["reasons"][0]["code"] == "DUPLICATE_KEY"
    bad = hub.bank_to_wallet({**rec, "end_to_end_id": "E2E-NEW", "currency": ""})
    assert not bad["accepted"] and bad["http_status"] == 422 and bad["reasons"][0]["code"] == "MISSING_CURRENCY"
    with pytest.raises(ValueError):
        hub.bank_to_wallet(rec, simulate="MAYBE")


def test_hub_reset_clears_state(bank_records):
    hub = Hub(PARTICIPANTS)
    rec = first_valid(hub, bank_records)
    hub.bank_to_wallet(rec)
    hub.reset()
    assert hub.get_payment(rec["end_to_end_id"]) is None and hub.wallet.notifications == []
    assert hub.bank_to_wallet(rec)["accepted"]


def test_hub_behaves_like_the_loader_on_the_whole_file(bank_records):
    """Same 402 records, same verdicts as loader.py: 382 accepted, 20 rejected for the same reasons."""
    hub = Hub(PARTICIPANTS)
    results = [hub.bank_to_wallet(r) for r in bank_records]
    assert len(results) == 402 and sum(r["accepted"] for r in results) == 382
    reasons = Counter(r["reasons"][0]["code"] for r in results if not r["accepted"])
    assert reasons == {"MISSING_CURRENCY": 6, "DUPLICATE_KEY": 8, "UNKNOWN_BIC": 6}
    assert {r["http_status"] for r in results if not r["accepted"]} == {422, 409}
    assert len(hub.wallet.orders) == 382 and all(o["status"] == "SUCCESS" for o in hub.wallet.orders.values())
