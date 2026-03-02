# Google ADK + Gemini 2.5 Flash Integration

## Overview

ServiceTsunami now uses **Google Agent Development Kit (ADK)** with **Gemini 2.5 Flash** as the core AI engine, replacing the previous Claude/Anthropic integration.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Frontend       │────▶│  API Service    │────▶│  ADK Service    │
│  (React)        │     │  (FastAPI)      │     │  (Google ADK)   │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                        │
                                                        ▼
                                               ┌─────────────────┐
                                               │  Gemini 2.5     │
                                               │  Flash (Vertex) │
                                               └─────────────────┘
```

### Service Components

| Service | Purpose | Port |
|---------|---------|------|
| `servicetsunami-web` | React frontend | 80 |
| `servicetsunami-api` | FastAPI backend, auth, data | 80 → 8000 |
| `servicetsunami-adk` | Google ADK agent server | 80 → 8080 |
| `mcp-server` | Databricks MCP connector | 80 → 8000 |

## Agent Structure

The ADK service uses a **hierarchical multi-team supervisor pattern**. The **Root Supervisor** routes requests to specialized team sub-supervisors:

### 1. Root Supervisor
- **Routing**: Personal Assistant, Dev Team, Data Team, Sales Team, Marketing Team.
- **Goal**: Analyze intent and dispatch to the correct specialized team.

### 2. Specialized Teams (Sub-Supervisors)

| Team | Agents | Purpose |
|------|--------|---------|
| **Personal Assistant** | Luna (WhatsApp-native) | Business co-pilot, scheduling, reminders. |
| **Dev Team** | Architect, Coder, Tester, DevOps, User Agent | **Self-modifying** team with shell/git access for autonomous coding. |
| **Data Team** | Data Analyst, Report Generator, Knowledge Manager | SQL queries, visualization, knowledge graph management. |
| **Sales Team** | Sales Agent, Customer Support | Deal pipeline management, inquiry handling. |
| **Marketing Team** | Web Researcher | Market intelligence, prospect discovery. |

### 3. Industry-Specific Agents
- **HealthPets**: Cardiac Analyst, Billing Agent, Vet Supervisor.
- **Deal Team**: Deal Analyst, Deal Researcher, Outreach Specialist.

## Configuration

### Environment Variables (API Service)

| Variable | Description | Example |
|----------|-------------|---------|
| `ADK_BASE_URL` | ADK service URL | `http://servicetsunami-adk` |
| `ADK_APP_NAME` | ADK app name | `servicetsunami_supervisor` |
| `HEALTHPETS_API_URL` | HealthPets backend | `http://healthpets-backend` |

### Environment Variables (ADK Service)

| Variable | Description | Example |
|----------|-------------|---------|
| `GOOGLE_GENAI_USE_VERTEXAI` | Use Vertex AI | `TRUE` |
| `ADK_MODEL` | Gemini model | `gemini-2.0-flash` (or 2.5) |
| `VERTEX_PROJECT` | GCP project | `ai-agency-479516` |
| `VERTEX_LOCATION` | GCP region | `us-central1` |
| `GIT_AUTH_TOKEN` | Token for self-modifying agents | `ghp_xxxxxx` |

## Authentication

The ADK service uses **Workload Identity** for GCP authentication:
- No API keys needed
- Service account: `dev-backend-app@ai-agency-479516.iam.gserviceaccount.com`
- Permissions: Vertex AI User, AI Platform Admin

## Chat Flow

1. User sends message via frontend (`/chat` page)
2. Frontend calls `POST /api/v1/chat/sessions/{id}/messages`
3. API service creates session with ADK via `POST /apps/{app}/users/{user}/sessions`
4. API sends message to ADK via `POST /run`
5. ADK routes to appropriate sub-agent
6. Gemini generates response
7. Response returned through chain to frontend

## Deployment

### CI/CD Workflows

- `.github/workflows/servicetsunami-api.yaml` - API service
- `.github/workflows/adk-deploy.yaml` - ADK service

### Helm Values

- `helm/values/servicetsunami-api.yaml`
- `helm/values/servicetsunami-adk.yaml`

## Monitoring

Check ADK logs:
```bash
kubectl logs -n prod -l app.kubernetes.io/name=servicetsunami-adk --tail=100
```

Verify ADK health:
```bash
kubectl exec -n prod $(kubectl get pods -n prod -l app.kubernetes.io/name=servicetsunami-adk -o name) -- curl http://localhost:8080/list-apps
```

## Date Completed

December 18, 2025
