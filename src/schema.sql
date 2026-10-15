-- schema.sql  |  Canonical store for the synthetic interoperability PoC (SQLite)
-- Report entities -> tables:  Transaction = transactions + transaction_source_ids
--                             Party       = parties
--                             Status Report = status_reports
--                             Reconciliation Record = reconciliation_records
-- Supporting tables: participants, settlement_journal, exceptions, mapping_registry, load_audit.
-- Rules: amounts are integer thebe (1 BWP = 100 thebe); currency is explicit; the five pacs.008
-- identifiers are kept as separate rows; reconciliation key = transactions.end_to_end_key.
PRAGMA foreign_keys = ON;

CREATE TABLE participants (
  participant_id INTEGER PRIMARY KEY,
  code           TEXT NOT NULL UNIQUE,
  name           TEXT NOT NULL,
  type           TEXT NOT NULL CHECK (type IN ('BANK','WALLET','PSP')),
  bic            TEXT NOT NULL UNIQUE CHECK (length(bic) IN (8,11)),
  active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  note           TEXT
);

CREATE TABLE load_audit (
  batch_id       TEXT PRIMARY KEY,
  source_system  TEXT NOT NULL CHECK (source_system IN ('PACS008','ORANGE')),
  loaded_at      TEXT NOT NULL,
  rows_in        INTEGER NOT NULL CHECK (rows_in >= 0),
  rows_mapped    INTEGER NOT NULL CHECK (rows_mapped >= 0),
  rows_rejected  INTEGER NOT NULL CHECK (rows_rejected >= 0),
  reject_reasons TEXT NOT NULL DEFAULT '{}',          -- JSON: {"reason": count}
  CHECK (rows_in = rows_mapped + rows_rejected)       -- the batch invariant
);

CREATE TABLE parties (
  party_id         INTEGER PRIMARY KEY,
  participant_id   INTEGER NOT NULL REFERENCES participants(participant_id),
  identifier_type  TEXT NOT NULL CHECK (identifier_type IN ('MSISDN','ORGID','PRVTID','ACCOUNT')),
  identifier_value TEXT NOT NULL,
  display_name     TEXT,
  UNIQUE (participant_id, identifier_type, identifier_value)
);

CREATE TABLE transactions (
  txn_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  source_system           TEXT NOT NULL CHECK (source_system IN ('PACS008','ORANGE')),
  channel                 TEXT NOT NULL CHECK (channel IN ('BANK','WALLET')),
  direction               TEXT NOT NULL CHECK (direction IN ('PUSH','PULL')),
  end_to_end_key          TEXT NOT NULL,              -- reconciliation key (EndToEndId / Orange order_id)
  debtor_party_id         INTEGER REFERENCES parties(party_id),
  creditor_party_id       INTEGER REFERENCES parties(party_id),
  debtor_participant_id   INTEGER REFERENCES participants(participant_id),
  creditor_participant_id INTEGER REFERENCES participants(participant_id),
  amount_minor            INTEGER NOT NULL CHECK (amount_minor > 0),
  currency                TEXT NOT NULL CHECK (length(currency) = 3),
  charge_bearer           TEXT CHECK (charge_bearer IN ('DEBT','CRED','SHARED','SERVICE_LEVEL')),
  remittance_info         TEXT,
  initiated_at            TEXT NOT NULL,              -- ISO-8601 UTC
  settled_at              TEXT,                       -- ISO date or timestamp
  status                  TEXT NOT NULL CHECK (status IN ('INITIATED','DISPATCHED','COMPLETED','FAILED','REVERSED')),
  status_source           TEXT NOT NULL CHECK (status_source IN ('SEED','SOURCE_ECHO','DERIVED_ACK','DERIVED_LEDGER')),
  status_detail           TEXT,
  batch_id                TEXT REFERENCES load_audit(batch_id),
  UNIQUE (source_system, end_to_end_key)              -- a duplicate key is rejected at load
);
CREATE INDEX ix_txn_key ON transactions(end_to_end_key);
CREATE INDEX ix_txn_batch ON transactions(batch_id);

CREATE TABLE transaction_source_ids (
  id              INTEGER PRIMARY KEY,
  txn_id          INTEGER NOT NULL REFERENCES transactions(txn_id) ON DELETE CASCADE,
  source_system   TEXT NOT NULL CHECK (source_system IN ('PACS008','ORANGE')),
  identifier_kind TEXT NOT NULL CHECK (identifier_kind IN
    ('MSG_ID','INSTR_ID','END_TO_END_ID','TX_ID','UETR','CLR_SYS_REF',
     'OM_TRANSACTION_ID','OM_ORDER_ID','OM_PAY_TOKEN')),
  identifier_value TEXT NOT NULL,
  scope           TEXT NOT NULL CHECK (scope IN ('MESSAGE','TRANSACTION')),
  UNIQUE (txn_id, identifier_kind)
);
-- transaction-scope identifiers are globally unique; message-scope ids may repeat inside a batch
CREATE UNIQUE INDEX ux_source_ids_txn_scope
  ON transaction_source_ids(source_system, identifier_kind, identifier_value) WHERE scope = 'TRANSACTION';

CREATE TABLE status_reports (
  report_id        INTEGER PRIMARY KEY,
  txn_id           INTEGER NOT NULL REFERENCES transactions(txn_id) ON DELETE CASCADE,
  iso_status       TEXT NOT NULL CHECK (iso_status IN ('ACSC','ACCP','PDNG','RJCT')),  -- pacs.002-style codes
  canonical_status TEXT NOT NULL CHECK (canonical_status IN ('INITIATED','DISPATCHED','COMPLETED','FAILED','REVERSED')),
  reason_code      TEXT,
  reason_text      TEXT,
  source           TEXT NOT NULL CHECK (source IN ('SOURCE_ECHO','DERIVED_ACK','DERIVED_LEDGER','GATEWAY')),
  created_at       TEXT NOT NULL
);

CREATE TABLE reconciliation_records (
  recon_id          INTEGER PRIMARY KEY,
  bank_txn_id       INTEGER REFERENCES transactions(txn_id),
  wallet_txn_id     INTEGER REFERENCES transactions(txn_id),
  match_method      TEXT NOT NULL CHECK (match_method IN ('KEY','TOLERANT','NONE')),
  outcome           TEXT NOT NULL CHECK (outcome IN ('MATCHED','DISCREPANT','UNMATCHED','REJECTED')),
  amount_diff_minor INTEGER,
  day_gap           INTEGER,
  flags             TEXT NOT NULL DEFAULT '[]',        -- JSON list, e.g. ["LAG_OUTLIER","STATUS_MISMATCH"]
  reconciled_at     TEXT NOT NULL,
  CHECK (bank_txn_id IS NOT NULL OR wallet_txn_id IS NOT NULL)
);

CREATE TABLE settlement_journal (
  journal_id     INTEGER PRIMARY KEY,
  txn_id         INTEGER NOT NULL REFERENCES transactions(txn_id) ON DELETE CASCADE,
  dc             TEXT NOT NULL CHECK (dc IN ('D','C')),
  account        TEXT NOT NULL,
  amount_minor   INTEGER NOT NULL CHECK (amount_minor > 0),
  currency       TEXT NOT NULL CHECK (length(currency) = 3),
  end_to_end_key TEXT NOT NULL,
  posted_at      TEXT NOT NULL
);

CREATE TABLE exceptions (
  exception_id  INTEGER PRIMARY KEY,
  batch_id      TEXT REFERENCES load_audit(batch_id),
  source_system TEXT NOT NULL,
  record_ref    TEXT,
  reason_code   TEXT NOT NULL,
  reason_detail TEXT,
  payload_json  TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE mapping_registry (
  field_id         INTEGER PRIMARY KEY,
  source_system    TEXT NOT NULL CHECK (source_system IN ('PACS008','ORANGE')),
  source_field     TEXT NOT NULL,
  canonical_target TEXT,
  status           TEXT NOT NULL CHECK (status IN ('MAPPED','NOT_MODELLED','PENDING_CATALOGUE')),
  note             TEXT,
  UNIQUE (source_system, source_field)
);

-- Views
CREATE VIEW v_journal_balance AS
  SELECT txn_id, SUM(CASE WHEN dc='D' THEN amount_minor ELSE 0 END) AS debits,
         SUM(CASE WHEN dc='C' THEN amount_minor ELSE 0 END) AS credits,
         SUM(CASE WHEN dc='D' THEN amount_minor ELSE -amount_minor END) AS imbalance
  FROM settlement_journal GROUP BY txn_id;

CREATE VIEW v_transactions_flat AS
  SELECT t.txn_id, t.source_system, t.channel, t.direction, t.end_to_end_key,
         dp.identifier_type AS debtor_id_type, dp.identifier_value AS debtor_id,
         cp.identifier_type AS creditor_id_type, cp.identifier_value AS creditor_id,
         t.amount_minor, t.currency, t.charge_bearer, t.initiated_at, t.settled_at,
         t.status, t.status_source, t.batch_id
  FROM transactions t
  LEFT JOIN parties dp ON dp.party_id = t.debtor_party_id
  LEFT JOIN parties cp ON cp.party_id = t.creditor_party_id;

CREATE VIEW v_recon_summary AS
  SELECT outcome, match_method, COUNT(*) AS n FROM reconciliation_records GROUP BY outcome, match_method;

-- Mapping registry seed (extend by INSERT only; never ALTER)
INSERT INTO mapping_registry(source_system, source_field, canonical_target, status, note) VALUES
 ('ORANGE','transactionId','transaction_source_ids OM_TRANSACTION_ID','MAPPED','Orange transaction reference'),
 ('ORANGE','order_id','transactions.end_to_end_key + OM_ORDER_ID','MAPPED','Reconciliation key; set from the bank EndToEndId in the PoC'),
 ('ORANGE','reference','transactions.remittance_info','MAPPED','Merchant reference text'),
 ('ORANGE','amount.value','transactions.amount_minor','MAPPED','Decimal BWP to integer thebe'),
 ('ORANGE','amount.unit','transactions.currency','MAPPED','Explicit, never defaulted'),
 ('ORANGE','customer.id','parties MSISDN (creditor)','MAPPED','PoC choice: wallet holder credited'),
 ('ORANGE','customer.idType','parties.identifier_type','MAPPED',NULL),
 ('ORANGE','partner.id','participants.code (debtor participant)','MAPPED','PoC choice: originating bank code'),
 ('ORANGE','partner.idType','(none)','NOT_MODELLED','Always CODE in the PoC'),
 ('ORANGE','status','transactions.status + status_source SOURCE_ECHO','MAPPED','Echoed, not a source of truth'),
 ('ORANGE','createdAt','transactions.initiated_at','MAPPED','UTC ISO-8601'),
 ('ORANGE','pay_token','transaction_source_ids OM_PAY_TOKEN','MAPPED',NULL),
 ('PACS008','GrpHdr.MsgId','transaction_source_ids MSG_ID (MESSAGE)','MAPPED','Shared by all transactions in a message'),
 ('PACS008','PmtId.InstrId','transaction_source_ids INSTR_ID','MAPPED','Never flattened'),
 ('PACS008','PmtId.EndToEndId','transactions.end_to_end_key + END_TO_END_ID','MAPPED','Reconciliation key'),
 ('PACS008','PmtId.TxId','transaction_source_ids TX_ID','MAPPED',NULL),
 ('PACS008','PmtId.UETR','transaction_source_ids UETR','MAPPED',NULL),
 ('PACS008','PmtId.ClrSysRef','transaction_source_ids CLR_SYS_REF','MAPPED',NULL),
 ('PACS008','IntrBkSttlmAmt','transactions.amount_minor','MAPPED','Decimal BWP to integer thebe'),
 ('PACS008','IntrBkSttlmAmt@Ccy','transactions.currency','MAPPED','Missing currency is rejected at the gateway'),
 ('PACS008','IntrBkSttlmDt','transactions.settled_at','MAPPED',NULL),
 ('PACS008','ChrgBr','transactions.charge_bearer','MAPPED','SLEV kept as SERVICE_LEVEL (no information loss)'),
 ('PACS008','Dbtr.Nm','parties.display_name (debtor)','MAPPED',NULL),
 ('PACS008','DbtrAcct.Id.Othr.Id','parties ACCOUNT (debtor)','MAPPED',NULL),
 ('PACS008','DbtrAgt.FinInstnId.BICFI','participants.bic (debtor participant)','MAPPED','Unknown BIC is rejected'),
 ('PACS008','Cdtr.Nm','parties.display_name (creditor)','MAPPED',NULL),
 ('PACS008','CdtrAcct.Id.Othr.Id','parties MSISDN (creditor wallet)','MAPPED',NULL),
 ('PACS008','CdtrAgt.FinInstnId.BICFI','participants.bic (creditor participant)','MAPPED',NULL),
 ('PACS008','RmtInf.Ustrd','transactions.remittance_info','MAPPED',NULL),
 ('PACS008','(status)','transactions.status + status_source DERIVED_ACK','MAPPED','pacs.008 carries no status element');
