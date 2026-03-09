-- Pipeline Triage Migration
-- Adds workstream + phase_gate columns, reclassifies FRG→LH, cancels dead items
-- Date: 2026-03-08

BEGIN;

-- 1. Add workstream column
ALTER TABLE bridge.pipeline_items ADD COLUMN IF NOT EXISTS workstream TEXT;
ALTER TABLE bridge.pipeline_items ADD COLUMN IF NOT EXISTS phase_gate TEXT;

-- 2. Cancel dead items
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Triage 2026-03-08: source parked/not in budget' WHERE id IN ('ACT-01', 'ACT-02', 'ACT-03', 'FRG-06');
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Triage 2026-03-08: superseded by FTB adapter architecture' WHERE id = 'VER-02';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Triage 2026-03-08: deferred, evidence-gated (NVT proxy)' WHERE id = 'F19-D10';

-- 3. Classify FTB items (FRG → LH reclassification + tagging)
-- Phase 1: Data Collection
-- FRG-22-BF → LH-01 (Backfill execution)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-01', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-22-BF. Triage 2026-03-08.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-22-BF';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-01' WHERE id = 'FRG-22-BF';

-- FRG-23 → LH-02 (Liquidation WebSocket)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-02', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-23. Triage 2026-03-08.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-23';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-02' WHERE id = 'FRG-23';

-- FRG-23-INGEST → LH-03 (Liquidation Ingestion)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-03', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-23-INGEST. Triage 2026-03-08.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-23-INGEST';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-03' WHERE id = 'FRG-23-INGEST';

-- FRG-24 → LH-04 (CoinMetrics agent)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-04', title, description, tier, status, '{fromthebridge}', '{LH-01}', enables, source, effort,
    'Reclassified from FRG-24. Triage 2026-03-08. blocked_by updated FRG-22-BF→LH-01.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-24';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-04' WHERE id = 'FRG-24';

-- FRG-25 → LH-05 (Binance metrics agent)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-05', title, description, tier, status, '{fromthebridge}', '{LH-01}', enables, source, effort,
    'Reclassified from FRG-25. Triage 2026-03-08. blocked_by updated FRG-22-BF→LH-01.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-25';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-05' WHERE id = 'FRG-25';

-- FRG-45 → LH-06 (Tiingo ToS verification — Phase 1 prerequisite per CLAUDE.md)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-06', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-45. Triage 2026-03-08.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-45';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-06' WHERE id = 'FRG-45';

-- FRG-40 through FRG-44, FRG-46 → LH-07 through LH-12 (ToS audits — Phase 1)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-07', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-40. Triage 2026-03-08.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-40';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-07' WHERE id = 'FRG-40';

INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-08', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-41. Triage 2026-03-08.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-41';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-08' WHERE id = 'FRG-41';

INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-09', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-42. Triage 2026-03-08.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-42';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-09' WHERE id = 'FRG-42';

INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-10', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-43. Triage 2026-03-08.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-43';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-10' WHERE id = 'FRG-43';

INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-11', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-44. Triage 2026-03-08.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-44';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-11' WHERE id = 'FRG-44';

INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-12', title, description, tier, status, '{fromthebridge}', '{LH-07,LH-08,LH-09,LH-10,LH-11,LH-06}', enables, source, effort,
    'Reclassified from FRG-46. Triage 2026-03-08. blocked_by updated to LH ToS items.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-46';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-12' WHERE id = 'FRG-46';

-- FRG-15 → LH-13 (OHLCV migration decision — Phase 1)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-13', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-15. Triage 2026-03-08.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-15';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-13' WHERE id = 'FRG-15';

-- FRG-22 → LH-14 (Dune API evaluation — Phase 1)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-14', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-22. Triage 2026-03-08.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-22';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-14' WHERE id = 'FRG-22';

-- FRG-10-CB → LH-15 (BOJ/PBOC — Phase 1, non-FRED source)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-15', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-10-CB. Triage 2026-03-08.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-10-CB';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-15' WHERE id = 'FRG-10-CB';

-- FRG-10-GOLD → LH-16 (Gold price — Phase 1)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-16', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-10-GOLD. Triage 2026-03-08.', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-10-GOLD';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-16' WHERE id = 'FRG-10-GOLD';

-- FRG-11 → LH-17 (CoinPaprika migration — Phase 1)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-17', title, description, tier, status, '{fromthebridge}', '{}', enables, source, effort,
    'Reclassified from FRG-11. Triage 2026-03-08. blocked_by cleared (FRG-10 complete).', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-11';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-17' WHERE id = 'FRG-11';

-- FRG-12 → LH-18 (GitHub collector migration — Phase 1)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-18', title, description, tier, status, '{fromthebridge}', '{}', enables, source, effort,
    'Reclassified from FRG-12. Triage 2026-03-08. blocked_by cleared (FRG-10 complete).', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-12';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-18' WHERE id = 'FRG-12';

-- FRG-13 → LH-19 (DeFiLlama migration — Phase 1)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-19', title, description, tier, status, '{fromthebridge}', '{}', enables, source, effort,
    'Reclassified from FRG-13. Triage 2026-03-08. blocked_by cleared (FRG-10 complete).', created_at, NOW(), 'ftb', 'ftb_p1'
  FROM bridge.pipeline_items WHERE id = 'FRG-13';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-19' WHERE id = 'FRG-13';

-- FRG-02 → LH-20 (Forge API — Phase 5/Serving)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-20', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-02. Triage 2026-03-08. Mapped to FTB Phase 5 (Serving layer).', created_at, NOW(), 'ftb', 'ftb_p5'
  FROM bridge.pipeline_items WHERE id = 'FRG-02';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-20' WHERE id = 'FRG-02';

-- FRG-17 → LH-21 (Tier 3 composites — Phase 2/Features)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-21', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-17. Triage 2026-03-08. Mapped to FTB Phase 2 (Feature Engineering).', created_at, NOW(), 'ftb', 'ftb_p2'
  FROM bridge.pipeline_items WHERE id = 'FRG-17';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-21' WHERE id = 'FRG-17';

-- FRG-18 → LH-22 (API security design — Phase 5)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-22', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-18. Triage 2026-03-08. Mapped to FTB Phase 5 (Serving).', created_at, NOW(), 'ftb', 'ftb_p5'
  FROM bridge.pipeline_items WHERE id = 'FRG-18';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-22' WHERE id = 'FRG-18';

-- FRG-14a → LH-23 (GPU compute approx — Phase 2)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-23', title, description, tier, status, '{fromthebridge}', blocked_by, enables, source, effort,
    'Reclassified from FRG-14a. Triage 2026-03-08. Mapped to FTB Phase 2.', created_at, NOW(), 'ftb', 'ftb_p2'
  FROM bridge.pipeline_items WHERE id = 'FRG-14a';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-23' WHERE id = 'FRG-14a';

-- FRG-14b → LH-24 (GPU compute true UTXO — Phase 2+)
INSERT INTO bridge.pipeline_items (id, title, description, tier, status, system_ids, blocked_by, enables, source, effort, decision_notes, created_at, updated_at, workstream, phase_gate)
  SELECT 'LH-24', title, description, tier, status, '{fromthebridge}', '{LH-23}', enables, source, effort,
    'Reclassified from FRG-14b. Triage 2026-03-08. blocked_by updated FRG-14a→LH-23.', created_at, NOW(), 'ftb', 'ftb_p2'
  FROM bridge.pipeline_items WHERE id = 'FRG-14b';
UPDATE bridge.pipeline_items SET status = 'cancelled', decision_notes = 'Reclassified to LH-24' WHERE id = 'FRG-14b';

-- 4. Tag ML items (keep ML-* IDs, add workstream + phase_gate)
UPDATE bridge.pipeline_items SET workstream = 'ftb', phase_gate = 'ftb_p4', system_ids = '{fromthebridge}' WHERE id LIKE 'ML-%';

-- 5. Tag EDS items
-- Pillar rebuild chain
UPDATE bridge.pipeline_items SET workstream = 'eds', phase_gate = 'eds_pillar' WHERE id IN ('REM-19', 'REM-21', 'REM-22', 'REM-23', 'REM-24');
-- Cohesion / enrichment
UPDATE bridge.pipeline_items SET workstream = 'eds', phase_gate = 'eds_cohesion' WHERE id IN ('REM-01', 'REM-03', 'REM-05', 'REM-06', 'REM-20');
-- EDS backlog (no phase gate)
UPDATE bridge.pipeline_items SET workstream = 'eds' WHERE id IN ('W7', 'W8', 'W9', 'W10', 'W11', 'W12', 'W14', 'W15', 'W16', 'W26', 'W27', 'W28', 'W29', 'W30', 'W31', 'B9', 'B10', 'B11', 'B16', 'B17', 'B21') AND workstream IS NULL;

-- 6. Tag Product items
UPDATE bridge.pipeline_items SET workstream = 'product', phase_gate = 'product_v1' WHERE id IN ('V1', 'V2', 'V3', 'V4', 'V5', 'V6', 'V7', 'V8', 'PL3', 'PL5', 'PL7', 'PL8', 'PL9', 'PL10', 'PL-L');

-- 7. Tag Bridge items
UPDATE bridge.pipeline_items SET workstream = 'bridge' WHERE id IN ('B1', 'B3', 'B4', 'B5', 'B6', 'B7', 'B8', 'B12', 'B13', 'B15', 'B20', 'B22', 'R2', 'W20', 'W32') AND workstream IS NULL;

-- 8. Tag Ops items (VER-01 is ops verification)
UPDATE bridge.pipeline_items SET workstream = 'ops' WHERE id = 'VER-01' AND workstream IS NULL;

-- 9. Tag remaining Bridge/misc
UPDATE bridge.pipeline_items SET workstream = 'bridge' WHERE id = 'B2' AND workstream IS NULL;

-- 10. Tag completed/cancelled items retroactively
UPDATE bridge.pipeline_items SET workstream = 'ftb' WHERE id LIKE 'FRG-%' AND workstream IS NULL AND status IN ('complete', 'cancelled');
UPDATE bridge.pipeline_items SET workstream = 'eds' WHERE id LIKE 'REM-%' AND workstream IS NULL;
UPDATE bridge.pipeline_items SET workstream = 'eds' WHERE id LIKE 'W%' AND workstream IS NULL;
UPDATE bridge.pipeline_items SET workstream = 'bridge' WHERE id LIKE 'B%' AND workstream IS NULL;
UPDATE bridge.pipeline_items SET workstream = 'product' WHERE id LIKE 'PL%' AND workstream IS NULL;
UPDATE bridge.pipeline_items SET workstream = 'product' WHERE id LIKE 'V%' AND workstream IS NULL;

COMMIT;
