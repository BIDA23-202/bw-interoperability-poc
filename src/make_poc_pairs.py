#!/usr/bin/env python3
"""
make_poc_pairs.py  |  Build the synthetic PoC dataset from the matched pool.

Input : MatchedPoolReference.csv  (PaySim columns: step, type, amount, nameOrig, nameDest, isFraud, ...)
Output: <out>_pacs008.xml     one pacs.008-style message with many CdtTrfTxInf (bank leg)
        <out>_orange.json     list of Orange-style wallet-leg records
        <out>_manifest.csv    ground truth: expected reconciliation outcome per pair, with the injected anomaly

Run   : python make_poc_pairs.py --matched-pool MatchedPoolReference.csv --participants participants.yaml \
                                 --n 400 --seed 42 --out data/poc
Everything is synthetic and seeded: the same seed gives byte-identical files.
Modelling choices (state them in the Methodology): the reconciliation key is the bank EndToEndId, which is also the
Orange order_id; the wallet leg uses customer.id = credited wallet MSISDN and partner.id = originating bank code;
amounts are decimal BWP with 2 places on both sides; PaySim amounts are filtered to [1, max_amount].
"""
import argparse, csv, hashlib, json, random, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
import pandas as pd
import yaml

NS = "urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"
BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)
RATES = {"AMOUNT_MISMATCH": 0.03, "FAILED_STATUS": 0.03, "LAG_OUTLIER": 0.02, "MISSING_CURRENCY": 0.015,
         "UNKNOWN_BIC": 0.015, "DUPLICATE_E2E": 0.02, "ORPHAN_ISO": 0.015, "ORPHAN_OM": 0.015}
EXPECTED = {"": "MATCHED", "LAG_OUTLIER": "MATCHED", "DUPLICATE_E2E": "MATCHED", "AMOUNT_MISMATCH": "DISCREPANT",
            "FAILED_STATUS": "DISCREPANT", "MISSING_CURRENCY": "REJECTED", "UNKNOWN_BIC": "REJECTED",
            "ORPHAN_ISO": "UNMATCHED", "ORPHAN_OM": "UNMATCHED"}
FLAGS = {"LAG_OUTLIER": "LAG_OUTLIER", "AMOUNT_MISMATCH": "AMOUNT_DIFF", "FAILED_STATUS": "STATUS_MISMATCH"}

def digits(seed_text, n):
    return str(int(hashlib.md5(seed_text.encode()).hexdigest(), 16))[:n].zfill(n)

def msisdn(name):   # synthetic Botswana-style mobile number: 267 7x xxx xxx
    return "2677" + digits("m" + name, 7)

def account(name):  # synthetic 10-digit bank account number
    return digits("a" + name, 10)

def load_participants(path):
    roster = yaml.safe_load(open(path))["participants"]
    banks = [p for p in roster if p["type"] == "BANK" and p["active"]]
    wallet = next(p for p in roster if p["code"] == "ORANGE_MONEY")
    return banks, wallet

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matched-pool", required=True); ap.add_argument("--participants", default="participants.yaml")
    ap.add_argument("--n", type=int, default=400); ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-amount", type=float, default=50000.0); ap.add_argument("--out", default="data/poc")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    banks, wallet = load_participants(a.participants)
    pool = pd.read_csv(a.matched_pool, usecols=["step", "amount", "nameOrig", "nameDest"])
    pool = pool[(pool["amount"] >= 1) & (pool["amount"] <= a.max_amount)]
    sample = pool.sample(n=a.n, random_state=a.seed).reset_index(drop=True)

    kinds = []
    for k, r in RATES.items(): kinds += [k] * round(r * a.n)
    anomalous = rng.sample(range(a.n), len(kinds)); plan = {i: k for i, k in zip(anomalous, kinds)}

    iso_rows, om_rows, manifest = [], [], []
    for i, row in sample.iterrows():
        n = i + 1; kind = plan.get(i, "")
        start = BASE + timedelta(hours=int(row["step"]), minutes=rng.randint(0, 59))
        amt = round(float(row["amount"]), 2)
        lag = rng.choice([0, 1, 2, 3])
        if kind == "LAG_OUTLIER": lag = rng.choice([-5, -4, -3, -2, -1, 4, 5, 6, 7, 8, 9, 10])
        e2e = f"E2E-BW-{start:%Y%m%d}-{n:04d}"
        bank = rng.choice(banks); cdtr_msisdn = msisdn(row["nameDest"]); dbtr_acct = account(row["nameOrig"])
        iso = {"instr": f"INSTR-BW-{n:05d}", "e2e": e2e, "tx": f"TX-BW-{n:05d}", "uetr": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
               "clr": f"CLR-BW-{n:05d}", "amt": f"{amt:.2f}", "ccy": "BWP", "dt": (start.date() + timedelta(days=lag)).isoformat(),
               "chrg": rng.choice(["DEBT", "CRED", "SHAR", "SLEV"]), "dbtr_nm": f"DBTR-{digits(row['nameOrig'], 6)}",
               "dbtr_acct": dbtr_acct, "dbtr_bic": bank["bic"], "cdtr_nm": f"CDTR-{digits(row['nameDest'], 6)}",
               "cdtr_acct": cdtr_msisdn, "cdtr_bic": wallet["bic"], "rmt": f"Invoice 2026-{n:04d}"}
        status = "SUCCESS"; om_amt = amt
        if kind == "AMOUNT_MISMATCH":
            delta = max(1.0, amt * rng.uniform(0.01, 0.05)); om_amt = round(amt + rng.choice([-1, 1]) * delta, 2)
            if om_amt <= 0: om_amt = round(amt + delta, 2)
        if kind == "FAILED_STATUS": status = rng.choice(["FAILED", "REJECTED", "CANCELLED"])
        if kind == "MISSING_CURRENCY": iso["ccy"] = ""
        if kind == "UNKNOWN_BIC": iso["cdtr_bic"] = "MOCKBWZZXXX"
        om = {"transactionId": f"OM-{start:%Y%m%d}-{n:05d}", "order_id": e2e, "reference": iso["rmt"],
              "amount": {"value": om_amt, "unit": "BWP"}, "customer": {"id": cdtr_msisdn, "idType": "MSISDN"},
              "partner": {"id": bank["code"], "idType": "CODE"}, "status": status,
              "createdAt": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "pay_token": f"{rng.getrandbits(96):024x}"}
        iso_present = kind != "ORPHAN_OM"; om_present = kind != "ORPHAN_ISO"
        if iso_present: iso_rows.append(iso)
        if om_present: om_rows.append(om)
        manifest.append({"pair_id": f"P{n:04d}", "end_to_end_id": e2e, "anomaly_type": kind, "iso_present": int(iso_present),
                         "om_present": int(om_present), "expected_outcome": EXPECTED[kind], "expected_flag": FLAGS.get(kind, ""),
                         "amount_iso": iso["amt"] if iso_present else "", "amount_om": f"{om_amt:.2f}" if om_present else "",
                         "currency_iso": iso["ccy"] if iso_present else "", "creditor_msisdn": cdtr_msisdn, "debtor_account": dbtr_acct,
                         "debtor_bic": iso["dbtr_bic"], "creditor_bic": iso["cdtr_bic"],
                         "iso_settlement_date": iso["dt"] if iso_present else "", "om_created_at": om["createdAt"] if om_present else "",
                         "lag_days": lag})
        if kind == "DUPLICATE_E2E":
            dup = dict(iso, instr=f"INSTR-BW-{n:05d}D", tx=f"TX-BW-{n:05d}D", clr=f"CLR-BW-{n:05d}D", uetr=str(uuid.UUID(int=rng.getrandbits(128), version=4)))
            iso_rows.append(dup)
            manifest.append({**manifest[-1], "pair_id": f"P{n:04d}D", "iso_present": 1, "om_present": 0, "expected_outcome": "REJECTED",
                             "expected_flag": "DUPLICATE_KEY", "amount_om": "", "om_created_at": ""})

    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    ET.register_namespace("", NS)
    q = lambda t: f"{{{NS}}}{t}"
    doc = ET.Element(q("Document")); root = ET.SubElement(doc, q("FIToFICstmrCdtTrf"))
    gh = ET.SubElement(root, q("GrpHdr"))
    ET.SubElement(gh, q("MsgId")).text = "MSG-BW-20260801-POC"
    ET.SubElement(gh, q("CreDtTm")).text = "2026-10-15T08:00:00Z"
    ET.SubElement(gh, q("NbOfTxs")).text = str(len(iso_rows))
    total = ET.SubElement(gh, q("TtlIntrBkSttlmAmt"), Ccy="BWP"); total.text = f"{sum(float(r['amt']) for r in iso_rows):.2f}"
    ET.SubElement(ET.SubElement(gh, q("SttlmInf")), q("SttlmMtd")).text = "INGA"
    for r in iso_rows:
        t = ET.SubElement(root, q("CdtTrfTxInf")); p = ET.SubElement(t, q("PmtId"))
        for tag, key in [("InstrId", "instr"), ("EndToEndId", "e2e"), ("TxId", "tx"), ("UETR", "uetr"), ("ClrSysRef", "clr")]:
            ET.SubElement(p, q(tag)).text = r[key]
        amt_el = ET.SubElement(t, q("IntrBkSttlmAmt")); amt_el.text = r["amt"]
        if r["ccy"]: amt_el.set("Ccy", r["ccy"])
        ET.SubElement(t, q("IntrBkSttlmDt")).text = r["dt"]
        ET.SubElement(t, q("ChrgBr")).text = r["chrg"]
        ET.SubElement(ET.SubElement(t, q("Dbtr")), q("Nm")).text = r["dbtr_nm"]
        ET.SubElement(ET.SubElement(ET.SubElement(ET.SubElement(t, q("DbtrAcct")), q("Id")), q("Othr")), q("Id")).text = r["dbtr_acct"]
        ET.SubElement(ET.SubElement(ET.SubElement(t, q("DbtrAgt")), q("FinInstnId")), q("BICFI")).text = r["dbtr_bic"]
        ET.SubElement(ET.SubElement(ET.SubElement(t, q("CdtrAgt")), q("FinInstnId")), q("BICFI")).text = r["cdtr_bic"]
        ET.SubElement(ET.SubElement(t, q("Cdtr")), q("Nm")).text = r["cdtr_nm"]
        ET.SubElement(ET.SubElement(ET.SubElement(ET.SubElement(t, q("CdtrAcct")), q("Id")), q("Othr")), q("Id")).text = r["cdtr_acct"]
        ET.SubElement(ET.SubElement(t, q("RmtInf")), q("Ustrd")).text = r["rmt"]
    ET.indent(doc)
    ET.ElementTree(doc).write(f"{out}_pacs008.xml", encoding="utf-8", xml_declaration=True)
    json.dump(om_rows, open(f"{out}_orange.json", "w"), indent=2)
    with open(f"{out}_manifest.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(manifest[0])); w.writeheader(); w.writerows(manifest)
    man = pd.DataFrame(manifest)
    print(f"pacs.008 records: {len(iso_rows)}  Orange records: {len(om_rows)}  manifest rows: {len(man)}")
    print(man["expected_outcome"].value_counts().to_string()); print(man["anomaly_type"].replace("", "(clean)").value_counts().to_string())

if __name__ == "__main__":
    main()
