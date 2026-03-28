"""Knowledge graph MCP tools.

Entity and relation CRUD, semantic search, and observation management.
Uses asyncpg directly (consistent with the rest of the MCP server) for all
knowledge graph SQL operations.
"""
import json
import logging
import os
import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import Optional

import asyncpg
from mcp.server.fastmcp import Context

from src.mcp_app import mcp
from src.mcp_auth import resolve_tenant_id

logger = logging.getLogger(__name__)

# Module-level caches
_pool: Optional[asyncpg.Pool] = None
_pgvector_available: Optional[bool] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db_url() -> str:
    """Return the asyncpg-compatible DATABASE_URL from env/config."""
    from src.config import settings
    url = settings.DATABASE_URL or os.environ.get("DATABASE_URL", "")
    # asyncpg uses postgresql://, not postgresql+asyncpg://
    return url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")


async def _get_pool() -> asyncpg.Pool:
    """Return a shared connection pool (created on first call)."""
    global _pool
    if _pool is None or _pool._closed:
        db_url = _get_db_url()
        if not db_url:
            raise RuntimeError("DATABASE_URL not configured")
        _pool = await asyncpg.create_pool(db_url, min_size=2, max_size=10)
    return _pool


async def _get_conn():
    """Acquire a connection from the pool. Use as: async with _get_conn() as conn:"""
    pool = await _get_pool()
    return pool.acquire()


def _serialize_row(row: asyncpg.Record) -> dict:
    """Convert an asyncpg Record to a JSON-safe dict."""
    result = {}
    for k, v in dict(row).items():
        if isinstance(v, uuid.UUID):
            result[k] = str(v)
        elif isinstance(v, (datetime, date)):
            result[k] = v.isoformat()
        elif isinstance(v, Decimal):
            result[k] = float(v)
        else:
            result[k] = v
    return result


def _parse_json(val, default=None):
    """Parse a JSON string or pass through if already a dict/list."""
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


async def _has_pgvector(conn: asyncpg.Connection) -> bool:
    """Return True if the pgvector extension is installed (cached after first check)."""
    global _pgvector_available
    if _pgvector_available is not None:
        return _pgvector_available
    try:
        row = await conn.fetchrow("SELECT 1 FROM pg_extension WHERE extname='vector'")
        _pgvector_available = row is not None
    except Exception:
        _pgvector_available = False
    return _pgvector_available


async def _get_embedding(text: str) -> Optional[list]:
    """Generate a 768-dim embedding via nomic-embed-text-v1.5 (local, no API key)."""
    try:
        from sentence_transformers import SentenceTransformer
        _model_cache = getattr(_get_embedding, "_model", None)
        if _model_cache is None:
            _get_embedding._model = SentenceTransformer(
                "nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True
            )
        model = _get_embedding._model
        prefixed = f"search_document: {text[:8000]}"
        embedding = model.encode(prefixed, normalize_embeddings=True)
        return embedding.tolist()
    except Exception as e:
        logger.warning("Embedding generation skipped: %s", e)
        return None


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_entity(
    name: str,
    entity_type: str,
    tenant_id: str = "",
    description: str = "",
    category: str = "",
    properties: str = "{}",
    aliases: str = "[]",
    confidence: float = 1.0,
    ctx: Context = None,
) -> dict:
    """Create a new knowledge entity (person, company, project, lead, etc.).

    Args:
        name: Entity name.
        entity_type: Type such as customer, product, organization, person.
        tenant_id: Tenant UUID (resolved from session if omitted).
        description: Human-readable description.
        category: High-level category: lead, contact, investor, competitor, organization, person.
        properties: Additional properties as JSON string e.g. '{"key": "value"}'.
        aliases: Alternative names as JSON array string e.g. '["alias1"]'.
        confidence: Confidence score 0.0-1.0 (default 1.0).
        ctx: MCP request context (injected automatically).

    Returns:
        dict with id, name, entity_type, category of the created entity.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    props = _parse_json(properties, {})
    alias_list = _parse_json(aliases, [])

    async with (await _get_pool()).acquire() as conn:
        # Dedup: check if entity with same name+type already exists for this tenant
        existing = await conn.fetchrow(
            "SELECT id, name, entity_type, category FROM knowledge_entities WHERE tenant_id = $1 AND LOWER(name) = LOWER($2) AND entity_type = $3",
            tid, name, entity_type,
        )
        if existing:
            return {"id": str(existing["id"]), "name": existing["name"], "entity_type": existing["entity_type"], "category": existing["category"], "already_exists": True}

        entity_id = str(uuid.uuid4())
        pgvector = await _has_pgvector(conn)
        if pgvector:
            embedding = await _get_embedding(f"{name} {description or ''}")
            if embedding:
                await conn.execute(
                    """
                    INSERT INTO knowledge_entities
                    (id, tenant_id, name, entity_type, category, description, properties, aliases, confidence, embedding, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::vector, NOW(), NOW())
                    """,
                    entity_id, tid, name, entity_type,
                    category or None, description or None,
                    json.dumps(props), json.dumps(alias_list),
                    confidence, str(embedding),
                )
            else:
                pgvector = False  # fall through to no-embedding path

        if not pgvector:
            await conn.execute(
                """
                INSERT INTO knowledge_entities
                (id, tenant_id, name, entity_type, category, description, properties, aliases, confidence, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW())
                """,
                entity_id, tid, name, entity_type,
                category or None, description or None,
                json.dumps(props), json.dumps(alias_list),
                confidence,
            )
    # connection returned to pool automatically

    return {"id": entity_id, "name": name, "entity_type": entity_type, "category": category}


@mcp.tool()
async def find_entities(
    query: str,
    tenant_id: str = "",
    entity_types: str = "[]",
    limit: int = 10,
    min_confidence: float = 0.5,
    ctx: Context = None,
) -> list:
    """Semantic search for entities by name, description, or properties.

    Args:
        query: Natural language search query.
        tenant_id: Tenant UUID (resolved from session if omitted).
        entity_types: JSON array of types to filter e.g. '["person","company"]'.
        limit: Maximum number of results (default 10).
        min_confidence: Minimum confidence threshold 0.0-1.0 (default 0.5).
        ctx: MCP request context (injected automatically).

    Returns:
        List of matching entities ranked by relevance.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    types = _parse_json(entity_types, [])

    async with (await _get_pool()).acquire() as conn:
        pgvector = await _has_pgvector(conn)

        type_filter = ""
        if types:
            placeholders = ",".join(f"${i + 4}" for i in range(len(types)))
            type_filter = f"AND entity_type IN ({placeholders})"

        if pgvector:
            embedding = await _get_embedding(query)
            if embedding:
                sql = f"""
                    SELECT id, name, entity_type, category, description, confidence,
                           1 - (embedding <=> $3::vector) as similarity
                    FROM knowledge_entities
                    WHERE tenant_id = $1
                    AND confidence >= $2
                    {type_filter}
                    ORDER BY embedding <=> $3::vector
                    LIMIT {limit}
                """
                args = [tid, min_confidence, str(embedding)] + (types if types else [])
                rows = await conn.fetch(sql, *args)
                return [_serialize_row(r) for r in rows]

        # Text fallback
        sql = f"""
            SELECT id, name, entity_type, category, description, confidence,
                   1.0 as similarity
            FROM knowledge_entities
            WHERE tenant_id = $1
            AND confidence >= $2
            AND (name ILIKE $3 OR description ILIKE $3)
            {type_filter}
            ORDER BY confidence DESC
            LIMIT {limit}
        """
        args = [tid, min_confidence, f"%{query}%"] + (types if types else [])
        rows = await conn.fetch(sql, *args)
        return [_serialize_row(r) for r in rows]
    # connection returned to pool automatically


@mcp.tool()
async def update_entity(
    entity_id: str,
    updates: str,
    reason: str = "",
    ctx: Context = None,
) -> dict:
    """Update entity properties (creates version history for audit).

    Args:
        entity_id: Entity UUID to update.
        updates: Properties to update as JSON string e.g. '{"name": "new_name"}'.
        reason: Reason for the change (stored in audit history).
        ctx: MCP request context (injected automatically).

    Returns:
        Updated entity dict.
    """
    updates_dict = _parse_json(updates, {})

    db_url = _get_db_url()
    if not db_url:
        return {"error": "DATABASE_URL not configured"}

    tid = resolve_tenant_id(ctx)

    async with (await _get_pool()).acquire() as conn:
        current = await conn.fetchrow(
            "SELECT properties FROM knowledge_entities WHERE id = $1 AND tenant_id = $2",
            entity_id, tid,
        )
        if current:
            props = current["properties"]
            if isinstance(props, dict):
                props = json.dumps(props)
            await conn.execute(
                """
                INSERT INTO knowledge_entity_history
                (entity_id, tenant_id, version, properties_snapshot, change_reason, changed_by_platform)
                SELECT $1, $4, COALESCE(MAX(version), 0) + 1, $2, $3, 'mcp'
                FROM knowledge_entity_history WHERE entity_id = $1
                """,
                entity_id, props, reason or None, tid,
            )

        await conn.execute(
            """
            UPDATE knowledge_entities
            SET properties = $1, updated_at = NOW()
            WHERE id = $2 AND tenant_id = $3
            """,
            json.dumps(updates_dict), entity_id, tid,
        )

        updated = await conn.fetchrow(
            """
            SELECT id, tenant_id, name, entity_type, category, description,
                   properties, aliases, confidence, created_at, updated_at
            FROM knowledge_entities WHERE id = $1 AND tenant_id = $2
            """,
            entity_id, tid,
        )
        if not updated:
            return {"error": "Entity not found"}
        return _serialize_row(updated)
    # connection returned to pool automatically


@mcp.tool()
async def merge_entities(
    primary_entity_id: str,
    duplicate_entity_ids: str,
    reason: str,
    ctx: Context = None,
) -> dict:
    """Merge duplicate entities into the primary, preserving all relationships.

    Args:
        primary_entity_id: UUID of the entity to keep.
        duplicate_entity_ids: JSON array of UUIDs to merge and delete e.g. '["uuid1","uuid2"]'.
        reason: Reason for the merge (stored in history).
        ctx: MCP request context (injected automatically).

    Returns:
        The surviving (primary) entity with all relations.
    """
    dup_ids = _parse_json(duplicate_entity_ids, [])
    tid = resolve_tenant_id(ctx)

    async with (await _get_pool()).acquire() as conn:
        async with conn.transaction():
            for dup_id in dup_ids:
                await conn.execute(
                    "UPDATE knowledge_relations SET from_entity_id = $1 WHERE from_entity_id = $2",
                    primary_entity_id, dup_id,
                )
                await conn.execute(
                    "UPDATE knowledge_relations SET to_entity_id = $1 WHERE to_entity_id = $2",
                    primary_entity_id, dup_id,
                )
                await conn.execute(
                    "DELETE FROM knowledge_entities WHERE id = $1 AND tenant_id = $2",
                    dup_id, tid,
                )

        # Return primary with relations
        entity = await conn.fetchrow(
            """
            SELECT id, tenant_id, name, entity_type, category, description,
                   properties, aliases, confidence, created_at, updated_at
            FROM knowledge_entities WHERE id = $1 AND tenant_id = $2
            """,
            primary_entity_id, tid,
        )
        if not entity:
            return {"error": "Primary entity not found"}
        result = _serialize_row(entity)

        relations = await conn.fetch(
            """
            SELECT r.id, r.relation_type, r.strength, r.evidence,
                   e.id as target_id, e.name as target_name, e.entity_type as target_type
            FROM knowledge_relations r
            JOIN knowledge_entities e ON r.to_entity_id = e.id
            WHERE r.from_entity_id = $1
            UNION ALL
            SELECT r.id, r.relation_type, r.strength, r.evidence,
                   e.id as target_id, e.name as target_name, e.entity_type as target_type
            FROM knowledge_relations r
            JOIN knowledge_entities e ON r.from_entity_id = e.id
            WHERE r.to_entity_id = $1
            """,
            primary_entity_id,
        )
        result["relations"] = [_serialize_row(r) for r in relations]
        return result
    # connection returned to pool automatically


@mcp.tool()
async def create_relation(
    source_entity_id: str,
    target_entity_id: str,
    relation_type: str,
    tenant_id: str = "",
    properties: str = "{}",
    strength: float = 1.0,
    evidence: str = "",
    bidirectional: bool = False,
    ctx: Context = None,
) -> dict:
    """Create a relationship between two knowledge entities.

    Args:
        source_entity_id: UUID of the source entity.
        target_entity_id: UUID of the target entity.
        relation_type: Relationship type e.g. purchased, works_at, derived_from.
        tenant_id: Tenant UUID (resolved from session if omitted).
        properties: Additional properties as JSON string.
        strength: Relationship strength 0.0-1.0 (default 1.0).
        evidence: Supporting context or citation.
        bidirectional: If true, also creates the reverse relation.
        ctx: MCP request context (injected automatically).

    Returns:
        dict with id and relation_type of the created relationship.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    props = _parse_json(properties, {})
    relation_id = str(uuid.uuid4())
    evidence_json = json.dumps({"text": evidence or "", "properties": props})

    async with (await _get_pool()).acquire() as conn:
        await conn.execute(
            """
            INSERT INTO knowledge_relations
            (id, tenant_id, from_entity_id, to_entity_id, relation_type, strength, evidence, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            """,
            relation_id, tid, source_entity_id, target_entity_id,
            relation_type, strength, evidence_json,
        )

        if bidirectional:
            reverse_id = str(uuid.uuid4())
            await conn.execute(
                """
                INSERT INTO knowledge_relations
                (id, tenant_id, from_entity_id, to_entity_id, relation_type, strength, evidence, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                """,
                reverse_id, tid, target_entity_id, source_entity_id,
                relation_type, strength, evidence_json,
            )
    # connection returned to pool automatically

    return {"id": relation_id, "relation_type": relation_type}


@mcp.tool()
async def find_relations(
    tenant_id: str = "",
    entity_id: str = "",
    relation_types: str = "[]",
    direction: str = "both",
    min_strength: float = 0.0,
    ctx: Context = None,
) -> list:
    """Find relationships for an entity or across a tenant.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        entity_id: Entity UUID to find relations for (optional — omit for all tenant relations).
        relation_types: JSON array of types to filter e.g. '["works_at","purchased"]'.
        direction: 'outgoing', 'incoming', or 'both' (default 'both').
        min_strength: Minimum relationship strength threshold (default 0.0).
        ctx: MCP request context (injected automatically).

    Returns:
        List of relationship dicts including source/target entity names.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    types = _parse_json(relation_types, [])

    async with (await _get_pool()).acquire() as conn:
        conditions = ["r.tenant_id = $1", "r.strength >= $2"]
        params: list = [tid, min_strength]
        idx = 3

        if entity_id:
            if direction == "outgoing":
                conditions.append(f"r.from_entity_id = ${idx}")
            elif direction == "incoming":
                conditions.append(f"r.to_entity_id = ${idx}")
            else:
                conditions.append(f"(r.from_entity_id = ${idx} OR r.to_entity_id = ${idx})")
            params.append(entity_id)
            idx += 1

        if types:
            placeholders = ",".join(f"${idx + i}" for i in range(len(types)))
            conditions.append(f"r.relation_type IN ({placeholders})")
            params.extend(types)

        where = " AND ".join(conditions)
        sql = f"""
            SELECT r.id, r.tenant_id, r.from_entity_id as source_entity_id,
                   r.to_entity_id as target_entity_id, r.relation_type,
                   r.strength, r.evidence, r.created_at,
                   s.name as source_name, s.entity_type as source_type,
                   t.name as target_name, t.entity_type as target_type
            FROM knowledge_relations r
            JOIN knowledge_entities s ON r.from_entity_id = s.id
            JOIN knowledge_entities t ON r.to_entity_id = t.id
            WHERE {where}
        """
        rows = await conn.fetch(sql, *params)
        return [_serialize_row(r) for r in rows]
    # connection returned to pool automatically


@mcp.tool()
async def get_neighborhood(
    entity_id: str,
    depth: int = 2,
    relation_types: str = "[]",
    entity_types: str = "[]",
    ctx: Context = None,
) -> dict:
    """Get the entity neighborhood graph up to N hops away.

    Args:
        entity_id: UUID of the center entity.
        depth: Number of hops to expand (default 2, max practical value is 3).
        relation_types: JSON array of relationship types to follow e.g. '["works_at"]'.
        entity_types: JSON array of entity types to include e.g. '["person","company"]'.
        ctx: MCP request context (injected automatically).

    Returns:
        dict with 'entities' list and 'relations' list forming the subgraph.
    """
    rel_types = _parse_json(relation_types, [])
    ent_types = _parse_json(entity_types, [])
    tid = resolve_tenant_id(ctx)

    async with (await _get_pool()).acquire() as conn:
        visited_entities: dict = {}
        all_relations: list = []

        async def expand(eid: str, current_depth: int):
            if current_depth > depth or eid in visited_entities:
                return

            entity = await conn.fetchrow(
                """
                SELECT id, tenant_id, name, entity_type, category, description,
                       confidence, created_at, updated_at
                FROM knowledge_entities WHERE id = $1 AND tenant_id = $2
                """,
                eid, tid,
            )
            if not entity:
                return

            entity_dict = _serialize_row(entity)
            if ent_types and entity_dict.get("entity_type") not in ent_types:
                return

            visited_entities[eid] = entity_dict

            # Fetch relations
            conditions = ["(r.from_entity_id = $1 OR r.to_entity_id = $1)"]
            params: list = [eid]
            if rel_types:
                placeholders = ",".join(f"${i + 2}" for i in range(len(rel_types)))
                conditions.append(f"r.relation_type IN ({placeholders})")
                params.extend(rel_types)

            where = " AND ".join(conditions)
            rels = await conn.fetch(
                f"""
                SELECT r.id, r.tenant_id, r.from_entity_id as source_entity_id,
                       r.to_entity_id as target_entity_id, r.relation_type,
                       r.strength, r.evidence, r.created_at,
                       s.name as source_name, t.name as target_name
                FROM knowledge_relations r
                JOIN knowledge_entities s ON r.from_entity_id = s.id
                JOIN knowledge_entities t ON r.to_entity_id = t.id
                WHERE {where}
                """,
                *params,
            )

            for rel in rels:
                rel_dict = _serialize_row(rel)
                all_relations.append(rel_dict)
                next_id = (
                    rel_dict["target_entity_id"]
                    if rel_dict["source_entity_id"] == eid
                    else rel_dict["source_entity_id"]
                )
                await expand(next_id, current_depth + 1)

        await expand(entity_id, 0)
        return {
            "entities": list(visited_entities.values()),
            "relations": all_relations,
        }
    # connection returned to pool automatically


@mcp.tool()
async def record_observation(
    observation_text: str,
    tenant_id: str = "",
    observation_type: str = "fact",
    source_type: str = "conversation",
    entity_id: str = "",
    source_platform: str = "",
    source_agent: str = "",
    source_channel: str = "",
    source_ref: str = "",
    ctx: Context = None,
) -> dict:
    """Record a raw observation for later entity extraction.

    Args:
        observation_text: The observation or fact to record.
        tenant_id: Tenant UUID (resolved from session if omitted).
        observation_type: Type — fact, opinion, question, or hypothesis (default 'fact').
        source_type: Source — conversation, dataset, or document (default 'conversation').
        entity_id: Optional entity UUID to link this observation to.
        source_platform: Platform that generated this observation (e.g. claude_code, gemini_cli, git).
        source_agent: Agent name/id that created this observation.
        source_channel: Channel where this was learned — chat, gmail, calendar, web (default '').
        source_ref: Human-readable reference e.g. 'gmail Mar 27' or 'chat Mar 28' (default '').
        ctx: MCP request context (injected automatically).

    Returns:
        dict with observation_id.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    obs_id = str(uuid.uuid4())
    eid = entity_id or None
    s_platform = source_platform or None
    s_agent = source_agent or None
    s_channel = source_channel or None
    s_ref = source_ref or None

    async with (await _get_pool()).acquire() as conn:
        pgvector = await _has_pgvector(conn)
        if pgvector:
            embedding = await _get_embedding(observation_text)
            if embedding:
                await conn.execute(
                    """
                    INSERT INTO knowledge_observations
                    (id, tenant_id, entity_id, observation_text, observation_type, source_type,
                     source_platform, source_agent, source_channel, source_ref, embedding)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::vector)
                    """,
                    obs_id, tid, eid, observation_text, observation_type, source_type,
                    s_platform, s_agent, s_channel, s_ref, str(embedding),
                )
                return {"observation_id": obs_id}

        await conn.execute(
            """
            INSERT INTO knowledge_observations
            (id, tenant_id, entity_id, observation_text, observation_type, source_type,
             source_platform, source_agent, source_channel, source_ref)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            obs_id, tid, eid, observation_text, observation_type, source_type,
            s_platform, s_agent, s_channel, s_ref,
        )
    # connection returned to pool automatically

    return {"observation_id": obs_id}


@mcp.tool()
async def get_entity_timeline(
    entity_id: str,
    include_relations: bool = True,
    ctx: Context = None,
) -> list:
    """Get chronological history of entity changes and interactions.

    Args:
        entity_id: UUID of the entity to retrieve history for.
        include_relations: Whether to include relationship changes (currently returns entity history).
        ctx: MCP request context (injected automatically).

    Returns:
        List of timeline events ordered by most recent first.
    """
    tid = resolve_tenant_id(ctx)

    async with (await _get_pool()).acquire() as conn:
        # Verify the entity belongs to the tenant before returning its history
        owner = await conn.fetchrow(
            "SELECT id FROM knowledge_entities WHERE id = $1 AND tenant_id = $2",
            entity_id, tid,
        )
        if not owner:
            return [{"error": "Entity not found"}]

        rows = await conn.fetch(
            """
            SELECT version, properties_snapshot, change_reason, changed_at
            FROM knowledge_entity_history
            WHERE entity_id = $1
            ORDER BY changed_at DESC
            """,
            entity_id,
        )
        return [_serialize_row(r) for r in rows]
    # connection returned to pool automatically


@mcp.tool()
async def search_knowledge(
    query: str,
    tenant_id: str = "",
    top_k: int = 5,
    filters: str = "{}",
    ctx: Context = None,
) -> list:
    """Semantic search across the knowledge base using vector similarity.

    Args:
        query: Natural language search query.
        tenant_id: Tenant UUID (resolved from session if omitted).
        top_k: Number of results to return (default 5).
        filters: Optional metadata filters as JSON string e.g. '{"entity_types": ["person"]}'.
        ctx: MCP request context (injected automatically).

    Returns:
        Ranked list of knowledge entities with relevance scores.
    """
    tid = resolve_tenant_id(ctx) or tenant_id
    filter_dict = _parse_json(filters, {})
    entity_types = filter_dict.get("entity_types") if filter_dict else None
    types_json = json.dumps(entity_types or [])

    # Delegate to find_entities which already handles vector + text fallback
    return await find_entities(
        query=query,
        tenant_id=tid,
        entity_types=types_json,
        limit=top_k,
        min_confidence=0.0,
        ctx=ctx,
    )


@mcp.tool()
async def get_git_history(
    tenant_id: str = "",
    path: str = "",
    days: int = 7,
    limit: int = 20,
    ctx: Context = None,
) -> list:
    """Get recent git commit history from the knowledge graph.

    Returns git_commit observations stored by the periodic git history
    extraction activity. Optionally scope to a file/directory path.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        path: Optional file/directory path to scope results (substring match).
        days: Look back N days (default 7).
        limit: Maximum number of results (default 20).
        ctx: MCP request context (injected automatically).

    Returns:
        List of git commit observations with text, type, and date.
    """
    tid = resolve_tenant_id(ctx) or tenant_id

    async with (await _get_pool()).acquire() as conn:
        conditions = [
            "tenant_id = $1",
            "observation_type = 'git_commit'",
            f"created_at > NOW() - INTERVAL '{int(days)} days'",
        ]
        params = [tid]
        idx = 2

        if path:
            conditions.append(f"observation_text ILIKE ${idx}")
            params.append(f"%{path}%")
            idx += 1

        where = " AND ".join(conditions)
        sql = f"""
            SELECT id, observation_text, observation_type, source_type, created_at
            FROM knowledge_observations
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT {int(limit)}
        """
        rows = await conn.fetch(sql, *params)
        return [_serialize_row(r) for r in rows]
    # connection returned to pool automatically


@mcp.tool()
async def get_pr_status(
    tenant_id: str = "",
    pr_number: int = 0,
    branch: str = "",
    ctx: Context = None,
) -> list:
    """Get PR status and review feedback from the knowledge graph.

    Returns git_pr observations stored when PRs are merged, closed, or reverted.
    Filter by PR number or branch name.

    Args:
        tenant_id: Tenant UUID (resolved from session if omitted).
        pr_number: PR number to look up (optional).
        branch: Branch name to filter by (optional, substring match).
        ctx: MCP request context (injected automatically).

    Returns:
        List of PR outcome observations with text, type, and date.
    """
    tid = resolve_tenant_id(ctx) or tenant_id

    async with (await _get_pool()).acquire() as conn:
        conditions = [
            "tenant_id = $1",
            "observation_type = 'git_pr'",
        ]
        params = [tid]
        idx = 2

        if pr_number > 0:
            conditions.append(f"observation_text ILIKE ${idx}")
            params.append(f"%PR #{pr_number}%")
            idx += 1
        elif branch:
            conditions.append(f"observation_text ILIKE ${idx}")
            params.append(f"%{branch}%")
            idx += 1

        where = " AND ".join(conditions)
        sql = f"""
            SELECT id, observation_text, observation_type, source_type, created_at
            FROM knowledge_observations
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT 10
        """
        rows = await conn.fetch(sql, *params)
        return [_serialize_row(r) for r in rows]
    # connection returned to pool automatically


@mcp.tool()
async def ask_knowledge_graph(
    natural_language_question: str,
    tenant_id: str = "",
    ctx: Context = None,
) -> dict:
    """Answer a question using knowledge graph traversal and context retrieval.

    Finds relevant entities and their relationships so an LLM can synthesize
    a grounded answer from the returned context.

    Args:
        natural_language_question: The question to answer.
        tenant_id: Tenant UUID (resolved from session if omitted).
        ctx: MCP request context (injected automatically).

    Returns:
        dict with question, relevant_entities, relevant_relations, and a synthesis note.
    """
    tid = resolve_tenant_id(ctx) or tenant_id

    entities = await find_entities(
        query=natural_language_question,
        tenant_id=tid,
        entity_types="[]",
        limit=5,
        min_confidence=0.0,
        ctx=ctx,
    )

    relations: list = []
    for entity in entities[:3]:
        rels = await find_relations(
            tenant_id=tid,
            entity_id=entity["id"],
            ctx=ctx,
        )
        relations.extend(rels[:5])

    return {
        "question": natural_language_question,
        "relevant_entities": entities,
        "relevant_relations": relations,
        "note": "Synthesize the answer from the entities and relations above.",
    }
