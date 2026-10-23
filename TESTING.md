# Testing

This project is a **simulation**. All participants, provider contracts and fee policies are project-defined mocks, not provider schemas or tariffs. The tests check the simulator's own rules.

Last full run: 24 October 2026, 99 tests, all passing (commit `8b505c3`).

## Running the tests

```powershell
.\.venv-multiroute\Scripts\python.exe -m unittest discover -s tests
```

Add `-v` for per-test output, or `-p 'test_gateway.py'` to run a single file. A `StarletteDeprecationWarning` about `httpx` appears during the gateway tests. It is a warning only and does not affect results.

## What each test file covers

| File | Tests | Covers |
|---|---|---|
| `test_multiroute_core.py` | 22 | Route support for every participant pair against the registry, quote requirement, duplicate and conflicting references, concurrency, session isolation, pending and failure scenarios |
| `test_fee_rules.py`, `test_fee_failure_paths.py`, `test_quote_rules.py` | 30 | Fee arithmetic (half-up rounding, minimum, cap), fee flow on every route type, refunds on failure, quote single-use, expiry, binding to route and amount |
| `test_gateway.py` | 33 | HTTP layer: session lifecycle and limits, request validation (422), engine error mapping (400, 404, 409), the pending-to-resolved flow, password protection |
| `test_engine_input_rules.py` | 7 | Engine-level checks that the gateway would otherwise hide: quote source binding, currency, scenario, reference length, 200-transfer session limit |
| `test_ledger_rules.py` | 7 | Journal integrity: balanced two-line entries, unique journal IDs, no zero-value lines, expected operations per outcome, overdraw guard |

## Mutation testing

To check that the tests can actually fail, single-line faults were applied one at a time to `src/multiroute_core.py`. The suite was run after each, and the file was restored byte for byte afterwards. A fault that makes a test fail is *killed*. One that passes unnoticed *survived*.

| Round | Rules mutated | Mutations | Killed |
|---|---|---|---|
| 1 | Fee maths, quotes, idempotency, settlement, input limits | 19 | 18 |
| 2 | Balance, ledger, refund, retry, reconciliation | 14 | 14 |
| 3 | Source-debit funds check, fee hold, cancellation, pending | 6 | 6 |
| **Total** | | **39** | **38** |

Several mutations first survived and exposed gaps (quote source binding, engine-level input limits, ledger journaling, zero-value postings, the session cap). Each was closed with new tests and re-run until killed.

**The one survivor is an equivalent mutant.** Removing the fee minimum (`fee = max(fee, policy["min_minor"])`) changes nothing, because the flat fee in both policies already equals or exceeds the minimum. No input can tell the two versions apart.

## Limitations

- **Four mutations were detected by an unexpected exception, not a targeted assertion:** quote expiry at the exact boundary, the replay lookup being skipped, the operator REFUND action being disabled, and the source-debit funds check being removed. They count as detected, but the failing test crashed instead of asserting the specific outcome. Tightening these is future work.
- **The mutation set is hand-chosen and not exhaustive.** It covers 39 single-line faults in the rules listed above. It does not cover multi-line changes, the adapters or frontend, or every branch.
- **One test is white-box.** `test_post_refuses_to_overdraw_an_account` calls the private `_post` method, because the engine checks balances earlier and the guard cannot be reached through the public flow. It will break if that method is renamed.
- **Not tested:** the demo page served at `GET /` and the frontend in `web/`. An unknown session ID is checked only as "a client error".
- **Overlap:** `test_expired_quote_is_rejected` exists in two files, and several tests check the same behaviour under different names. The count of 99 therefore slightly overstates distinct coverage.
- **Mock scope:** passing tests show the simulator behaves as designed. They say nothing about any real bank, wallet or provider.
- **Mutation tooling is local.** The mutation scripts were run on a development machine and are not in the repository, so the tallies above are a record of those runs.