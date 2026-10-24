# bw-interop-poc: synthetic interoperability proof of concept

Two simulations of a hub that connects Botswana banks and mobile-money wallets. Everything is synthetic. No real accounts, credentials or live integrations are used, and the banks and wallets are mocks, not any provider's API.

1. **Multi-route simulation.** An interactive app with a browser interface: ten mock participants, three transfer directions, fee quotes, step-by-step processing and reconciliation.
2. **Batch pipeline.** A hub between banks (ISO 20022 pacs.008) and wallets (Orange Money style JSON) that validates, loads, reconciles and reverse-maps a seeded data set.

## Folders
- `src/`   code, schema, roster, and the original pytest suites
- `tests/` unit tests for the multi-route simulation (see [TESTING.md](TESTING.md))
- `web/`   browser interfaces; `multiroute-demo.html` is served by the multi-route gateway
- `data/`  inputs and outputs (keep the large `MatchedPoolReference.csv` out of git)
- `docs/`  decision log, evidence, screenshots

## Set up
Python 3.9 or later for the batch pipeline. The multi-route simulation and its tests were developed and run on Python 3.12.
```
python -m venv .venv
.venv\Scripts\activate          # Windows; use "source .venv/bin/activate" on macOS or Linux
pip install -r requirements.txt
```


## Multi-route simulation

### Run it
```
python -m uvicorn multiroute_gateway:app --app-dir src --port 8000
```
Then open `http://127.0.0.1:8000/` for the interface, `http://127.0.0.1:8000/docs` for the API documentation, or `http://127.0.0.1:8000/health` for a status check.

To require a password, set `DEMO_PASSWORD` before starting. The server then asks for HTTP Basic credentials: any username, and the password you set. `/health` stays open.
```
$env:DEMO_PASSWORD = "choose-a-password"     # PowerShell
```


### What it does
- **Participants.** Six banks (Absa Bank Botswana, Standard Chartered Botswana, First National Bank of Botswana, Stanbic Bank Botswana, Bank Gaborone, Access Bank Botswana) and four wallets (Orange Money, BTC Smega, Mascom MyZaka, PosoMoney). All are labelled as mocks. They are defined in `src/registry.py`.
- **Directions.** Bank to wallet, wallet to bank, and wallet to wallet. All six banks can send to wallets; only Absa, Standard Chartered and Bank Gaborone can receive from wallets; wallet-to-wallet pairs are simulated. Pair-level participation is shown as `UNCONFIRMED` unless set in `PAIR_STATUS` in `registry.py`.
- **Fee quote first.** Every transfer needs a fee quote. A quote is single use, valid for 120 seconds, and bound to the source, destination and amount.
- **Fee policies.** `NONE`, and `EXPERIMENTAL` (BWP 1.00 + 0.50%, minimum BWP 1.00, maximum BWP 25.00, paid by the sender). These are project proposals for demonstration, not provider tariffs.
- **Processing.** Validate, normalise to a canonical transfer, route to the destination adapter, debit the source, credit the destination. Principal and fee are held separately in clearing, and the fee is recognised only when the transfer settles. On failure, both are refunded.
- **Scenarios.** Successful transfer, pending destination response, destination rejects (refund), injected insufficient funds, destination rejects and refund fails, and cancel before debit.
- **Resolution.** A pending transfer can be resolved as success or failure, and a failed compensation can be retried with a refund.
- **Accounting.** Each transfer has a double-entry journal, and the interface shows reconciliation checks (journal balanced, session money conserved, status consistent). Each participant starts with BWP 1,000.00 per session.
- **Sessions.** In memory only. Each browser session is isolated. Limits: 50 sessions, a 4-hour lifetime, and 200 transfers per session.

### API
| Method and path | Purpose |
|---|---|
| `GET /` | The browser interface |
| `GET /health` | Status, version and mode |
| `POST /api/demo/sessions` | Create a session; returns participants, route capabilities and fee policies |
| `GET /api/demo/sessions/{session_id}` | Balances and transfer list |
| `POST /api/demo/sessions/{session_id}/quotes` | Request a fee quote |
| `POST /api/demo/sessions/{session_id}/transfers` | Create a transfer from a quote |
| `GET /api/demo/sessions/{session_id}/transfers/{transfer_id}` | Read a transfer |
| `POST /api/demo/sessions/{session_id}/transfers/{transfer_id}/advance` | Run one processing step |
| `POST /api/demo/sessions/{session_id}/transfers/{transfer_id}/resolve` | Resolve a pending or failed transfer |

## Tests
```
python -m unittest discover -s tests
```
This runs the 107 tests for the multi-route simulation. [TESTING.md](TESTING.md) describes what each file covers, the mutation-testing results (38 of 39 injected faults detected), and the limitations. The mutation run can be repeated with `python mutate.py` on a clean working tree.

The original pytest suites for the batch pipeline are in `src/`:
```
cd src
python -m pytest -q
```
`test_mock_gateway_api.py` is skipped when FastAPI is not installed.

## Batch pipeline

### Files in `src/`
| File | Purpose |
|---|---|
| `make_poc_pairs.py` | builds `poc_pacs008.xml`, `poc_orange.json`, `poc_manifest.csv` from the matched pool |
| `schema.sql`, `participants.yaml` | canonical SQLite schema and the participant roster (mock BICs) |
| `gateway.py` | Pydantic validation with reason codes |
| `loader.py` | validates, deduplicates, and loads the canonical store |
| `recon.py` | matches the two legs, posts the ledger, fills the exception queue, updates statuses, scores against the manifest |
| `reverse_mappers.py` | canonical to native formats, round-trip and translation checks |
| `run_all.py` | one command for the batch pipeline above |
| `mock_core.py` | framework-free mock wallet (token, payment, notification, status) and the hub that drives it |
| `mock_gateway.py` | thin FastAPI wrapper with a Swagger page at `/docs` |
| `patch_v1_1.py` | applies the v1.1 fixes (SERVICE_LEVEL charge bearer, shared reference) |
| `multiroute_core.py` | multi-route engine: quotes, fees, transfers, ledger, reconciliation |
| `multiroute_gateway.py` | FastAPI wrapper for the multi-route engine: sessions, validation, optional password |
| `registry.py` | multi-route participants, route capabilities and fee policies |
| `test_*.py` | pytest suites for the batch pipeline |

### Run the batch pipeline
```
python src/patch_v1_1.py --src src        # once
python -m pytest -q                        # from the src folder
python src/run_all.py --base . --pool data/MatchedPoolReference.csv
```
Expected for the default `--n 400 --seed 42`: pacs.008 402 in / 382 mapped / 20 rejected, Orange 394 / 394 / 0,
reconciliation 352 matched / 24 discrepant / 24 unmatched, 704 balanced ledger entries, manifest 408/408,
round trip 776/776 identical, translation 352/352 in both directions, and `ALL CHECKS PASSED`.

### Run the mock gateway
```
python -m uvicorn mock_gateway:app --app-dir src --port 8001
```
Then open `http://127.0.0.1:8001/docs`. (Port 8001 avoids a clash with the multi-route simulation on 8000.) Key endpoints: `POST /interop/v1/bank-to-wallet?simulate=SUCCESS|FAILED|CANCELLED|PENDING`,
`GET /interop/v1/payments/{end_to_end_id}`, and the wallet side `POST /oauth/v3/token`, `POST /orange-money-webpay/dev/v1/webpayment`,
`POST /orange-money-webpay/dev/v1/transactionstatus`, `POST /mock/customer/pay/{pay_token}`. State is in memory; `POST /mock/reset` clears it.

## Outputs in `data/`
`poc.db` (canonical store), `recon_results.csv` and `recon_summary.json` (reconciliation and scoring),
`out_pacs008.xml` and `out_orange.json` (reverse-mapped files), `out_fidelity.csv` (field-level round trip),
`out_samples.json` (one payment shown as bank record, translated wallet request, and real wallet leg).

## Limits to state in the report
- Synthetic data and a simulated wallet leg; the real Orange Web Payment API is open to merchants only.
- The mock gateway's endpoints and statuses are modelled on public documentation and are not verified against Orange's current API.
- The manifest and the engine share the same rules, so 408/408 shows conformance to the specification, not accuracy on real payments.
- The wallet record cannot supply debtor name, debtor account, or the clearing reference when translated to pacs.008.
- In the multi-route simulation, participants, provider contracts and fee policies are project-defined mocks. Bank instructions are simplified JSON, not validated pacs.008 XML, and pair-level participation is unconfirmed unless configured.
- Passing tests show the simulator behaves as designed. They say nothing about any real bank, wallet or provider. The browser interface is covered by string-level tests and manual checks, not browser automation.

*Note: the notebooks are written for Google Colab with files in My Drive/bw-interop-poc.*
