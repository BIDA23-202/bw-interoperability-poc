"""
mock_core.py  |  Framework-free core of the mock gateway (tasks 4.1 to 4.3).

MockWallet : in-memory SIMULATION of an Orange Money Web Payment style sandbox (OAuth token, create payment,
             customer completes or cancels, notification, transaction status). It is not Orange's code and not their API.
Hub        : the interoperability layer in front of it. bank_to_wallet() validates a pacs.008-style record through
             gateway.py, translates it into the wallet request, drives the mock wallet, verifies the notification,
             and returns a pacs.002-style status report.

Statuses follow the project's dataset (INITIATED, SUCCESS, FAILED, CANCELLED, EXPIRED). The 2019 Orange sandbox guide lists
INITIATED, PENDING, EXPIRED, SUCCESS, FAILED, so compare them in task 1.2 before presenting this as Orange-faithful.
All state is in memory: a restart clears it. Nothing here touches poc.db.
"""
import base64
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import yaml

from gateway import build_roster, validate_pacs
from reverse_mappers import translate_bank_to_wallet

CUSTOMER_OUTCOMES = {"SUCCESS", "FAILED", "CANCELLED"}
HUB_SIMULATIONS = CUSTOMER_OUTCOMES | {"PENDING"}
WALLET_TO_CANONICAL = {"SUCCESS": ("COMPLETED", "ACSC"), "FAILED": ("FAILED", "RJCT"), "CANCELLED": ("FAILED", "RJCT"),
                       "EXPIRED": ("FAILED", "RJCT"), "INITIATED": ("DISPATCHED", "PDNG")}


class WalletError(Exception):
    def __init__(self, http_status, code, message):
        super().__init__(f"{code}: {message}")
        self.http_status, self.code, self.message = http_status, code, message


def _utcnow():
    return datetime.now(timezone.utc)


def _amount(v):
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise WalletError(400, "INVALID_AMOUNT", f"not a number: {v!r}")
    if not d.is_finite() or d <= 0 or d.as_tuple().exponent < -2:
        raise WalletError(400, "INVALID_AMOUNT", "amount must be positive with at most 2 decimal places")
    return d


class MockWallet:
    def __init__(self, client_id="mock-client", client_secret="mock-secret", merchant_key="MOCK-MERCHANT-KEY",
                 clock=_utcnow, expiry_minutes=10):
        self.client_id, self.client_secret, self.merchant_key = client_id, client_secret, merchant_key
        self.clock, self.expiry = clock, timedelta(minutes=expiry_minutes)
        self.reset()

    def reset(self):
        self.tokens, self.orders, self.notifications = {}, {}, []

    # --- auth ---
    def basic_header(self):
        return "Basic " + base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()

    def issue_token(self, authorization):
        if not authorization or authorization != self.basic_header():
            raise WalletError(401, "INVALID_CLIENT", "missing or wrong Basic credentials")
        token = secrets.token_hex(16)
        self.tokens[token] = self.clock() + timedelta(hours=1)
        return {"token_type": "Bearer", "access_token": token, "expires_in": 3600}

    def _check_bearer(self, authorization):
        if not authorization or not authorization.startswith("Bearer "):
            raise WalletError(401, "MISSING_TOKEN", "Bearer token required")
        expires = self.tokens.get(authorization[7:])
        if expires is None or expires < self.clock():
            raise WalletError(401, "INVALID_TOKEN", "unknown or expired token")

    # --- payment lifecycle ---
    def create_payment(self, authorization, body):
        self._check_bearer(authorization)
        if body.get("merchant_key") != self.merchant_key:
            raise WalletError(403, "INVALID_MERCHANT_KEY", "merchant key not recognised")
        if body.get("currency") not in ("BWP", "OUV"):
            raise WalletError(400, "INVALID_CURRENCY", f"unsupported currency {body.get('currency')!r}")
        order_id = body.get("order_id")
        if not isinstance(order_id, str) or not 1 <= len(order_id) <= 30:
            raise WalletError(400, "INVALID_ORDER_ID", "order_id must be 1 to 30 characters")
        if any(o["order_id"] == order_id for o in self.orders.values()):
            raise WalletError(409, "DUPLICATE_ORDER_ID", f"order_id {order_id!r} already used")
        amount = _amount(body.get("amount"))
        for field in ("return_url", "cancel_url", "notif_url"):
            if not body.get(field):
                raise WalletError(400, "MISSING_URL", f"{field} is required")
        pay_token, notif_token = secrets.token_hex(12), secrets.token_hex(12)
        self.orders[pay_token] = {"order_id": order_id, "amount": amount, "currency": body["currency"], "status": "INITIATED",
                                  "created_at": self.clock(), "notif_token": notif_token, "notif_url": body["notif_url"],
                                  "reference": body.get("reference"), "txnid": None}
        return {"status": 201, "message": "OK", "pay_token": pay_token, "payment_url": f"https://mock-wallet.invalid/pay/{pay_token}",
                "notif_token": notif_token}

    def _refresh(self, order):
        if order["status"] == "INITIATED" and self.clock() - order["created_at"] > self.expiry:
            order["status"] = "EXPIRED"

    def complete(self, pay_token, outcome="SUCCESS"):
        """The customer side: pay, fail (wrong OTP) or cancel. Returns the notification the wallet would POST to notif_url."""
        order = self.orders.get(pay_token)
        if order is None:
            raise WalletError(404, "ORDER_NOT_FOUND", "unknown pay_token")
        if outcome not in CUSTOMER_OUTCOMES:
            raise WalletError(400, "INVALID_OUTCOME", f"outcome must be one of {sorted(CUSTOMER_OUTCOMES)}")
        self._refresh(order)
        if order["status"] == "EXPIRED":
            raise WalletError(410, "EXPIRED", "the payment session expired")
        if order["status"] != "INITIATED":
            raise WalletError(409, "ALREADY_FINAL", f"order is already {order['status']}")
        order["status"] = outcome
        if outcome == "SUCCESS":
            order["txnid"] = "MP" + secrets.token_hex(6).upper()
        note = {"order_id": order["order_id"], "status": outcome, "notif_token": order["notif_token"], "txnid": order["txnid"],
                "amount": str(order["amount"]), "currency": order["currency"]}
        self.notifications.append(note)
        return note

    def status(self, authorization, body):
        self._check_bearer(authorization)
        order = self.orders.get(body.get("pay_token"))
        if order is None or order["order_id"] != body.get("order_id") or order["amount"] != _amount(body.get("amount")):
            raise WalletError(404, "ORDER_NOT_FOUND", "no order matches order_id, amount and pay_token")
        self._refresh(order)
        return {"status": order["status"], "order_id": order["order_id"], "txnid": order["txnid"]}


class Hub:
    def __init__(self, participants_path, wallet=None):
        self.roster = build_roster(participants_path)
        people = yaml.safe_load(open(participants_path))["participants"]
        self.bic_to_code = {p["bic"]: p["code"] for p in people}
        self.wallet = wallet or MockWallet()
        self.processed = {}

    def reset(self):
        self.processed = {}
        self.wallet.reset()

    def bank_to_wallet(self, raw, simulate="SUCCESS"):
        if simulate not in HUB_SIMULATIONS:
            raise ValueError(f"simulate must be one of {sorted(HUB_SIMULATIONS)}")
        model, reasons = validate_pacs(raw, self.roster)
        if model is None:
            return {"accepted": False, "http_status": 422, "reasons": [{"code": c, "detail": d} for c, d in reasons]}
        key = model.end_to_end_id
        if key in self.processed:
            return {"accepted": False, "http_status": 409, "reasons": [{"code": "DUPLICATE_KEY", "detail": f"key {key!r} already processed"}]}
        canonical = {"key": key, "remittance_info": model.remittance_info, "amount_minor": int((model.amount * 100).to_integral_value()),
                     "currency": model.currency, "creditor_id": model.creditor_account, "debtor_code": self.bic_to_code[model.debtor_bic]}
        request = translate_bank_to_wallet(canonical)
        token = self.wallet.issue_token(self.wallet.basic_header())["access_token"]
        bearer = f"Bearer {token}"
        created = self.wallet.create_payment(bearer, {
            "merchant_key": self.wallet.merchant_key, "currency": request["amount"]["unit"], "order_id": request["order_id"],
            "amount": str(model.amount), "return_url": "https://hub.invalid/return", "cancel_url": "https://hub.invalid/cancel",
            "notif_url": "https://hub.invalid/notify", "lang": "en", "reference": request["reference"]})
        verified = None
        if simulate != "PENDING":
            note = self.wallet.complete(created["pay_token"], simulate)
            verified = note["notif_token"] == created["notif_token"] and note["order_id"] == request["order_id"]
        final = self.wallet.status(bearer, {"order_id": request["order_id"], "amount": str(model.amount), "pay_token": created["pay_token"]})
        canonical_status, iso = WALLET_TO_CANONICAL[final["status"]]
        result = {"accepted": True, "http_status": 200, "end_to_end_id": key, "wallet_request": request,
                  "wallet_order": {"pay_token": created["pay_token"], "payment_url": created["payment_url"], "status": final["status"], "txnid": final["txnid"]},
                  "notification_verified": verified, "canonical_status": canonical_status, "iso_status": iso,
                  "status_report": {"iso_status": iso, "canonical_status": canonical_status, "wallet_status": final["status"],
                                    "source": "SOURCE_ECHO", "note": "wallet status is an echo, not a source of truth"}}
        self.processed[key] = result
        return result

    def get_payment(self, end_to_end_id):
        return self.processed.get(end_to_end_id)
