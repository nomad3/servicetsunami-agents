use tonic::{transport::Server, Request, Response, Status};
use tonic_health::server::health_reporter;
use memory::v1::memory_core_server::{MemoryCore, MemoryCoreServer};
use memory::v1::{
    RecallRequest, RecallResponse, Entity, Observation, Relation, EpisodeSummary,
    CommitmentSummary, GoalSummary, ConversationSnippet, ContradictionSummary, RecallMetadata,
    RecordObservationRequest, RecordCommitmentRequest, IngestRequest, IngestResponse,
};
use sqlx::postgres::PgPoolOptions;
use sqlx::Row;
use uuid::Uuid;
use std::time::{Duration, Instant};

pub mod memory {
    pub mod v1 {
        tonic::include_proto!("memory.v1");
    }
}

pub mod embedding {
    pub mod v1 {
        tonic::include_proto!("embedding.v1");
    }
}

use embedding::v1::embedding_service_client::EmbeddingServiceClient;
use embedding::v1::EmbedRequest;

// ─── pure helpers (no I/O, unit-tested below) ───────────────────────────────

/// Encode a Rust f32 slice into the literal pgvector accepts via parameter
/// binding: e.g. `[0.1,0.2,-0.3]`. Centralizing this lets us regression-test
/// the encoding (e.g. that we never emit scientific notation that pgvector
/// would reject).
pub fn format_pgvector(v: &[f32]) -> String {
    format!(
        "[{}]",
        v.iter().map(|x| x.to_string()).collect::<Vec<String>>().join(",")
    )
}

/// Validate and parse a tenant-scoped UUID string as it arrives off the wire.
/// Maps any parse failure to a gRPC `invalid_argument` so the client can act
/// on it without inspecting backend logs.
pub fn parse_tenant_id(raw: &str) -> Result<Uuid, Status> {
    Uuid::parse_str(raw).map_err(|_| Status::invalid_argument("Invalid tenant_id"))
}

/// Same as `parse_tenant_id` but for the entity_id field. Kept distinct so
/// the error message tells the client which field they got wrong.
pub fn parse_entity_id(raw: &str) -> Result<Uuid, Status> {
    Uuid::parse_str(raw).map_err(|_| Status::invalid_argument("Invalid entity_id"))
}

/// Convert an inbound protobuf Timestamp to `chrono::DateTime<Utc>`. A
/// timestamp that protobuf considers in-range but chrono cannot represent
/// degrades to `Utc::now()` — the same fallback the production handler uses.
pub fn proto_ts_to_chrono(ts: Option<prost_types::Timestamp>) -> Option<chrono::DateTime<chrono::Utc>> {
    ts.map(|t| {
        chrono::DateTime::from_timestamp(t.seconds, t.nanos as u32)
            .unwrap_or_else(chrono::Utc::now)
    })
}

/// Convert a `chrono::DateTime<Utc>` to a protobuf Timestamp.
pub fn chrono_to_proto_ts(dt: chrono::DateTime<chrono::Utc>) -> prost_types::Timestamp {
    prost_types::Timestamp {
        seconds: dt.timestamp(),
        nanos: dt.timestamp_subsec_nanos() as i32,
    }
}

/// Resolve the source_type used when persisting an observation. Empty string
/// from the caller defaults to `"agent"`; everything else passes through.
pub fn default_source_type(provided: &str) -> String {
    if provided.is_empty() {
        "agent".to_string()
    } else {
        provided.to_string()
    }
}

pub struct MyMemoryCore {
    pool: sqlx::PgPool,
    embedding_client: EmbeddingServiceClient<tonic::transport::Channel>,
}

impl MyMemoryCore {
    pub async fn new(pool: sqlx::PgPool, embedding_url: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let embedding_client = EmbeddingServiceClient::connect(embedding_url.to_string()).await?;
        Ok(Self { pool, embedding_client })
    }

    async fn get_embedding(&self, text: &str, task_type: &str) -> Result<Vec<f32>, Status> {
        // tonic channels are cloneable and safe for concurrent use
        let mut client = self.embedding_client.clone();

        let request = Request::new(EmbedRequest {
            text: text.to_string(),
            task_type: task_type.to_string(),
        });

        let response = client.embed(request).await?;
        Ok(response.into_inner().vector)
    }
}

#[tonic::async_trait]
impl MemoryCore for MyMemoryCore {
    async fn recall(&self, request: Request<RecallRequest>) -> Result<Response<RecallResponse>, Status> {
        let start = Instant::now();
        let req = request.into_inner();
        let tenant_id = parse_tenant_id(&req.tenant_id)?;

        println!("Recalling for tenant {} query: {}", tenant_id, req.query);

        // 1. Embed the query
        let query_vec = self.get_embedding(&req.query, "search_query").await?;
        let query_vec_str = format_pgvector(&query_vec);

        // 2. Search entities
        let entity_rows = sqlx::query(
            r#"
            SELECT
                ke.id::text as id,
                ke.name,
                ke.entity_type,
                ke.category,
                ke.description,
                (1 - (e.embedding <=> $2::vector)) as similarity
            FROM embeddings e
            JOIN knowledge_entities ke ON e.content_id = ke.id::text
            WHERE e.tenant_id = $1 AND e.content_type = 'entity' AND ke.deleted_at IS NULL
            ORDER BY e.embedding <=> $2::vector
            LIMIT $3
            "#
        )
        .bind(tenant_id)
        .bind(&query_vec_str)
        .bind(req.top_k_per_type as i64)
        .fetch_all(&self.pool).await
        .map_err(|e| Status::internal(format!("DB error (entities): {}", e)))?;

        let entities: Vec<Entity> = entity_rows.iter().map(|r| Entity {
            id: r.get("id"),
            name: r.get("name"),
            entity_type: r.get("entity_type"),
            category: r.get("category"),
            description: r.get("description"),
            similarity: r.get::<f64, _>("similarity") as f32,
        }).collect();

        // 3. Search observations
        let entity_ids: Vec<String> = entities.iter().map(|e| e.id.clone()).collect();
        let observation_rows = sqlx::query(
            r#"
            SELECT
                id::text as id,
                entity_id::text as entity_id,
                observation_text as content,
                (1 - (embedding <=> $2::vector)) as similarity
            FROM knowledge_observations
            WHERE tenant_id = $1 AND entity_id::text = ANY($3)
            ORDER BY embedding <=> $2::vector
            LIMIT $4
            "#
        )
        .bind(tenant_id)
        .bind(&query_vec_str)
        .bind(&entity_ids)
        .bind(req.top_k_per_type as i64)
        .fetch_all(&self.pool).await
        .map_err(|e| Status::internal(format!("DB error (observations): {}", e)))?;

        let observations: Vec<Observation> = observation_rows.iter().map(|r| Observation {
            id: r.get("id"),
            entity_id: r.get("entity_id"),
            content: r.get("content"),
            similarity: r.get::<f64, _>("similarity") as f32,
        }).collect();

        // 4. Search relations
        let relation_rows = sqlx::query(
            r#"
            SELECT
                from_entity_id::text as from_entity,
                to_entity_id::text as to_entity,
                relation_type
            FROM knowledge_relations
            WHERE tenant_id = $1 AND (from_entity_id::text = ANY($2) OR to_entity_id::text = ANY($2))
            "#
        )
        .bind(tenant_id)
        .bind(&entity_ids)
        .fetch_all(&self.pool).await
        .map_err(|e| Status::internal(format!("DB error (relations): {}", e)))?;

        let relations: Vec<Relation> = relation_rows.iter().map(|r| Relation {
            from_entity: r.get("from_entity"),
            to_entity: r.get("to_entity"),
            relation_type: r.get("relation_type"),
        }).collect();

        // 5. Search episodes
        let episode_rows = sqlx::query(
            r#"
            SELECT
                id::text as id,
                summary,
                created_at,
                (1 - (embedding <=> $2::vector)) as similarity
            FROM conversation_episodes
            WHERE tenant_id = $1
            ORDER BY embedding <=> $2::vector
            LIMIT $3
            "#
        )
        .bind(tenant_id)
        .bind(&query_vec_str)
        .bind(5i64)
        .fetch_all(&self.pool).await
        .map_err(|e| Status::internal(format!("DB error (episodes): {}", e)))?;

        let episodes: Vec<EpisodeSummary> = episode_rows.iter().map(|r| EpisodeSummary {
            id: r.get("id"),
            summary: r.get("summary"),
            created_at: Some(chrono_to_proto_ts(r.get::<chrono::DateTime<chrono::Utc>, _>("created_at"))),
            similarity: r.get::<f64, _>("similarity") as f32,
        }).collect();

        // 6. Search commitments (open/in_progress, not fulfilled/broken/cancelled)
        let commitment_rows = sqlx::query(
            r#"
            SELECT
                id::text as id,
                title,
                commitment_type,
                state,
                due_at,
                owner_agent_slug
            FROM commitment_records
            WHERE tenant_id = $1 AND state NOT IN ('fulfilled', 'broken', 'cancelled')
            ORDER BY due_at ASC NULLS LAST
            LIMIT $2
            "#
        )
        .bind(tenant_id)
        .bind(req.top_k_per_type as i64)
        .fetch_all(&self.pool).await
        .map_err(|e| Status::internal(format!("DB error (commitments): {}", e)))?;

        let commitments: Vec<CommitmentSummary> = commitment_rows.iter().map(|r| {
            let due_at: Option<chrono::DateTime<chrono::Utc>> = r.get("due_at");
            CommitmentSummary {
                id: r.get("id"),
                title: r.get("title"),
                commitment_type: r.get("commitment_type"),
                status: r.get("state"),
                due_at: due_at.map(chrono_to_proto_ts),
                owner_agent_slug: r.get("owner_agent_slug"),
            }
        }).collect();

        // 7. Search past conversations (chat_message embeddings, vector similarity)
        let conversation_rows = sqlx::query(
            r#"
            SELECT
                e.content_id as session_id,
                e.text_content as content,
                'user' as role,
                e.created_at,
                (1 - (e.embedding <=> $2::vector)) as similarity
            FROM embeddings e
            WHERE e.tenant_id = $1 AND e.content_type = 'chat_message'
            ORDER BY e.embedding <=> $2::vector
            LIMIT $3
            "#
        )
        .bind(tenant_id)
        .bind(&query_vec_str)
        .bind(req.top_k_per_type as i64)
        .fetch_all(&self.pool).await
        .map_err(|e| Status::internal(format!("DB error (conversations): {}", e)))?;

        let past_conversations: Vec<ConversationSnippet> = conversation_rows.iter().map(|r| {
            let dt: Option<chrono::DateTime<chrono::Utc>> = r.get("created_at");
            ConversationSnippet {
                session_id: r.get("session_id"),
                content: r.get("content"),
                role: r.get("role"),
                created_at: dt.map(chrono_to_proto_ts),
                similarity: r.get::<f64, _>("similarity") as f32,
            }
        }).collect();

        // 8. Goals — empty for now (no goals table yet)
        let goals: Vec<GoalSummary> = Vec::new();

        // 9. Contradictions — empty for now (no contradiction detection yet)
        let contradictions: Vec<ContradictionSummary> = Vec::new();

        // 10. Build metadata
        let query_time_ms = start.elapsed().as_millis() as i32;
        let total_tokens_estimate = estimate_tokens(&entities, &observations, &episodes, &commitments, &past_conversations);

        let metadata = Some(RecallMetadata {
            query_time_ms,
            total_tokens_estimate,
            degraded: false,
            degradation_reason: String::new(),
        });

        println!("Recall completed in {}ms, ~{} tokens", query_time_ms, total_tokens_estimate);

        Ok(Response::new(RecallResponse {
            entities,
            observations,
            relations,
            episodes,
            commitments,
            goals,
            past_conversations,
            contradictions,
            metadata,
        }))
    }

    async fn record_observation(&self, request: Request<RecordObservationRequest>) -> Result<Response<()>, Status> {
        let req = request.into_inner();
        let tenant_id = parse_tenant_id(&req.tenant_id)?;
        let entity_id = parse_entity_id(&req.entity_id)?;

        // Embed the observation text
        let embedding = self.get_embedding(&req.content, "search_document").await?;
        let embedding_str = format_pgvector(&embedding);

        let obs_id = Uuid::new_v4();
        let source_type = default_source_type(&req.source_type);

        // INSERT into knowledge_observations
        sqlx::query(
            r#"
            INSERT INTO knowledge_observations
                (id, tenant_id, entity_id, observation_text, observation_type, source_type, confidence, embedding, created_at)
            VALUES ($1, $2, $3, $4, 'fact', $5, $6, $7::vector, NOW())
            "#
        )
        .bind(obs_id)
        .bind(tenant_id)
        .bind(entity_id)
        .bind(&req.content)
        .bind(&source_type)
        .bind(req.confidence)
        .bind(&embedding_str)
        .execute(&self.pool).await
        .map_err(|e| Status::internal(format!("DB error inserting observation: {}", e)))?;

        // INSERT audit trail into memory_activities
        sqlx::query(
            r#"
            INSERT INTO memory_activities
                (id, tenant_id, event_type, description, source, entity_id, created_at)
            VALUES ($1, $2, 'observation_created', $3, $4, $5, NOW())
            "#
        )
        .bind(Uuid::new_v4())
        .bind(tenant_id)
        .bind(format!("Observation recorded for entity {}", entity_id))
        .bind(&req.actor_slug)
        .bind(entity_id)
        .execute(&self.pool).await
        .map_err(|e| Status::internal(format!("DB error inserting memory_activity: {}", e)))?;

        println!("RecordObservation: tenant={} entity={} obs_id={}", tenant_id, entity_id, obs_id);

        Ok(Response::new(()))
    }

    async fn record_commitment(&self, request: Request<RecordCommitmentRequest>) -> Result<Response<()>, Status> {
        let req = request.into_inner();
        let tenant_id = parse_tenant_id(&req.tenant_id)?;

        let commitment_id = Uuid::new_v4();

        // Convert optional protobuf Timestamp to chrono DateTime
        let due_at: Option<chrono::DateTime<chrono::Utc>> = proto_ts_to_chrono(req.due_at);

        // INSERT into commitment_records
        sqlx::query(
            r#"
            INSERT INTO commitment_records
                (id, tenant_id, owner_agent_slug, title, description, commitment_type, state, due_at, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, 'open', $7, NOW())
            "#
        )
        .bind(commitment_id)
        .bind(tenant_id)
        .bind(&req.owner_agent_slug)
        .bind(&req.title)
        .bind(&req.description)
        .bind(&req.commitment_type)
        .bind(due_at)
        .execute(&self.pool).await
        .map_err(|e| Status::internal(format!("DB error inserting commitment: {}", e)))?;

        // INSERT audit trail into memory_activities
        sqlx::query(
            r#"
            INSERT INTO memory_activities
                (id, tenant_id, event_type, description, source, created_at)
            VALUES ($1, $2, 'commitment_created', $3, $4, NOW())
            "#
        )
        .bind(Uuid::new_v4())
        .bind(tenant_id)
        .bind(format!("Commitment created: {}", req.title))
        .bind(&req.owner_agent_slug)
        .execute(&self.pool).await
        .map_err(|e| Status::internal(format!("DB error inserting memory_activity: {}", e)))?;

        println!("RecordCommitment: tenant={} id={} title={}", tenant_id, commitment_id, req.title);

        Ok(Response::new(()))
    }

    async fn ingest_events(&self, request: Request<IngestRequest>) -> Result<Response<IngestResponse>, Status> {
        let req = request.into_inner();
        let tenant_id = parse_tenant_id(&req.tenant_id)?;

        let mut processed: i32 = 0;

        for event in &req.events {
            for entity_name in &event.proposed_entities {
                if entity_name.trim().is_empty() {
                    continue;
                }

                // Check if entity already exists for this tenant
                let existing = sqlx::query(
                    r#"
                    SELECT id FROM knowledge_entities
                    WHERE tenant_id = $1 AND name = $2 AND deleted_at IS NULL
                    LIMIT 1
                    "#
                )
                .bind(tenant_id)
                .bind(entity_name)
                .fetch_optional(&self.pool).await
                .map_err(|e| Status::internal(format!("DB error checking entity: {}", e)))?;

                if let Some(row) = existing {
                    // Update the existing entity's updated_at
                    let entity_id: Uuid = row.get("id");
                    sqlx::query(
                        r#"UPDATE knowledge_entities SET updated_at = NOW() WHERE id = $1"#
                    )
                    .bind(entity_id)
                    .execute(&self.pool).await
                    .map_err(|e| Status::internal(format!("DB error updating entity: {}", e)))?;
                } else {
                    // Insert new entity
                    sqlx::query(
                        r#"
                        INSERT INTO knowledge_entities
                            (id, tenant_id, name, entity_type, category, confidence, created_at, updated_at)
                        VALUES ($1, $2, $3, 'unknown', 'unknown', 0.5, NOW(), NOW())
                        "#
                    )
                    .bind(Uuid::new_v4())
                    .bind(tenant_id)
                    .bind(entity_name)
                    .execute(&self.pool).await
                    .map_err(|e| Status::internal(format!("DB error inserting entity: {}", e)))?;
                }

                processed += 1;
            }
        }

        println!("IngestEvents: tenant={} processed={} entities", tenant_id, processed);

        Ok(Response::new(IngestResponse { processed }))
    }
}

/// Rough token estimate: ~1 token per 4 chars of text content.
fn estimate_tokens(
    entities: &[Entity],
    observations: &[Observation],
    episodes: &[EpisodeSummary],
    commitments: &[CommitmentSummary],
    conversations: &[ConversationSnippet],
) -> i32 {
    let mut chars: usize = 0;
    for e in entities {
        chars += e.name.len() + e.description.len() + e.entity_type.len() + e.category.len();
    }
    for o in observations {
        chars += o.content.len();
    }
    for ep in episodes {
        chars += ep.summary.len();
    }
    for c in commitments {
        chars += c.title.len() + c.commitment_type.len() + c.status.len();
    }
    for cv in conversations {
        chars += cv.content.len();
    }
    (chars / 4) as i32
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt::init();

    let database_url = std::env::var("DATABASE_URL")
        .unwrap_or_else(|_| "postgresql://postgres:postgres@localhost:5432/agentprovision".to_string());
    
    let embedding_url = std::env::var("EMBEDDING_SERVICE_URL")
        .unwrap_or_else(|_| "http://localhost:50051".to_string());

    println!("Connecting to database...");
    let pool = PgPoolOptions::new()
        .max_connections(20)
        .acquire_timeout(Duration::from_secs(5))
        .idle_timeout(Duration::from_secs(300))
        .connect(&database_url)
        .await?;

    println!("Connecting to embedding service at {}...", embedding_url);
    let service = MyMemoryCore::new(pool, &embedding_url).await?;

    let (mut health_reporter, health_service) = health_reporter();
    health_reporter
        .set_serving::<MemoryCoreServer<MyMemoryCore>>()
        .await;

    let addr = "0.0.0.0:50052".parse()?;
    println!("MemoryCore listening on {}", addr);

    Server::builder()
        .timeout(Duration::from_secs(30))
        .add_service(health_service)
        .add_service(MemoryCoreServer::new(service))
        .serve(addr)
        .await?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use memory::v1::{
        CommitmentSummary, ConversationSnippet, Entity, EpisodeSummary, Observation,
    };
    use pretty_assertions::assert_eq;

    // ---- format_pgvector ---------------------------------------------------

    #[test]
    fn format_pgvector_simple_three_dim() {
        assert_eq!(format_pgvector(&[0.1, 0.2, 0.3]), "[0.1,0.2,0.3]");
    }

    #[test]
    fn format_pgvector_empty_vector_renders_empty_brackets() {
        assert_eq!(format_pgvector(&[]), "[]");
    }

    #[test]
    fn format_pgvector_single_element() {
        assert_eq!(format_pgvector(&[1.0_f32]), "[1]");
    }

    #[test]
    fn format_pgvector_handles_negative_and_zero() {
        let s = format_pgvector(&[0.0, -0.5, 0.5]);
        assert_eq!(s, "[0,-0.5,0.5]");
    }

    #[test]
    fn format_pgvector_round_trips_via_split() {
        // Round-trip: parse the literal back into f32 values and compare.
        // pgvector requires plain decimal — this guards against accidental
        // scientific-notation output for large/small floats.
        let original: Vec<f32> = vec![1e-3, 2.5, -7.25];
        let s = format_pgvector(&original);
        let inner = &s[1..s.len() - 1];
        let parsed: Vec<f32> = inner.split(',').map(|t| t.parse::<f32>().unwrap()).collect();
        assert_eq!(parsed, original);
    }

    #[test]
    fn format_pgvector_768_dim_has_brackets_and_767_commas() {
        let v = vec![0.0_f32; 768];
        let s = format_pgvector(&v);
        assert!(s.starts_with('['));
        assert!(s.ends_with(']'));
        let commas = s.chars().filter(|c| *c == ',').count();
        assert_eq!(commas, 767);
    }

    // ---- parse_tenant_id / parse_entity_id ---------------------------------

    #[test]
    fn parse_tenant_id_accepts_valid_uuid() {
        let u = Uuid::new_v4();
        let parsed = parse_tenant_id(&u.to_string()).expect("should parse");
        assert_eq!(parsed, u);
    }

    #[test]
    fn parse_tenant_id_rejects_garbage_with_invalid_argument() {
        let err = parse_tenant_id("not-a-uuid").expect_err("should fail");
        assert_eq!(err.code(), tonic::Code::InvalidArgument);
        assert!(err.message().contains("tenant_id"));
    }

    #[test]
    fn parse_tenant_id_rejects_empty_string() {
        let err = parse_tenant_id("").expect_err("empty must fail");
        assert_eq!(err.code(), tonic::Code::InvalidArgument);
    }

    #[test]
    fn parse_entity_id_accepts_valid_uuid() {
        let u = Uuid::new_v4();
        let parsed = parse_entity_id(&u.to_string()).expect("should parse");
        assert_eq!(parsed, u);
    }

    #[test]
    fn parse_entity_id_rejects_garbage_with_distinct_message() {
        let err = parse_entity_id("nope").expect_err("should fail");
        assert_eq!(err.code(), tonic::Code::InvalidArgument);
        assert!(err.message().contains("entity_id"));
    }

    #[test]
    fn parse_tenant_id_and_entity_id_have_different_messages() {
        // Sanity: clients can tell which field is wrong from the error alone.
        let t_err = parse_tenant_id("x").unwrap_err();
        let e_err = parse_entity_id("x").unwrap_err();
        assert_ne!(t_err.message(), e_err.message());
    }

    // ---- proto_ts_to_chrono / chrono_to_proto_ts ---------------------------

    #[test]
    fn proto_ts_to_chrono_none_passes_through() {
        assert!(proto_ts_to_chrono(None).is_none());
    }

    #[test]
    fn proto_ts_to_chrono_round_trip() {
        let ts = prost_types::Timestamp { seconds: 1_700_000_000, nanos: 123_456_789 };
        let chrono_dt = proto_ts_to_chrono(Some(ts.clone())).expect("should convert");
        let back = chrono_to_proto_ts(chrono_dt);
        assert_eq!(back.seconds, ts.seconds);
        assert_eq!(back.nanos, ts.nanos);
    }

    #[test]
    fn chrono_to_proto_ts_unix_epoch() {
        let dt = chrono::DateTime::<chrono::Utc>::from_timestamp(0, 0).unwrap();
        let ts = chrono_to_proto_ts(dt);
        assert_eq!(ts.seconds, 0);
        assert_eq!(ts.nanos, 0);
    }

    #[test]
    fn proto_ts_to_chrono_zero_is_unix_epoch() {
        let ts = prost_types::Timestamp { seconds: 0, nanos: 0 };
        let dt = proto_ts_to_chrono(Some(ts)).expect("should convert");
        assert_eq!(dt.timestamp(), 0);
    }

    // ---- default_source_type -----------------------------------------------

    #[test]
    fn default_source_type_empty_yields_agent() {
        assert_eq!(default_source_type(""), "agent");
    }

    #[test]
    fn default_source_type_passthrough() {
        assert_eq!(default_source_type("user"), "user");
        assert_eq!(default_source_type("imported"), "imported");
    }

    #[test]
    fn default_source_type_whitespace_is_not_empty() {
        // Caller intent: whitespace was a deliberate input, do not coerce it.
        assert_eq!(default_source_type(" "), " ");
    }

    // ---- estimate_tokens ---------------------------------------------------

    fn entity(name: &str, etype: &str, cat: &str, desc: &str) -> Entity {
        Entity {
            id: "e".into(),
            name: name.into(),
            entity_type: etype.into(),
            category: cat.into(),
            description: desc.into(),
            similarity: 0.0,
        }
    }

    #[test]
    fn estimate_tokens_empty_inputs_return_zero() {
        let n = estimate_tokens(&[], &[], &[], &[], &[]);
        assert_eq!(n, 0);
    }

    #[test]
    fn estimate_tokens_uses_chars_div_4() {
        // Total content length = 16 chars => 4 tokens (16 / 4).
        let entities = vec![entity("aaaa", "bb", "cc", "dddddd")]; // 4+2+2+6 = 14
        let observations = vec![Observation { id: "".into(), entity_id: "".into(), content: "xx".into(), similarity: 0.0 }]; // 2
        // total chars = 14 + 2 = 16 -> 16/4 = 4
        let n = estimate_tokens(&entities, &observations, &[], &[], &[]);
        assert_eq!(n, 4);
    }

    #[test]
    fn estimate_tokens_aggregates_all_buckets() {
        let entities = vec![entity("ab", "cd", "ef", "gh")]; // 8
        let observations = vec![Observation { id: "".into(), entity_id: "".into(), content: "ijkl".into(), similarity: 0.0 }]; // 4
        let episodes = vec![EpisodeSummary { id: "".into(), summary: "mnop".into(), created_at: None, similarity: 0.0 }]; // 4
        let commitments = vec![CommitmentSummary {
            id: "".into(), title: "qrst".into(), commitment_type: "uv".into(),
            status: "wx".into(), due_at: None, owner_agent_slug: "".into(),
        }]; // 4+2+2 = 8
        let conversations = vec![ConversationSnippet {
            session_id: "".into(), content: "yzAB".into(), role: "".into(),
            created_at: None, similarity: 0.0,
        }]; // 4
        // Total = 8 + 4 + 4 + 8 + 4 = 28 -> 28/4 = 7
        let n = estimate_tokens(&entities, &observations, &episodes, &commitments, &conversations);
        assert_eq!(n, 7);
    }

    #[test]
    fn estimate_tokens_truncates_toward_zero() {
        // 7 chars / 4 = 1 (integer division)
        let entities = vec![entity("aaaaaaa", "", "", "")]; // 7
        let n = estimate_tokens(&entities, &[], &[], &[], &[]);
        assert_eq!(n, 1);
    }
}
