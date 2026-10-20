import copy
import re
import threading
import uuid
from datetime import datetime, timezone


PROVIDERS = {
    "BANK_A": {
        "name": "Demo Bank A",
        "kind": "BANK",
        "account": "DEMO-BANK-A-001",
    },
    "BANK_B": {
        "name": "Demo Bank B",
        "kind": "BANK",
        "account": "DEMO-BANK-B-001",
    },
    "ORANGE_MOCK": {
        "name": "Orange-style mock",
        "kind": "WALLET",
        "account": "DEMO-ORANGE-001",
    },
    "SMEGA_MOCK": {
        "name": "Smega-style mock",
        "kind": "WALLET",
        "account": "DEMO-SMEGA-001",
    },
}

SCENARIOS = {
    "SUCCESS",
    "PENDING",
    "DESTINATION_FAILED",
    "INSUFFICIENT_FUNDS",
    "COMPENSATION_FAILED",
    "CANCELLED",
}

ROUTES = {
    ("BANK", "WALLET"): "BANK_TO_WALLET",
    ("WALLET", "BANK"): "WALLET_TO_BANK",
    ("WALLET", "WALLET"): "WALLET_TO_WALLET",
}


class DemoError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat()


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


class DemoEngine:
    def __init__(self):
        self.lock = threading.RLock()

        self.accounts = {
            provider["account"]: {
                "provider": provider_id,
                "provider_name": provider["name"],
                "kind": provider["kind"],
                "account": provider["account"],
                "opening_minor": 100_000,
                "balance_minor": 100_000,
            }
            for provider_id, provider in PROVIDERS.items()
        }

        self.transfers = {}
        self.references = {}

    def _event(self, transfer, code, description):
        transfer["events"].append({
            "sequence": len(transfer["events"]) + 1,
            "timestamp": now(),
            "code": code,
            "description": description,
        })

    def _source_payload(self, transfer):
        source = transfer["source"]
        destination = transfer["destination"]

        common_recipient = {
            "institution": destination,
            "account": PROVIDERS[destination]["account"],
        }

        if PROVIDERS[source]["kind"] == "BANK":
            return {
                "contract": "project.bank-instruction.v1",
                "instruction_id": transfer["reference"],
                "created_at": transfer["created_at"],
                "debtor_account": PROVIDERS[source]["account"],
                "creditor": common_recipient,
                "amount": money(transfer["amount_minor"]),
                "currency": "BWP",
            }

        if source == "ORANGE_MOCK":
            return {
                "contract": "project.orange-source.v1",
                "reference": transfer["reference"],
                "sent_at": transfer["created_at"],
                "sender_wallet": PROVIDERS[source]["account"],
                "recipient": common_recipient,
                "amount": {
                    "value": money(transfer["amount_minor"]),
                    "currency": "BWP",
                },
            }

        return {
            "contract": "project.smega-source.v1",
            "request_ref": transfer["reference"],
            "created_at": transfer["created_at"],
            "from_wallet": PROVIDERS[source]["account"],
            "to": common_recipient,
            "value": money(transfer["amount_minor"]),
            "currency": "BWP",
        }

    def _canonical(self, transfer):
        payload = transfer["source_payload"]
        source = transfer["source"]

        if PROVIDERS[source]["kind"] == "BANK":
            reference = payload["instruction_id"]
            amount = payload["amount"]
            currency = payload["currency"]
            account = payload["debtor_account"]
            recipient = payload["creditor"]

        elif source == "ORANGE_MOCK":
            reference = payload["reference"]
            amount = payload["amount"]["value"]
            currency = payload["amount"]["currency"]
            account = payload["sender_wallet"]
            recipient = payload["recipient"]

        else:
            reference = payload["request_ref"]
            amount = payload["value"]
            currency = payload["currency"]
            account = payload["from_wallet"]
            recipient = payload["to"]

        if minor_units(amount) != transfer["amount_minor"]:
            raise DemoError("Source mapping changed the amount.")

        return {
            "schema": "project.canonical-transfer.v1",
            "transaction_id": transfer["id"],
            "reference": reference,
            "timestamp": transfer["created_at"],
            "direction": transfer["direction"],
            "amount_minor": minor_units(amount),
            "currency": currency,
            "debtor": {
                "institution": source,
                "kind": PROVIDERS[source]["kind"],
                "account": account,
            },
            "creditor": {
                "institution": recipient["institution"],
                "kind": PROVIDERS[recipient["institution"]]["kind"],
                "account": recipient["account"],
            },
        }

    def _destination_payload(self, canonical):
        provider = canonical["creditor"]["institution"]
        account = canonical["creditor"]["account"]
        amount = money(canonical["amount_minor"])

        if PROVIDERS[provider]["kind"] == "BANK":
            return {
                "contract": "project.bank-credit.v1",
                "instruction_id": canonical["transaction_id"],
                "creditor_account": account,
                "instructed_amount": amount,
                "currency": canonical["currency"],
            }

        if provider == "ORANGE_MOCK":
            return {
                "contract": "project.orange-credit.v1",
                "reference": canonical["transaction_id"],
                "recipient_wallet": account,
                "amount": {
                    "value": amount,
                    "currency": canonical["currency"],
                },
            }

        return {
            "contract": "project.smega-credit.v1",
            "request_ref": canonical["transaction_id"],
            "to_wallet": account,
            "value": amount,
            "currency": canonical["currency"],
        }

    def _validate_destination_payload(self, transfer):
        payload = transfer["destination_payload"]
        provider = transfer["destination"]

        if PROVIDERS[provider]["kind"] == "BANK":
            account = payload["creditor_account"]
            value = payload["instructed_amount"]
            currency = payload["currency"]
        elif provider == "ORANGE_MOCK":
            account = payload["recipient_wallet"]
            value = payload["amount"]["value"]
            currency = payload["amount"]["currency"]
        else:
            account = payload["to_wallet"]
            value = payload["value"]
            currency = payload["currency"]

        if (
            account != PROVIDERS[provider]["account"]
            or minor_units(value) != transfer["amount_minor"]
            or currency != "BWP"
        ):
            raise DemoError("Destination adapter output failed validation.")

    def _move(self, transfer, source_account, destination_account, operation):
        amount = transfer["amount_minor"]
        clearing = f"CLEARING:{transfer['id']}"

        if source_account == clearing:
            if transfer["clearing_minor"] < amount:
                raise DemoError("Insufficient clearing funds.")
            transfer["clearing_minor"] -= amount
        else:
            account = self.accounts[source_account]
            if account["balance_minor"] < amount:
                raise DemoError("Insufficient source funds.")
            account["balance_minor"] -= amount

        if destination_account == clearing:
            transfer["clearing_minor"] += amount
        else:
            self.accounts[destination_account]["balance_minor"] += amount

        journal_id = f"{transfer['id']}-J{len(transfer['ledger']) // 2 + 1}"

        for account, delta in (
            (source_account, -amount),
            (destination_account, amount),
        ):
            transfer["ledger"].append({
                "journal_id": journal_id,
                "timestamp": now(),
                "operation": operation,
                "account": account,
                "delta_minor": delta,
                "currency": "BWP",
            })

    def _reconciliation(self, transfer):
        source = PROVIDERS[transfer["source"]]["account"]
        destination = PROVIDERS[transfer["destination"]]["account"]

        source_delta = sum(
            entry["delta_minor"]
            for entry in transfer["ledger"]
            if entry["account"] == source
        )

        destination_delta = sum(
            entry["delta_minor"]
            for entry in transfer["ledger"]
            if entry["account"] == destination
        )

        initial_total = sum(
            account["opening_minor"] for account in self.accounts.values()
        )

        current_total = sum(
            account["balance_minor"] for account in self.accounts.values()
        ) + sum(
            item["clearing_minor"] for item in self.transfers.values()
        )

        actual = (
            source_delta,
            destination_delta,
            transfer["clearing_minor"],
        )

        amount = transfer["amount_minor"]
        status = transfer["status"]

        if status == "SETTLED":
            expected = (-amount, amount, 0)
        elif status in {"PENDING", "EXCEPTION", "REVERSING"}:
            expected = (-amount, 0, amount)
        elif status in {"REJECTED", "REVERSED", "CANCELLED"}:
            expected = (0, 0, 0)
        else:
            expected = None

        return {
            "journal_balanced": sum(
                entry["delta_minor"] for entry in transfer["ledger"]
            ) == 0,
            "session_money_conserved": initial_total == current_total,
            "status_consistent": (
                actual == expected if expected is not None else None
            ),
            "source_net_minor": source_delta,
            "destination_net_minor": destination_delta,
            "unresolved_minor": transfer["clearing_minor"],
            "settled": status == "SETTLED",
            "note": (
                "Balanced accounting does not imply successful settlement."
            ),
        }

    def _view(self, transfer, replayed=False):
        return {
            "transfer": copy.deepcopy(transfer),
            "accounts": copy.deepcopy(list(self.accounts.values())),
            "reconciliation": self._reconciliation(transfer),
            "replayed": replayed,
        }

    def _finish(self, transfer):
        transfer["terminal"] = True
        self._event(
            transfer,
            "RECONCILED",
            f"Accounting checked; transaction outcome is {transfer['status']}.",
        )

    def _find(self, transfer_id):
        if transfer_id not in self.transfers:
            raise DemoError("Transfer not found.", 404)
        return self.transfers[transfer_id]

    def create(self, body):
        with self.lock:
            source = body["source"]
            destination = body["destination"]
            reference = body["reference"].strip()
            scenario = body.get("scenario", "SUCCESS")

            if source not in PROVIDERS or destination not in PROVIDERS:
                raise DemoError("Unknown demonstration institution.")

            if source == destination:
                raise DemoError("Choose different source and destination institutions.")

            direction = ROUTES.get((
                PROVIDERS[source]["kind"],
                PROVIDERS[destination]["kind"],
            ))

            if direction is None:
                raise DemoError("This build supports bank-wallet and wallet-wallet routes.")

            if not reference or len(reference) > 80:
                raise DemoError("Reference must contain 1–80 characters.")

            if scenario not in SCENARIOS:
                raise DemoError("Unknown demonstration scenario.")

            if body.get("currency", "BWP") != "BWP":
                raise DemoError("Only BWP is supported by this demonstration.")

            amount = minor_units(body["amount"])
            fingerprint = (source, destination, amount, "BWP", scenario)

            if reference in self.references:
                transfer_id, original = self.references[reference]
                if original != fingerprint:
                    raise DemoError(
                        "Reference already exists with different instruction details.",
                        409,
                    )
                return self._view(self.transfers[transfer_id], replayed=True)

            if len(self.transfers) >= 200:
                raise DemoError("Session limit reached. Start a new demo session.", 429)

            transfer_id = "TX-" + uuid.uuid4().hex[:16]

            transfer = {
                "id": transfer_id,
                "reference": reference,
                "created_at": now(),
                "direction": direction,
                "source": source,
                "destination": destination,
                "amount_minor": amount,
                "currency": "BWP",
                "scenario": scenario,
                "status": "QUEUED",
                "step": 0,
                "terminal": False,
                "clearing_minor": 0,
                "source_provider_reference": None,
                "destination_provider_reference": None,
                "source_payload": None,
                "canonical": None,
                "destination_payload": None,
                "events": [],
                "ledger": [],
            }

            transfer["source_payload"] = self._source_payload(transfer)
            self.transfers[transfer_id] = transfer
            self.references[reference] = (transfer_id, fingerprint)

            self._event(
                transfer,
                "RECEIVED",
                "Project-defined source instruction received.",
            )

            return self._view(transfer)

    def advance(self, transfer_id):
        with self.lock:
            transfer = self._find(transfer_id)

            if transfer["terminal"] or transfer["status"] == "PENDING":
                return self._view(transfer)

            source = PROVIDERS[transfer["source"]]["account"]
            destination = PROVIDERS[transfer["destination"]]["account"]
            clearing = f"CLEARING:{transfer_id}"
            step = transfer["step"]
            scenario = transfer["scenario"]

            if step == 0:
                transfer["status"] = "PROCESSING"
                self._event(
                    transfer,
                    "VALIDATED",
                    "Institutions, route, BWP amount and reference validated.",
                )
                transfer["step"] = 1

            elif step == 1:
                transfer["canonical"] = self._canonical(transfer)
                self._event(
                    transfer,
                    "NORMALISED",
                    "Source adapter produced the shared canonical transfer.",
                )
                transfer["step"] = 2

            elif step == 2:
                transfer["destination_payload"] = self._destination_payload(
                    transfer["canonical"]
                )
                self._validate_destination_payload(transfer)
                self._event(
                    transfer,
                    "ROUTED",
                    "Destination adapter built and validated its mock credit request.",
                )
                transfer["step"] = 3

            elif step == 3:
                if scenario == "CANCELLED":
                    transfer["status"] = "CANCELLED"
                    self._event(
                        transfer,
                        "CANCELLED",
                        "Cancelled before source debit; no balance movement.",
                    )
                    self._finish(transfer)

                elif (
                    scenario == "INSUFFICIENT_FUNDS"
                    or self.accounts[source]["balance_minor"] < transfer["amount_minor"]
                ):
                    transfer["status"] = "REJECTED"
                    self._event(
                        transfer,
                        "SOURCE_REJECTED",
                        (
                            "Injected insufficient-funds response; no debit."
                            if scenario == "INSUFFICIENT_FUNDS"
                            else "Source balance is insufficient; no debit."
                        ),
                    )
                    self._finish(transfer)

                else:
                    self._move(transfer, source, clearing, "SOURCE_DEBIT")
                    transfer["source_provider_reference"] = (
                        "MOCK-SRC-" + uuid.uuid4().hex[:10]
                    )
                    self._event(
                        transfer,
                        "SOURCE_DEBITED",
                        "Source debited; amount held in transaction clearing.",
                    )
                    transfer["step"] = 4

            elif step == 4:
                if scenario == "PENDING":
                    transfer["status"] = "PENDING"
                    self._event(
                        transfer,
                        "DESTINATION_PENDING",
                        "Destination outcome unknown; funds remain in clearing.",
                    )

                elif scenario in {"DESTINATION_FAILED", "COMPENSATION_FAILED"}:
                    transfer["status"] = "REVERSING"
                    self._event(
                        transfer,
                        "DESTINATION_REJECTED",
                        "Destination rejected credit; source refund required.",
                    )
                    transfer["step"] = 5

                else:
                    self._validate_destination_payload(transfer)
                    self._move(
                        transfer, clearing, destination, "DESTINATION_CREDIT"
                    )
                    transfer["destination_provider_reference"] = (
                        "MOCK-DST-" + uuid.uuid4().hex[:10]
                    )
                    transfer["status"] = "SETTLED"
                    self._event(
                        transfer,
                        "DESTINATION_CREDITED",
                        "Destination credited inside the simulation.",
                    )
                    transfer["step"] = 6

            elif step == 5:
                if scenario == "COMPENSATION_FAILED":
                    transfer["status"] = "EXCEPTION"
                    self._event(
                        transfer,
                        "REFUND_FAILED",
                        "Injected refund failure; funds remain unresolved in clearing.",
                    )
                    self._finish(transfer)

                else:
                    self._move(transfer, clearing, source, "SOURCE_REFUND")
                    transfer["status"] = "REVERSED"
                    self._event(
                        transfer,
                        "SOURCE_REFUNDED",
                        "Source refunded; destination was not credited.",
                    )
                    transfer["step"] = 6

            elif step == 6:
                self._finish(transfer)

            return self._view(transfer)

    def resolve(self, transfer_id, action):
        with self.lock:
            transfer = self._find(transfer_id)

            if transfer["status"] == "PENDING" and action in {"SUCCESS", "FAILED"}:
                transfer["scenario"] = (
                    "SUCCESS" if action == "SUCCESS" else "DESTINATION_FAILED"
                )
                transfer["status"] = "PROCESSING"

                self._event(
                    transfer,
                    "MOCK_CALLBACK",
                    f"Operator injected destination outcome: {action}.",
                )

            elif transfer["status"] == "EXCEPTION" and action == "REFUND":
                transfer["scenario"] = "DESTINATION_FAILED"
                transfer["status"] = "REVERSING"
                transfer["step"] = 5
                transfer["terminal"] = False

                self._event(
                    transfer,
                    "REFUND_RETRY",
                    "Operator requested a simulated compensation retry.",
                )

            else:
                raise DemoError("That action is not valid for the current status.", 409)

            return self._view(transfer)

    def get(self, transfer_id):
        with self.lock:
            return self._view(self._find(transfer_id))

    def overview(self):
        with self.lock:
            return {
                "accounts": copy.deepcopy(list(self.accounts.values())),
                "transfers": [
                    {
                        key: transfer[key]
                        for key in (
                            "id",
                            "reference",
                            "direction",
                            "source",
                            "destination",
                            "amount_minor",
                            "status",
                        )
                    }
                    for transfer in reversed(list(self.transfers.values()))
                ],
      }
