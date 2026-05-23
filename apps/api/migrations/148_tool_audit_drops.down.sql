-- 147_tool_audit_drops.down.sql

DROP INDEX IF EXISTS idx_tool_audit_drops_reason_created;
DROP INDEX IF EXISTS idx_tool_audit_drops_created_at;
DROP TABLE IF EXISTS tool_audit_drops;
