import copy
import re
import threading
import time
import uuid
from datetime import datetime, timezone

from registry import (
    ADAPTERS, FEE_POLICIES, PARTICIPANTS, capability,
)

FEE_ACCOUNT = "DEMO-FEES-COLLECTED"

SCENARIOS = {
    "SUCCESS", "PENDING", "DESTINATION_FAILED",
    "INSUFFICIENT_FUNDS", "COMPENSATION_FAILED", "CANCELLED",
}


class DemoError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat()


def acct(pid):
    return PARTICIPANTS[pid]["account"]


def kind(pid):
    return PARTICIPANTS[pid]["kind"]


def minor_units(value):
    text = str(value)
    if not re.fullmatch(r"(?:0|[1-9]\d{0,8})(?:\.\d{1,2})?", text):
        raise DemoError(
            "Amount must be a positive decimal with at most two decimal places."
        )
    whole, _, fraction = text.partition(".")
    amount = int(whole) * 100 + int(fraction.ljust(2, "0"))
    if amount <= 0:
        raise DemoError("Amount must be greater than zero.")
    return amount


def money(amount):
    return f"{amount // 100}.{amount % 100:02d}"


def compute_fee(policy_id, amount):
    policy = FEE_POLICIES[policy_id]
    fee = policy["flat_minor"] + (amount * policy["bps"] + 5000) // 10000
    fee = max(fee, policy["min_minor"])
    if policy["cap_minor"] is not None:
        fee = min(fee, policy["cap_minor"])
    return fee


def amount_fields(adapter, minor):
    value = money(minor)
    if adapter["nested"]:
        return {adapter["amount"]: {"value": value, "currency": "BWP"}}
    return {adapter["amount"]: value, "currency": "BWP"}


def read_amount(adapter, payload):
    if adapter["nested"]:
        block = payload[adapter["amount"]]
        return block["value"], block["currency"]
    return payload[adapter["amount"]], payload["currency"]


class DemoEngine:
    QUOTE_TTL = 120

    def __init__(self, clock=time.monotonic):
        self.lock = threading.RLock()
        self.clock = clock
        self.accounts = {}

        for pid, p in PARTICIPANTS.items():
            self.accounts[p["account"]] = {
                "provider": pid,
                "provider_name": p["name"],
                "kind": p["kind"],
                "account": p["account"],
                "opening_minor": 100_000,
                "balance_minor": 100_000,
            }

        self.accounts[FEE_ACCOUNT] = {
            "provider": "SYSTEM",
            "provider_name": "Demo fee account",
            "kind": "SYSTEM",
            "account": FEE_ACCOUNT,
            "opening_minor": 0,
            "balance_minor": 0,
        }

        self.transfers = {}
        self.references = {}
        self.quotes = {}

    # ----- events and postings -------------------------------------------

    def _event(self, t, code, description):
        t["events"].append({
            "sequence": len(t["events"]) + 1,
            "timestamp": now(),
            "code": code,
            "description": description,
        })

    def _get(self, t, key):
        if key.startswith("CLEARING_PRINCIPAL:"):
            return t["clearing_principal_minor"]
        if key.startswith("CLEARING_FEE:"):
            return t["clearing_fee_minor"]
        return self.accounts[key]["balance_minor"]

    def _set(self, t, key, value):
        if key.startswith("CLEARING_PRINCIPAL:"):
            t["clearing_principal_minor"] = value
        elif key.startswith("CLEARING_FEE:"):
            t["clearing_fee_minor"] = value
        else:
            self.accounts[key]["balance_minor"] = value

    def _post(self, t, from_key, to_key, amount, operation):
        if amount == 0:
            return
        if self._get(t, from_key) < amount:
            raise DemoError("Insufficient funds for posting.", 409)

        self._set(t, from_key, self._get(t, from_key) - amount)
        self._set(t, to_key, self._get(t, to_key) + amount)

        t["journal_seq"] += 1
        journal = f"{t['id']}-J{t['journal_seq']}"

        for key, delta in ((from_key, -amount), (to_key, amount)):
            t["ledger"].append({
                "journal_id": journal,
                "timestamp": now(),
                "operation": operation,
                "account": key,
                "delta_minor": delta,
                "currency": "BWP",
            })

    # ----- adapters -------------------------------------------------------

    def _source_payload(self, t):
        s, d = t["source"], t["destination"]

        if kind(s) == "BANK":
            return {
                "contract": "project.bank-instruction.v1",
                "instruction_id": t["reference"],
                "created_at": t["created_at"],
                "debtor_account": acct(s),
                "creditor": {"institution": d, "account": acct(d)},
                "amount": money(t["amount_minor"]),
                "currency": "BWP",
            }

        ad = ADAPTERS[s]
        payload = {
            "contract": f"project.{ad['contract']}-source.v1",
            ad["ref"]: t["reference"],
            "created_at": t["created_at"],
            ad["sender"]: acct(s),
            "recipient_institution": d,
            ad["recipient"]: acct(d),
        }
        payload.update(amount_fields(ad, t["amount_minor"]))
        return payload

    def _canonical(self, t):
        s = t["source"]
        p = t["source_payload"]

        if kind(s) == "BANK":
            reference = p["instruction_id"]
            account = p["debtor_account"]
            dest = p["creditor"]["institution"]
            dest_account = p["creditor"]["account"]
            amount, currency = p["amount"], p["currency"]
        else:
            ad = ADAPTERS[s]
            reference = p[ad["ref"]]
            account = p[ad["sender"]]
            dest = p["recipient_institution"]
            dest_account = p[ad["recipient"]]
            amount, currency = read_amount(ad, p)

        if (
            minor_units(amount) != t["amount_minor"]
            or currency != "BWP"
            or dest != t["destination"]
            or dest_account != acct(dest)
        ):
            raise DemoError("Source mapping failed validation.")

        if not capability(s, dest)["allowed"]:
            raise DemoError("Route is not permitted for these participants.", 422)

        return {
            "schema": "project.canonical-transfer.v1",
            "transaction_id": t["id"],
            "reference": reference,
            "timestamp": t["created_at"],
            "direction": t["direction"],
            "principal_minor": t["amount_minor"],
            "fee_minor": t["fee_minor"],
            "total_debit_minor": t["total_debit_minor"],
            "fee_policy": t["fee_policy"],
            "quote_id": t["quote_id"],
            "currency": currency,
            "debtor": {
                "institution": s, "name": PARTICIPANTS[s]["name"],
                "kind": kind(s), "account": account,
            },
            "creditor": {
                "institution": dest, "name": PARTICIPANTS[dest]["name"],
                "kind": kind(dest), "account": dest_account,
            },
        }

    def _destination_payload(self, canonical):
        d = canonical["creditor"]["institution"]
        account = canonical["creditor"]["account"]
        minor = canonical["principal_minor"]

        if kind(d) == "BANK":
            return {
                "contract": "project.bank-credit.v1",
                "instruction_id": canonical["transaction_id"],
                "creditor_account": account,
                "instructed_amount": money(minor),
                "currency": canonical["currency"],
            }

        ad = ADAPTERS[d]
        payload = {
            "contract": f"project.{ad['contract']}-credit.v1",
            ad["ref"]: canonical["transaction_id"],
            ad["recipient"]: account,
        }
        payload.update(amount_fields(ad, minor))
        return payload

    def _validate_destination(self, t):
        d = t["destination"]
        p = t["destination_payload"]

        if kind(d) == "BANK":
            account = p["creditor_account"]
            amount, currency = p["instructed_amount"], p["currency"]
        else:
            ad = ADAPTERS[d]
            account = p[ad["recipient"]]
            amount, currency = read_amount(ad, p)

        if (
            account != acct(d)
            or minor_units(amount) != t["amount_minor"]
            or currency != "BWP"
        ):
            raise DemoError("Destination adapter output failed validation.")

    # ----- accounting views ----------------------------------------------

    def _reconciliation(self, t):
        src, dst = acct(t["source"]), acct(t["destination"])

        def net(account):
            return sum(
                e["delta_minor"] for e in t["ledger"] if e["account"] == account
            )

        P, F = t["amount_minor"], t["fee_minor"]
        actual = (
            net(src), net(dst), net(FEE_ACCOUNT),
            t["clearing_principal_minor"], t["clearing_fee_minor"],
        )

        status = t["status"]
        if status == "SETTLED":
            expected = (-(P + F), P, F, 0, 0)
        elif status in {"PENDING", "EXCEPTION", "REVERSING"}:
            expected = (-(P + F), 0, 0, P, F)
        elif status in {"REJECTED", "REVERSED", "CANCELLED"}:
            expected = (0, 0, 0, 0, 0)
        else:
            expected = None

        return {
            "journal_balanced": sum(e["delta_minor"] for e in t["ledger"]) == 0,
            "session_money_conserved": self._conserved(),
            "status_consistent": None if expected is None else actual == expected,
            "source_net_minor": actual[0],
            "destination_net_minor": actual[1],
            "fee_collected_minor": actual[2],
            "unresolved_principal_minor": actual[3],
            "unresolved_fee_minor": actual[4],
            "settled": status == "SETTLED",
            "note": "Balanced accounting does not imply successful settlement.",
        }

    def _conserved(self):
        opening = sum(a["opening_minor"] for a in self.accounts.values())
        current = (
            sum(a["balance_minor"] for a in self.accounts.values())
            + sum(
                x["clearing_principal_minor"] + x["clearing_fee_minor"]
                for x in self.transfers.values()
            )
        )
        return opening == current

    def _accounting(self):
        customers = sum(
            a["balance_minor"] for a in self.accounts.values()
            if a["kind"] != "SYSTEM"
        )
        fees = self.accounts[FEE_ACCOUNT]["balance_minor"]
        cp = sum(x["clearing_principal_minor"] for x in self.transfers.values())
        cf = sum(x["clearing_fee_minor"] for x in self.transfers.values())

        unresolved = [
            {
                "id": x["id"], "direction": x["direction"],
                "status": x["status"], "amount_minor": x["amount_minor"],
                "fee_minor": x["fee_minor"],
            }
            for x in reversed(list(self.transfers.values()))
            if (not x["terminal"]) or x["status"] == "EXCEPTION"
        ]

        return {
            "customer_balances_minor": customers,
            "fees_collected_minor": fees,
            "principal_in_clearing_minor": cp,
            "fees_in_clearing_minor": cf,
            "total_accounted_minor": customers + fees + cp + cf,
            "opening_total_minor": sum(
                a["opening_minor"] for a in self.accounts.values()
            ),
            "conserved": self._conserved(),
            "unresolved_count": len(unresolved),
            "unresolved": unresolved,
        }

    def _view(self, t, replayed=False):
        return {
            "transfer": copy.deepcopy(t),
            "accounts": copy.deepcopy(list(self.accounts.values())),
            "reconciliation": self._reconciliation(t),
            "accounting": self._accounting(),
            "replayed": replayed,
        }

    def _find(self, transfer_id):
        if transfer_id not in self.transfers:
            raise DemoError("Transfer not found.", 404)
        return self.transfers[transfer_id]

    def _finish(self, t):
        t["terminal"] = True
        self._event(
            t, "RECONCILED",
            f"Accounting checked; transaction outcome is {t['status']}.",
        )

    def _check_route(self, source, destination):
        if source not in PARTICIPANTS or destination not in PARTICIPANTS:
            raise DemoError("Unknown participant.")
        cap = capability(source, destination)
        if not cap["allowed"]:
            raise DemoError(
                f"{PARTICIPANTS[source]['name']} to "
                f"{PARTICIPANTS[destination]['name']} is not supported: "
                f"{cap['note']}",
                422,
            )
        return cap

    # ----- public operations ---------------------------------------------

    def quote(self, body):
        with self.lock:
            source, destination = body["source"], body["destination"]
            self._check_route(source, destination)

            policy = body.get("fee_policy", "NONE")
            if policy not in FEE_POLICIES:
                raise DemoError("Unknown fee policy.")

            amount = minor_units(body["amount"])
            fee = compute_fee(policy, amount)
            quote_id = "Q-" + uuid.uuid4().hex[:12]

            record = {
                "quote_id": quote_id,
                "source": source,
                "destination": destination,
                "amount_minor": amount,
                "fee_minor": fee,
                "total_debit_minor": amount + fee,
                "recipient_receives_minor": amount,
                "fee_policy": policy,
                "fee_policy_name": FEE_POLICIES[policy]["name"],
                "fee_policy_version": FEE_POLICIES[policy]["version"],
                "fee_payer": "SENDER",
                "currency": "BWP",
                "expires_in_seconds": self.QUOTE_TTL,
            }

            self.quotes[quote_id] = {
                **record,
                "expires_at": self.clock() + self.QUOTE_TTL,
                "used": False,
            }

            while len(self.quotes) > 500:
                self.quotes.pop(next(iter(self.quotes)))

            return record

    def create(self, body):
        with self.lock:
            source, destination = body["source"], body["destination"]
            reference = body["reference"].strip()
            scenario = body.get("scenario", "SUCCESS")

            cap = self._check_route(source, destination)

            if not reference or len(reference) > 80:
                raise DemoError("Reference must contain 1-80 characters.")
            if scenario not in SCENARIOS:
                raise DemoError("Unknown demonstration scenario.")
            if body.get("currency", "BWP") != "BWP":
                raise DemoError("Only BWP is supported by this demonstration.")

            amount = minor_units(body["amount"])
            quote_id = body.get("quote_id")
            fingerprint = (source, destination, amount, scenario, quote_id)

            if reference in self.references:
                transfer_id, original = self.references[reference]
                if original != fingerprint:
                    raise DemoError(
                        "Reference already exists with different instruction details.",
                        409,
                    )
                return self._view(self.transfers[transfer_id], replayed=True)

            q = self.quotes.get(quote_id)
            if q is None:
                raise DemoError("A valid fee quote is required. Request a new quote.", 409)
            if q["used"]:
                raise DemoError("This quote has already been used. Request a new quote.", 409)
            if self.clock() > q["expires_at"]:
                raise DemoError("Quote expired. Request a new quote.", 410)
            if (q["source"], q["destination"], q["amount_minor"]) != (
                source, destination, amount
            ):
                raise DemoError("Instruction does not match the quote.", 409)

            if len(self.transfers) >= 200:
                raise DemoError("Session limit reached. Start a new demo session.", 429)

            q["used"] = True
            transfer_id = "TX-" + uuid.uuid4().hex[:16]

            t = {
                "id": transfer_id,
                "reference": reference,
                "created_at": now(),
                "direction": cap["route"],
                "participation_status": cap["status"],
                "source": source,
                "destination": destination,
                "amount_minor": amount,
                "fee_minor": q["fee_minor"],
                "total_debit_minor": q["total_debit_minor"],
                "fee_policy": q["fee_policy"],
                "fee_policy_version": q["fee_policy_version"],
                "quote_id": quote_id,
                "currency": "BWP",
                "scenario": scenario,
                "status": "QUEUED",
                "step": 0,
                "terminal": False,
                "clearing_principal_minor": 0,
                "clearing_fee_minor": 0,
                "journal_seq": 0,
                "source_provider_reference": None,
                "destination_provider_reference": None,
                "source_payload": None,
                "canonical": None,
                "destination_payload": None,
                "events": [],
                "ledger": [],
            }

            t["source_payload"] = self._source_payload(t)
            self.transfers[transfer_id] = t
            self.references[reference] = (transfer_id, fingerprint)

            self._event(t, "RECEIVED", "Project-defined source instruction received.")
            return self._view(t)

    def advance(self, transfer_id):
        with self.lock:
            t = self._find(transfer_id)

            if t["terminal"] or t["status"] == "PENDING":
                return self._view(t)

            src, dst = acct(t["source"]), acct(t["destination"])
            cp = f"CLEARING_PRINCIPAL:{transfer_id}"
            cf = f"CLEARING_FEE:{transfer_id}"
            P, F = t["amount_minor"], t["fee_minor"]
            step, scenario = t["step"], t["scenario"]

            if step == 0:
                t["status"] = "PROCESSING"
                self._event(
                    t, "VALIDATED",
                    "Participants, route, BWP amount, quote and reference validated.",
                )
                t["step"] = 1

            elif step == 1:
                t["canonical"] = self._canonical(t)
                self._event(
                    t, "NORMALISED",
                    "Source adapter produced the shared canonical transfer.",
                )
                t["step"] = 2

            elif step == 2:
                t["destination_payload"] = self._destination_payload(t["canonical"])
                self._validate_destination(t)
                self._event(
                    t, "ROUTED",
                    "Destination adapter built and validated its mock credit request.",
                )
                t["step"] = 3

            elif step == 3:
                if scenario == "CANCELLED":
                    t["status"] = "CANCELLED"
                    self._event(t, "CANCELLED", "Cancelled before source debit; no balance movement.")
                    self._finish(t)

                elif (
                    scenario == "INSUFFICIENT_FUNDS"
                    or self.accounts[src]["balance_minor"] < t["total_debit_minor"]
                ):
                    t["status"] = "REJECTED"
                    self._event(
                        t, "SOURCE_REJECTED",
                        "Injected insufficient-funds response; no debit."
                        if scenario == "INSUFFICIENT_FUNDS"
                        else "Source balance cannot cover principal plus fee; no debit.",
                    )
                    self._finish(t)

                else:
                    self._post(t, src, cp, P, "SOURCE_DEBIT_PRINCIPAL")
                    self._post(t, src, cf, F, "SOURCE_DEBIT_FEE")
                    t["source_provider_reference"] = "MOCK-SRC-" + uuid.uuid4().hex[:10]
                    self._event(
                        t, "SOURCE_DEBITED",
                        "Source debited; principal and fee held separately in clearing.",
                    )
                    t["step"] = 4

            elif step == 4:
                if scenario == "PENDING":
                    t["status"] = "PENDING"
                    self._event(
                        t, "DESTINATION_PENDING",
                        "Destination outcome unknown; principal and fee remain in clearing.",
                    )

                elif scenario in {"DESTINATION_FAILED", "COMPENSATION_FAILED"}:
                    t["status"] = "REVERSING"
                    self._event(
                        t, "DESTINATION_REJECTED",
                        "Destination rejected credit; source refund required.",
                    )
                    t["step"] = 5

                else:
                    self._validate_destination(t)
                    self._post(t, cp, dst, P, "DESTINATION_CREDIT")
                    self._post(t, cf, FEE_ACCOUNT, F, "FEE_RECOGNISED")
                    t["destination_provider_reference"] = "MOCK-DST-" + uuid.uuid4().hex[:10]
                    t["status"] = "SETTLED"
                    self._event(
                        t, "DESTINATION_CREDITED",
                        "Destination credited and fee recognised inside the simulation.",
                    )
                    t["step"] = 6

            elif step == 5:
                if scenario == "COMPENSATION_FAILED":
                    t["status"] = "EXCEPTION"
                    self._event(
                        t, "REFUND_FAILED",
                        "Injected refund failure; principal and fee remain unresolved in clearing.",
                    )
                    self._finish(t)
                else:
                    self._post(t, cp, src, P, "SOURCE_REFUND_PRINCIPAL")
                    self._post(t, cf, src, F, "SOURCE_REFUND_FEE")
                    t["status"] = "REVERSED"
                    self._event(
                        t, "SOURCE_REFUNDED",
                        "Principal and fee refunded; destination was not credited.",
                    )
                    t["step"] = 6

            elif step == 6:
                self._finish(t)

            return self._view(t)

    def resolve(self, transfer_id, action):
        with self.lock:
            t = self._find(transfer_id)

            if t["status"] == "PENDING" and action in {"SUCCESS", "FAILED"}:
                t["scenario"] = "SUCCESS" if action == "SUCCESS" else "DESTINATION_FAILED"
                t["status"] = "PROCESSING"
                self._event(
                    t, "MOCK_CALLBACK",
                    f"Operator injected destination outcome: {action}.",
                )

            elif t["status"] == "EXCEPTION" and action == "REFUND":
                t["scenario"] = "DESTINATION_FAILED"
                t["status"] = "REVERSING"
                t["step"] = 5
                t["terminal"] = False
                self._event(
                    t, "REFUND_RETRY",
                    "Operator requested a simulated compensation retry.",
                )

            else:
                raise DemoError("That action is not valid for the current status.", 409)

            return self._view(t)

    def get(self, transfer_id):
        with self.lock:
            return self._view(self._find(transfer_id))

    def overview(self):
        with self.lock:
            return {
                "accounts": copy.deepcopy(list(self.accounts.values())),
                "transfers": [
                    {k: t[k] for k in (
                        "id", "reference", "direction", "source", "destination",
                        "amount_minor", "fee_minor", "status",
                    )}
                    for t in reversed(list(self.transfers.values()))
                ],
                "accounting": self._accounting(),
            }
