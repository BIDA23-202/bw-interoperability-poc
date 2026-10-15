"""
gateway.py  |  Gateway validation for the PoC (task 3.1). Pydantic v2.

validate_pacs(raw, roster)   -> (PacsRecord | None, [(code, detail), ...])
validate_orange(raw, roster) -> (OrangeRecord | None, [(code, detail), ...])

`roster` comes from build_roster() and maps BICs and participant codes to active flags.
Reason codes: MISSING_FIELD, MISSING_CURRENCY, INVALID_CURRENCY, INVALID_AMOUNT, INVALID_MSISDN, INVALID_ACCOUNT,
INVALID_DATE, INVALID_KEY, INVALID_UETR, INVALID_BIC, INVALID_CHARGE_BEARER, INVALID_STATUS, INVALID_ID_TYPE,
UNKNOWN_BIC, UNKNOWN_PARTICIPANT, INACTIVE_PARTICIPANT, INVALID_FIELD.  (DUPLICATE_KEY is added by the loader.)
"""
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator

CURRENCY = "BWP"
MSISDN_RE = re.compile(r"^2677\d{7}$")
ACCOUNT_RE = re.compile(r"^\d{10}$")
BIC_RE = re.compile(r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$")
CHARGE_BEARERS = {"DEBT", "CRED", "SHAR", "SLEV"}
OM_STATUSES = {"SUCCESS", "ACCEPTED", "FAILED", "REJECTED", "CANCELLED", "EXPIRED"}


def build_roster(yaml_path):
    """Return {'bics': {bic: active}, 'codes': {code: active}} from participants.yaml."""
    people = yaml.safe_load(open(yaml_path))["participants"]
    return {"bics": {p["bic"]: bool(p["active"]) for p in people},
            "codes": {p["code"]: bool(p["active"]) for p in people}}


def _fail(code, detail):
    raise ValueError(f"{code}: {detail}")


def _amount(v):
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        _fail("INVALID_AMOUNT", f"not a number: {v!r}")
    if not d.is_finite():
        _fail("INVALID_AMOUNT", "not finite")
    if d <= 0:
        _fail("INVALID_AMOUNT", "must be greater than zero")
    if d.as_tuple().exponent < -2:
        _fail("INVALID_AMOUNT", "more than 2 decimal places")
    return d


def _currency(v):
    if v is None or str(v).strip() == "":
        _fail("MISSING_CURRENCY", "currency is absent or empty")
    if str(v).strip() != CURRENCY:
        _fail("INVALID_CURRENCY", f"expected {CURRENCY}, got {v!r}")
    return str(v).strip()


def _lookup(value, table, unknown_code, what):
    if not isinstance(value, str) or value not in table:
        _fail(unknown_code, f"{what} {value!r} is not in the roster")
    if not table[value]:
        _fail("INACTIVE_PARTICIPANT", f"{what} {value!r} is inactive")
    return value


class PacsRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    msg_id: str
    instr_id: str
    end_to_end_id: str
    tx_id: str
    uetr: str
    clr_sys_ref: Optional[str] = None
    amount: Decimal
    currency: Optional[str] = Field(default=None, validate_default=True)
    settlement_date: date
    charge_bearer: str
    debtor_name: str
    debtor_account: str
    debtor_bic: str
    creditor_name: str
    creditor_account: str
    creditor_bic: str
    remittance_info: Optional[str] = None

    @field_validator("instr_id", "end_to_end_id", "tx_id")
    @classmethod
    def _ids(cls, v):
        if not 1 <= len(v) <= 35:
            _fail("INVALID_KEY", "identifier must be 1 to 35 characters")
        return v

    @field_validator("uetr")
    @classmethod
    def _uetr(cls, v):
        try:
            uuid.UUID(v)
        except ValueError:
            _fail("INVALID_UETR", f"not a UUID: {v!r}")
        return v

    @field_validator("amount", mode="before")
    @classmethod
    def _amt(cls, v):
        return _amount(v)

    @field_validator("currency", mode="before")
    @classmethod
    def _ccy(cls, v):
        return _currency(v)

    @field_validator("charge_bearer")
    @classmethod
    def _chrg(cls, v):
        if v not in CHARGE_BEARERS:
            _fail("INVALID_CHARGE_BEARER", f"{v!r}")
        return v

    @field_validator("debtor_account")
    @classmethod
    def _dacct(cls, v):
        if not ACCOUNT_RE.match(v):
            _fail("INVALID_ACCOUNT", "expected 10 digits")
        return v

    @field_validator("creditor_account")
    @classmethod
    def _cacct(cls, v):
        if not MSISDN_RE.match(v):
            _fail("INVALID_MSISDN", "expected 2677 followed by 7 digits")
        return v

    @field_validator("debtor_bic", "creditor_bic")
    @classmethod
    def _bic(cls, v, info: ValidationInfo):
        if not BIC_RE.match(v):
            _fail("INVALID_BIC", f"bad BIC format {v!r}")
        return _lookup(v, (info.context or {}).get("bics", {}), "UNKNOWN_BIC", "BIC")


class OrangeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    transaction_id: str
    order_id: str
    reference: Optional[str] = None
    amount: Decimal
    unit: Optional[str] = Field(default=None, validate_default=True)
    customer_id: str
    customer_id_type: str
    partner_id: str
    partner_id_type: str
    status: str
    created_at: datetime
    pay_token: Optional[str] = None

    @field_validator("order_id")
    @classmethod
    def _oid(cls, v):
        if not 1 <= len(v) <= 30:
            _fail("INVALID_KEY", "order_id must be 1 to 30 characters")
        return v

    @field_validator("amount", mode="before")
    @classmethod
    def _amt(cls, v):
        return _amount(v)

    @field_validator("unit", mode="before")
    @classmethod
    def _unit(cls, v):
        return _currency(v)

    @field_validator("customer_id")
    @classmethod
    def _cid(cls, v):
        if not MSISDN_RE.match(v):
            _fail("INVALID_MSISDN", "expected 2677 followed by 7 digits")
        return v

    @field_validator("customer_id_type")
    @classmethod
    def _ctype(cls, v):
        if v != "MSISDN":
            _fail("INVALID_ID_TYPE", f"customer.idType must be MSISDN, got {v!r}")
        return v

    @field_validator("partner_id_type")
    @classmethod
    def _ptype(cls, v):
        if v != "CODE":
            _fail("INVALID_ID_TYPE", f"partner.idType must be CODE, got {v!r}")
        return v

    @field_validator("partner_id")
    @classmethod
    def _pid(cls, v, info: ValidationInfo):
        return _lookup(v, (info.context or {}).get("codes", {}), "UNKNOWN_PARTICIPANT", "partner code")

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v not in OM_STATUSES:
            _fail("INVALID_STATUS", f"{v!r}")
        return v


def flatten_orange(raw):
    """Turn the nested Orange-style JSON into the flat field names used by OrangeRecord."""
    amt, cust, part = raw.get("amount") or {}, raw.get("customer") or {}, raw.get("partner") or {}
    return {"transaction_id": raw.get("transactionId"), "order_id": raw.get("order_id"), "reference": raw.get("reference"),
            "amount": amt.get("value"), "unit": amt.get("unit"), "customer_id": cust.get("id"),
            "customer_id_type": cust.get("idType"), "partner_id": part.get("id"), "partner_id_type": part.get("idType"),
            "status": raw.get("status"), "created_at": raw.get("createdAt"), "pay_token": raw.get("pay_token")}


def reasons_from(exc: ValidationError):
    """Convert a Pydantic error into [(reason_code, detail)], in field order."""
    out = []
    for err in exc.errors():
        msg = err["msg"]
        field = ".".join(str(p) for p in err["loc"])
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        head, sep, detail = msg.partition(":")
        if sep and re.fullmatch(r"[A-Z_]+", head.strip()):
            out.append((head.strip(), f"{field}: {detail.strip()}"))
        elif err["type"] == "missing" or err.get("input") is None:
            out.append(("MISSING_FIELD", f"{field} is missing"))
        elif err["type"].startswith(("date", "datetime")):
            out.append(("INVALID_DATE", f"{field}: {msg}"))
        else:
            out.append(("INVALID_FIELD", f"{field}: {msg}"))
    return out


def validate_pacs(raw, roster):
    try:
        return PacsRecord.model_validate(raw, context=roster), []
    except ValidationError as exc:
        return None, reasons_from(exc)


def validate_orange(raw, roster):
    try:
        return OrangeRecord.model_validate(flatten_orange(raw), context=roster), []
    except ValidationError as exc:
        return None, reasons_from(exc)
