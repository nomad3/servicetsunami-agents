-- 041_rename_skill_name_to_integration_name.sql
-- Rename skill_name column to integration_name in integration_configs table

ALTER TABLE integration_configs RENAME COLUMN skill_name TO integration_name;
