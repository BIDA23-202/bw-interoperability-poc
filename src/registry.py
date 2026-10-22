BANKS = {
    "ABSA_BW": "Absa Bank Botswana",
    "STANCHART_BW": "Standard Chartered Botswana",
    "FNBB": "First National Bank of Botswana",
    "STANBIC_BW": "Stanbic Bank Botswana",
    "BANK_GABORONE": "Bank Gaborone",
    "ACCESS_FCB": "Access Bank Botswana (FCB)",
}

WALLETS = {
    "ORANGE_MONEY": "Orange Money",
    "BTC_SMEGA": "BTC Smega",
    "MYZAKA": "Mascom MyZaka",
    "POSOMONEY": "PosoMoney",
}

PARTICIPANTS = {}
for _pid, _name in BANKS.items():
    PARTICIPANTS[_pid] = {
        "name": _name + " (mock)",
        "kind": "BANK",
        "account": "DEMO-" + _pid + "-001",
    }
for _pid, _name in WALLETS.items():
    PARTICIPANTS[_pid] = {
        "name": _name + " (mock)",
        "kind": "WALLET",
        "account": "DEMO-" + _pid + "-001",
    }

# Project-defined mock contracts. These are NOT official provider schemas.
ADAPTERS = {
    "ORANGE_MONEY": {
        "contract": "orange", "ref": "reference",
        "sender": "sender_wallet", "recipient": "recipient_wallet",
        "amount": "amount", "nested": True,
    },
    "BTC_SMEGA": {
        "contract": "smega", "ref": "request_ref",
        "sender": "from_wallet", "recipient": "to_wallet",
        "amount": "value", "nested": False,
    },
    "MYZAKA": {
        "contract": "myzaka", "ref": "transaction_reference",
        "sender": "payer_msisdn", "recipient": "payee_msisdn",
        "amount": "amount", "nested": False,
    },
    "POSOMONEY": {
        "contract": "posomoney", "ref": "txn_id",
        "sender": "source_account", "recipient": "target_account",
        "amount": "txn_amount", "nested": False,
    },
}

BANK_TO_WALLET_BANKS = {
    "ABSA_BW", "STANCHART_BW", "FNBB",
    "STANBIC_BW", "BANK_GABORONE", "ACCESS_FCB",
}

WALLET_TO_BANK_BANKS = {"ABSA_BW", "STANCHART_BW", "BANK_GABORONE"}

# Pair-level overrides. Valid values: CONFIRMED, UNCONFIRMED, NOT_SUPPORTED.
# Format: (route, bank_or_source, wallet_or_destination): status
# Examples (remove the leading # to apply):
#   ("BANK_TO_WALLET", "FNBB", "POSOMONEY"): "NOT_SUPPORTED",
#   ("BANK_TO_WALLET", "ABSA_BW", "ORANGE_MONEY"): "CONFIRMED",
#   ("WALLET_TO_BANK", "BTC_SMEGA", "ABSA_BW"): "NOT_SUPPORTED",
PAIR_STATUS = {
}

# Fees are PROJECT PROPOSALS for demonstration, not provider tariffs.
FEE_POLICIES = {
    "NONE": {
        "name": "Fee-free baseline",
        "version": "none-1",
        "flat_minor": 0, "bps": 0, "min_minor": 0, "cap_minor": None,
        "description": "No transfer charge.",
    },
    "EXPERIMENTAL": {
        "name": "Experimental sender-pays (project proposal)",
        "version": "exp-0.1",
        "flat_minor": 100, "bps": 50, "min_minor": 100, "cap_minor": 2500,
        "description": (
            "Demonstration only: BWP 1.00 + 0.50%, minimum BWP 1.00, "
            "maximum BWP 25.00. Not a provider tariff."
        ),
    },
}

NOTES = {
    "CONFIRMED": "Pair participation confirmed by the project team.",
    "UNCONFIRMED": (
        "Bank is on the supplied participant list; pair-level wallet "
        "participation is not yet confirmed (edit PAIR_STATUS)."
    ),
    "SIMULATED": "Wallet-to-wallet pair simulated to demonstrate interoperability.",
    "NOT_SUPPORTED": "Not supported for this route.",
}


def capability(source, destination):
    if source not in PARTICIPANTS or destination not in PARTICIPANTS:
        return {"route": None, "allowed": False,
                "status": "NOT_SUPPORTED", "note": "Unknown participant."}

    kinds = (PARTICIPANTS[source]["kind"], PARTICIPANTS[destination]["kind"])

    if kinds == ("BANK", "WALLET"):
        route, default = "BANK_TO_WALLET", "UNCONFIRMED"
        if source not in BANK_TO_WALLET_BANKS:
            return {"route": route, "allowed": False, "status": "NOT_SUPPORTED",
                    "note": "Bank is not on the bank-to-wallet participant list."}
    elif kinds == ("WALLET", "BANK"):
        route, default = "WALLET_TO_BANK", "UNCONFIRMED"
        if destination not in WALLET_TO_BANK_BANKS:
            return {"route": route, "allowed": False, "status": "NOT_SUPPORTED",
                    "note": "Bank is not on the wallet-to-bank participant list."}
    elif kinds == ("WALLET", "WALLET") and source != destination:
        route, default = "WALLET_TO_WALLET", "SIMULATED"
    else:
        return {"route": None, "allowed": False, "status": "NOT_SUPPORTED",
                "note": "Route not supported by this build."}

    status = PAIR_STATUS.get((route, source, destination), default)
    note = (
        "Excluded by pair-level configuration."
        if status == "NOT_SUPPORTED" else NOTES[status]
    )
    return {"route": route, "allowed": status != "NOT_SUPPORTED",
            "status": status, "note": note}


def capability_matrix():
    banks = [i for i, p in PARTICIPANTS.items() if p["kind"] == "BANK"]
    wallets = [i for i, p in PARTICIPANTS.items() if p["kind"] == "WALLET"]
    pairs = (
        [(b, w) for b in banks for w in wallets]
        + [(w, b) for w in wallets for b in banks]
        + [(s, d) for s in wallets for d in wallets if s != d]
    )
    return [{"source": s, "destination": d, **capability(s, d)} for s, d in pairs]


def participants_view():
    return {
        pid: {"name": p["name"], "kind": p["kind"], "account": p["account"]}
        for pid, p in PARTICIPANTS.items()
    }
