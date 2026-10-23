import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import multiroute_core as core
from multiroute_core import DemoError, compute_fee

ENGINE = next(
    c for _, c in inspect.getmembers(core, inspect.isclass)
    if hasattr(c, "quote") and hasattr(c, "create") and hasattr(c, "advance")
)

SRC, DST = "ABSA_BW", "ORANGE_MONEY"


class FeeArithmeticTests(unittest.TestCase):
    def test_experimental_known_values(self):
        cases = {
            10_000: 150,
            20_000: 200,
            1_000_000: 2_500,
        }
        for amount, expected in cases.items():
            with self.subTest(amount=amount):
                self.assertEqual(compute_fee("EXPERIMENTAL", amount), expected)

    def test_rounding_is_half_up(self):
        cases = {100: 101, 300: 102}
        for amount, expected in cases.items():
            with self.subTest(amount=amount):
                self.assertEqual(compute_fee("EXPERIMENTAL", amount), expected)

    def test_cap_boundary(self):
        self.assertEqual(compute_fee("EXPERIMENTAL", 479_899), 2_499)
        self.assertEqual(compute_fee("EXPERIMENTAL", 479_900), 2_500)

    def test_none_policy_is_free(self):
        for amount in (1, 100, 12_500, 1_000_000):
            with self.subTest(amount=amount):
                self.assertEqual(compute_fee("NONE", amount), 0)

    def test_fee_never_below_minimum_or_above_cap(self):
        for amount in range(1, 1_000_001, 9_973):
            fee = compute_fee("EXPERIMENTAL", amount)
            self.assertGreaterEqual(fee, 100)
            self.assertLessEqual(fee, 2_500)


class FeeFlowTests(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.engine = ENGINE(clock=lambda: self.now[0])

    def quote(self, amount="200.00", policy="EXPERIMENTAL"):
        return self.engine.quote({
            "source": SRC, "destination": DST,
            "amount": amount, "fee_policy": policy,
        })

    def body(self, quote, amount="200.00", reference="FEE-001",
             scenario="SUCCESS"):
        return {
            "source": SRC, "destination": DST, "amount": amount,
            "reference": reference, "scenario": scenario,
            "quote_id": quote["quote_id"],
        }

    def finish(self, result):
        for _ in range(12):
            transfer = result["transfer"]
            if transfer["terminal"] or transfer["status"] == "PENDING":
                return result
            result = self.engine.advance(transfer["id"])
        self.fail("Transfer did not reach a stopping state.")

    def assert_clean(self, result):
        rec = result["reconciliation"]
        self.assertTrue(rec["journal_balanced"])
        self.assertTrue(rec["session_money_conserved"])
        self.assertTrue(rec["status_consistent"])

    def test_quote_contents(self):
        q = self.quote()
        self.assertEqual(q["fee_minor"], 200)
        self.assertEqual(q["total_debit_minor"], 20_200)
        self.assertEqual(q["recipient_receives_minor"], 20_000)
        self.assertEqual(q["fee_payer"], "SENDER")

    def test_unknown_policy_rejected(self):
        with self.assertRaises(DemoError):
            self.quote(policy="MADE_UP")

    def test_settled_moves_money_correctly(self):
        q = self.quote()
        result = self.finish(self.engine.create(self.body(q)))
        rec = result["reconciliation"]
        self.assertEqual(result["transfer"]["status"], "SETTLED")
        self.assertEqual(rec["source_net_minor"], -20_200)
        self.assertEqual(rec["destination_net_minor"], 20_000)
        self.assertEqual(rec["fee_collected_minor"], 200)
        self.assertEqual(rec["unresolved_principal_minor"], 0)
        self.assertEqual(rec["unresolved_fee_minor"], 0)
        self.assert_clean(result)

    def test_none_policy_collects_no_fee(self):
        q = self.quote(policy="NONE")
        result = self.finish(self.engine.create(self.body(q)))
        rec = result["reconciliation"]
        self.assertEqual(rec["source_net_minor"], -20_000)
        self.assertEqual(rec["fee_collected_minor"], 0)
        self.assert_clean(result)

    def test_destination_failure_collects_no_fee(self):
        q = self.quote()
        result = self.finish(
            self.engine.create(self.body(q, scenario="DESTINATION_FAILED"))
        )
        rec = result["reconciliation"]
        self.assertEqual(rec["fee_collected_minor"], 0)
        self.assertFalse(rec["settled"])
        self.assert_clean(result)

    def test_fee_pushes_transfer_over_balance(self):
        opening = self.engine.accounts["DEMO-%s-001" % SRC]["opening_minor"]
        self.assertEqual(opening, 100_000)

        q = self.quote("999.50")
        self.assertEqual(q["amount_minor"], 99_950)
        self.assertGreater(q["total_debit_minor"], opening)

        result = self.engine.create(self.body(q, "999.50", "FEE-OVER"))
        self.assertEqual(result["transfer"]["status"], "QUEUED")

        result = self.finish(result)
        self.assertEqual(result["transfer"]["status"], "REJECTED")
        self.assertEqual(result["reconciliation"]["fee_collected_minor"], 0)
        self.assertEqual(result["reconciliation"]["source_net_minor"], 0)
        self.assert_clean(result)

    def test_same_amount_without_fee_succeeds(self):
        q = self.quote("999.50", policy="NONE")
        result = self.finish(
            self.engine.create(self.body(q, "999.50", "FEE-FREE"))
        )
        self.assertEqual(result["transfer"]["status"], "SETTLED")

    def test_quote_is_single_use(self):
        q = self.quote()
        self.engine.create(self.body(q, reference="FEE-A"))
        with self.assertRaises(DemoError) as ctx:
            self.engine.create(self.body(q, reference="FEE-B"))
        self.assertEqual(ctx.exception.status, 409)

    def test_quote_expires(self):
        q = self.quote()
        self.now[0] += self.engine.QUOTE_TTL + 1
        with self.assertRaises(DemoError) as ctx:
            self.engine.create(self.body(q))
        self.assertEqual(ctx.exception.status, 410)

    def test_quote_valid_at_ttl_boundary(self):
        q = self.quote()
        self.now[0] += self.engine.QUOTE_TTL
        result = self.engine.create(self.body(q))
        self.assertIn("transfer", result)

    def test_quote_bound_to_amount(self):
        q = self.quote("200.00")
        with self.assertRaises(DemoError) as ctx:
            self.engine.create(self.body(q, amount="500.00"))
        self.assertEqual(ctx.exception.status, 409)

    def test_missing_or_unknown_quote_rejected(self):
        body = {
            "source": SRC, "destination": DST, "amount": "200.00",
            "reference": "NOQ-1", "scenario": "SUCCESS",
        }
        with self.assertRaises(DemoError):
            self.engine.create(body)
        body["quote_id"] = "Q-doesnotexist"
        with self.assertRaises(DemoError):
            self.engine.create(body)

    def test_replay_does_not_charge_twice(self):
        q = self.quote()
        body = self.body(q, reference="FEE-REPLAY")
        first = self.finish(self.engine.create(body))
        again = self.engine.create(body)
        self.assertEqual(again["transfer"]["id"], first["transfer"]["id"])
        rec = again["reconciliation"]
        self.assertEqual(rec["source_net_minor"], -20_200)
        self.assertEqual(rec["fee_collected_minor"], 200)

    def test_replay_with_different_details_conflicts(self):
        q = self.quote()
        body = self.body(q, reference="FEE-CONFLICT")
        self.engine.create(body)
        changed = dict(body, scenario="PENDING")
        with self.assertRaises(DemoError) as ctx:
            self.engine.create(changed)
        self.assertEqual(ctx.exception.status, 409)


if __name__ == "__main__":
    unittest.main()
