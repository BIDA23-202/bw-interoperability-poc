"""
test_mock_gateway_api.py  |  HTTP tests for mock_gateway.py. Skipped automatically when FastAPI is not installed.
Run from the src folder after:  pip install fastapi uvicorn httpx
"""
import os
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mock_gateway   # noqa: E402

client = TestClient(mock_gateway.app)
BANK_RECORD = {"msg_id": "MSG-1", "instr_id": "INSTR-1", "end_to_end_id": "E2E-API-1", "tx_id": "TX-1", "uetr": "3f8a1c2e-9b4d-4e7a-8c5f-1d2e3f4a5b6c",
               "clr_sys_ref": "CLR-1", "amount": "1250.50", "currency": "BWP", "settlement_date": "2026-08-08", "charge_bearer": "SHAR",
               "debtor_name": "DBTR-1", "debtor_account": "1155104816", "debtor_bic": "MOCKBWBRXXX", "creditor_name": "CDTR-1",
               "creditor_account": "26779399039", "creditor_bic": "MOCKBWOMXXX", "remittance_info": "Invoice 1"}


@pytest.fixture(autouse=True)
def fresh_state():
    client.post("/mock/reset")


def bearer():
    basic = mock_gateway.wallet.basic_header()
    r = client.post("/oauth/v3/token", headers={"Authorization": basic})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_health_says_simulation():
    r = client.get("/health")
    assert r.status_code == 200 and "SIMULATION" in r.json()["mode"]


def test_token_requires_credentials():
    assert client.post("/oauth/v3/token").status_code == 401
    assert client.post("/oauth/v3/token", headers={"Authorization": "Basic nope"}).json()["detail"]["code"] == "INVALID_CLIENT"


def test_full_wallet_flow_over_http():
    h = bearer()
    body = {"merchant_key": mock_gateway.wallet.merchant_key, "currency": "BWP", "order_id": "API-ORDER-1", "amount": 125.5,
            "return_url": "https://x.invalid/r", "cancel_url": "https://x.invalid/c", "notif_url": "https://x.invalid/n", "reference": "REF"}
    created = client.post("/orange-money-webpay/dev/v1/webpayment", json=body, headers=h)
    assert created.status_code == 201
    token = created.json()["pay_token"]
    note = client.post(f"/mock/customer/pay/{token}", json={"outcome": "SUCCESS"})
    assert note.status_code == 200 and note.json()["notif_token"] == created.json()["notif_token"]
    status = client.post("/orange-money-webpay/dev/v1/transactionstatus", json={"order_id": "API-ORDER-1", "amount": 125.5, "pay_token": token}, headers=h)
    assert status.status_code == 200 and status.json()["status"] == "SUCCESS"
    assert len(client.get("/mock/notifications").json()) == 1


def test_wallet_errors_use_the_right_status_codes():
    h = bearer()
    body = {"merchant_key": "wrong", "currency": "BWP", "order_id": "A", "amount": 1, "return_url": "r", "cancel_url": "c", "notif_url": "n"}
    r = client.post("/orange-money-webpay/dev/v1/webpayment", json=body, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "INVALID_MERCHANT_KEY"
    assert client.post("/orange-money-webpay/dev/v1/webpayment", json=body).status_code == 401
    assert client.post("/orange-money-webpay/dev/v1/webpayment", json={"order_id": "A"}, headers=h).status_code == 422   # body fails validation


def test_hub_bank_to_wallet_success_and_lookup():
    r = client.post("/interop/v1/bank-to-wallet", json=BANK_RECORD)
    assert r.status_code == 200
    out = r.json()
    assert out["canonical_status"] == "COMPLETED" and out["iso_status"] == "ACSC" and out["notification_verified"] is True
    again = client.get("/interop/v1/payments/E2E-API-1")
    assert again.status_code == 200 and again.json()["end_to_end_id"] == "E2E-API-1"
    assert client.get("/interop/v1/payments/NOPE").status_code == 404


def test_hub_rejections_and_simulations():
    assert client.post("/interop/v1/bank-to-wallet", json=BANK_RECORD).status_code == 200
    dup = client.post("/interop/v1/bank-to-wallet", json=BANK_RECORD)
    assert dup.status_code == 409 and dup.json()["detail"][0]["code"] == "DUPLICATE_KEY"
    bad = client.post("/interop/v1/bank-to-wallet", json={**BANK_RECORD, "end_to_end_id": "E2E-API-2", "currency": ""})
    assert bad.status_code == 422 and bad.json()["detail"][0]["code"] == "MISSING_CURRENCY"
    failed = client.post("/interop/v1/bank-to-wallet?simulate=FAILED", json={**BANK_RECORD, "end_to_end_id": "E2E-API-3"})
    assert failed.status_code == 200 and failed.json()["iso_status"] == "RJCT"
    assert client.post("/interop/v1/bank-to-wallet?simulate=MAYBE", json={**BANK_RECORD, "end_to_end_id": "E2E-API-4"}).status_code == 400
