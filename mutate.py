"""Mutation checks for src/multiroute_core.py.

Applies one single-line fault at a time, runs the full test suite, then
restores the file. A mutation is KILLED if any test fails. Run from anywhere:

    python mutate.py

Requires a clean `src` directory (commit or stash first).
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "src" / "multiroute_core.py"
os.chdir(ROOT)

# Mutants that cannot change behaviour with the current fee policies.
EQUIVALENT = {"fee: minimum removed"}

# (round, label, exact text to find once, replacement)
MUTATIONS = [
    # Round 1: fee, quote, idempotency, settlement, input rules
    (1, "fee: rounding removed",
     'policy["bps"] + 5000) // 10000', 'policy["bps"]) // 10000'),
    (1, "fee: flat part dropped",
     'fee = policy["flat_minor"] + (amount', 'fee = 0 + (amount'),
    (1, "fee: minimum removed",
     'fee = max(fee, policy["min_minor"])', 'fee = fee'),
    (1, "fee: cap removed",
     'fee = min(fee, policy["cap_minor"])', 'fee = fee'),
    (1, "quote: never marked used",
     'q["used"] = True', 'q["used"] = False'),
    (1, "quote: used check skipped",
     'if q["used"]:', 'if False:'),
    (1, "quote: expiry check skipped",
     'if self.clock() > q["expires_at"]:', 'if False:'),
    (1, "quote: expires at the boundary",
     'self.clock() > q["expires_at"]', 'self.clock() >= q["expires_at"]'),
    (1, "quote: source not bound",
     '(q["source"], q["destination"], q["amount_minor"]) != (',
     '(source, q["destination"], q["amount_minor"]) != ('),
    (1, "quote: destination not bound",
     '(q["source"], q["destination"], q["amount_minor"]) != (',
     '(q["source"], destination, q["amount_minor"]) != ('),
    (1, "quote: amount not bound",
     '(q["source"], q["destination"], q["amount_minor"]) != (',
     '(q["source"], q["destination"], amount) != ('),
    (1, "idempotency: replay lookup skipped",
     'if reference in self.references:', 'if False:'),
    (1, "idempotency: conflict check skipped",
     'if original != fingerprint:', 'if False:'),
    (1, "settle: fee never recognised",
     'self._post(t, cf, FEE_ACCOUNT, F, "FEE_RECOGNISED")', 'pass'),
    (1, "settle: destination never credited",
     'self._post(t, cp, dst, P, "DESTINATION_CREDIT")', 'pass'),
    (1, "limits: session cap of 200 removed",
     'if len(self.transfers) >= 200:', 'if False:'),
    (1, "input: currency check removed",
     'if body.get("currency", "BWP") != "BWP":', 'if False:'),
    (1, "input: scenario check removed",
     'if scenario not in SCENARIOS:', 'if False:'),
    (1, "input: reference limit 80 to 81",
     'len(reference) > 80', 'len(reference) > 81'),

    # Round 2: posting, ledger, refund, retry, reconciliation
    (2, "post: overdraw guard removed",
     'if self._get(t, from_key) < amount:', 'if False:'),
    (2, "post: exact balance rejected (< to <=)",
     'if self._get(t, from_key) < amount:',
     'if self._get(t, from_key) <= amount:'),
    (2, "post: zero-amount shortcut removed",
     'if amount == 0:', 'if False:'),
    (2, "post: debit never applied",
     'self._set(t, from_key, self._get(t, from_key) - amount)', 'pass'),
    (2, "ledger: debit recorded as credit",
     'for key, delta in ((from_key, -amount), (to_key, amount)):',
     'for key, delta in ((from_key, amount), (to_key, amount)):'),
    (2, "ledger: journal sequence frozen",
     't["journal_seq"] += 1', 't["journal_seq"] += 0'),
    (2, "clearing: fee balance reads principal",
     'return t["clearing_fee_minor"]', 'return t["clearing_principal_minor"]'),
    (2, "refund: principal not refunded",
     'self._post(t, cp, src, P, "SOURCE_REFUND_PRINCIPAL")', 'pass'),
    (2, "refund: fee not refunded",
     'self._post(t, cf, src, F, "SOURCE_REFUND_FEE")', 'pass'),
    (2, "refund: fee sent to fee account instead of sender",
     'self._post(t, cf, src, F, "SOURCE_REFUND_FEE")',
     'self._post(t, cf, FEE_ACCOUNT, F, "SOURCE_REFUND_FEE")'),
    (2, "retry: operator REFUND action disabled",
     'elif t["status"] == "EXCEPTION" and action == "REFUND":', 'elif False:'),
    (2, "retry: scenario not reset to a working one",
     't["scenario"] = "DESTINATION_FAILED"',
     't["scenario"] = "COMPENSATION_FAILED"'),
    (2, "reconciliation: settled expects no fee",
     'expected = (-(P + F), P, F, 0, 0)', 'expected = (-(P + F), P, 0, 0, 0)'),
    (2, "reconciliation: pending expects no fee in clearing",
     'expected = (-(P + F), 0, 0, P, F)', 'expected = (-(P + F), 0, 0, P, 0)'),

    # Round 3: source debit, fee hold, scenario branches
    (3, "step3: funds check removed",
     'or self.accounts[src]["balance_minor"] < t["total_debit_minor"]',
     'or False'),
    (3, "step3: exact balance rejected (< to <=)",
     'or self.accounts[src]["balance_minor"] < t["total_debit_minor"]',
     'or self.accounts[src]["balance_minor"] <= t["total_debit_minor"]'),
    (3, "hold: principal never debited",
     'self._post(t, src, cp, P, "SOURCE_DEBIT_PRINCIPAL")', 'pass'),
    (3, "hold: fee never debited",
     'self._post(t, src, cf, F, "SOURCE_DEBIT_FEE")', 'pass'),
    (3, "cancel: cancellation ignored",
     'if scenario == "CANCELLED":', 'if False:'),
    (3, "pending: pending outcome ignored",
     'if scenario == "PENDING":', 'if False:'),
]


def run_suite():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        p = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
            capture_output=True, text=True, env=env, timeout=180,
        )
    except subprocess.TimeoutExpired:
        return 1, "TIMEOUT"
    lines = [l for l in p.stderr.strip().splitlines() if l.strip()]
    return p.returncode, (lines[-1] if lines else "no output")


def git_dirty():
    return subprocess.run(
        ["git", "status", "--porcelain", "--", "src"],
        capture_output=True, text=True,
    ).stdout.strip()


def main():
    if git_dirty():
        sys.exit("src has uncommitted changes; commit or stash first.")

    code, tail = run_suite()
    if code != 0:
        sys.exit(f"Baseline is not green: {tail}")
    print(f"Baseline OK: {tail}\n")

    original = TARGET.read_bytes()
    text = original.decode("utf-8")
    results = []
    try:
        for rnd, label, old, new in MUTATIONS:
            n = text.count(old)
            if n != 1:
                results.append((rnd, label, "NOT APPLIED", f"found {n} times"))
                continue
            TARGET.write_bytes(text.replace(old, new).encode("utf-8"))
            try:
                code, tail = run_suite()
            finally:
                TARGET.write_bytes(original)
            if code == 0:
                status = "SURVIVED"
            else:
                status = "KILLED/assert" if "failures=" in tail else "KILLED/crash"
            results.append((rnd, label, status, tail))
    finally:
        TARGET.write_bytes(original)
        subprocess.run(["git", "checkout", "--", str(TARGET)])

    width = max(len(r[1]) for r in results)
    for rnd, label, status, tail in results:
        print(f"R{rnd}  {label:<{width}}  {status:<14}  {tail}")

    killed = sum(1 for r in results if r[2].startswith("KILLED"))
    survivors = [r[1] for r in results if r[2] == "SURVIVED"]
    unexpected = [s for s in survivors if s not in EQUIVALENT]
    unapplied = [r[1] for r in results if r[2] == "NOT APPLIED"]
    crash_only = [r[1] for r in results if r[2] == "KILLED/crash"]

    print(f"\nKilled {killed} of {len(results)}.")
    print("Equivalent survivors:", [s for s in survivors if s in EQUIVALENT] or "none")
    print("Unexpected survivors:", unexpected or "none")
    print("Not applied:", unapplied or "none")
    print("Killed by crash only:", crash_only or "none")

    code, tail = run_suite()
    print(f"\nAfter restore: {tail}")
    print("src status:", git_dirty() or "clean")
    return 1 if (unexpected or unapplied) else 0


if __name__ == "__main__":
    sys.exit(main())