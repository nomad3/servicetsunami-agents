# MCP Server Configuration for AgentProvision

AgentProvision includes 81 MCP tools accessible through multiple MCP servers. This configuration enables Copilot to discover, validate, and invoke these tools.

## Local Development Setup

### Start the MCP Server

The MCP server runs on ports 8086-8087 when using Docker Compose:

```bash
# Start all services (includes MCP server on 8087)
DB_PORT=8003 API_PORT=8001 WEB_PORT=8002 docker-compose up --build

# Or run MCP server standalone
cd apps/mcp-server
pip install -e ".[dev]"
python -m src.server
```

The server will be available at `http://localhost:8087`.

## Available MCP Tool Categories

### Knowledge Graph (11 tools)
- `create_entity`: Add new entity to knowledge graph
- `find_entity`: Search entities by name/type
- `update_entity`: Modify entity properties
- `create_relation`: Link two entities
- `create_observation`: Add fact/insight
- `entity_timeline`: Get entity history
- `search_entities`: Semantic search via pgvector (768-dim embeddings)
- `recall_entities`: Retrieve similar entities from memory
- `delete_entity`, `delete_relation`, `delete_observation`

### Email & Communication (8 tools)
- `search_emails`: Query Gmail inbox
- `read_email`: Get full email content
- `send_email`: Compose and send email
- `list_email_accounts`: Get connected Gmail accounts
- `download_attachment`: Fetch email attachments (auto-embedded for semantic search)
- `deep_scan_emails`: Advanced email analysis
- `whatsapp_send_message`: Send WhatsApp via Neonize
- `whatsapp_receive_webhook`: Process WhatsApp webhooks

### Calendar (2 tools)
- `list_calendar_events`: Fetch upcoming events
- `create_calendar_event`: Schedule new event

### Project Management (5 tools)
- `search_jira_issues`: Query Jira
- `get_jira_issue`: Fetch issue details
- `create_jira_issue`: Create new issue
- `update_jira_issue`: Modify issue
- `list_jira_projects`: Get available projects

### Source Control (8 tools)
- `list_github_repos`: Enumerate repositories
- `list_github_issues`: Query issues/PRs
- `get_github_file`: Read file content
- `create_github_issue`: Open new issue
- `search_github_code`: Full-text code search
- `list_github_prs`, `get_github_pr`, `create_github_pr`

### Advertising & Marketing (12 tools)
- `list_meta_campaigns`: Get Meta Ads campaigns
- `get_meta_campaign_insights`: Campaign performance metrics
- `pause_meta_campaign`: Pause campaign
- `list_google_ads_campaigns`: Get Google Ads campaigns
- `get_google_ads_insights`: Performance data
- `list_tiktok_campaigns`: Get TikTok campaigns
- `search_meta_ad_library`: Search public ads (Meta)
- `search_google_ad_library`: Search public ads (Google)
- `create_meta_campaign`, `create_google_campaign`, `create_tiktok_campaign`

### Data & Analytics (7 tools)
- `execute_sql_query`: Run SQL against PostgreSQL
- `get_dataset_schema`: Describe dataset structure
- `get_dataset_insights`: Automated analysis and stats
- `list_datasets`: Enumerate available datasets
- `calculate_metrics`: Compute aggregations
- `compare_periods`: Period-over-period analysis
- `forecast_trend`: Simple forecasting

### Sales & Pipeline (6 tools)
- `qualify_lead`: BANT qualification with rubric scoring
- `create_prospect`: Add prospect to CRM
- `update_pipeline_status`: Advance deal through stages
- `create_outreach`: Draft outreach message
- `create_proposal`: Generate proposal
- `send_followup`: Schedule follow-up activity

### Competitor Monitoring (5 tools)
- `add_competitor`: Register competitor in knowledge graph
- `list_competitors`: Get monitoring list
- `get_competitor_report`: Aggregated analysis
- `compare_competitors`: Head-to-head analysis
- `remove_competitor`: Stop monitoring

### System Monitoring (6 tools)
- `start_inbox_monitor`: Enable Gmail/Calendar monitoring
- `stop_inbox_monitor`: Disable monitoring
- `get_inbox_monitor_status`: Check monitor state
- `start_competitor_monitor`: Enable competitor monitoring
- `stop_competitor_monitor`: Disable competitor monitoring
- `get_competitor_monitor_status`: Check state

### Reports & Documents (2 tools)
- `extract_from_document`: Parse PDF/Excel/document content
- `generate_excel_report`: Create Excel workbook with data

### Skill Marketplace (4 tools)
- `list_skills`: Get all available skills (native/community/custom)
- `run_skill`: Execute a skill with parameters
- `match_skills_in_context`: Find relevant skills for current task
- `recall_memory_from_skill`: Retrieve stored skill outputs

### Shell & System (2 tools)
- `execute_shell_command`: Run arbitrary shell commands (requires permissions)
- `deploy_changes`: Deploy code changes via git

### Google Drive (3 tools)
- `search_drive_files`: Find files in Google Drive
- `read_drive_file`: Get file content
- `list_drive_folder`: List folder contents

### Data Connectors (1 tool)
- `query_data_source`: Query connected PostgreSQL/data warehouse

## Configuration for Claude Code & Codex

When agents run via Claude Code CLI or Codex, the MCP tools are automatically available if:

1. **Service is running**: MCP server at `http://mcp-server:8087` (or localhost:8087 for local dev)
2. **Token is valid**: Agent has authenticated with appropriate provider (Claude, OpenAI, Google)
3. **Tenant subscription active**: Tenant OAuth token stored in credential vault
4. **Tool permissions enabled**: Integration config allows tool execution

### Session Configuration

The CLI orchestrator (`session_manager.py`) automatically:
1. Generates MCP config JSON with tool list and server endpoint
2. Embeds config in Claude/Codex session
3. Passes all 81 tools to agent at runtime
4. Handles tool call responses and error handling

## Using Tools in Agent Code

When writing agent code (Python/JavaScript) or skill scripts, access tools via:

### Python (Temporal Activities)
```python
from app.services.knowledge import KnowledgeService

service = KnowledgeService()
entity = service.create_entity(
    tenant_id=tenant_id,
    name="Acme Corp",
    entity_type="company",
    properties={"industry": "SaaS"}
)
```

### Claude Code CLI
```bash
claude -p "Use the execute_sql_query tool to find all customers in the CUSTOMERS table"
```

### JavaScript/TypeScript
```javascript
const axios = require('axios');

const response = await axios.post(
  'http://localhost:8087/tools/execute_sql_query',
  { query: 'SELECT * FROM customers LIMIT 10' },
  { headers: { Authorization: `Bearer ${token}` } }
);
```

## Tool Authentication

### API Key Requirement
Most MCP tools require:
- **Bearer token**: JWT from AgentProvision API (`/api/v1/auth/login`)
- **Tenant context**: Automatic from token claims (`tenant_id`, `user_id`)

### External Platform Credentials
Tools requiring external credentials (Gmail, Jira, Meta Ads) retrieve OAuth tokens from the **credential vault** (`integration_credential` table) at runtime. Tokens are Fernet-encrypted.

### Example: Send Email
```bash
curl -X POST http://localhost:8087/tools/send_email \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "to": "recipient@example.com",
    "subject": "Hello",
    "body": "Test email"
  }'
```

## Tool Response Format

All tools return JSON:

```json
{
  "success": true,
  "data": { "entity_id": "uuid", "name": "..." },
  "error": null
}
```

On error:
```json
{
  "success": false,
  "data": null,
  "error": "Description of failure"
}
```

## Performance Notes

- **Knowledge search**: Powered by pgvector (768-dim embeddings via nomic-embed-text), sub-100ms latency
- **Email**: Gmail API calls cached for 5 minutes
- **Data queries**: PostgreSQL with query timeout (30s default)
- **External APIs**: Rate-limited per integration config (Meta, Google, TikTok)

## Debugging Tools

### Check Tool Server Status
```bash
curl http://localhost:8087/health
```

### List Available Tools
```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8087/tools
```

### View Tool Schema
```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8087/tools/execute_sql_query/schema
```

### Test Tool Call
```bash
curl -X POST http://localhost:8087/tools/execute_sql_query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT 1"}'
```

## Integration with Copilot

Copilot can:
1. **Discover tools**: Query `/tools` endpoint to enumerate available tools
2. **Validate calls**: Check tool schema before agent invocation
3. **Execute**: Call MCP endpoints with proper authentication
4. **Monitor**: Track tool usage and performance in RL experiences

For integration details, see `apps/api/services/mcp_client.py` and `cli_session_manager.py`.

---

**Note**: MCP server is included in Docker Compose stack. For production Kubernetes deployment, see `helm/values/mcp-server.yaml`.
