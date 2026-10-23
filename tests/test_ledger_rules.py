import sys
import unittest
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from multiroute_core import DemoEngine, DemoError, FEE_ACCOUNT


def run(policy, scenario="SUCCESS", amount="125.00"):
    engine = DemoEngine()
    quote_id = engine.quote({
        "source": "ABSA_BW", "destination": "ORANGE_MONEY",
        "amount": amount, "fee_policy": policy,
    })["quote_id"]
    result = engine.create({
        "source": "ABSA_BW", "destination": "ORANGE_MONEY",
        "reference": "LEDGER-001", "amount": amount,
        "quote_id": quote_id, "scenario": scenario,
    })
    for _ in range(20):
        if result["transfer"]["terminal"]:
            break
        result = engine.advance(result["transfer"]["id"])
    else:
        raise AssertionError("transfer never finished")
    tid = result["transfer"]["id"]
    return engine, engine.transfers[tid]["ledger"]


def by_journal(ledger):
    groups = defaultdict(list)
    for line in ledger:
        groups[line["journal_id"]].append(line)
    return groups


class LedgerRuleTests(unittest.TestCase):
    def test_every_journal_has_two_balanced_lines(self):
        for scenario in ("SUCCESS", "DESTINATION_FAILED"):
            with self.subTest(scenario=scenario):
                _, ledger = run("EXPERIMENTAL", scenario)
                for journal, lines in by_journal(ledger).items():
                    self.assertEqual(len(lines), 2, journal)
                    self.assertEqual(sum(l["delta_minor"] for l in lines), 0, journal)

    def test_each_posting_gets_its_own_journal_id(self):
        _, ledger = run("EXPERIMENTAL", "SUCCESS")
        self.assertEqual(len(ledger), 8)
        self.assertEqual(len(by_journal(ledger)), 4)

    def test_ledger_nets_to_zero(self):
        for scenario in ("SUCCESS", "DESTINATION_FAILED"):
            with self.subTest(scenario=scenario):
                _, ledger = run("EXPERIMENTAL", scenario)
                self.assertEqual(sum(l["delta_minor"] for l in ledger), 0)

    def test_none_policy_writes_no_zero_value_lines(self):
        _, ledger = run("NONE", "SUCCESS")
        self.assertTrue(all(l["delta_minor"] != 0 for l in ledger))
        self.assertEqual(
            {l["operation"] for l in ledger},
            {"SOURCE_DEBIT_PRINCIPAL", "DESTINATION_CREDIT"},
        )

    def test_operations_on_settlement_with_fee(self):
        _, ledger = run("EXPERIMENTAL", "SUCCESS")
        self.assertEqual(
            {l["operation"] for l in ledger},
            {"SOURCE_DEBIT_PRINCIPAL", "SOURCE_DEBIT_FEE",
             "DESTINATION_CREDIT", "FEE_RECOGNISED"},
        )

    def test_operations_on_destination_failure_with_fee(self):
        _, ledger = run("EXPERIMENTAL", "DESTINATION_FAILED")
        self.assertEqual(
            {l["operation"] for l in ledger},
            {"SOURCE_DEBIT_PRINCIPAL", "SOURCE_DEBIT_FEE",
             "SOURCE_REFUND_PRINCIPAL", "SOURCE_REFUND_FEE"},
        )

    def test_post_refuses_to_overdraw_an_account(self):
        # White-box: calls the private _post, because step 3 pre-checks balances.
        engine = DemoEngine()
        other = next(k for k in engine.accounts if k != FEE_ACCOUNT)
        stub = {"id": "TX-STUB", "journal_seq": 0, "ledger": []}
        before = engine.accounts[other]["balance_minor"]
        with self.assertRaises(DemoError) as ctx:
            engine._post(stub, FEE_ACCOUNT, other, 1, "TEST")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(engine.accounts[other]["balance_minor"], before)
        self.assertEqual(stub["ledger"], [])


if __name__ == "__main__":
    unittest.main()