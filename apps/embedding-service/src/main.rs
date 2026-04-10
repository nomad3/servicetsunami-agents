use tonic::{transport::Server, Request, Response, Status};
use tonic_health::server::health_reporter;
use embedding::v1::embedding_service_server::{EmbeddingService, EmbeddingServiceServer};
use embedding::v1::{EmbedRequest, EmbedResponse, EmbedBatchRequest, EmbedBatchResponse, HealthResponse};
use std::time::Instant;
use std::sync::Arc;

use candle_core::{Device, Tensor, DType};
use candle_nn::VarBuilder;
use candle_transformers::models::bert::{BertModel, Config};
use hf_hub::{api::sync::Api, Repo};
use tokenizers::Tokenizer;

pub mod embedding {
    pub mod v1 {
        tonic::include_proto!("embedding.v1");
    }
}

pub struct Model {
    model: BertModel,
    tokenizer: Tokenizer,
    device: Device,
}

impl Model {
    pub fn load() -> anyhow::Result<Self> {
        let device = Device::Cpu; // Default to CPU for now
        let api = Api::new()?;
        let repo = api.repo(Repo::model("nomic-ai/nomic-embed-text-v1.5".to_string()));
        
        let config_filename = repo.get("config.json")?;
        let tokenizer_filename = repo.get("tokenizer.json")?;
        let weights_filename = repo.get("model.safetensors")?;

        let config: Config = serde_json::from_reader(std::fs::File::open(config_filename)?)?;
        let tokenizer = Tokenizer::from_file(tokenizer_filename).map_err(anyhow::Error::msg)?;
        
        let vb = unsafe {
            VarBuilder::from_mmaped_safetensors(&[weights_filename], DType::F32, &device)?
        };
        let model = BertModel::load(vb, &config)?;

        Ok(Self {
            model,
            tokenizer,
            device,
        })
    }

    pub fn embed(&self, text: &str, task_type: &str) -> anyhow::Result<Vec<f32>> {
        let prefix = match task_type {
            "search_query" => "search_query: ",
            "search_document" => "search_document: ",
            "classification" => "classification: ",
            "clustering" => "clustering: ",
            _ => "",
        };
        let full_text = format!("{}{}", prefix, text);

        let tokens = self.tokenizer.encode(full_text, true).map_err(anyhow::Error::msg)?;
        let token_ids = tokens.get_ids();
        let input_ids = Tensor::new(token_ids, &self.device)?.unsqueeze(0)?;
        let token_type_ids = Tensor::new(vec![0u32; token_ids.len()], &self.device)?.unsqueeze(0)?;
        
        // Attention mask: 1 for tokens, 0 for padding
        let attention_mask = Tensor::new(vec![1u32; token_ids.len()], &self.device)?.unsqueeze(0)?;
        
        let embeddings = self.model.forward(&input_ids, &token_type_ids, Some(&attention_mask))?;
        
        // Mean pooling
        let (_n_batch, n_tokens, _hidden_size) = embeddings.dims3()?;
        let embeddings = (embeddings.sum(1)? / (n_tokens as f64))?;
        let embeddings = embeddings.get(0)?;
        
        // L2 normalization
        let norm = embeddings.sqr()?.sum_all()?.sqrt()?;
        let embeddings = (embeddings / norm)?;
        
        Ok(embeddings.to_vec1()?)
    }
}

pub struct MyEmbeddingService {
    model: Arc<Model>,
    start_time: Instant,
}

impl MyEmbeddingService {
    pub fn new(model: Model) -> Self {
        Self {
            model: Arc::new(model),
            start_time: Instant::now(),
        }
    }
}

#[tonic::async_trait]
impl EmbeddingService for MyEmbeddingService {
    async fn embed(&self, request: Request<EmbedRequest>) -> Result<Response<EmbedResponse>, Status> {
        let req = request.into_inner();
        let model = self.model.clone();
        
        let vector = tokio::task::spawn_blocking(move || {
            model.embed(&req.text, &req.task_type)
        }).await.map_err(|e| Status::internal(e.to_string()))?
          .map_err(|e| Status::internal(e.to_string()))?;

        Ok(Response::new(EmbedResponse {
            vector,
            model: "nomic-embed-text-v1.5".to_string(),
            dimensions: 768,
        }))
    }

    async fn embed_batch(&self, request: Request<EmbedBatchRequest>) -> Result<Response<EmbedBatchResponse>, Status> {
        let req = request.into_inner();
        let model = self.model.clone();

        // Spawn all blocking tasks in parallel
        let handles: Vec<_> = req.texts.into_iter().map(|text| {
            let m = model.clone();
            let tt = req.task_type.clone();
            tokio::task::spawn_blocking(move || m.embed(&text, &tt))
        }).collect();

        // Await all results in order
        let mut results = Vec::with_capacity(handles.len());
        for handle in handles {
            let vector = handle.await
                .map_err(|e| Status::internal(e.to_string()))?
                .map_err(|e| Status::internal(e.to_string()))?;
            results.push(EmbedResponse {
                vector,
                model: "nomic-embed-text-v1.5".to_string(),
                dimensions: 768,
            });
        }

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

    println!("Loading model...");
    let model = Model::load()?;
    let service = MyEmbeddingService::new(model);

    let (mut health_reporter, health_service) = health_reporter();
    health_reporter
        .set_serving::<EmbeddingServiceServer<MyEmbeddingService>>()
        .await;

    let addr = "0.0.0.0:50051".parse()?;
    println!("EmbeddingService listening on {}", addr);

    Server::builder()
        .add_service(health_service)
        .add_service(EmbeddingServiceServer::new(service))
        .serve(addr)
        .await?;

    Ok(())
}
