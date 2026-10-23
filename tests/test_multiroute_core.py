import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from multiroute_core import (
    DemoEngine, DemoError, FEE_ACCOUNT, compute_fee, minor_units,
)
from registry import (
    PARTICIPANTS, BANK_TO_WALLET_BANKS, WALLET_TO_BANK_BANKS, PAIR_STATUS,
)

ABSA, ORANGE, SMEGA = "ABSA_BW", "ORANGE_MONEY", "BTC_SMEGA"
OPENING = 100_000


def acct(pid):
    return PARTICIPANTS[pid]["account"]


def expected_supported(source, destination):
    sk = PARTICIPANTS[source]["kind"]
    dk = PARTICIPANTS[destination]["kind"]
    if source == destination:
        return False
    if sk == "BANK" and dk == "WALLET":
        route, ok = "BANK_TO_WALLET", source in BANK_TO_WALLET_BANKS
    elif sk == "WALLET" and dk == "BANK":
        route, ok = "WALLET_TO_BANK", destination in WALLET_TO_BANK_BANKS
    elif sk == "WALLET" and dk == "WALLET":
        route, ok = "WALLET_TO_WALLET", True
    else:
        return False
    return ok and PAIR_STATUS.get((route, source, destination)) != "NOT_SUPPORTED"


class Base(unittest.TestCase):
    def setUp(self):
        self.engine = DemoEngine()

    def quote(self, source=ABSA, destination=ORANGE, amount="125.00",
              policy="NONE", engine=None):
        engine = engine or self.engine
        q = engine.quote({
            "source": source, "destination": destination,
            "amount": amount, "fee_policy": policy,
        })
        return q.get("quote", q)

    def body(self, quote, source=ABSA, destination=ORANGE, amount="125.00",
             scenario="SUCCESS", reference="TEST-001"):
        return {
            "source": source, "destination": destination,
            "amount": amount, "currency": "BWP",
            "scenario": scenario, "reference": reference,
            "quote_id": quote["quote_id"],
        }

    def run_transfer(self, source=ABSA, destination=ORANGE, amount="125.00",
                     policy="NONE", scenario="SUCCESS", reference="TEST-001",
                     engine=None):
        engine = engine or self.engine
        q = self.quote(source, destination, amount, policy, engine)
        result = engine.create(
            self.body(q, source, destination, amount, scenario, reference)
        )
        return self.finish(engine, result), q

    def finish(self, engine, result):
        for _ in range(20):
            t = result["transfer"]
            if t["terminal"] or t["status"] == "PENDING":
                return result
            result = engine.advance(t["id"])
        self.fail("Transfer did not reach a stopping state.")

    def balance(self, pid, engine=None):
        return (engine or self.engine).accounts[acct(pid)]["balance_minor"]

    def fees_collected(self, engine=None):
        return (engine or self.engine).accounts[FEE_ACCOUNT]["balance_minor"]

    def assert_accounting(self, result):
        r = result["reconciliation"]
        for key in ("journal_balanced", "session_money_conserved",
                    "status_consistent"):
            self.assertIn(key, r, f"reconciliation lacks {key}: {sorted(r)}")
            self.assertTrue(r[key], f"{key} is false: {r}")


class RegistryAndRoutes(Base):
    def test_every_ordered_pair_matches_registry(self):
        for source in PARTICIPANTS:
            for destination in PARTICIPANTS:
                if source == destination:
                    continue
                with self.subTest(source=source, destination=destination):
                    engine = DemoEngine()
                    supported = expected_supported(source, destination)
                    if supported:
                        result, _ = self.run_transfer(
                            source, destination, engine=engine)
                        self.assertEqual(result["transfer"]["status"], "SETTLED")
                        self.assertEqual(
                            self.balance(destination, engine), OPENING + 12_500)
                        self.assert_accounting(result)
                    else:
                        with self.assertRaises(DemoError):
                            q = self.quote(source, destination, engine=engine)
                            engine.create(
                                self.body(q, source, destination))

    def test_all_four_wallets_present(self):
        for wallet in ("ORANGE_MONEY", "BTC_SMEGA", "MYZAKA", "POSOMONEY"):
            self.assertIn(wallet, PARTICIPANTS)
            self.assertEqual(PARTICIPANTS[wallet]["kind"], "WALLET")

    def test_names_are_labelled_mock(self):
        for pid, p in PARTICIPANTS.items():
            self.assertIn("(mock)", p["name"], pid)


class Fees(Base):
    def test_fee_free_baseline(self):
        for amount in (1, 100, 12_500, 100_000):
            self.assertEqual(compute_fee("NONE", amount), 0)

    def test_experimental_fee_is_sane(self):
        for amount in (1, 99, 100, 12_500, 100_000):
            fee = compute_fee("EXPERIMENTAL", amount)
            self.assertIsInstance(fee, int)
            self.assertGreaterEqual(fee, 0)

    def test_success_with_fee_moves_exact_amounts(self):
        fee = compute_fee("EXPERIMENTAL", 12_500)
        result, q = self.run_transfer(policy="EXPERIMENTAL")
        self.assertEqual(q["fee_minor"], fee)
        self.assertEqual(q["total_debit_minor"], 12_500 + fee)
        self.assertEqual(result["transfer"]["status"], "SETTLED")
        self.assertEqual(self.balance(ABSA), OPENING - 12_500 - fee)
        self.assertEqual(self.balance(ORANGE), OPENING + 12_500)
        self.assertEqual(self.fees_collected(), fee)
        self.assert_accounting(result)

    def test_exact_balance_boundary_without_fee(self):
        result, _ = self.run_transfer(amount="1000.00")
        self.assertEqual(result["transfer"]["status"], "SETTLED")
        self.assertEqual(self.balance(ABSA), 0)

    def test_fee_pushes_sender_over_balance(self):
        if compute_fee("EXPERIMENTAL", 100_000) == 0:
            self.skipTest("EXPERIMENTAL fee is zero at this amount.")
        result, _ = self.run_transfer(amount="1000.00", policy="EXPERIMENTAL")
        self.assertEqual(result["transfer"]["status"], "REJECTED")
        self.assertEqual(self.balance(ABSA), OPENING)
        self.assertEqual(self.fees_collected(), 0)
        self.assert_accounting(result)


class QuoteIntegrity(Base):
    def test_create_without_quote_is_rejected(self):
        with self.assertRaises(DemoError):
            self.engine.create({
                "source": ABSA, "destination": ORANGE, "amount": "125.00",
                "currency": "BWP", "scenario": "SUCCESS",
                "reference": "NO-QUOTE",
            })

    def test_quote_cannot_be_reused_with_new_reference(self):
        q = self.quote()
        self.finish(self.engine, self.engine.create(self.body(q)))
        with self.assertRaises(DemoError):
            self.engine.create(self.body(q, reference="TEST-002"))

    def test_quote_is_bound_to_amount(self):
        q = self.quote(amount="100.00")
        with self.assertRaises(DemoError):
            self.engine.create(self.body(q, amount="200.00"))

    def test_quote_is_bound_to_route(self):
        q = self.quote(destination=ORANGE)
        with self.assertRaises(DemoError):
            self.engine.create(self.body(q, destination=SMEGA))

    def test_expired_quote_is_rejected(self):
        q = self.quote()
        base = self.engine.clock()
        self.engine.clock = lambda: base + self.engine.QUOTE_TTL + 1
        with self.assertRaises(DemoError):
            self.engine.create(self.body(q))


class FailureHandling(Base):
    PAIRS = [
        (ABSA, ORANGE), (ORANGE, ABSA), (ORANGE, SMEGA), (SMEGA, ORANGE),
    ]
    EXPECTED = {
        "DESTINATION_FAILED": "REVERSED",
        "INSUFFICIENT_FUNDS": "REJECTED",
        "COMPENSATION_FAILED": "EXCEPTION",
        "CANCELLED": "CANCELLED",
    }

    def test_scenarios_across_directions_with_fees(self):
        for source, destination in self.PAIRS:
            for scenario, status in self.EXPECTED.items():
                with self.subTest(source=source, destination=destination,
                                  scenario=scenario):
                    engine = DemoEngine()
                    result, _ = self.run_transfer(
                        source, destination, policy="EXPERIMENTAL",
                        scenario=scenario, engine=engine)
                    self.assertEqual(result["transfer"]["status"], status)
                    self.assert_accounting(result)
                    if status in ("REVERSED", "REJECTED", "CANCELLED"):
                        self.assertEqual(self.balance(source, engine), OPENING)
                        self.assertEqual(self.fees_collected(engine), 0)

    def test_pending_holds_principal_and_fee_then_resolves(self):
        fee = compute_fee("EXPERIMENTAL", 12_500)
        for action, expected in (("SUCCESS", "SETTLED"), ("FAILED", "REVERSED")):
            with self.subTest(action=action):
                engine = DemoEngine()
                result, _ = self.run_transfer(
                    policy="EXPERIMENTAL", scenario="PENDING", engine=engine)
                r = result["reconciliation"]
                self.assertEqual(result["transfer"]["status"], "PENDING")
                self.assertEqual(r["unresolved_principal_minor"], 12_500)
                self.assertEqual(r["unresolved_fee_minor"], fee)
                self.assertEqual(self.fees_collected(engine), 0)
                self.assert_accounting(result)

                result = engine.resolve(result["transfer"]["id"], action)
                result = self.finish(engine, result)
                self.assertEqual(result["transfer"]["status"], expected)
                self.assert_accounting(result)

    def test_compensation_retry_clears_clearing(self):
        result, _ = self.run_transfer(
            policy="EXPERIMENTAL", scenario="COMPENSATION_FAILED")
        self.assertEqual(result["transfer"]["status"], "EXCEPTION")
        result = self.engine.resolve(result["transfer"]["id"], "REFUND")
        result = self.finish(self.engine, result)
        r = result["reconciliation"]
        self.assertEqual(result["transfer"]["status"], "REVERSED")
        self.assertEqual(r["unresolved_principal_minor"], 0)
        self.assertEqual(r["unresolved_fee_minor"], 0)
        self.assertEqual(self.balance(ABSA), OPENING)
        self.assert_accounting(result)


class Idempotency(Base):
    def test_duplicate_does_not_debit_or_charge_twice(self):
        q = self.quote(policy="EXPERIMENTAL")
        body = self.body(q)
        first = self.finish(self.engine, self.engine.create(body))
        after = (self.balance(ABSA), self.balance(ORANGE),
                 self.fees_collected())
        replay = self.engine.create(body)
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["transfer"]["id"], replay["transfer"]["id"])
        self.assertEqual(after, (self.balance(ABSA), self.balance(ORANGE),
                                 self.fees_collected()))

    def test_conflicting_reference_is_rejected(self):
        q = self.quote()
        self.engine.create(self.body(q))
        with self.assertRaises(DemoError):
            self.engine.create(self.body(q, amount="126.00"))

    def test_concurrent_duplicate_creates(self):
        q = self.quote()
        body = self.body(q)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda _: self._try_create(body), range(8)))
        ok = [r for r in results if r is not None]
        self.assertEqual(len({r["transfer"]["id"] for r in ok}), 1)
        self.assertEqual(len(self.engine.transfers), 1)

    def _try_create(self, body):
        try:
            return self.engine.create(body)
        except DemoError:
            return None

    def test_concurrent_advances_credit_once(self):
        q = self.quote(policy="EXPERIMENTAL")
        tid = self.engine.create(self.body(q))["transfer"]["id"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.engine.advance(tid), range(24)))
        result = self.engine.get(tid)
        self.assertEqual(result["transfer"]["status"], "SETTLED")
        self.assertEqual(self.balance(ORANGE), OPENING + 12_500)
        self.assert_accounting(result)


class Inputs(Base):
    def test_invalid_amounts(self):
        for value in ("0", "-1", "NaN", "1.001", "1e3", "hello", ""):
            with self.subTest(value=value):
                with self.assertRaises(DemoError):
                    minor_units(value)

    def test_session_isolation(self):
        self.run_transfer()
        other = DemoEngine()
        for pid in PARTICIPANTS:
            self.assertEqual(other.accounts[acct(pid)]["balance_minor"], OPENING)


if __name__ == "__main__":
    unittest.main()
