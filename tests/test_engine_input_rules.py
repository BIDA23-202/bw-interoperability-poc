import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from multiroute_core import DemoEngine, DemoError


class EngineInputRuleTests(unittest.TestCase):
    def setUp(self):
        self.engine = DemoEngine()

    def quote(self, source="ABSA_BW", destination="ORANGE_MONEY", amount="125.00"):
        return self.engine.quote({
            "source": source, "destination": destination,
            "amount": amount, "fee_policy": "NONE",
        })["quote_id"]

    def body(self, quote_id, **override):
        body = {
            "source": "ABSA_BW", "destination": "ORANGE_MONEY",
            "reference": "RULE-001", "amount": "125.00",
            "quote_id": quote_id, "scenario": "SUCCESS",
        }
        body.update(override)
        return body

    def test_quote_is_bound_to_source(self):
        quote_id = self.quote(source="ABSA_BW")
        with self.assertRaises(DemoError) as ctx:
            self.engine.create(self.body(quote_id, source="STANCHART_BW"))
        self.assertEqual(ctx.exception.status, 409)

    def test_non_bwp_currency_is_rejected(self):
        quote_id = self.quote()
        with self.assertRaises(DemoError) as ctx:
            self.engine.create(self.body(quote_id, currency="USD"))
        self.assertIn("BWP", str(ctx.exception))

    def test_unknown_scenario_is_rejected_by_engine(self):
        quote_id = self.quote()
        with self.assertRaises(DemoError) as ctx:
            self.engine.create(self.body(quote_id, scenario="EXPLODE"))
        self.assertIn("scenario", str(ctx.exception).lower())

    def test_reference_of_81_characters_is_rejected(self):
        quote_id = self.quote()
        with self.assertRaises(DemoError):
            self.engine.create(self.body(quote_id, reference="X" * 81))

    def test_reference_of_80_characters_is_accepted(self):
        quote_id = self.quote()
        result = self.engine.create(self.body(quote_id, reference="X" * 80))
        self.assertIn("transfer", result)

    def test_empty_reference_is_rejected(self):
        quote_id = self.quote()
        with self.assertRaises(DemoError):
            self.engine.create(self.body(quote_id, reference="   "))

    def test_session_limit_of_200_transfers(self):
        for i in range(200):
            quote_id = self.quote(amount="0.01")
            self.engine.create(
                self.body(quote_id, reference=f"CAP-{i:03d}", amount="0.01")
            )
        quote_id = self.quote(amount="0.01")
        with self.assertRaises(DemoError) as ctx:
            self.engine.create(self.body(quote_id, reference="CAP-200", amount="0.01"))
        self.assertEqual(ctx.exception.status, 429)


if __name__ == "__main__":
    unittest.main()