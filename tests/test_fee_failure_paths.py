import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import multiroute_core as core

ENGINE = next(
    c for _, c in inspect.getmembers(core, inspect.isclass)
    if hasattr(c, "quote") and hasattr(c, "create") and hasattr(c, "advance")
)

SRC, DST = "ABSA_BW", "ORANGE_MONEY"
SRC_ACCOUNT = "DEMO-ABSA_BW-001"


class FeeFailurePathTests(unittest.TestCase):
    def setUp(self):
        self.engine = ENGINE()

    def run_transfer(self, scenario, source=SRC, destination=DST,
                     amount="200.00", reference="FP-1"):
        q = self.engine.quote({
            "source": source, "destination": destination,
            "amount": amount, "fee_policy": "EXPERIMENTAL",
        })
        result = self.engine.create({
            "source": source, "destination": destination, "amount": amount,
            "reference": reference, "scenario": scenario,
            "quote_id": q["quote_id"],
        })
        for _ in range(12):
            t = result["transfer"]
            if t["terminal"] or t["status"] == "PENDING":
                return result
            result = self.engine.advance(t["id"])
        self.fail("Transfer did not reach a stopping state.")

    def assert_clean(self, result):
        rec = result["reconciliation"]
        self.assertTrue(rec["journal_balanced"])
        self.assertTrue(rec["session_money_conserved"])
        self.assertTrue(rec["status_consistent"])

    def test_pending_holds_fee_in_clearing(self):
        result = self.run_transfer("PENDING")
        rec = result["reconciliation"]
        self.assertEqual(result["transfer"]["status"], "PENDING")
        self.assertEqual(rec["source_net_minor"], -20_200)
        self.assertEqual(rec["destination_net_minor"], 0)
        self.assertEqual(rec["fee_collected_minor"], 0)
        self.assertEqual(rec["unresolved_principal_minor"], 20_000)
        self.assertEqual(rec["unresolved_fee_minor"], 200)
        self.assertFalse(rec["settled"])
        self.assert_clean(result)

    def test_destination_failure_refunds_principal_and_fee(self):
        result = self.run_transfer("DESTINATION_FAILED")
        rec = result["reconciliation"]
        self.assertEqual(result["transfer"]["status"], "REVERSED")
        self.assertEqual(rec["source_net_minor"], 0)
        self.assertEqual(rec["destination_net_minor"], 0)
        self.assertEqual(rec["fee_collected_minor"], 0)
        self.assertEqual(rec["unresolved_principal_minor"], 0)
        self.assertEqual(rec["unresolved_fee_minor"], 0)
        self.assertEqual(
            self.engine.accounts[SRC_ACCOUNT]["balance_minor"], 100_000
        )
        self.assert_clean(result)

    def test_failed_compensation_keeps_fee_unearned(self):
        result = self.run_transfer("COMPENSATION_FAILED")
        rec = result["reconciliation"]
        self.assertEqual(result["transfer"]["status"], "EXCEPTION")
        self.assertEqual(rec["source_net_minor"], -20_200)
        self.assertEqual(rec["fee_collected_minor"], 0)
        self.assertEqual(rec["unresolved_principal_minor"], 20_000)
        self.assertEqual(rec["unresolved_fee_minor"], 200)
        self.assertFalse(rec["settled"])
        self.assert_clean(result)

    def test_cancelled_charges_nothing(self):
        result = self.run_transfer("CANCELLED")
        rec = result["reconciliation"]
        self.assertEqual(result["transfer"]["status"], "CANCELLED")
        self.assertEqual(rec["source_net_minor"], 0)
        self.assertEqual(rec["fee_collected_minor"], 0)
        self.assertEqual(
            self.engine.accounts[SRC_ACCOUNT]["balance_minor"], 100_000
        )
        self.assert_clean(result)

    def test_pending_source_balance_reflects_hold(self):
        self.run_transfer("PENDING")
        self.assertEqual(
            self.engine.accounts[SRC_ACCOUNT]["balance_minor"], 79_800
        )

    def test_fee_charged_on_every_route_type(self):
        routes = [
            ("ABSA_BW", "ORANGE_MONEY"),
            ("ORANGE_MONEY", "ABSA_BW"),
            ("ORANGE_MONEY", "BTC_SMEGA"),
            ("BTC_SMEGA", "ORANGE_MONEY"),
        ]
        for source, destination in routes:
            with self.subTest(source=source, destination=destination):
                engine = ENGINE()
                q = engine.quote({
                    "source": source, "destination": destination,
                    "amount": "200.00", "fee_policy": "EXPERIMENTAL",
                })
                self.assertEqual(q["fee_minor"], 200)
                result = engine.create({
                    "source": source, "destination": destination,
                    "amount": "200.00", "reference": "ROUTE-1",
                    "scenario": "SUCCESS", "quote_id": q["quote_id"],
                })
                for _ in range(12):
                    t = result["transfer"]
                    if t["terminal"]:
                        break
                    result = engine.advance(t["id"])
                rec = result["reconciliation"]
                self.assertEqual(result["transfer"]["status"], "SETTLED")
                self.assertEqual(rec["source_net_minor"], -20_200)
                self.assertEqual(rec["destination_net_minor"], 20_000)
                self.assertEqual(rec["fee_collected_minor"], 200)
                self.assertTrue(rec["journal_balanced"])
                self.assertTrue(rec["session_money_conserved"])
                self.assertTrue(rec["status_consistent"])


if __name__ == "__main__":
    unittest.main()
