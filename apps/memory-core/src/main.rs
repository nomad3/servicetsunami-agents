use tonic::{transport::Server, Request, Response, Status};
use memory::v1::memory_core_server::{MemoryCore, MemoryCoreServer};
use memory::v1::{RecallRequest, RecallResponse, Entity, Observation, Relation, EpisodeSummary, RecordObservationRequest, RecordCommitmentRequest, IngestRequest, IngestResponse};
use sqlx::postgres::PgPoolOptions;
use sqlx::{Row, FromRow};
use uuid::Uuid;
use std::sync::Arc;

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
}

impl MyMemoryCore {
    pub fn new(pool: sqlx::PgPool, embedding_url: String) -> Self {
        Self { pool, embedding_url }
    }

    async fn get_embedding(&self, text: &str, task_type: &str) -> Result<Vec<f32>, Status> {
        let mut client = EmbeddingServiceClient::connect(self.embedding_url.clone())
            .await
            .map_err(|e| Status::internal(format!("Failed to connect to embedding service: {}", e)))?;

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

        Ok(Response::new(RecallResponse {
            entities,
            observations,
            relations,
            episodes,
        }))
    }

    async fn record_observation(&self, _request: Request<RecordObservationRequest>) -> Result<Response<()>, Status> {
        Ok(Response::new(()))
    }

    async fn record_commitment(&self, _request: Request<RecordCommitmentRequest>) -> Result<Response<()>, Status> {
        Ok(Response::new(()))
    }

    async fn ingest_events(&self, _request: Request<IngestRequest>) -> Result<Response<IngestResponse>, Status> {
        Ok(Response::new(IngestResponse { processed: 0 }))
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt::init();

    let database_url = std::env::var("DATABASE_URL")
        .unwrap_or_else(|_| "postgresql://postgres:postgres@localhost:5432/servicetsunami".to_string());
    
    let embedding_url = std::env::var("EMBEDDING_SERVICE_URL")
        .unwrap_or_else(|_| "http://localhost:50051".to_string());

    println!("Connecting to database...");
    let pool = PgPoolOptions::new()
        .max_connections(5)
        .connect(&database_url)
        .await?;

    let service = MyMemoryCore::new(pool, embedding_url);
    let addr = "0.0.0.0:50052".parse()?;
    println!("MemoryCore listening on {}", addr);

    Server::builder()
        .add_service(MemoryCoreServer::new(service))
        .serve(addr)
        .await?;

    Ok(())
}
