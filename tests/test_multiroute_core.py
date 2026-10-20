import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src"),
)

from multiroute_core import DemoEngine, DemoError, minor_units


class MultiRouteTests(unittest.TestCase):
    def instruction(
        self,
        source="BANK_A",
        destination="ORANGE_MOCK",
        scenario="SUCCESS",
        reference="TEST-001",
        amount="125.00",
    ):
        return {
            "source": source,
            "destination": destination,
            "scenario": scenario,
            "reference": reference,
            "amount": amount,
            "currency": "BWP",
        }

    def finish(self, engine, result):
        for _ in range(12):
            transfer = result["transfer"]

            if transfer["terminal"] or transfer["status"] == "PENDING":
                return result

            result = engine.advance(transfer["id"])

        self.fail("Transfer did not reach a stopping state.")

    def assert_accounting(self, result):
        self.assertTrue(result["reconciliation"]["journal_balanced"])
        self.assertTrue(result["reconciliation"]["session_money_conserved"])
        self.assertTrue(result["reconciliation"]["status_consistent"])

    def test_all_route_combinations(self):
        banks = ["BANK_A", "BANK_B"]
        wallets = ["ORANGE_MOCK", "SMEGA_MOCK"]

        pairs = (
            [(bank, wallet) for bank in banks for wallet in wallets]
            + [(wallet, bank) for wallet in wallets for bank in banks]
            + [("ORANGE_MOCK", "SMEGA_MOCK"), ("SMEGA_MOCK", "ORANGE_MOCK")]
        )

        for source, destination in pairs:
            with self.subTest(source=source, destination=destination):
                engine = DemoEngine()
                result = self.finish(
                    engine,
                    engine.create(self.instruction(source, destination)),
                )

                self.assertEqual(result["transfer"]["status"], "SETTLED")
                self.assertEqual(
                    result["reconciliation"]["destination_net_minor"],
                    12_500,
                )
                self.assert_accounting(result)

    def test_failure_scenarios_across_directions(self):
        pairs = [
            ("BANK_A", "ORANGE_MOCK"),
            ("ORANGE_MOCK", "BANK_A"),
            ("ORANGE_MOCK", "SMEGA_MOCK"),
            ("SMEGA_MOCK", "ORANGE_MOCK"),
        ]

        expectations = {
            "DESTINATION_FAILED": "REVERSED",
            "INSUFFICIENT_FUNDS": "REJECTED",
            "COMPENSATION_FAILED": "EXCEPTION",
            "CANCELLED": "CANCELLED",
        }

        for source, destination in pairs:
            for scenario, status in expectations.items():
                with self.subTest(
                    source=source,
                    destination=destination,
                    scenario=scenario,
                ):
                    engine = DemoEngine()
                    result = self.finish(
                        engine,
                        engine.create(
                            self.instruction(source, destination, scenario)
                        ),
                    )

                    self.assertEqual(result["transfer"]["status"], status)
                    self.assert_accounting(result)

    def test_pending_success_and_failure(self):
        for action, expected in [
            ("SUCCESS", "SETTLED"),
            ("FAILED", "REVERSED"),
        ]:
            with self.subTest(action=action):
                engine = DemoEngine()
                result = self.finish(
                    engine,
                    engine.create(self.instruction(scenario="PENDING")),
                )

                self.assertEqual(result["transfer"]["status"], "PENDING")
                self.assertEqual(
                    result["reconciliation"]["unresolved_minor"], 12_500
                )
                self.assert_accounting(result)

                result = engine.resolve(result["transfer"]["id"], action)
                result = self.finish(engine, result)

                self.assertEqual(result["transfer"]["status"], expected)
                self.assert_accounting(result)

    def test_compensation_retry(self):
        engine = DemoEngine()

        result = self.finish(
            engine,
            engine.create(
                self.instruction(scenario="COMPENSATION_FAILED")
            ),
        )

        self.assertEqual(result["transfer"]["status"], "EXCEPTION")

        result = engine.resolve(result["transfer"]["id"], "REFUND")
        result = self.finish(engine, result)

        self.assertEqual(result["transfer"]["status"], "REVERSED")
        self.assertEqual(result["reconciliation"]["unresolved_minor"], 0)
        self.assert_accounting(result)

    def test_duplicate_has_no_second_debit(self):
        engine = DemoEngine()
        body = self.instruction()

        first = self.finish(engine, engine.create(body))
        replay = engine.create(body)

        self.assertTrue(replay["replayed"])
        self.assertEqual(first["transfer"]["id"], replay["transfer"]["id"])
        self.assertEqual(first["accounts"], replay["accounts"])
        self.assertEqual(first["transfer"]["ledger"], replay["transfer"]["ledger"])

    def test_conflicting_reference_is_rejected(self):
        engine = DemoEngine()
        engine.create(self.instruction())

        with self.assertRaises(DemoError) as caught:
            engine.create(self.instruction(amount="126.00"))

        self.assertEqual(caught.exception.status, 409)

    def test_real_insufficient_balance(self):
        engine = DemoEngine()

        result = self.finish(
            engine,
            engine.create(self.instruction(amount="1000.01")),
        )

        self.assertEqual(result["transfer"]["status"], "REJECTED")
        self.assertEqual(result["transfer"]["ledger"], [])
        self.assert_accounting(result)

    def test_invalid_amounts(self):
        for value in ["0", "-1", "NaN", "1.001", "1e3", "hello"]:
            with self.subTest(value=value):
                with self.assertRaises(DemoError):
                    minor_units(value)

    def test_session_isolation(self):
        first = DemoEngine()
        second = DemoEngine()

        self.finish(first, first.create(self.instruction()))

        self.assertTrue(all(
            account["balance_minor"] == 100_000
            for account in second.overview()["accounts"]
        ))

    def test_concurrent_duplicate_requests(self):
        engine = DemoEngine()
        body = self.instruction()

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: engine.create(body), range(8)))

        ids = {result["transfer"]["id"] for result in results}

        self.assertEqual(len(ids), 1)
        self.assertEqual(len(engine.transfers), 1)

    def test_concurrent_advances_do_not_double_credit(self):
        engine = DemoEngine()
        transfer_id = engine.create(self.instruction())["transfer"]["id"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: engine.advance(transfer_id), range(16)))

        result = engine.get(transfer_id)

        self.assertEqual(result["transfer"]["status"], "SETTLED")
        self.assertEqual(len(result["transfer"]["ledger"]), 4)
        self.assert_accounting(result)


if __name__ == "__main__":
    unittest.main()
