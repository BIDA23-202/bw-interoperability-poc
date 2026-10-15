"""
loader.py  |  Batch loader for the PoC (task 3.4).  Validates every record through gateway.py, then writes
accepted records to the canonical SQLite store and rejected ones to `exceptions`.

Run (from the folder that holds src/ and data/):
  python src/loader.py --reset --db data/poc.db --schema src/schema.sql --participants src/participants.yaml \
         --xml data/poc_pacs008.xml --orange data/poc_orange.json

Batch invariant: rows_in = rows_mapped + rows_rejected (enforced by a CHECK constraint in load_audit).
A record whose key already exists (in the same batch or in the database) is rejected as DUPLICATE_KEY; the first
copy wins. Nothing is written for a source until every record in it has been validated.
"""
import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from xml.etree import ElementTree as ET

import yaml

from gateway import build_roster, validate_orange, validate_pacs

CHARGE_MAP = {"DEBT": "DEBT", "CRED": "CRED", "SHAR": "SHARED", "SLEV": "SERVICE_LEVEL"}
OM_STATUS_MAP = {"SUCCESS": "COMPLETED", "ACCEPTED": "DISPATCHED", "FAILED": "FAILED", "REJECTED": "FAILED",
                 "CANCELLED": "FAILED", "EXPIRED": "FAILED"}
ISO_STATUS = {"COMPLETED": "ACSC", "DISPATCHED": "PDNG", "INITIATED": "PDNG", "FAILED": "RJCT", "REVERSED": "RJCT"}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path):
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys = ON")
    return db


def create_schema(db, schema_path):
    db.executescript(open(schema_path).read())
    db.execute("PRAGMA foreign_keys = ON")


def seed_participants(db, yaml_path):
    with db:
        for p in yaml.safe_load(open(yaml_path))["participants"]:
            db.execute(
                """INSERT INTO participants(code,name,type,bic,active,note) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(code) DO UPDATE SET name=excluded.name, type=excluded.type, bic=excluded.bic,
                   active=excluded.active, note=excluded.note""",
                (p["code"], p["name"], p["type"], p["bic"], int(p["active"]), p.get("note")))


def _participant_maps(db):
    by_bic = {r[1]: r[0] for r in db.execute("SELECT participant_id, bic FROM participants")}
    by_code = {r[1]: r[0] for r in db.execute("SELECT participant_id, code FROM participants")}
    return by_bic, by_code


def _party(db, participant_id, id_type, value, name=None):
    db.execute("INSERT OR IGNORE INTO parties(participant_id,identifier_type,identifier_value,display_name) VALUES (?,?,?,?)",
               (participant_id, id_type, value, name))
    return db.execute("SELECT party_id FROM parties WHERE participant_id=? AND identifier_type=? AND identifier_value=?",
                      (participant_id, id_type, value)).fetchone()[0]


def _minor(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


# ---------- parsing ----------
def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _find(el, *path):
    cur = el
    for name in path:
        nxt = next((c for c in cur if _local(c.tag) == name), None)
        if nxt is None:
            return None
        cur = nxt
    return cur


def _text(el, *path):
    e = _find(el, *path)
    return e.text.strip() if e is not None and e.text and e.text.strip() else None


def parse_pacs008(xml_path):
    """Return (group_header, [flat record dicts]). Tolerates both BICFI and BIC element names."""
    root = ET.parse(xml_path).getroot()
    msg = _find(root, "FIToFICstmrCdtTrf") if _local(root.tag) == "Document" else root
    header = {"msg_id": _text(msg, "GrpHdr", "MsgId"), "created": _text(msg, "GrpHdr", "CreDtTm")}
    records = []
    for tx in (c for c in msg if _local(c.tag) == "CdtTrfTxInf"):
        amt = _find(tx, "IntrBkSttlmAmt")
        records.append({
            "msg_id": header["msg_id"], "instr_id": _text(tx, "PmtId", "InstrId"), "end_to_end_id": _text(tx, "PmtId", "EndToEndId"),
            "tx_id": _text(tx, "PmtId", "TxId"), "uetr": _text(tx, "PmtId", "UETR"), "clr_sys_ref": _text(tx, "PmtId", "ClrSysRef"),
            "amount": amt.text.strip() if amt is not None and amt.text else None, "currency": amt.get("Ccy") if amt is not None else None,
            "settlement_date": _text(tx, "IntrBkSttlmDt"), "charge_bearer": _text(tx, "ChrgBr"),
            "debtor_name": _text(tx, "Dbtr", "Nm"), "debtor_account": _text(tx, "DbtrAcct", "Id", "Othr", "Id"),
            "debtor_bic": _text(tx, "DbtrAgt", "FinInstnId", "BICFI") or _text(tx, "DbtrAgt", "FinInstnId", "BIC"),
            "creditor_name": _text(tx, "Cdtr", "Nm"), "creditor_account": _text(tx, "CdtrAcct", "Id", "Othr", "Id"),
            "creditor_bic": _text(tx, "CdtrAgt", "FinInstnId", "BICFI") or _text(tx, "CdtrAgt", "FinInstnId", "BIC"),
            "remittance_info": _text(tx, "RmtInf", "Ustrd")})
    return header, records


# ---------- shared pipeline ----------
def _ingest(db, source, raws, validate, key_of, insert_one, batch_id, now):
    roster = build_roster_from_db(db)
    existing = {r[0] for r in db.execute("SELECT end_to_end_key FROM transactions WHERE source_system=?", (source,))}
    seen, accepted, rejected = set(), [], []
    for idx, raw in enumerate(raws):
        model, reasons = validate(raw, roster)
        ref = str(raw.get("end_to_end_id") or raw.get("order_id") or f"row {idx}")
        if model is not None:
            key = key_of(model)
            if key in seen or key in existing:
                model, reasons = None, [("DUPLICATE_KEY", f"key {key!r} already loaded or repeated in this batch")]
            else:
                seen.add(key)
        if model is None:
            rejected.append((ref, reasons, raw))
        else:
            accepted.append(model)
    counts = Counter(r[1][0][0] for r in rejected)
    with db:   # one transaction: all or nothing
        db.execute("INSERT INTO load_audit(batch_id,source_system,loaded_at,rows_in,rows_mapped,rows_rejected,reject_reasons) VALUES (?,?,?,?,?,?,?)",
                   (batch_id, source, now, len(raws), len(accepted), len(rejected), json.dumps(dict(counts))))
        for ref, reasons, raw in rejected:
            db.execute("INSERT INTO exceptions(batch_id,source_system,record_ref,reason_code,reason_detail,payload_json,created_at) VALUES (?,?,?,?,?,?,?)",
                       (batch_id, source, ref, reasons[0][0], "; ".join(d for _, d in reasons), json.dumps(raw, default=str), now))
        for model in accepted:
            insert_one(db, model, batch_id, now)
    return {"batch_id": batch_id, "source": source, "rows_in": len(raws), "rows_mapped": len(accepted),
            "rows_rejected": len(rejected), "reasons": dict(counts)}


def build_roster_from_db(db):
    rows = db.execute("SELECT bic, code, active FROM participants").fetchall()
    return {"bics": {r[0]: bool(r[2]) for r in rows}, "codes": {r[1]: bool(r[2]) for r in rows}}


def _add_ids(db, txn_id, source, items):
    for kind, value, scope in items:
        if value:
            db.execute("INSERT INTO transaction_source_ids(txn_id,source_system,identifier_kind,identifier_value,scope) VALUES (?,?,?,?,?)",
                       (txn_id, source, kind, value, scope))


def _status_report(db, txn_id, status, source, now, code=None, text=None):
    db.execute("INSERT INTO status_reports(txn_id,iso_status,canonical_status,reason_code,reason_text,source,created_at) VALUES (?,?,?,?,?,?,?)",
               (txn_id, ISO_STATUS[status], status, code, text, source, now))


# ---------- pacs.008 ----------
def load_pacs008(db, xml_path, batch_id=None, now=None):
    now = now or now_iso()
    header, raws = parse_pacs008(xml_path)
    by_bic, _ = _participant_maps(db)
    batch_id = batch_id or f"PACS008-{now}"

    def insert_one(db, m, batch, ts):
        d_party = _party(db, by_bic[m.debtor_bic], "ACCOUNT", m.debtor_account, m.debtor_name)
        c_party = _party(db, by_bic[m.creditor_bic], "MSISDN", m.creditor_account, m.creditor_name)
        cur = db.execute(
            """INSERT INTO transactions(source_system,channel,direction,end_to_end_key,debtor_party_id,creditor_party_id,
               debtor_participant_id,creditor_participant_id,amount_minor,currency,charge_bearer,remittance_info,initiated_at,
               settled_at,status,status_source,status_detail,batch_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("PACS008", "BANK", "PUSH", m.end_to_end_id, d_party, c_party, by_bic[m.debtor_bic], by_bic[m.creditor_bic],
             _minor(m.amount), m.currency, CHARGE_MAP[m.charge_bearer], m.remittance_info, header["created"] or ts,
             m.settlement_date.isoformat(), "DISPATCHED", "DERIVED_ACK", "ack received, awaiting reconciliation", batch))
        tid = cur.lastrowid
        _add_ids(db, tid, "PACS008", [("MSG_ID", m.msg_id, "MESSAGE"), ("INSTR_ID", m.instr_id, "TRANSACTION"),
                                      ("END_TO_END_ID", m.end_to_end_id, "TRANSACTION"), ("TX_ID", m.tx_id, "TRANSACTION"),
                                      ("UETR", m.uetr, "TRANSACTION"), ("CLR_SYS_REF", m.clr_sys_ref, "TRANSACTION")])
        _status_report(db, tid, "DISPATCHED", "DERIVED_ACK", ts, None, "derived from acknowledgement; pacs.008 carries no status")

    return _ingest(db, "PACS008", raws, validate_pacs, lambda m: m.end_to_end_id, insert_one, batch_id, now)


# ---------- Orange ----------
def load_orange(db, json_path, batch_id=None, now=None):
    now = now or now_iso()
    raws = json.load(open(json_path))
    by_bic, by_code = _participant_maps(db)
    wallet = by_code["ORANGE_MONEY"]
    batch_id = batch_id or f"ORANGE-{now}"

    def insert_one(db, m, batch, ts):
        c_party = _party(db, wallet, "MSISDN", m.customer_id)
        status = OM_STATUS_MAP[m.status]
        cur = db.execute(
            """INSERT INTO transactions(source_system,channel,direction,end_to_end_key,debtor_party_id,creditor_party_id,
               debtor_participant_id,creditor_participant_id,amount_minor,currency,charge_bearer,remittance_info,initiated_at,
               settled_at,status,status_source,status_detail,batch_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("ORANGE", "WALLET", "PUSH", m.order_id, None, c_party, by_code[m.partner_id], wallet, _minor(m.amount), m.unit, None,
             m.reference, m.created_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), None, status, "SOURCE_ECHO",
             f"Orange status {m.status}", batch))
        tid = cur.lastrowid
        _add_ids(db, tid, "ORANGE", [("OM_TRANSACTION_ID", m.transaction_id, "TRANSACTION"), ("OM_ORDER_ID", m.order_id, "TRANSACTION"),
                                     ("OM_PAY_TOKEN", m.pay_token, "TRANSACTION")])
        _status_report(db, tid, status, "SOURCE_ECHO", ts, m.status if status == "FAILED" else None, f"Orange status {m.status} (echoed)")

    return _ingest(db, "ORANGE", raws, validate_orange, lambda m: m.order_id, insert_one, batch_id, now)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/poc.db"); ap.add_argument("--schema", default="src/schema.sql")
    ap.add_argument("--participants", default="src/participants.yaml")
    ap.add_argument("--xml", default="data/poc_pacs008.xml"); ap.add_argument("--orange", default="data/poc_orange.json")
    ap.add_argument("--reset", action="store_true", help="delete and rebuild the database first")
    a = ap.parse_args()
    if a.reset and os.path.exists(a.db):
        os.remove(a.db)
    fresh = not os.path.exists(a.db)
    db = connect(a.db)
    if fresh:
        create_schema(db, a.schema)
    seed_participants(db, a.participants)
    for summary in (load_pacs008(db, a.xml), load_orange(db, a.orange)):
        print(f"{summary['source']:8} rows_in={summary['rows_in']:4} mapped={summary['rows_mapped']:4} "
              f"rejected={summary['rows_rejected']:3} reasons={summary['reasons']}")
    for table in ("transactions", "transaction_source_ids", "parties", "status_reports", "exceptions", "load_audit"):
        print(f"{table:24}", db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


if __name__ == "__main__":
    main()
