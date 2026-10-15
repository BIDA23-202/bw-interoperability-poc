"""
patch_v1_1.py  |  Applies the three v1.1 fixes found by the round-trip test. Safe to run twice.
  1. schema.sql         : charge_bearer also allows SERVICE_LEVEL (so SLEV is no longer merged into SHARED)
  2. loader.py          : SLEV maps to SERVICE_LEVEL
  3. make_poc_pairs.py  : the wallet leg's reference carries the bank's remittance text (the semantic twin of RmtInf.Ustrd)
Usage:  python patch_v1_1.py --src /content/drive/MyDrive/bw-interop-poc/src
After patching, re-run step 1 (make_poc_pairs.py) and the loader with --reset.
"""
import argparse
import os
import sys

EDITS = [
    ("schema.sql", "CHECK (charge_bearer IN ('DEBT','CRED','SHARED'))", "CHECK (charge_bearer IN ('DEBT','CRED','SHARED','SERVICE_LEVEL'))", True),
    ("schema.sql", "SLEV normalised to SHARED", "SLEV kept as SERVICE_LEVEL (no information loss)", False),
    ("loader.py", '"SLEV": "SHARED"', '"SLEV": "SERVICE_LEVEL"', True),
    ("make_poc_pairs.py", '"reference": f"REF-{n:06d}"', '"reference": iso["rmt"]', True),
]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--src", default="src"); a = ap.parse_args()
    problems = 0
    for name, old, new, required in EDITS:
        path = os.path.join(a.src, name)
        text = open(path).read()
        if new in text:
            print(f"{name:20} already patched: {new[:55]}")
        elif old in text:
            open(path, "w").write(text.replace(old, new)); print(f"{name:20} patched: {old[:45]}  ->  {new[:45]}")
        else:
            print(f"{name:20} {'NOT FOUND' if required else 'not found (optional)'}: {old[:60]}")
            problems += 1 if required else 0
    print("DONE" if not problems else f"{problems} required edit(s) not found: edit the file by hand")
    sys.exit(1 if problems else 0)

if __name__ == "__main__":
    main()
