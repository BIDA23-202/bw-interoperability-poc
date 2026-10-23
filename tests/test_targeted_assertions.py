import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from multiroute_core import DemoEngine, DemoError
from registry import PARTICIPANTS

SRC, DST = "ABSA_BW", "ORANGE_MONEY"
SRC_ACCOUNT = PARTICIPANTS[SRC]["account"]
OPENING = 100_000


def make_quote(engine, amount="125.00", policy="NONE"):
    return engine.quote({
        "source": SRC, "destination": DST,
        "amount": amount, "fee_policy": policy,
    })["quote_id"]


def instruction(quote_id, reference, amount="125.00", scenario="SUCCESS"):
    return {
        "source": SRC, "destination": DST, "reference": reference,
        "amount": amount, "quote_id": quote_id, "scenario": scenario,
    }


def advance_until_terminal(engine, result):
    for _ in range(20):
        if result["transfer"]["terminal"]:
            return result
        result = engine.advance(result["transfer"]["id"])
    raise AssertionError("transfer never reached a terminal state")


class TargetedAssertionTests(unittest.TestCase):
    def test_quote_is_accepted_at_its_exact_expiry_instant(self):
        engine = DemoEngine()
        quote_id = make_quote(engine)
        expires_at = engine.quotes[quote_id]["expires_at"]
        engine.clock = lambda: expires_at
        try:
            result = engine.create(instruction(quote_id, "BOUNDARY-1"))
        except DemoError as exc:
            self.fail(f"quote rejected at its exact expiry: {exc} (status {exc.status})")
        self.assertIn("transfer", result)

    def test_replay_returns_the_original_transfer_without_a_second_one(self):
        engine = DemoEngine()
        quote_id = make_quote(engine)
        body = instruction(quote_id, "REPLAY-1")
        first = advance_until_terminal(engine, engine.create(dict(body)))
        balance = engine.accounts[SRC_ACCOUNT]["balance_minor"]
        try:
            second = engine.create(dict(body))
        except DemoError as exc:
            self.fail(f"replay was rejected instead of returned: {exc} (status {exc.status})")
        self.assertEqual(second["transfer"]["id"], first["transfer"]["id"])
        self.assertEqual(len(engine.transfers), 1)
        self.assertEqual(engine.accounts[SRC_ACCOUNT]["balance_minor"], balance)

    def test_operator_refund_reverses_a_failed_compensation(self):
        engine = DemoEngine()
        quote_id = make_quote(engine, policy="EXPERIMENTAL")
        result = engine.create(
            instruction(quote_id, "REFUND-1", scenario="COMPENSATION_FAILED")
        )
        result = advance_until_terminal(engine, result)
        self.assertEqual(result["transfer"]["status"], "EXCEPTION")
        try:
            result = engine.resolve(result["transfer"]["id"], "REFUND")
        except DemoError as exc:
            self.fail(f"REFUND was refused on an EXCEPTION transfer: {exc} (status {exc.status})")
        result = advance_until_terminal(engine, result)
        self.assertEqual(result["transfer"]["status"], "REVERSED")
        self.assertEqual(engine.accounts[SRC_ACCOUNT]["balance_minor"], OPENING)

    def test_unaffordable_transfer_is_rejected_not_errored(self):
        cases = (("1000.01", "NONE"), ("999.00", "EXPERIMENTAL"))
        for amount, policy in cases:
            with self.subTest(amount=amount, policy=policy):
                engine = DemoEngine()
                quote_id = make_quote(engine, amount, policy)
                result = engine.create(instruction(quote_id, "FUNDS-1", amount))
                try:
                    result = advance_until_terminal(engine, result)
                except DemoError as exc:
                    self.fail(f"engine raised instead of rejecting: {exc} (status {exc.status})")
                self.assertEqual(result["transfer"]["status"], "REJECTED")
                self.assertEqual(engine.accounts[SRC_ACCOUNT]["balance_minor"], OPENING)


if __name__ == "__main__":
    unittest.main()