# bw-interop-poc: synthetic interoperability proof of concept

A simulation of a hub between banks (ISO 20022 pacs.008) and wallets (Orange Money style JSON). Everything is synthetic and seeded.
No real accounts, credentials, or live integrations are used. The wallet is a mock, not Orange's API.

## Folders
- `src/`   code, schema, roster, tests
- `data/`  inputs and outputs (keep the large `MatchedPoolReference.csv` out of git)
- `docs/`  decision log, evidence, screenshots

## Files in `src/`
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
| `test_*.py` | pytest suites (`test_mock_gateway_api.py` is skipped when FastAPI is not installed) |

## Set up
Python 3.9 or later. `pip install -r requirements.txt`

## Run the batch pipeline
```
python src/patch_v1_1.py --src src        # once
python -m pytest -q                        # from the src folder
python src/run_all.py --base . --pool data/MatchedPoolReference.csv
```
Expected for the default `--n 400 --seed 42`: pacs.008 402 in / 382 mapped / 20 rejected, Orange 394 / 394 / 0,
reconciliation 352 matched / 24 discrepant / 24 unmatched, 704 balanced ledger entries, manifest 408/408,
round trip 776/776 identical, translation 352/352 in both directions, and `ALL CHECKS PASSED`.

## Run the mock gateway
```
python -m uvicorn mock_gateway:app --app-dir src --port 8000
```
Then open `http://127.0.0.1:8000/docs`. Key endpoints: `POST /interop/v1/bank-to-wallet?simulate=SUCCESS|FAILED|CANCELLED|PENDING`,
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
