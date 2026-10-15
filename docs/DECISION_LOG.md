# Decision Log: Interoperability Layer for Bank–Wallet Payments

This log records the decisions that shaped the project, the evidence behind them, and who made them. Entries are in date order. Status values: Active (in force), Superseded (replaced by a later entry), To verify (depends on a source not yet checked), Open (not yet decided). Earlier decisions (September 2026) are recorded in more detail in the Consolidated Record Keeping Timeline.

## A. Scope and framework decisions (September 2026)

| ID | Date | Decision | Reason and evidence | Decided by | Status |
|---|---|---|---|---|---|
| D-01 | 2026-09-04 | BTC Smega was removed from the technical scope; the project was narrowed to Orange Money and ISO 20022 pacs.008. | Smega has no formally published, machine-readable schema (illustrative JSON in a static HTML page only). Recorded in the ToR Limitations and Constraints. See Consolidated Record Keeping Timeline. | Supervisor approved | Active |
| D-02 | 2026-09-05 | A four-entity canonical model was adopted: Transaction, Party, Status Report, Reconciliation Record. | Designed for the asymmetry between the two sources: five identifiers versus one, embedded status versus none, fused currency versus a sibling field. See Consolidated Record Keeping Timeline. | Team | Active |
| D-03 | 2026-09-08 | A shared matched pool (about 15% of rows) is carved out before the PaySim split so that cross-source reconciliation is possible. | A plain 50/50 split guarantees zero overlap. The matched pool contains 953,921 rows (verified 2026-09-09 and 2026-10-15). See Consolidated Record Keeping Timeline. | Team | Active |
| D-04 | 2026-09-18 | The post-lag dashboard values are the project's reported figures: 960K matched pairs, 24.91% same-day settlement, 1.50 days average lag, 0.64% discrepancy rate. | Settlement lag of 0 to 3 days was injected by design (about 239K records per day-gap; tail of about 0.22% to plus or minus 30 days). The earlier 99.40% same-day figure came from the pre-lag data and is retired. The ranking of discrepancy rate by status changed after the refresh (CANCELLED highest, FAILED lowest) and the refreshed ranking is the one reported. | Team | Active |
| D-05 | 2026-09-18 | The framework follows the tutor's structure: Wallet A, API Gateway, Interoperability Layer, Wallet B and Bank, assessed through six stages (ecosystem, barriers, requirements, framework, proof of concept, evaluation). | Project Review feedback from the tutor. | Tutor / Team | Active |
| D-06 | 2026-09-19 | A simulated proof of concept was accepted in place of a live integration. | The tutor confirmed that a simulated demonstration is possible and acceptable. The ToR already excludes live integration and licensed access. | Tutor | Active |
| D-07 | 2026-09-19 | Scope of flows: bank to wallet and wallet to bank are built; wallet to wallet is documented as an architectural extension. MyZaka, Smega, Mukuru and Stripe are not built. | No machine-readable schema was found for MyZaka or Smega. Mukuru is licensed as a non-account-based remittance provider and Stripe is a cross-border payment service; both are recorded as future extensions. A generic e-money adapter is shown as illustrative (dashed) in the architecture. | Team | Active |

## B. Evidence, access and delivery decisions (October 2026)

| ID | Date | Decision | Reason and evidence | Decided by | Status |
|---|---|---|---|---|---|
| D-08 | 2026-10-06 | Review 2 outcome recorded: about 67%. The assessor expected a test of Bank to Orange Money with real data, using an Orange Money API registered under the Orange network. | Reported by the team after Review 2. The team registered an Orange Developer account and application. | Assessor | Active |
| D-09 | 2026-10-06 | The background report interoperability.docx is used as supporting material only. Its claim that Botswana has no interoperability mandate is not used. | The Bank of Botswana Banking Supervision Annual Report 2025 states that the Bank is mandating and pursuing system-wide interoperability between banks and licensed electronic-money issuers (Foreword and Introduction). The report's Tanzania and Ghana statements were also replaced by IFC (2015) and Bank of Ghana (2018) sources. Statistics in the background report (220.2M transactions, 1.24M accounts, 75% Orange share) are unverified and are not used. | Team | Active |
| D-10 | 2026-10-06 | The specific directive details (two phases, compliance by 1 July 2026, and the statement that cross-wallet transfers were unavailable) are not yet confirmed. They are treated as unverified until checked in Chapter 3 of the Bank of Botswana report (National Payments System, from page 55). | The portion of the report retrieved on 2026-10-15 confirms the interoperability objective but not these specifics. The poster and Literature Review v2 must not state the deadline until it is verified. | Team | To verify |
| D-11 | 2026-10-06 | Orange's offer to send money to non-Orange users is classed as cash-out at an agent, not as account-to-account interoperability. | Orange's FAQ states that recipients outside the network withdraw the funds at an authorised point of sale. An earlier assumption that wallet-to-wallet transfers do not exist was replaced by the narrower wording: domestic account-to-account transfers between different issuers are not evidenced. | Team | Active |
| D-12 | 2026-10-08 | Orange Money Web Payment and Pay with Orange Bill are not available for testing. No public Orange API that credits a wallet from a bank was found. | The Orange Developer console lists the Network APIs Playground (mock CAMARA network APIs) but not Orange Money. Orange describes Web Payment as for registered Orange Money merchants. Pay with Orange Bill is carrier billing through an aggregator or direct contract. The access barrier is reported as a finding on the barriers to interoperability. | Team | Active |
| D-13 | 2026-10-13 | The supervisor closed the Orange API chapter and approved the synthetic route. Three deliverables remain: a synthetic proof of concept, an academic poster and the final report. | Supervisor decision, reported by the team. | Supervisor | Active |
| D-14 | 2026-10-13 | A delegation plan was adopted with owners, gates and a risk register. Final report submission is assumed to be 2026-10-30 and Review 3 is assumed to be 2026-10-26. | Assumed from a one-week holiday shift of all later milestones. The submission date has not been confirmed in writing. | Team | Open [CONFIRM] |
| D-15 | 2026-10-14 | Poster: Template 1 (BIDA A1 landscape conference) was chosen. The draft is due 2026-10-19 and the final print-ready PDF is due 2026-10-23. | School guidelines: A1 (841 x 594 mm landscape), PDF only, printed once at 100%, no reprint if the file is faulty. A pre-flight script checks page count, size, embedded fonts and image resolution. | Team | Active |
| D-16 | 2026-10-15 | Poster figures use the Power BI dashboard values (960K, 24.91%, 0.64%). Reference [1] is the Bank of Botswana Banking Supervision Annual Report 2025. | Single source of numbers for the poster and the report. Reference confirmed by the team. | Team | Active |

## C. Technical design decisions

| ID | Date | Decision | Reason and evidence | Decided by | Status |
|---|---|---|---|---|---|
| T-01 | 2026-10-15 | The reconciliation key is the bank EndToEndId, which is also the wallet order_id. A tolerant second pass matches on creditor MSISDN, amount within 5% and date within 10 days, and only when exactly one candidate exists. | The two sources generate identifiers independently, so a shared key has to be defined. The tolerant pass protects against false matches. | Team | Active |
| T-02 | 2026-10-15 | Amounts are stored as integer thebe (1 BWP = 100 thebe) with an explicit currency; floating point is not used. | Avoids rounding errors (for example 0.29 x 100 = 28.999999999999996 in floating point). | Team | Active |
| T-03 | 2026-10-15 | The five pacs.008 identifiers (InstrId, EndToEndId, TxId, UETR, ClrSysRef) and the message id are stored as separate rows and are never merged into one field. | Preserves the structural asymmetry between the sources and keeps reconciliation on EndToEndId a plain equality join. | Team | Active |
| T-04 | 2026-10-15 | Status is derived, not trusted. The bank leg is set to DISPATCHED (derived from the acknowledgement) on load and to COMPLETED, FAILED or DISPATCHED (derived from the ledger) after reconciliation. The wallet's status is stored as an echo. | pacs.008 has no status element. A wallet's SUCCESS is a report by one party and not independent evidence. | Team | Active |
| T-05 | 2026-10-15 | Duplicate keys: the first copy is loaded and later copies are rejected with DUPLICATE_KEY. Invalid records go to the exception queue with a reason code. A batch loads only if rows in equal rows mapped plus rows rejected. | Makes loads auditable and repeatable. | Team | Active |
| T-06 | 2026-10-15 | Settlement lag policy is 0 to 3 days with zero amount tolerance. A lag outside the window adds a LAG_OUTLIER flag but the pair stays MATCHED. | The outlier is an anomaly to review and not a failed payment. | Team | Active |
| T-07 | 2026-10-15 | Double-entry journal entries are posted for MATCHED pairs only, and every transaction's debits must equal its credits. | Unmatched or discrepant items must not settle. | Team | Active |
| T-08 | 2026-10-15 | Defects found by the round-trip test were fixed in version 1.1: (1) the ISO charge bearer SLEV was merged into SHARED, so only 270 of 382 bank records came back identical; SERVICE_LEVEL is now kept. (2) The wallet reference did not match the bank remittance text, so 0 of 352 translations matched; the wallet reference now carries the remittance text. | After the fix, 776 of 776 records round-trip identically and 352 of 352 translations match in both directions. Applied by patch_v1_1.py. | Team | Active |
| T-09 | 2026-10-15 | The mock gateway is a FastAPI wrapper around a framework-free core. Its endpoints and statuses follow public documentation (the 2019 Orange sandbox guide) and are labelled as a simulation. | Not verified against the current Orange API. Statuses follow the project dataset (INITIATED, SUCCESS, FAILED, CANCELLED, EXPIRED). Task 1.2 (field verification) is still open. | Team | Active |
| T-10 | 2026-10-15 | The score of 408 of 408 is reported as conformance to the specification and not as accuracy on real payments. | The ground-truth manifest and the reconciliation engine are built from the same rules. | Team | Active |
| T-11 | 2026-10-15 | Wallet to bank translation needs two extra debtor fields (name and account) that a wallet record cannot supply; the gateway rejects the translated record until they are provided. This is reported as a finding. | Shown by a test and by the field comparison; it illustrates the structural asymmetry the canonical model was designed for. | Team | Active |
| T-12 | 2026-10-15 | Code and sample data are published in the GitHub repository BIDA23-202/bw-interoperability-poc. The 954K-row matched pool, the database, and any credentials are excluded. | Reproducibility without exposing large files or secrets. Repository visibility (public) is to be confirmed before the poster's QR code is printed. | Team | Closed |

## D. Proof-of-concept run evidence (2026-10-15)

Outputs were produced by the team in Google Colab from the repository code with seed 42 and 400 pairs.

| Date | Step | Result | Check |
|---|---|---|---|
| 2026-10-15 | Step 1: dataset | Pool 953,921 rows; generated 402 bank records, 394 wallet records, 408 manifest rows | As expected |
| 2026-10-15 | Step 2: validate and load | 36 tests passed. Bank: 402 in, 382 mapped, 20 rejected (6 MISSING_CURRENCY, 8 DUPLICATE_KEY, 6 UNKNOWN_BIC). Wallet: 394 in, 394 mapped, 0 rejected. 776 transactions, 3,474 source identifiers, 782 parties, 776 status reports, 20 exceptions | As expected |
| 2026-10-15 | Step 3: reconciliation | 50 tests passed. 352 matched, 24 discrepant, 24 unmatched. 704 ledger entries, none unbalanced. 408 of 408 correct against the manifest | As expected |
| 2026-10-15 | Step 4: reverse mapping | 58 tests passed. Round trip: 776 of 776 records and 11,222 of 11,222 fields identical. Translation: 352 of 352 in both directions. ALL CHECKS PASSED (after the v1.1 patch) | As expected |
| 2026-10-15 | Step 5: mock gateway | 93 tests passed. Demo: success returns COMPLETED / ACSC; repeat returns 409 DUPLICATE_KEY; missing currency returns 422; failed wallet payment returns FAILED / RJCT; unpaid returns DISPATCHED / PDNG. Replay of all 402 bank records: 382 accepted; rejected 6 MISSING_CURRENCY, 8 DUPLICATE_KEY, 6 UNKNOWN_BIC | As expected |

## E. Open decisions and items to verify

| Item | Owner |
|---|---|
| Final report submission date and Review 3 date (assumed 2026-10-30 and 2026-10-26). | Supervisor |
| Directive details (two phases, 1 July 2026 deadline): verify in Chapter 3 of the Bank of Botswana report, or remove from the poster and Literature Review v2. | Literature Review owner |
| Orange field catalogue verification against the public OpenAPI file (task 1.2). | Atang / Kitso |
| Architecture decision record: scored comparison of central switch, hub-and-spoke and bilateral models, with a plus or minus 20% weight sensitivity test (task 8.4). | Ryan / Shewabo |
| Repository visibility: confirm the repository opens without logging in, then fix the poster QR code. | Kitso |
| Poster placeholders: team email, institution logo, and the final proof-of-concept results lines. | Upington |
