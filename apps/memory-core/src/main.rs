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
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

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

pub struct MyMemoryCore {
    pool: sqlx::PgPool,
    embedding_url: String,
    embedding_client: Arc<Mutex<Option<EmbeddingServiceClient<tonic::transport::Channel>>>>,
}

impl MyMemoryCore {
    pub fn new(pool: sqlx::PgPool, embedding_url: String) -> Self {
        Self {
            pool,
            embedding_url,
            embedding_client: Arc::new(Mutex::new(None)),
        }
    }

    async fn get_embedding(&self, text: &str, task_type: &str) -> Result<Vec<f32>, Status> {
        let mut guard = self.embedding_client.lock().await;
        if guard.is_none() {
            let client = EmbeddingServiceClient::connect(self.embedding_url.clone())
                .await
                .map_err(|e| Status::internal(format!("Failed to connect to embedding service: {}", e)))?;
            *guard = Some(client);
        }
        let client = guard.as_mut().unwrap();

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
        let tenant_id = Uuid::parse_str(&req.tenant_id)
            .map_err(|_| Status::invalid_argument("Invalid tenant_id"))?;

        println!("Recalling for tenant {} query: {}", tenant_id, req.query);

        // 1. Embed the query
        let query_vec = self.get_embedding(&req.query, "search_query").await?;
        let query_vec_str = format!("[{}]", query_vec.iter().map(|v| v.to_string()).collect::<Vec<String>>().join(","));

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
            created_at: Some(prost_types::Timestamp {
                seconds: r.get::<chrono::DateTime<chrono::Utc>, _>("created_at").timestamp(),
                nanos: r.get::<chrono::DateTime<chrono::Utc>, _>("created_at").timestamp_subsec_nanos() as i32,
            }),
            similarity: r.get::<f64, _>("similarity") as f32,
        }).collect();

        // 6. Search commitments (active/pending, not completed)
        let commitment_rows = sqlx::query(
            r#"
            SELECT
                id::text as id,
                title,
                commitment_type,
                status,
                due_at,
                owner_agent_slug
            FROM commitment_records
            WHERE tenant_id = $1 AND status != 'completed'
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
                status: r.get("status"),
                due_at: due_at.map(|dt| prost_types::Timestamp {
                    seconds: dt.timestamp(),
                    nanos: dt.timestamp_subsec_nanos() as i32,
                }),
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
            ConversationSnippet {
                session_id: r.get("session_id"),
                content: r.get("content"),
                role: r.get("role"),
                created_at: None, // embeddings table does not have created_at
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
        let tenant_id = Uuid::parse_str(&req.tenant_id)
            .map_err(|_| Status::invalid_argument("Invalid tenant_id"))?;
        let entity_id = Uuid::parse_str(&req.entity_id)
            .map_err(|_| Status::invalid_argument("Invalid entity_id"))?;

        // Embed the observation text
        let embedding = self.get_embedding(&req.content, "search_document").await?;
        let embedding_str = format!(
            "[{}]",
            embedding.iter().map(|v| v.to_string()).collect::<Vec<String>>().join(",")
        );

        let obs_id = Uuid::new_v4();
        let source_type = if req.source_type.is_empty() { "agent".to_string() } else { req.source_type.clone() };

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
        let tenant_id = Uuid::parse_str(&req.tenant_id)
            .map_err(|_| Status::invalid_argument("Invalid tenant_id"))?;

        let commitment_id = Uuid::new_v4();

        // Convert optional protobuf Timestamp to chrono DateTime
        let due_at: Option<chrono::DateTime<chrono::Utc>> = req.due_at.map(|ts| {
            chrono::DateTime::from_timestamp(ts.seconds, ts.nanos as u32)
                .unwrap_or_else(|| chrono::Utc::now())
        });

        // INSERT into commitment_records
        sqlx::query(
            r#"
            INSERT INTO commitment_records
                (id, tenant_id, owner_agent_slug, title, description, commitment_type, status, due_at, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, 'active', $7, NOW())
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
        let tenant_id = Uuid::parse_str(&req.tenant_id)
            .map_err(|_| Status::invalid_argument("Invalid tenant_id"))?;

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

    let service = MyMemoryCore::new(pool, embedding_url);

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
