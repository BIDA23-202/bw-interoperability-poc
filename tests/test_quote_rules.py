import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from multiroute_core import DemoEngine, DemoError


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def body(**overrides):
    base = {
        "source": "ABSA_BW", "destination": "ORANGE_MONEY",
        "amount": "125.00", "currency": "BWP",
        "scenario": "SUCCESS", "reference": "QT-001",
    }
    base.update(overrides)
    return base


class QuoteRuleTests(unittest.TestCase):
    def test_missing_quote_is_rejected(self):
        with self.assertRaises(DemoError) as caught:
            DemoEngine().create(body())
        self.assertEqual(caught.exception.status, 409)

    def test_valid_quote_creates_transfer(self):
        engine = DemoEngine()
        quote = engine.quote(body())
        result = engine.create(body(quote_id=quote["quote_id"]))
        transfer = result["transfer"]
        self.assertEqual(transfer["quote_id"], quote["quote_id"])
        self.assertEqual(transfer["total_debit_minor"], quote["total_debit_minor"])

    def test_quote_cannot_be_reused(self):
        engine = DemoEngine()
        quote_id = engine.quote(body())["quote_id"]
        engine.create(body(quote_id=quote_id))
        with self.assertRaises(DemoError) as caught:
            engine.create(body(quote_id=quote_id, reference="QT-002"))
        self.assertEqual(caught.exception.status, 409)

    def test_expired_quote_is_rejected(self):
        clock = FakeClock()
        engine = DemoEngine(clock=clock)
        quote_id = engine.quote(body())["quote_id"]
        clock.now += DemoEngine.QUOTE_TTL + 1
        with self.assertRaises(DemoError) as caught:
            engine.create(body(quote_id=quote_id))
        self.assertEqual(caught.exception.status, 410)

    def test_quote_must_match_instruction(self):
        engine = DemoEngine()
        quote_id = engine.quote(body())["quote_id"]
        with self.assertRaises(DemoError) as caught:
            engine.create(body(quote_id=quote_id, amount="126.00"))
        self.assertEqual(caught.exception.status, 409)


if __name__ == "__main__":
    unittest.main()
