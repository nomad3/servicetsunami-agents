-- Rollback for 107_aremko_catalog_seed.sql
--
-- Removes the 15 manually-seeded catalog entities for aremko. Identifies
-- them by extraction_platform='manual_seed' AND tenant_id, so we don't
-- accidentally delete entities that arrived through normal extraction.
--
-- Soft delete (sets deleted_at) rather than hard delete, in case any
-- relations / observations were attached. Hard delete is available by
-- replacing UPDATE with DELETE if you want a complete reset.

UPDATE knowledge_entities
SET deleted_at = NOW(),
    updated_at = NOW()
WHERE tenant_id = '73583e84-c025-4880-84b7-360f40602797'
  AND extraction_platform = 'manual_seed'
  AND deleted_at IS NULL;
