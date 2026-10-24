# Testing

This project is a **simulation**. All participants, provider contracts and fee policies are project-defined mocks, not provider schemas or tariffs. The tests check the simulator's own rules.

Last full run: 24 October 2026. 107 tests, all passing.

## Running the tests

```powershell
.\.venv-multiroute\Scripts\python.exe -m unittest discover -s tests
```

Add `-v` for per-test output, or `-p 'test_gateway.py'` to run a single file. A `StarletteDeprecationWarning` about `httpx` appears during the gateway tests. It is a warning only and does not affect results.

## What each test file covers

| File | Tests | Covers |
|---|---|--|
| `test_multiroute_core.py` | 22 | Route support for every participant pair against the registry, quote requirement, duplicate and conflicting references, concurrency, session isolation, pending and failure scenarios |
| `test_fee_rules.py`, `test_fee_failure_paths.py`, `test_quote_rules.py` | 30 | Fee arithmetic (half-up rounding, minimum, cap), fee flow on every route type, refunds on failure, quote single use, expiry, binding to route and amount |
| `test_gateway.py` | 33 | HTTP layer: session lifecycle and limits, request validation (422), engine error mapping (400, 404, 409), the pending-to-resolved flow, password protection |
| `test_engine_input_rules.py` | 7 | Engine-level checks the gateway would otherwise hide: quote source binding, currency, scenario, reference length, 200-transfer session limit |
| `test_ledger_rules.py` | 7 | Journal integrity: balanced two-line entries, unique journal IDs, no zero-value lines, expected operations per outcome, overdraw guard |
| `test_targeted_assertions.py` | 4 | Assertion-based checks for the quote expiry boundary, replay handling, operator refund, and rejection of unaffordable transfers |
`test_frontend_page.py` | 4 |  |

## Mutation testing

To check that the tests can actually fail, single-line faults were applied one at a time to `src/multiroute_core.py`. The suite was run after each, and the file was restored byte for byte afterwards. A fault that makes a test fail is *killed*. One that passes unnoticed *survived*.

| Round | Rules mutated | Mutations | Killed |
|---|---|---|---|
| 1 | Fee maths, quotes, idempotency, settlement, input limits | 19 | 18 |
| 2 | Posting, ledger, refund, retry, reconciliation | 14 | 14 |
| 3 | Source-debit funds check, fee hold, cancellation, pending | 6 | 6 |
| **Total** | | **39** | **38** |

Every kill is caused by a failing assertion, not only by a crash. Several mutations initially survived and exposed gaps (quote source binding, engine-level input limits, ledger journaling, zero-value postings, the session cap). Each was closed with new tests and re-run until killed. Four mutations that were first caught only by an unexpected exception are now also caught by targeted assertions in `test_targeted_assertions.py`.

**The one survivor is an equivalent mutant.** Removing the fee minimum (`fee = max(fee, policy["min_minor"])`) changes nothing, because the flat fee in both policies already equals or exceeds the minimum. No input can tell the two versions apart.

## Reproducing the mutation run

```powershell
git status --short -- src
.\.venv-multiroute\Scripts\python.exe mutate.py
```

`src` must have no uncommitted changes, and the engine file should not be open in an editor. The run takes about a minute. It restores the engine afterwards, prints one line per mutation, and exits non-zero if an unexpected mutant survives. The expected summary is `Killed 38 of 39`, with `fee: minimum removed` as the only survivor.

## Limitations

- **The mutation set is hand-chosen and not exhaustive.** It covers 39 single-line faults in the rules listed above. It does not cover multi-line changes, the adapters, or the frontend.
- **One test is white-box.** `test_post_refuses_to_overdraw_an_account` calls the private `_post` method, because the engine checks balances earlier and the guard cannot be reached through the public flow. It will break if that method is renamed.
- **The page has string-level smoke tests.** the demo page served at `GET /` and the frontend in `web/`.
- **Overlap:** `test_expired_quote_is_rejected` exists in two files, and several tests check the same behaviour under different names. The count of 103 therefore slightly overstates distinct coverage.
- **Mock scope:** passing tests show the simulator behaves as designed. They say nothing about any real bank, wallet or provider.