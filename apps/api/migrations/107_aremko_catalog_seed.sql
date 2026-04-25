-- 107 — Seed aremko's knowledge graph with the real product catalog.
--
-- Why: aremko owner Jorge reported Luna inventing cabin/tinaja names.
-- Investigation in docs/plans/2026-04-25-luna-hallucination-reduction-plan.md
-- found the aremko knowledge graph had 0 service/product entities, so
-- the memory-recall path could not anchor a booking response on real
-- data. PR #174 (receptionist skill) bundled the catalog into CLAUDE.md
-- via the skill body. This migration ALSO adds the catalog as
-- knowledge_entities so:
--   1. semantic recall surfaces them when a user asks about a specific
--      cabin/tinaja by name (memory-first hot path);
--   2. observations/commitments can be attached to the right entity;
--   3. downstream tools (find_entities, search_knowledge) work for
--      these names without first needing a tool call.
--
-- Source: apps/mcp-server/src/mcp_tools/aremko.py module docstring.
-- Tenant: aremko (73583e84-c025-4880-84b7-360f40602797).
--
-- Idempotent: uses INSERT ... SELECT ... WHERE NOT EXISTS, keyed on
-- (tenant_id, name). Safe to re-run. We can't use ON CONFLICT because
-- knowledge_entities has no UNIQUE constraint on (tenant_id, name).
--
-- Embeddings: NOT seeded here. The api's embedding backfill picks up
-- entities with NULL embedding on a periodic cycle. If you want immediate
-- recall, run scripts/backfill_embeddings.py after this migration.

INSERT INTO knowledge_entities (
    id, tenant_id, entity_type, category, name, description,
    attributes, properties, aliases, confidence, status,
    extraction_platform, created_at, updated_at
)
SELECT
    gen_random_uuid(),
    '73583e84-c025-4880-84b7-360f40602797'::uuid,
    'product',
    v.category,
    v.name,
    v.description,
    v.attributes,
    v.properties,
    v.aliases,
    1.0,
    'verified',
    'manual_seed',
    NOW(),
    NOW()
FROM (VALUES
  -- Cabañas (5)
  ('cabaña', 'Cabaña Arrayán',
   'Cabaña en Aremko Spa & Cabañas, Puerto Varas. Servicio de alojamiento.',
   '{"aremko_id": 9, "service_type": "cabanas"}'::json,
   '{"location": "Aremko Puerto Varas"}'::json,
   '["Arrayán", "Arrayan"]'::json),
  ('cabaña', 'Cabaña Laurel',
   'Cabaña en Aremko Spa & Cabañas, Puerto Varas. Servicio de alojamiento.',
   '{"aremko_id": 8, "service_type": "cabanas"}'::json,
   '{"location": "Aremko Puerto Varas"}'::json,
   '["Laurel"]'::json),
  ('cabaña', 'Cabaña Tepa',
   'Cabaña en Aremko Spa & Cabañas, Puerto Varas. Servicio de alojamiento.',
   '{"aremko_id": 7, "service_type": "cabanas"}'::json,
   '{"location": "Aremko Puerto Varas"}'::json,
   '["Tepa"]'::json),
  ('cabaña', 'Cabaña Torre',
   'Cabaña en Aremko Spa & Cabañas, Puerto Varas. Servicio de alojamiento.',
   '{"aremko_id": 3, "service_type": "cabanas"}'::json,
   '{"location": "Aremko Puerto Varas"}'::json,
   '["Torre"]'::json),
  ('cabaña', 'Cabaña Acantilado',
   'Cabaña en Aremko Spa & Cabañas, Puerto Varas. Servicio de alojamiento.',
   '{"aremko_id": 6, "service_type": "cabanas"}'::json,
   '{"location": "Aremko Puerto Varas"}'::json,
   '["Acantilado"]'::json),

  -- Tinajas (8)
  ('tinaja', 'Tinaja Hornopirén',
   'Tinaja caliente en Aremko Spa, Puerto Varas. Servicio de relajación al aire libre.',
   '{"aremko_id": 1, "service_type": "tinajas"}'::json,
   '{"location": "Aremko Puerto Varas"}'::json,
   '["Hornopirén", "Hornopiren"]'::json),
  ('tinaja', 'Tinaja Tronador',
   'Tinaja caliente en Aremko Spa, Puerto Varas. Servicio de relajación al aire libre.',
   '{"aremko_id": 10, "service_type": "tinajas"}'::json,
   '{"location": "Aremko Puerto Varas"}'::json,
   '["Tronador"]'::json),
  ('tinaja', 'Tinaja Osorno',
   'Tinaja caliente en Aremko Spa, Puerto Varas. Servicio de relajación al aire libre.',
   '{"aremko_id": 11, "service_type": "tinajas"}'::json,
   '{"location": "Aremko Puerto Varas"}'::json,
   '["Osorno"]'::json),
  ('tinaja', 'Tinaja Calbuco',
   'Tinaja caliente en Aremko Spa, Puerto Varas. Servicio de relajación al aire libre.',
   '{"aremko_id": 12, "service_type": "tinajas"}'::json,
   '{"location": "Aremko Puerto Varas"}'::json,
   '["Calbuco"]'::json),
  ('tinaja', 'Tinaja Hidromasaje Puntiagudo',
   'Tinaja caliente con hidromasaje en Aremko Spa, Puerto Varas.',
   '{"aremko_id": 13, "service_type": "tinajas", "hidromasaje": true}'::json,
   '{"location": "Aremko Puerto Varas"}'::json,
   '["Puntiagudo", "Hidromasaje Puntiagudo"]'::json),
  ('tinaja', 'Tinaja Llaima',
   'Tinaja caliente con hidromasaje en Aremko Spa, Puerto Varas.',
   '{"aremko_id": 14, "service_type": "tinajas", "hidromasaje": true}'::json,
   '{"location": "Aremko Puerto Varas"}'::json,
   '["Llaima"]'::json),
  ('tinaja', 'Tinaja Villarrica',
   'Tinaja caliente con hidromasaje en Aremko Spa, Puerto Varas.',
   '{"aremko_id": 15, "service_type": "tinajas", "hidromasaje": true}'::json,
   '{"location": "Aremko Puerto Varas"}'::json,
   '["Villarrica"]'::json),
  ('tinaja', 'Tinaja Puyehue',
   'Tinaja caliente con hidromasaje en Aremko Spa, Puerto Varas.',
   '{"aremko_id": 16, "service_type": "tinajas", "hidromasaje": true}'::json,
   '{"location": "Aremko Puerto Varas"}'::json,
   '["Puyehue"]'::json),

  -- Masajes (1)
  ('masaje', 'Masaje Relajación o Descontracturante',
   'Masaje de relajación o descontracturante en Aremko Spa, Puerto Varas. Capacidad para 2 simultáneos.',
   '{"aremko_id": 53, "service_type": "masajes"}'::json,
   '{"location": "Aremko Puerto Varas", "capacity_simultaneous": 2}'::json,
   '["Masaje", "Masaje Relajación", "Descontracturante"]'::json),

  -- Desayuno (1)
  ('desayuno', 'Desayuno Aremko',
   'Desayuno servido en Aremko Spa, Puerto Varas. Tarifa única — mismo precio para 1 o 2 personas, una entrada por reserva.',
   '{"aremko_id": 26, "service_type": "desayunos", "flat_rate": true}'::json,
   '{"location": "Aremko Puerto Varas"}'::json,
   '["Desayuno"]'::json)
) AS v(category, name, description, attributes, properties, aliases)
WHERE NOT EXISTS (
    SELECT 1 FROM knowledge_entities ke
    WHERE ke.tenant_id = '73583e84-c025-4880-84b7-360f40602797'::uuid
      AND ke.name = v.name
);
