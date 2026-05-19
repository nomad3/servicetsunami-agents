//! Async HTTP client for the AgentProvision API.
//!
//! Wraps `reqwest::Client` with:
//! * a configured base URL (defaults to `https://agentprovision.com`)
//! * an optional bearer token (set via [`ApiClient::set_token`])
//! * a uniform error model — non-2xx responses become `Error::Api`

use reqwest::header::{HeaderMap, HeaderValue, AUTHORIZATION, CONTENT_TYPE};
use reqwest::{Client, Method, RequestBuilder, Response, StatusCode};
use serde::de::DeserializeOwned;
use serde::Serialize;
use std::sync::Arc;
use std::sync::Mutex as StdMutex;
use std::time::Duration;
use tokio::sync::Mutex as AsyncMutex;
use url::Url;

use crate::error::{Error, Result};
use crate::models::{
    Agent, ChatMessage, ChatMessageRequest, ChatSession, ChatTurn, CreateEntityRequest,
    DynamicWorkflow, DynamicWorkflowRun, FileSkill, IntegrationStatus, KnowledgeEntity, Tenant,
    Token, User, Workflow, WorkflowRun, WorkflowRunRequest, WorkspaceCloneRequest,
    WorkspaceCloneResponse,
};

pub const DEFAULT_BASE_URL: &str = "https://agentprovision.com";

/// Return shape from [`ApiClient::maybe_refresh_and_retry`].
enum MaybeRetried {
    Replied(Response),
    FreshFailure(Error),
}

/// Render a bounded preview of a response body for error messages. Returns
/// the body as UTF-8 if it decodes; otherwise shows a hex fingerprint of
/// the first 32 bytes so callers can distinguish 'truncated JSON' from
/// 'compressed bytes we couldn't decode' from 'binary garbage'.
fn preview_body(bytes: &[u8]) -> String {
    const MAX_PREVIEW: usize = 400;
    match std::str::from_utf8(bytes) {
        Ok(s) if s.len() <= MAX_PREVIEW => s.to_string(),
        Ok(s) => {
            let head: String = s.chars().take(MAX_PREVIEW / 2).collect();
            let tail: String = s
                .chars()
                .rev()
                .take(MAX_PREVIEW / 2)
                .collect::<String>()
                .chars()
                .rev()
                .collect();
            format!("{}…{}", head, tail)
        }
        Err(_) => {
            // Binary — show first 32 bytes as hex. Brotli starts 0x1B/0xCE
            // and similar fingerprints, so users seeing hex here know to
            // suspect compression.
            let n = bytes.len().min(32);
            let hex: String = bytes[..n].iter().map(|b| format!("{:02x}", b)).collect();
            format!("<non-utf8, first {} bytes hex: {}>", n, hex)
        }
    }
}

/// Hook invoked after a successful refresh-token rotation so the CLI
/// can persist the new credentials into its keychain entry. The second
/// argument is `None` when the server's reuse-detection grace pathway
/// returned a fresh access_token but no new refresh token (B-1 race);
/// the CLI then keeps the existing refresh token in place.
pub type RefreshPersistFn = dyn Fn(&str, Option<&str>) + Send + Sync;

#[derive(Clone)]
pub struct ApiClient {
    inner: Client,
    base: Url,
    token: Arc<StdMutex<Option<String>>>,
    tenant_id: Arc<StdMutex<Option<String>>>,
    /// Long-lived opaque exchange credential, set after login. None
    /// until the CLI's startup wires it in via [`set_refresh_token`].
    refresh_token: Arc<StdMutex<Option<String>>>,
    /// Callback fired after a successful auto-refresh. Defaults to None.
    refresh_persist: Arc<StdMutex<Option<Arc<RefreshPersistFn>>>>,
    /// Serializes concurrent auto-refresh attempts within this
    /// process. Without this, two parallel requests both hitting 401
    /// after expiry both call /auth/token/refresh, the server rotates
    /// one and triggers reuse-detection on the second — wiping the
    /// chain mid-session. Review finding B-1 on PR #442.
    refresh_lock: Arc<AsyncMutex<()>>,
    /// Optional device label sent on every request as the
    /// `X-AP-Device-Label` header. The server records this on the
    /// refresh_tokens row so `alpha sessions list` can show
    /// "alpha CLI on simon-laptop (darwin aarch64)" instead of the
    /// generic User-Agent. Review finding I-1.
    device_label: Arc<StdMutex<Option<String>>>,
}

impl ApiClient {
    pub fn new(base_url: &str) -> Result<Self> {
        let base = Url::parse(base_url)?;
        let inner = Client::builder()
            // Chat turns can run >60s (agent router → Temporal → MCP → LLM).
            // The streaming SSE endpoints aren't bounded by this timeout
            // because they consume `bytes_stream`, which is fine.
            .timeout(Duration::from_secs(180))
            .user_agent(concat!("agentprovision-core/", env!("CARGO_PKG_VERSION")))
            .build()?;
        Ok(Self {
            inner,
            base,
            token: Arc::new(StdMutex::new(None)),
            tenant_id: Arc::new(StdMutex::new(None)),
            refresh_token: Arc::new(StdMutex::new(None)),
            refresh_persist: Arc::new(StdMutex::new(None)),
            refresh_lock: Arc::new(AsyncMutex::new(())),
            device_label: Arc::new(StdMutex::new(None)),
        })
    }

    /// Set the human-readable device label sent as `X-AP-Device-Label`
    /// on every request. The CLI computes this from hostname + OS at
    /// startup; callers without a meaningful label can leave it None
    /// and the server falls back to the User-Agent.
    pub fn set_device_label(&self, label: Option<String>) {
        *self.device_label.lock().expect("device label lock") = label;
    }

    pub fn with_token(self, token: impl Into<String>) -> Self {
        self.set_token(Some(token.into()));
        self
    }

    pub fn set_token(&self, token: Option<String>) {
        *self.token.lock().expect("token lock") = token;
    }

    pub fn token(&self) -> Option<String> {
        self.token.lock().expect("token lock").clone()
    }

    /// Stash the long-lived refresh credential so auto-refresh kicks
    /// in on the next 401. None disables the middleware (any failed
    /// 401 surfaces as `Error::Unauthorized` like the legacy path).
    pub fn set_refresh_token(&self, token: Option<String>) {
        *self.refresh_token.lock().expect("refresh token lock") = token;
    }

    pub fn refresh_token(&self) -> Option<String> {
        self.refresh_token
            .lock()
            .expect("refresh token lock")
            .clone()
    }

    /// Register a persistence callback fired after each successful
    /// auto-refresh. Wires the keychain update without coupling
    /// `agentprovision-core` to `keyring` directly.
    pub fn set_refresh_persist(&self, cb: Option<Arc<RefreshPersistFn>>) {
        *self.refresh_persist.lock().expect("persist lock") = cb;
    }

    pub fn set_tenant_id(&self, tenant_id: Option<String>) {
        *self.tenant_id.lock().expect("tenant lock") = tenant_id;
    }

    pub fn base_url(&self) -> &Url {
        &self.base
    }

    pub fn build_url(&self, path: &str) -> Result<Url> {
        // `Url::join` rejects leading slash on relative paths in some cases;
        // strip our leading slash and let the base's path drive the join.
        let trimmed = path.trim_start_matches('/');
        // Ensure the base ends with `/` so `join` treats it as a directory.
        let mut base = self.base.clone();
        if !base.path().ends_with('/') {
            let new_path = format!("{}/", base.path());
            base.set_path(&new_path);
        }
        Ok(base.join(trimmed)?)
    }

    /// Inner `reqwest::Client` accessor for low-level operations
    /// (used by `chat::stream_chat` and `events::tail_session_events` to
    /// open SSE connections that the high-level helpers don't model).
    pub fn http(&self) -> &Client {
        &self.inner
    }

    fn auth_headers(&self) -> HeaderMap {
        let mut headers = HeaderMap::new();
        if let Some(tok) = self.token() {
            if let Ok(val) = HeaderValue::from_str(&format!("Bearer {tok}")) {
                headers.insert(AUTHORIZATION, val);
            }
        }
        if let Some(tenant) = self.tenant_id.lock().expect("tenant lock").clone() {
            if let Ok(val) = HeaderValue::from_str(&tenant) {
                headers.insert("X-Tenant-Id", val);
            }
        }
        // X-AP-Device-Label: human-readable origin recorded on the
        // refresh_tokens row server-side for `alpha sessions list` UX.
        // Review finding I-1 on PR #442.
        if let Some(label) = self.device_label.lock().expect("device label lock").clone() {
            if let Ok(val) = HeaderValue::from_str(&label) {
                headers.insert("X-AP-Device-Label", val);
            }
        }
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        headers
    }

    pub fn request(&self, method: Method, path: &str) -> Result<RequestBuilder> {
        let url = self.build_url(path)?;
        let req = self.inner.request(method, url).headers(self.auth_headers());
        Ok(req)
    }

    /// Generic typed `GET` helper. Useful for ad-hoc / prototype endpoints
    /// that don't yet have a dedicated typed method on `Client`. Once an
    /// endpoint stabilizes, promote it to its own method for discoverability.
    pub async fn get_json<T: DeserializeOwned>(&self, path: &str) -> Result<T> {
        let req = self.request(Method::GET, path)?;
        self.send_json(req).await
    }

    /// Generic typed `POST` helper. Body is serialized as JSON; auth
    /// headers are attached. Same promotion rule as `get_json`.
    pub async fn post_json<B: Serialize, T: DeserializeOwned>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T> {
        let req = self.request(Method::POST, path)?.json(body);
        self.send_json(req).await
    }

    /// POST with a JSON body, expecting no JSON response (204 No Content
    /// or empty 200). Used for revoke-style endpoints that don't have
    /// a response shape to decode. Reviewer IMPORTANT-3 on PR #445:
    /// avoids the brittle "got empty body" string-match the previous
    /// logout.rs needed.
    pub async fn post_no_body_json<B: Serialize>(&self, path: &str, body: &B) -> Result<()> {
        let req = self.request(Method::POST, path)?.json(body);
        self.send_no_body(req).await
    }

    /// Send a request and decode the JSON response body, mapping non-2xx into
    /// `Error::Api` with the response body included for debugging.
    ///
    /// **Auto-refresh:** if the response is 401 and we hold a refresh
    /// token, the middleware exchanges it for fresh credentials via
    /// `/auth/token/refresh`, updates `self.token`, fires the persist
    /// callback, and retries the original request **exactly once**.
    /// The retry uses `try_clone()` so non-cloneable bodies (streams)
    /// still fall through to the original `Error::Unauthorized` path.
    pub async fn send_json<T: DeserializeOwned>(&self, req: RequestBuilder) -> Result<T> {
        // Clone the request before the first send so we can retry. If
        // the body is non-cloneable (multipart streams, etc.) the
        // clone returns None and we skip the auto-refresh path.
        let clone = req.try_clone();
        let resp = req.send().await?;
        let resp = match self.maybe_refresh_and_retry(resp, clone).await? {
            MaybeRetried::Replied(r) => r,
            MaybeRetried::FreshFailure(e) => return Err(e),
        };
        let resp = self.check_status(resp).await?;
        let bytes = resp.bytes().await?;
        if bytes.is_empty() {
            // Try to coerce empty response into `()` or a default; otherwise
            // fail loud.
            return Err(Error::other("expected JSON response, got empty body"));
        }
        match serde_json::from_slice::<T>(&bytes) {
            Ok(v) => Ok(v),
            Err(e) => {
                // Surface the actual response so the cryptic
                // "premature end of input at line 1 column 600"
                // error has a fighting chance of being debugged. The
                // first/last 200 bytes are a fingerprint: header (open
                // brace / bracket) + trailing structure usually tell us
                // whether the body is truncated mid-stream, returned
                // compressed, or shaped differently than our model
                // expects. We log on stderr at the `info` level via
                // `log::warn!` so users running with `-v` see it
                // without polluting normal output.
                let body_dump = preview_body(&bytes);
                log::warn!(
                    "serde decode failed at line {} column {}: {}\n  bytes len: {}\n  preview: {}",
                    e.line(),
                    e.column(),
                    e,
                    bytes.len(),
                    body_dump,
                );
                Err(Error::Serde(e))
            }
        }
    }

    pub async fn send_no_body(&self, req: RequestBuilder) -> Result<()> {
        let clone = req.try_clone();
        let resp = req.send().await?;
        let resp = match self.maybe_refresh_and_retry(resp, clone).await? {
            MaybeRetried::Replied(r) => r,
            MaybeRetried::FreshFailure(e) => return Err(e),
        };
        let _ = self.check_status(resp).await?;
        Ok(())
    }

    /// Auto-refresh seam. On 401 with a refresh_token configured,
    /// exchange it for a fresh access token and retry the cloned
    /// request once. Returns:
    ///   * `MaybeRetried::Replied(resp)` — the retried (or original)
    ///     response, ready for status / body decoding.
    ///   * `MaybeRetried::FreshFailure(e)` — refresh attempt itself
    ///     failed cleanly (network, JSON shape). Caller propagates.
    async fn maybe_refresh_and_retry(
        &self,
        resp: Response,
        retry_req: Option<RequestBuilder>,
    ) -> Result<MaybeRetried> {
        if resp.status() != StatusCode::UNAUTHORIZED {
            return Ok(MaybeRetried::Replied(resp));
        }
        let Some(retry_req) = retry_req else {
            // Body wasn't cloneable; fall back to original 401.
            return Ok(MaybeRetried::Replied(resp));
        };
        let Some(refresh_secret) = self.refresh_token() else {
            // No refresh credential; legacy behavior.
            return Ok(MaybeRetried::Replied(resp));
        };
        // Drain the original 401 body to free the connection.
        let _ = resp.bytes().await;

        // B-1: serialize concurrent refresh attempts. Without this,
        // `alpha chat` + `alpha watch` both hitting 401 in the same
        // window both call /auth/token/refresh, one rotates the row,
        // the other triggers reuse-detection on the server and the
        // entire chain burns. Inside the lock we re-check whether
        // someone else already refreshed; if the in-memory token
        // changed since we entered, just retry with the new bearer.
        let _refresh_guard = self.refresh_lock.lock().await;

        // Race detection: if the refresh secret we captured before
        // taking the lock isn't the one currently in memory, another
        // caller rotated under us — use their freshly-minted access
        // token instead of re-exchanging. Reviewer IMPORTANT-1 on
        // PR #445: previous heuristic included a noise-bit
        // `bearer_was_stale` that hid the real invariant.
        let in_memory_refresh = self.refresh_token();
        let already_rotated = in_memory_refresh.is_some()
            && in_memory_refresh.as_deref() != Some(refresh_secret.as_str());
        let pair_access_token: String;
        if already_rotated {
            // Someone else just rotated. Use the freshest in-memory
            // access token without hitting the server. The freshly
            // rotated refresh token is already in `self.refresh_token`
            // (set by the winning racer), and the keychain was
            // already updated via their persist callback, so we don't
            // re-fire ours.
            pair_access_token = self
                .token()
                .ok_or_else(|| Error::other("racing refresh left empty token"))?;
        } else {
            // Exchange. If the exchange itself returns 401 the
            // refresh credential is dead — clear in-memory state so
            // we don't loop. I-6: a 400 means the secret is malformed
            // (empty / corrupted); same recovery, otherwise we'd
            // loop forever pinging /auth/token/refresh with a string
            // the server can't parse.
            let pair = match self.exchange_refresh(&refresh_secret).await {
                Ok(p) => p,
                Err(Error::Unauthorized)
                | Err(Error::Api { status: 401, .. })
                | Err(Error::Api { status: 400, .. }) => {
                    self.set_refresh_token(None);
                    self.set_token(None);
                    return Ok(MaybeRetried::FreshFailure(Error::Unauthorized));
                }
                Err(e) => return Ok(MaybeRetried::FreshFailure(e)),
            };
            pair_access_token = pair.access_token.clone();
            let pair_refresh_token = pair.refresh_token.clone();
            // Persist the new pair under the lock so concurrent
            // callers see the same state.
            self.set_token(Some(pair_access_token.clone()));
            if let Some(new_refresh) = pair_refresh_token.as_deref() {
                self.set_refresh_token(Some(new_refresh.to_string()));
            }
            if let Some(cb) = self.refresh_persist.lock().expect("persist lock").clone() {
                // I-7: pass the new refresh token as Option<&str> so
                // the callback can decide whether to overwrite (Some)
                // or leave the existing one (None — happens when the
                // server's grace-window pathway returns a new access
                // token but no new refresh credential, B-1).
                cb(&pair_access_token, pair_refresh_token.as_deref());
            }
        }
        // Rebuild auth headers on the retry — try_clone() copies the
        // original headers including the *stale* Authorization. Drop
        // it so the new one takes effect.
        let retry_req = retry_req.header(
            AUTHORIZATION,
            HeaderValue::from_str(&format!("Bearer {}", pair_access_token))
                .map_err(|e| Error::other(format!("invalid bearer header: {e}")))?,
        );
        let retried = retry_req.send().await?;
        Ok(MaybeRetried::Replied(retried))
    }

    /// `POST /api/v1/auth/token/refresh` — exchange the long-lived
    /// refresh credential for a fresh (access, refresh) pair. Called
    /// by the auto-refresh middleware; CLIs can call it directly too.
    pub async fn exchange_refresh(&self, refresh_token: &str) -> Result<Token> {
        #[derive(Serialize)]
        struct Body<'a> {
            refresh_token: &'a str,
        }
        // Build the request manually so we don't attach the (stale)
        // Authorization header — the refresh endpoint is unauth'd by
        // design.
        let url = self.build_url("/api/v1/auth/token/refresh")?;
        let req = self
            .inner
            .post(url)
            .header(CONTENT_TYPE, HeaderValue::from_static("application/json"))
            .json(&Body { refresh_token });
        let resp = req.send().await?;
        let resp = self.check_status(resp).await?;
        Ok(resp.json::<Token>().await?)
    }

    pub async fn check_status(&self, resp: Response) -> Result<Response> {
        let status = resp.status();
        if status.is_success() {
            return Ok(resp);
        }
        if status == StatusCode::UNAUTHORIZED {
            return Err(Error::Unauthorized);
        }
        let body = resp.text().await.unwrap_or_default();
        Err(Error::Api {
            status: status.as_u16(),
            body,
        })
    }

    // ---- High-level endpoints --------------------------------------------------

    /// `POST /api/v1/auth/login` — OAuth2-style form-encoded login.
    /// Returns the access token but does **not** mutate this client's token.
    /// Callers decide whether to persist (CLI: yes, into keychain; ad-hoc: no).
    pub async fn login_password(&self, email: &str, password: &str) -> Result<Token> {
        let url = self.build_url("/api/v1/auth/login")?;
        let req = self
            .inner
            .post(url)
            .form(&[("username", email), ("password", password)]);
        let resp = req.send().await?;
        let resp = self.check_status(resp).await?;
        Ok(resp.json::<Token>().await?)
    }

    /// `GET /api/v1/auth/users/me`
    pub async fn current_user(&self) -> Result<User> {
        let req = self.request(Method::GET, "/api/v1/auth/users/me")?;
        self.send_json(req).await
    }

    /// `GET /api/v1/agents`
    pub async fn list_agents(&self) -> Result<Vec<Agent>> {
        let req = self.request(Method::GET, "/api/v1/agents")?;
        self.send_json(req).await
    }

    /// `GET /api/v1/agents/{agent_id}` — single-agent detail. The backend
    /// returns the same shape as the list endpoint plus runtime fields that
    /// our `Agent` model carries forward-compatibly via `#[serde(default)]`.
    pub async fn get_agent(&self, agent_id: &str) -> Result<Agent> {
        let req = self.request(Method::GET, &format!("/api/v1/agents/{agent_id}"))?;
        self.send_json(req).await
    }

    /// `GET /api/v1/tenants/{id}` — caller must have the id (from the JWT or
    /// `current_user`).
    pub async fn get_tenant(&self, tenant_id: &str) -> Result<Tenant> {
        let req = self.request(Method::GET, &format!("/api/v1/tenants/{tenant_id}"))?;
        self.send_json(req).await
    }

    /// `GET /api/v1/chat/sessions`
    pub async fn list_chat_sessions(&self) -> Result<Vec<ChatSession>> {
        let req = self.request(Method::GET, "/api/v1/chat/sessions")?;
        self.send_json(req).await
    }

    /// `GET /api/v1/chat/sessions/{id}/messages`
    pub async fn list_chat_messages(&self, session_id: &str) -> Result<Vec<ChatMessage>> {
        let req = self.request(
            Method::GET,
            &format!("/api/v1/chat/sessions/{session_id}/messages"),
        )?;
        self.send_json(req).await
    }

    /// `POST /api/v1/chat/sessions` — create a session (optionally bound to an
    /// agent).
    pub async fn create_chat_session(
        &self,
        title: Option<&str>,
        agent_id: Option<&str>,
    ) -> Result<ChatSession> {
        #[derive(Serialize)]
        struct Body<'a> {
            #[serde(skip_serializing_if = "Option::is_none")]
            title: Option<&'a str>,
            #[serde(skip_serializing_if = "Option::is_none")]
            agent_id: Option<&'a str>,
        }
        let req = self
            .request(Method::POST, "/api/v1/chat/sessions")?
            .json(&Body { title, agent_id });
        self.send_json(req).await
    }

    /// `POST /api/v1/chat/sessions/{id}/messages` — non-streaming send.
    pub async fn send_chat_message(&self, session_id: &str, content: &str) -> Result<ChatTurn> {
        let req = self
            .request(
                Method::POST,
                &format!("/api/v1/chat/sessions/{session_id}/messages"),
            )?
            .json(&ChatMessageRequest { content });
        self.send_json(req).await
    }

    /// `GET /api/v1/workflows`
    pub async fn list_workflows(&self) -> Result<Vec<Workflow>> {
        let req = self.request(Method::GET, "/api/v1/workflows")?;
        self.send_json(req).await
    }

    /// `GET /api/v1/workflows/runs/{id}`
    pub async fn get_workflow_run(&self, run_id: &str) -> Result<WorkflowRun> {
        let req = self.request(Method::GET, &format!("/api/v1/workflows/runs/{run_id}"))?;
        self.send_json(req).await
    }

    // ── Dynamic workflows ───────────────────────────────────────────
    // These match the endpoints the web `WorkflowsPage` hits via
    // `apps/web/src/services/dynamicWorkflowService.js`. The legacy
    // `list_workflows` / `get_workflow_run` methods above hit the
    // older `/workflows` summary endpoint and are kept for compatibility.

    /// `GET /api/v1/dynamic-workflows[?status=<state>]`
    pub async fn list_dynamic_workflows(
        &self,
        status: Option<&str>,
    ) -> Result<Vec<DynamicWorkflow>> {
        let mut req = self.request(Method::GET, "/api/v1/dynamic-workflows")?;
        if let Some(s) = status {
            req = req.query(&[("status", s)]);
        }
        self.send_json(req).await
    }

    /// `GET /api/v1/dynamic-workflows/{id}`
    pub async fn get_dynamic_workflow(&self, workflow_id: &str) -> Result<DynamicWorkflow> {
        let req = self.request(
            Method::GET,
            &format!("/api/v1/dynamic-workflows/{workflow_id}"),
        )?;
        self.send_json(req).await
    }

    /// `POST /api/v1/dynamic-workflows/{id}/activate`
    pub async fn activate_dynamic_workflow(&self, workflow_id: &str) -> Result<()> {
        let req = self.request(
            Method::POST,
            &format!("/api/v1/dynamic-workflows/{workflow_id}/activate"),
        )?;
        self.send_no_body(req).await
    }

    /// `POST /api/v1/dynamic-workflows/{id}/pause`
    pub async fn pause_dynamic_workflow(&self, workflow_id: &str) -> Result<()> {
        let req = self.request(
            Method::POST,
            &format!("/api/v1/dynamic-workflows/{workflow_id}/pause"),
        )?;
        self.send_no_body(req).await
    }

    /// `POST /api/v1/dynamic-workflows/{id}/run`
    ///
    /// `dry_run` mirrors the web TestConsole — the backend validates the
    /// definition without dispatching to Temporal, useful for `alpha workflow run
    /// --dry-run` ahead of a real run.
    pub async fn run_dynamic_workflow(
        &self,
        workflow_id: &str,
        input_data: Option<serde_json::Value>,
        dry_run: bool,
    ) -> Result<DynamicWorkflowRun> {
        let body = WorkflowRunRequest {
            input_data,
            dry_run,
        };
        let req = self
            .request(
                Method::POST,
                &format!("/api/v1/dynamic-workflows/{workflow_id}/run"),
            )?
            .json(&body);
        self.send_json(req).await
    }

    /// `GET /api/v1/dynamic-workflows/{id}/runs?limit=N`
    pub async fn list_dynamic_workflow_runs(
        &self,
        workflow_id: &str,
        limit: Option<u32>,
    ) -> Result<Vec<DynamicWorkflowRun>> {
        let mut req = self.request(
            Method::GET,
            &format!("/api/v1/dynamic-workflows/{workflow_id}/runs"),
        )?;
        if let Some(n) = limit {
            req = req.query(&[("limit", n.to_string())]);
        }
        self.send_json(req).await
    }

    /// `GET /api/v1/dynamic-workflows/runs/{run_id}`
    pub async fn get_dynamic_workflow_run(&self, run_id: &str) -> Result<DynamicWorkflowRun> {
        let req = self.request(
            Method::GET,
            &format!("/api/v1/dynamic-workflows/runs/{run_id}"),
        )?;
        self.send_json(req).await
    }

    // ── Integration status ─────────────────────────────────────────
    // Mirrors what the web IntegrationsPage and the workflow
    // activation-gate consume via `dynamicWorkflowService.getIntegrationStatus`.

    /// `GET /api/v1/integrations/status`
    pub async fn list_integration_status(&self) -> Result<Vec<IntegrationStatus>> {
        let req = self.request(Method::GET, "/api/v1/integrations/status")?;
        self.send_json(req).await
    }

    // ── Skill library ─────────────────────────────────────────────
    // Surfaces the file-based skill registry. Matches what the web
    // SkillsPage consumes via `/api/v1/skills/library`. `tier` filters
    // native/community/custom; `category` filters by the manifest category;
    // `search` triggers pgvector embedding match with text fallback.

    /// `GET /api/v1/skills/library[?tier=…&category=…&search=…]`
    pub async fn list_skills(
        &self,
        tier: Option<&str>,
        category: Option<&str>,
        search: Option<&str>,
    ) -> Result<Vec<FileSkill>> {
        let mut req = self.request(Method::GET, "/api/v1/skills/library")?;
        let mut params: Vec<(&str, &str)> = Vec::new();
        if let Some(t) = tier {
            params.push(("tier", t));
        }
        if let Some(c) = category {
            params.push(("category", c));
        }
        if let Some(s) = search {
            params.push(("search", s));
        }
        if !params.is_empty() {
            req = req.query(&params);
        }
        self.send_json(req).await
    }

    // ── Knowledge graph ─────────────────────────────────────────────
    // Surfaces the same endpoints the web MemoryPage hits to browse
    // `KnowledgeEntity` rows.

    /// `GET /api/v1/knowledge/entities[?entity_type=…&category=…&limit=…&skip=…]`
    pub async fn list_entities(
        &self,
        entity_type: Option<&str>,
        category: Option<&str>,
        limit: Option<u32>,
        skip: Option<u32>,
    ) -> Result<Vec<KnowledgeEntity>> {
        let mut req = self.request(Method::GET, "/api/v1/knowledge/entities")?;
        let limit_s = limit.map(|n| n.to_string());
        let skip_s = skip.map(|n| n.to_string());
        let mut params: Vec<(&str, &str)> = Vec::new();
        if let Some(t) = entity_type {
            params.push(("entity_type", t));
        }
        if let Some(c) = category {
            params.push(("category", c));
        }
        if let Some(ref l) = limit_s {
            params.push(("limit", l.as_str()));
        }
        if let Some(ref s) = skip_s {
            params.push(("skip", s.as_str()));
        }
        if !params.is_empty() {
            req = req.query(&params);
        }
        self.send_json(req).await
    }

    /// `POST /api/v1/knowledge/entities`
    ///
    /// Required: `entity_type` + `name`. Other fields fall back to backend
    /// defaults. Body shape matches `KnowledgeEntityCreate` in
    /// `apps/api/app/schemas/knowledge_entity.py`.
    pub async fn create_entity(&self, body: &CreateEntityRequest) -> Result<KnowledgeEntity> {
        let req = self
            .request(Method::POST, "/api/v1/knowledge/entities")?
            .json(body);
        self.send_json(req).await
    }

    /// `GET /api/v1/knowledge/entities/search?q=<term>[&entity_type=…&category=…]`
    pub async fn search_entities(
        &self,
        query: &str,
        entity_type: Option<&str>,
        category: Option<&str>,
    ) -> Result<Vec<KnowledgeEntity>> {
        let mut req = self.request(Method::GET, "/api/v1/knowledge/entities/search")?;
        let mut params: Vec<(&str, &str)> = vec![("q", query)];
        if let Some(t) = entity_type {
            params.push(("entity_type", t));
        }
        if let Some(c) = category {
            params.push(("category", c));
        }
        req = req.query(&params);
        self.send_json(req).await
    }

    // ─── Onboarding (PR-Q0 endpoints) ─────────────────────────────

    /// `GET /api/v1/onboarding/status` — drives `alpha login` auto-trigger
    /// of `alpha quickstart` for un-onboarded tenants.
    pub async fn get_onboarding_status(&self) -> Result<crate::models::OnboardingStatus> {
        let req = self.request(Method::GET, "/api/v1/onboarding/status")?;
        self.send_json(req).await
    }

    /// `POST /api/v1/onboarding/defer` — user pressed Skip. Suppresses
    /// next auto-trigger but doesn't block explicit `alpha quickstart`.
    pub async fn defer_onboarding(&self) -> Result<()> {
        let req = self.request(Method::POST, "/api/v1/onboarding/defer")?;
        let _ = self.send_no_body(req).await?;
        Ok(())
    }

    /// `POST /api/v1/onboarding/complete` — stamps `onboarded_at` so
    /// the user never sees the wedge picker again unless they pass
    /// `--force` to `alpha quickstart`.
    pub async fn complete_onboarding(&self, source: &str) -> Result<()> {
        #[derive(Serialize)]
        struct Body<'a> {
            source: &'a str,
        }
        let req = self
            .request(Method::POST, "/api/v1/onboarding/complete")?
            .json(&Body { source });
        let _ = self.send_no_body(req).await?;
        Ok(())
    }

    // ─── Training pipeline (PR-Q1 endpoints) ──────────────────────

    /// `POST /api/v1/memory/training/bulk-ingest` — kick off (or
    /// re-attach to) an initial-training pass. Idempotent on
    /// `(tenant_id, snapshot_id)`. The caller generates `snapshot_id`
    /// once per quickstart run and passes it on every retry so the
    /// server returns the existing row instead of spawning a parallel
    /// workflow.
    pub async fn bulk_ingest_training(
        &self,
        source: &str,
        items: &[serde_json::Value],
        snapshot_id: &str,
    ) -> Result<crate::models::BulkIngestResponse> {
        #[derive(Serialize)]
        struct Body<'a> {
            source: &'a str,
            items: &'a [serde_json::Value],
            snapshot_id: &'a str,
        }
        let req = self
            .request(Method::POST, "/api/v1/memory/training/bulk-ingest")?
            .json(&Body {
                source,
                items,
                snapshot_id,
            });
        self.send_json(req).await
    }

    /// `GET /api/v1/memory/training/{run_id}` — poll status. The CLI's
    /// progress bar polls this every 2s until status reaches a terminal
    /// state (`complete` | `failed`). SSE will replace polling in PR-Q1b.
    pub async fn get_training_run(&self, run_id: &str) -> Result<crate::models::TrainingRun> {
        let req = self.request(Method::GET, &format!("/api/v1/memory/training/{run_id}"))?;
        self.send_json(req).await
    }

    // ── Workspace clone ─────────────────────────────────────────────
    // Backs `alpha workspace clone <repo>` — kicks off a server-side
    // git clone (or fetch + reset for idempotency) into the tenant's
    // workspace under `projects/<repo>/`. Returns immediately with a
    // job_id while the clone runs in the API's BackgroundTasks pool.

    /// `POST /api/v1/workspace/clone`
    pub async fn clone_workspace_repo(
        &self,
        repo: &str,
        branch: Option<&str>,
        force: bool,
    ) -> Result<WorkspaceCloneResponse> {
        let body = WorkspaceCloneRequest {
            repo,
            branch,
            force,
        };
        self.post_json("/api/v1/workspace/clone", &body).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_url_handles_trailing_and_leading_slashes() {
        let c = ApiClient::new("https://example.com").unwrap();
        let u = c.build_url("/api/v1/auth/users/me").unwrap();
        assert_eq!(u.as_str(), "https://example.com/api/v1/auth/users/me");

        let c2 = ApiClient::new("https://example.com/base").unwrap();
        let u2 = c2.build_url("api/v1/foo").unwrap();
        assert_eq!(u2.as_str(), "https://example.com/base/api/v1/foo");
    }

    #[test]
    fn token_round_trip() {
        let c = ApiClient::new("https://example.com").unwrap();
        assert!(c.token().is_none());
        c.set_token(Some("abc".into()));
        assert_eq!(c.token().as_deref(), Some("abc"));
        c.set_token(None);
        assert!(c.token().is_none());
    }
}
