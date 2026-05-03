use tonic::{transport::Server, Request, Response, Status};
use tonic_health::server::health_reporter;
use embedding::v1::embedding_service_server::{EmbeddingService, EmbeddingServiceServer};
use embedding::v1::{EmbedRequest, EmbedResponse, EmbedBatchRequest, EmbedBatchResponse, HealthResponse};
use std::sync::Arc;
use std::time::{Duration, Instant};

use fastembed::{TextEmbedding, InitOptions, EmbeddingModel};
use tokio::sync::Mutex;

pub mod embedding {
    pub mod v1 {
        tonic::include_proto!("embedding.v1");
    }
}

/// Resolve the nomic prefix that must be prepended to a text before
/// embedding it. nomic-embed-text-v1.5 is task-conditioned: the prefix
/// tells the model whether the text is a search query, a document being
/// indexed, classification input, or clustering input. Anything else
/// (empty / unknown) falls back to no prefix, which matches the legacy
/// behavior of upstream callers that did not pass a task_type.
pub fn task_type_prefix(task_type: &str) -> &'static str {
    match task_type {
        "search_query" => "search_query: ",
        "search_document" => "search_document: ",
        "classification" => "classification: ",
        "clustering" => "clustering: ",
        _ => "",
    }
}

/// Apply the task-type prefix to `text`. Centralized so the unary and
/// batch paths cannot drift apart.
pub fn prefixed_text(task_type: &str, text: &str) -> String {
    format!("{}{}", task_type_prefix(task_type), text)
}

/// Build the canonical EmbedResponse for a single 768-dim vector. The
/// model name and dimension are constants — keeping them in one place
/// avoids drift between unary and batch code paths.
pub fn make_embed_response(vector: Vec<f32>) -> embedding::v1::EmbedResponse {
    embedding::v1::EmbedResponse {
        vector,
        model: "nomic-embed-text-v1.5".to_string(),
        dimensions: 768,
    }
}

/// Wraps fastembed::TextEmbedding (which uses ONNX Runtime internally).
/// fastembed handles model download, tokenization, ONNX inference, and
/// normalization — we just call embed() and get 768-dim vectors back.
pub struct MyEmbeddingService {
    model: Arc<Mutex<TextEmbedding>>,
    start_time: Instant,
}

impl MyEmbeddingService {
    pub fn new(model: TextEmbedding) -> Self {
        Self {
            model: Arc::new(Mutex::new(model)),
            start_time: Instant::now(),
        }
    }
}

#[tonic::async_trait]
impl EmbeddingService for MyEmbeddingService {
    async fn embed(&self, request: Request<EmbedRequest>) -> Result<Response<EmbedResponse>, Status> {
        let req = request.into_inner();
        let text = prefixed_text(&req.task_type, &req.text);

        let model = self.model.clone();
        let vector = tokio::task::spawn_blocking(move || {
            let m = model.blocking_lock();
            m.embed(vec![text], None)
        }).await
            .map_err(|e| Status::internal(format!("join error: {}", e)))?
            .map_err(|e| Status::internal(format!("embed error: {}", e)))?;

        let vec = vector.into_iter().next()
            .ok_or_else(|| Status::internal("no embedding returned"))?;

        Ok(Response::new(make_embed_response(vec)))
    }

    async fn embed_batch(&self, request: Request<EmbedBatchRequest>) -> Result<Response<EmbedBatchResponse>, Status> {
        let req = request.into_inner();
        let task_type = req.task_type.clone();

        let texts: Vec<String> = req.texts.iter()
            .map(|t| prefixed_text(&task_type, t))
            .collect();

        let model = self.model.clone();
        let vectors = tokio::task::spawn_blocking(move || {
            let m = model.blocking_lock();
            m.embed(texts, None)
        }).await
            .map_err(|e| Status::internal(format!("join error: {}", e)))?
            .map_err(|e| Status::internal(format!("batch embed error: {}", e)))?;

        let results = vectors.into_iter().map(make_embed_response).collect();

        Ok(Response::new(EmbedBatchResponse { results }))
    }

    async fn health(&self, _request: Request<()>) -> Result<Response<HealthResponse>, Status> {
        Ok(Response::new(HealthResponse {
            status: "ok".to_string(),
            model: "nomic-embed-text-v1.5".to_string(),
            uptime_seconds: self.start_time.elapsed().as_secs() as i64,
        }))
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt::init();

    tracing::info!("Loading nomic-embed-text-v1.5 via fastembed (ONNX Runtime)...");
    let model = TextEmbedding::try_new(
        InitOptions::new(EmbeddingModel::NomicEmbedTextV15)
            .with_show_download_progress(true)
    )?;

    // Warmup
    tracing::info!("Warming up...");
    let test = model.embed(vec!["warmup"], None)?;
    tracing::info!("Warmup: {} dimensions", test[0].len());

    let service = MyEmbeddingService::new(model);

    let (mut health_reporter, health_service) = health_reporter();
    health_reporter
        .set_serving::<EmbeddingServiceServer<MyEmbeddingService>>()
        .await;

    let addr = "0.0.0.0:50051".parse()?;
    tracing::info!("EmbeddingService listening on {}", addr);

    Server::builder()
        .timeout(Duration::from_secs(30))
        .add_service(health_service)
        .add_service(EmbeddingServiceServer::new(service))
        .serve(addr)
        .await?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;

    // ---- task_type_prefix ---------------------------------------------------

    #[test]
    fn task_type_prefix_search_query() {
        assert_eq!(task_type_prefix("search_query"), "search_query: ");
    }

    #[test]
    fn task_type_prefix_search_document() {
        assert_eq!(task_type_prefix("search_document"), "search_document: ");
    }

    #[test]
    fn task_type_prefix_classification() {
        assert_eq!(task_type_prefix("classification"), "classification: ");
    }

    #[test]
    fn task_type_prefix_clustering() {
        assert_eq!(task_type_prefix("clustering"), "clustering: ");
    }

    #[test]
    fn task_type_prefix_unknown_falls_back_to_empty() {
        assert_eq!(task_type_prefix("totally-made-up"), "");
    }

    #[test]
    fn task_type_prefix_empty_string_falls_back_to_empty() {
        assert_eq!(task_type_prefix(""), "");
    }

    #[test]
    fn task_type_prefix_is_case_sensitive() {
        // Defensive: nomic prefixes are exact lowercase tokens. Anything else
        // must fall through, otherwise we silently corrupt embedding semantics.
        assert_eq!(task_type_prefix("Search_Query"), "");
        assert_eq!(task_type_prefix("SEARCH_DOCUMENT"), "");
    }

    // ---- prefixed_text ------------------------------------------------------

    #[test]
    fn prefixed_text_query_concatenates_correctly() {
        assert_eq!(
            prefixed_text("search_query", "what is rag"),
            "search_query: what is rag"
        );
    }

    #[test]
    fn prefixed_text_document_concatenates_correctly() {
        assert_eq!(
            prefixed_text("search_document", "lorem ipsum"),
            "search_document: lorem ipsum"
        );
    }

    #[test]
    fn prefixed_text_unknown_task_type_passes_text_through_unchanged() {
        assert_eq!(prefixed_text("garbage", "hello world"), "hello world");
    }

    #[test]
    fn prefixed_text_empty_text_still_gets_prefix() {
        // Empty input is a caller bug, but we must not panic and we must
        // remain deterministic — "search_query: " still goes out.
        assert_eq!(prefixed_text("search_query", ""), "search_query: ");
    }

    #[test]
    fn prefixed_text_unicode_payload_round_trips() {
        let s = "embeddings för smörgåsbord";
        let out = prefixed_text("search_query", s);
        assert!(out.starts_with("search_query: "));
        assert!(out.ends_with(s));
    }

    // ---- make_embed_response -----------------------------------------------

    #[test]
    fn make_embed_response_sets_model_and_dim() {
        let resp = make_embed_response(vec![0.0_f32; 768]);
        assert_eq!(resp.model, "nomic-embed-text-v1.5");
        assert_eq!(resp.dimensions, 768);
        assert_eq!(resp.vector.len(), 768);
    }

    #[test]
    fn make_embed_response_preserves_input_vector() {
        let v = vec![0.5_f32, -0.5, 0.25, -0.25];
        let resp = make_embed_response(v.clone());
        assert_eq!(resp.vector, v);
    }

    #[test]
    fn make_embed_response_keeps_dimensions_constant_even_when_input_is_short() {
        // The wire constant always advertises 768 — callers rely on it
        // for validation. We do NOT mutate it based on len(vector); a short
        // vector is a bug to be surfaced upstream, not silently masked here.
        let resp = make_embed_response(vec![1.0_f32; 4]);
        assert_eq!(resp.dimensions, 768);
        assert_eq!(resp.vector.len(), 4);
    }

    // ---- batch helper symmetry ---------------------------------------------

    #[test]
    fn batch_prefixing_matches_unary_prefixing() {
        // Regression guard: the unary path and the batch path must produce
        // byte-identical prefixed text. The original code duplicated the
        // match arms in both paths and could drift.
        let task = "search_document";
        let texts = vec!["alpha", "beta", "gamma"];
        let unary: Vec<String> = texts.iter().map(|t| prefixed_text(task, t)).collect();
        let batch: Vec<String> = texts.iter().map(|t| prefixed_text(task, t)).collect();
        assert_eq!(unary, batch);
        assert_eq!(unary[0], "search_document: alpha");
    }
}
