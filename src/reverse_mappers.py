"""
reverse_mappers.py  |  Reverse mappers, cross-format translation and fidelity checks (tasks 3.5 and 3.7).

Reads the canonical store and writes the two native formats again:
  canonical -> pacs.008 XML   and   canonical -> Orange-style JSON
Then proves two things:
  1. ROUND TRIP: source -> canonical -> same source format. Every field is compared with the original record
     (this is evidence for the "standardisation without data loss" criterion).
  2. TRANSLATION: a bank record is translated into the wallet request and compared with the real wallet leg
     (bank -> wallet), and a wallet record is translated into a pacs.008 record and compared with the real
     bank leg (wallet -> bank). Fields that the wallet record cannot supply are listed as NOT_DERIVABLE.

Run:  python src/reverse_mappers.py --db data/poc.db --xml data/poc_pacs008.xml --orange data/poc_orange.json --out data/out
Writes: <out>_pacs008.xml, <out>_orange.json, <out>_fidelity.csv, <out>_samples.json, <out>_summary.json
"""
import argparse
import json
import os
import sqlite3
import uuid
from decimal import Decimal
from xml.etree import ElementTree as ET

import pandas as pd

from loader import parse_pacs008

NS = "urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"
CHARGE_BACK = {"DEBT": "DEBT", "CRED": "CRED", "SHARED": "SHAR", "SERVICE_LEVEL": "SLEV"}
OM_BACK = {"COMPLETED": "SUCCESS", "DISPATCHED": "ACCEPTED"}
PACS_FIELDS = ["msg_id", "instr_id", "end_to_end_id", "tx_id", "uetr", "clr_sys_ref", "amount", "currency", "settlement_date", "charge_bearer",
               "debtor_name", "debtor_account", "debtor_bic", "creditor_name", "creditor_account", "creditor_bic", "remittance_info"]
OM_FIELDS = ["transactionId", "order_id", "reference", "amount", "unit", "customer_id", "customer_idType", "partner_id", "partner_idType",
             "status", "createdAt", "pay_token"]
UUID_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _money(minor):
    return f"{minor // 100}.{minor % 100:02d}"


def load_canonical(db, source):
    """Return {key: dict} for one source, with identifiers and (for wallets) the echoed Orange status."""
    db.row_factory = sqlite3.Row
    sql = """SELECT t.txn_id, t.end_to_end_key AS key, t.amount_minor, t.currency, t.charge_bearer, t.remittance_info, t.initiated_at,
                    t.settled_at, t.status, dp.identifier_value AS debtor_id, dp.display_name AS debtor_name,
                    cp.identifier_value AS creditor_id, cp.display_name AS creditor_name,
                    da.bic AS debtor_bic, da.code AS debtor_code, ca.bic AS creditor_bic, ca.code AS creditor_code
             FROM transactions t
             LEFT JOIN parties dp ON dp.party_id = t.debtor_party_id LEFT JOIN parties cp ON cp.party_id = t.creditor_party_id
             LEFT JOIN participants da ON da.participant_id = t.debtor_participant_id
             LEFT JOIN participants ca ON ca.participant_id = t.creditor_participant_id
             WHERE t.source_system = ? ORDER BY t.txn_id"""
    rows = {r["txn_id"]: dict(r) for r in db.execute(sql, (source,))}
    for r in db.execute("SELECT txn_id, identifier_kind, identifier_value FROM transaction_source_ids WHERE source_system = ?", (source,)):
        rows[r["txn_id"]].setdefault("ids", {})[r["identifier_kind"]] = r["identifier_value"]
    for r in db.execute("SELECT txn_id, reason_code FROM status_reports WHERE source = 'SOURCE_ECHO'"):
        if r["txn_id"] in rows:
            rows[r["txn_id"]]["echo_reason"] = r["reason_code"]
    db.row_factory = None
    return {c["key"]: c for c in rows.values()}


# ---------- canonical -> native ----------
def canonical_to_pacs_record(c):
    ids = c.get("ids", {})
    return {"msg_id": ids.get("MSG_ID"), "instr_id": ids.get("INSTR_ID"), "end_to_end_id": c["key"], "tx_id": ids.get("TX_ID"),
            "uetr": ids.get("UETR"), "clr_sys_ref": ids.get("CLR_SYS_REF"), "amount": _money(c["amount_minor"]), "currency": c["currency"],
            "settlement_date": (c["settled_at"] or "")[:10], "charge_bearer": CHARGE_BACK.get(c["charge_bearer"]),
            "debtor_name": c["debtor_name"], "debtor_account": c["debtor_id"], "debtor_bic": c["debtor_bic"],
            "creditor_name": c["creditor_name"], "creditor_account": c["creditor_id"], "creditor_bic": c["creditor_bic"],
            "remittance_info": c["remittance_info"]}


def canonical_to_orange_record(c):
    ids = c.get("ids", {})
    if c["status"] == "FAILED":
        native = c.get("echo_reason") or "FAILED"
    else:
        native = OM_BACK.get(c["status"], "ACCEPTED")
    return {"transactionId": ids.get("OM_TRANSACTION_ID"), "order_id": c["key"], "reference": c["remittance_info"],
            "amount": {"value": float(Decimal(c["amount_minor"]) / 100), "unit": c["currency"]},
            "customer": {"id": c["creditor_id"], "idType": "MSISDN"}, "partner": {"id": c["debtor_code"], "idType": "CODE"},
            "status": native, "createdAt": c["initiated_at"], "pay_token": ids.get("OM_PAY_TOKEN")}


def build_pacs008_xml(records, created, msg_id):
    ET.register_namespace("", NS)
    q = lambda t: f"{{{NS}}}{t}"
    doc = ET.Element(q("Document")); root = ET.SubElement(doc, q("FIToFICstmrCdtTrf")); gh = ET.SubElement(root, q("GrpHdr"))
    ET.SubElement(gh, q("MsgId")).text = msg_id
    ET.SubElement(gh, q("CreDtTm")).text = created
    ET.SubElement(gh, q("NbOfTxs")).text = str(len(records))
    total = ET.SubElement(gh, q("TtlIntrBkSttlmAmt"), Ccy="BWP")
    total.text = _money(sum(int(Decimal(r["amount"]) * 100) for r in records))
    ET.SubElement(ET.SubElement(gh, q("SttlmInf")), q("SttlmMtd")).text = "INGA"
    for r in records:
        t = ET.SubElement(root, q("CdtTrfTxInf")); p = ET.SubElement(t, q("PmtId"))
        for tag, key in [("InstrId", "instr_id"), ("EndToEndId", "end_to_end_id"), ("TxId", "tx_id"), ("UETR", "uetr"), ("ClrSysRef", "clr_sys_ref")]:
            if r[key]:
                ET.SubElement(p, q(tag)).text = r[key]
        amt = ET.SubElement(t, q("IntrBkSttlmAmt")); amt.text = r["amount"]
        if r["currency"]:
            amt.set("Ccy", r["currency"])
        ET.SubElement(t, q("IntrBkSttlmDt")).text = r["settlement_date"]
        ET.SubElement(t, q("ChrgBr")).text = r["charge_bearer"]
        ET.SubElement(ET.SubElement(t, q("Dbtr")), q("Nm")).text = r["debtor_name"]
        ET.SubElement(ET.SubElement(ET.SubElement(ET.SubElement(t, q("DbtrAcct")), q("Id")), q("Othr")), q("Id")).text = r["debtor_account"]
        ET.SubElement(ET.SubElement(ET.SubElement(t, q("DbtrAgt")), q("FinInstnId")), q("BICFI")).text = r["debtor_bic"]
        ET.SubElement(ET.SubElement(ET.SubElement(t, q("CdtrAgt")), q("FinInstnId")), q("BICFI")).text = r["creditor_bic"]
        ET.SubElement(ET.SubElement(t, q("Cdtr")), q("Nm")).text = r["creditor_name"]
        ET.SubElement(ET.SubElement(ET.SubElement(ET.SubElement(t, q("CdtrAcct")), q("Id")), q("Othr")), q("Id")).text = r["creditor_account"]
        if r["remittance_info"]:
            ET.SubElement(ET.SubElement(t, q("RmtInf")), q("Ustrd")).text = r["remittance_info"]
    ET.indent(doc)
    return ET.ElementTree(doc)


# ---------- cross-format translation ----------
def translate_bank_to_wallet(c):
    """Bank-leg canonical record -> the wallet request that should credit the creditor.
    transactionId, pay_token, createdAt and the final status are assigned by the wallet, so they are left out."""
    return {"order_id": c["key"], "reference": c["remittance_info"], "amount": {"value": float(Decimal(c["amount_minor"]) / 100), "unit": c["currency"]},
            "customer": {"id": c["creditor_id"], "idType": "MSISDN"}, "partner": {"id": c["debtor_code"], "idType": "CODE"}, "status": "ACCEPTED"}


def translate_wallet_to_bank(c, orange_bic):
    """Wallet-leg canonical record -> pacs.008 record. Debtor name and account are NOT in the wallet record, so they are
    placeholders and reported as NOT_DERIVABLE. Identifiers that pacs.008 requires are derived deterministically."""
    key = c["key"]
    return {"end_to_end_id": key, "instr_id": f"INSTR-{key}"[:35], "tx_id": f"TX-{key}"[:35], "uetr": str(uuid.uuid5(UUID_NS, key)),
            "amount": _money(c["amount_minor"]), "currency": c["currency"], "creditor_account": c["creditor_id"], "creditor_bic": orange_bic,
            "debtor_bic": c["debtor_bic"], "remittance_info": c["remittance_info"], "settlement_date": c["initiated_at"][:10],
            "debtor_account": None, "debtor_name": None}


# ---------- fidelity ----------
def _flat_orange(raw):
    return {"transactionId": raw.get("transactionId"), "order_id": raw.get("order_id"), "reference": raw.get("reference"),
            "amount": raw["amount"]["value"], "unit": raw["amount"]["unit"], "customer_id": raw["customer"]["id"],
            "customer_idType": raw["customer"]["idType"], "partner_id": raw["partner"]["id"], "partner_idType": raw["partner"]["idType"],
            "status": raw.get("status"), "createdAt": raw.get("createdAt"), "pay_token": raw.get("pay_token")}


def _same(field, a, b):
    if field in ("amount",) and a is not None and b is not None:
        return Decimal(str(a)) == Decimal(str(b))
    return a == b


def roundtrip_fidelity(db, xml_path, json_path):
    """Compare every field of every loaded record with the original. Returns (rows, summary)."""
    header, originals = parse_pacs008(xml_path)
    first = {}
    for r in originals:
        first.setdefault(r["end_to_end_id"], r)                       # the first copy of a key is the one that was loaded
    bank = load_canonical(db, "PACS008"); wallet = load_canonical(db, "ORANGE")
    rows = []
    for key, c in bank.items():
        back, orig = canonical_to_pacs_record(c), first[key]
        for f in PACS_FIELDS:
            rows.append({"source": "PACS008", "key": key, "field": f, "original": orig[f], "reversed": back[f], "same": _same(f, orig[f], back[f])})
    for raw in json.load(open(json_path)):
        key = raw["order_id"]
        if key not in wallet:
            continue
        back, orig = _flat_orange(canonical_to_orange_record(wallet[key])), _flat_orange(raw)
        for f in OM_FIELDS:
            rows.append({"source": "ORANGE", "key": key, "field": f, "original": orig[f], "reversed": back[f], "same": _same(f, orig[f], back[f])})
    df = pd.DataFrame(rows)
    per_record = df.groupby(["source", "key"])["same"].all()
    summary = {"records_checked": int(len(per_record)), "records_identical": int(per_record.sum()),
               "fields_checked": int(len(df)), "fields_identical": int(df["same"].sum()),
               "by_source": {s: {"records": int(len(g)), "identical": int(g.sum())} for s, g in per_record.groupby(level=0)},
               "lossy_fields": sorted(df.loc[~df["same"], "field"].unique().tolist())}
    return df, summary


def translation_fidelity(db):
    """Translate each side of every KEY-matched pair and compare with the real other leg."""
    bank = load_canonical(db, "PACS008"); wallet = load_canonical(db, "ORANGE")
    orange_bic = db.execute("SELECT bic FROM participants WHERE code='ORANGE_MONEY'").fetchone()[0]
    keys = [r[0] for r in db.execute("""SELECT b.end_to_end_key FROM reconciliation_records r JOIN transactions b ON b.txn_id=r.bank_txn_id
                                        WHERE r.wallet_txn_id IS NOT NULL AND r.match_method='KEY' AND r.outcome='MATCHED'""")]
    b2w_fields = ["order_id", "reference", "amount", "unit", "customer_id", "customer_idType", "partner_id", "partner_idType"]
    w2b_fields = ["end_to_end_id", "amount", "currency", "creditor_account", "creditor_bic", "debtor_bic", "remittance_info"]
    ok_b2w = ok_w2b = 0
    for k in keys:
        t = _flat_orange({**translate_bank_to_wallet(bank[k]), "transactionId": None, "createdAt": None, "pay_token": None})
        real = _flat_orange(canonical_to_orange_record(wallet[k]))
        ok_b2w += all(_same(f, t[f], real[f]) for f in b2w_fields)
        t2, real2 = translate_wallet_to_bank(wallet[k], orange_bic), canonical_to_pacs_record(bank[k])
        ok_w2b += all(_same(f, t2[f], real2[f]) for f in w2b_fields)
    return {"matched_pairs": len(keys), "bank_to_wallet_identical": ok_b2w, "wallet_to_bank_identical": ok_w2b,
            "bank_to_wallet_fields": b2w_fields, "wallet_to_bank_fields_checked": w2b_fields,
            "wallet_to_bank_not_derivable": ["debtor_name", "debtor_account", "clr_sys_ref", "charge_bearer (wallet record carries none)"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/poc.db"); ap.add_argument("--xml", default="data/poc_pacs008.xml")
    ap.add_argument("--orange", default="data/poc_orange.json"); ap.add_argument("--out", default="data/out")
    a = ap.parse_args()
    db = sqlite3.connect(a.db)
    bank = load_canonical(db, "PACS008"); wallet = load_canonical(db, "ORANGE")
    records = [canonical_to_pacs_record(c) for c in bank.values()]
    created = next(iter(bank.values()))["initiated_at"]
    msg_id = next(iter(bank.values()))["ids"]["MSG_ID"]
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    build_pacs008_xml(records, created, msg_id).write(f"{a.out}_pacs008.xml", encoding="utf-8", xml_declaration=True)
    json.dump([canonical_to_orange_record(c) for c in wallet.values()], open(f"{a.out}_orange.json", "w"), indent=2)
    # the XML we just wrote must parse back to the same records
    _, reparsed = parse_pacs008(f"{a.out}_pacs008.xml")
    assert all(_same(f, x[f], y[f]) for x, y in zip(records, reparsed) for f in PACS_FIELDS), "written XML does not parse back to the same records"
    df, summary = roundtrip_fidelity(db, a.xml, a.orange)
    tr = translation_fidelity(db)
    summary["translation"] = tr
    df.to_csv(f"{a.out}_fidelity.csv", index=False)
    key0 = next(k for k in bank if k in wallet)
    json.dump({"bank_record": canonical_to_pacs_record(bank[key0]), "translated_to_wallet": translate_bank_to_wallet(bank[key0]),
               "actual_wallet_leg": canonical_to_orange_record(wallet[key0])}, open(f"{a.out}_samples.json", "w"), indent=2)
    json.dump(summary, open(f"{a.out}_summary.json", "w"), indent=2)
    print(f"Round trip: {summary['records_identical']}/{summary['records_checked']} records identical, "
          f"{summary['fields_identical']}/{summary['fields_checked']} fields identical; by source {summary['by_source']}")
    print("Lossy fields:", summary["lossy_fields"] or "none")
    print(f"Translation bank->wallet: {tr['bank_to_wallet_identical']}/{tr['matched_pairs']} identical on {len(tr['bank_to_wallet_fields'])} fields | "
          f"wallet->bank: {tr['wallet_to_bank_identical']}/{tr['matched_pairs']} identical on {len(tr['wallet_to_bank_fields_checked'])} derivable fields")


if __name__ == "__main__":
    main()
