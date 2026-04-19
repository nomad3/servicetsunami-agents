-- Migration: 102_agent_name_unique_per_tenant
-- Guard the name-based backfill invariant used by migration 101.
-- Before dropping agent_kit_id in a future migration, we need to guarantee
-- that `(tenant_id, LOWER(name))` is unique on the `agents` table — otherwise
-- the name-match join in migration 101 could bind a chat_session to the
-- wrong agent.

-- Deduplicate before creating the index. For any collisions, keep the oldest
-- agent (smallest id), and rename the others by appending a short suffix.
DO $$
DECLARE
    dup RECORD;
    counter INTEGER;
BEGIN
    FOR dup IN
        SELECT tenant_id, LOWER(name) AS lname
        FROM agents
        GROUP BY tenant_id, LOWER(name)
        HAVING COUNT(*) > 1
    LOOP
        counter := 0;
        FOR dup IN
            SELECT id, name FROM agents
            WHERE tenant_id = dup.tenant_id AND LOWER(name) = dup.lname
            ORDER BY id ASC OFFSET 1
        LOOP
            counter := counter + 1;
            UPDATE agents
            SET name = name || ' (' || counter || ')'
            WHERE id = dup.id;
        END LOOP;
    END LOOP;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_tenant_name_unique
    ON agents (tenant_id, LOWER(name));

INSERT INTO _migrations(filename) VALUES ('102_agent_name_unique_per_tenant') ON CONFLICT DO NOTHING;
