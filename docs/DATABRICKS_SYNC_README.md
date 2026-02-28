# Databricks Dataset Sync

Automatic synchronization of uploaded datasets to Databricks Unity Catalog with Bronze and Silver layer medallion architecture.

## Overview

ServiceTsunami automatically syncs uploaded datasets to Databricks Unity Catalog using a durable Temporal workflow orchestration pattern. This integration provides:

- **Instant Local Access**: Datasets stored as Parquet files with DuckDB querying (< 2 seconds)
- **Background Sync**: Asynchronous Temporal workflows sync to Databricks without blocking uploads
- **Medallion Architecture**: Bronze (raw) and Silver (cleaned) layers in Unity Catalog
- **Graceful Degradation**: Local data always available; Databricks is additive enhancement
- **Multi-tenant Isolation**: Tenant-specific catalogs and schemas in Unity Catalog

## Architecture

### Components

```
Dataset Upload → ServiceTsunami API → Local Parquet Storage
                         ↓
                 Temporal Workflow (async)
                         ↓
                    MCP Server → Downloads Parquet via HTTP
                         ↓
                 Databricks Unity Catalog
                         ↓
                 Bronze Table (external) + Silver Table (managed)
```

### Data Flow

1. **Upload**: User uploads CSV/Excel via `/api/v1/datasets/ingest`
2. **Local Storage**: ServiceTsunami converts to Parquet and stores locally
3. **Workflow Trigger**: If `DATABRICKS_AUTO_SYNC=true`, Temporal workflow starts
4. **Bronze Creation**: MCP server downloads Parquet, uploads to DBFS, creates external table
5. **Silver Transformation**: MCP server applies type inference and cleaning, creates managed table
6. **Metadata Update**: Dataset metadata updated with table names and sync status

### Technologies

- **Temporal**: Durable workflow orchestration with automatic retries
- **MCP Server**: Microservice handling Databricks API interactions
- **Unity Catalog**: Databricks lakehouse with governance and multi-tenancy
- **Parquet**: Columnar storage format for efficient data transfer

## Configuration

### Required Environment Variables

Add to `apps/api/.env`:

```bash
# MCP Server Configuration
MCP_SERVER_URL=http://localhost:8085
MCP_API_KEY=your-shared-secret-key-here
MCP_ENABLED=true

# Databricks Sync Configuration
DATABRICKS_SYNC_ENABLED=true
DATABRICKS_AUTO_SYNC=true
DATABRICKS_RETRY_ATTEMPTS=3
DATABRICKS_RETRY_INTERVAL=300

# Temporal Configuration
TEMPORAL_ADDRESS=temporal:7233
TEMPORAL_NAMESPACE=default
```

### Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABRICKS_SYNC_ENABLED` | `true` | Master switch for Databricks integration |
| `DATABRICKS_AUTO_SYNC` | `true` | Automatically sync datasets on upload |
| `DATABRICKS_RETRY_ATTEMPTS` | `3` | Number of retry attempts for failed syncs |
| `DATABRICKS_RETRY_INTERVAL` | `300` | Seconds between retry attempts |
| `MCP_SERVER_URL` | `http://localhost:8085` | MCP server endpoint |
| `MCP_API_KEY` | - | Shared secret for MCP server authentication |
| `MCP_ENABLED` | `true` | Enable MCP server integration |

### Disabling Auto-Sync

To upload datasets without automatic Databricks sync:

```bash
# In apps/api/.env
DATABRICKS_AUTO_SYNC=false
```

Datasets will remain local-only unless manually triggered.

## Usage

### 1. Automatic Sync (Default Behavior)

Upload a dataset via API:

```bash
POST /api/v1/datasets/ingest
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "Revenue Q1 2025",
  "description": "Quarterly revenue data",
  "records": [
    {"order_id": "1001", "amount": 450.00, "date": "2025-01-15"},
    {"order_id": "1002", "amount": 820.50, "date": "2025-01-16"}
  ]
}
```

Response includes sync status:

```json
{
  "id": "abc-123-def-456",
  "name": "Revenue Q1 2025",
  "row_count": 2,
  "file_name": "abc-123-def-456.parquet",
  "metadata": {
    "databricks_enabled": true,
    "sync_status": "pending"
  }
}
```

### 2. Check Sync Status

Query the sync status endpoint:

```bash
GET /api/v1/datasets/{dataset_id}/databricks/status
Authorization: Bearer <token>

Response:
{
  "dataset_id": "abc-123-def-456",
  "dataset_name": "Revenue Q1 2025",
  "databricks_enabled": true,
  "sync_status": "synced",
  "bronze_table": "catalog_tenant_xyz.bronze.revenue_q1_2025",
  "silver_table": "catalog_tenant_xyz.silver.revenue_q1_2025_clean",
  "last_sync_at": "2025-11-06T14:30:00Z",
  "row_count_local": 2,
  "row_count_databricks": 2
}
```

### 3. Sync Status Values

| Status | Description |
|--------|-------------|
| `not_synced` | Dataset is local-only (auto-sync disabled) |
| `pending` | Workflow queued, waiting for worker |
| `syncing` | Workflow in progress (Bronze or Silver creation) |
| `synced` | Successfully synced to Databricks |
| `failed` | Sync failed (check `last_sync_error` field) |

### 4. UI Integration

The web interface displays sync status badges on the Datasets page:

- **Local Only** (gray): Dataset not synced to Databricks
- **Pending Sync** (blue): Workflow queued
- **Syncing...** (yellow): Sync in progress
- **Synced to Databricks** (green): Successfully synced
- **Sync Failed** (red): Error occurred (hover for details)

### 5. Query Options

After sync completes, you have two query options:

**Local DuckDB** (fast, single dataset):
```bash
GET /api/v1/datasets/{id}/query?sql=SELECT * FROM dataset LIMIT 10
```

**Databricks** (slower, supports joins, ML features):
```sql
-- Query Bronze table (raw data)
SELECT * FROM catalog_tenant_xyz.bronze.revenue_q1_2025

-- Query Silver table (cleaned, typed)
SELECT * FROM catalog_tenant_xyz.silver.revenue_q1_2025_clean
```

## Workflow Details

### Temporal Workflow: DatasetSyncWorkflow

The workflow executes three activities in sequence:

#### Activity 1: sync_to_bronze
- Duration: ~30-60 seconds
- Creates external table pointing to uploaded Parquet file
- MCP server downloads file from ServiceTsunami
- Uploads to Databricks DBFS Volume
- Creates Bronze table with schema inference

#### Activity 2: transform_to_silver
- Duration: ~10-30 seconds
- Reads Bronze table
- Applies transformations:
  - Type inference (string → integer, float, timestamp)
  - Null handling
  - Duplicate removal
  - Column name normalization (snake_case)
- Creates Silver managed table

#### Activity 3: update_dataset_metadata
- Duration: < 1 second
- Updates PostgreSQL dataset metadata
- Records table names and sync timestamp

### Retry Policy

Workflows automatically retry on failure:

- **Maximum Attempts**: 3 (configurable via `DATABRICKS_RETRY_ATTEMPTS`)
- **Initial Interval**: 5 minutes (configurable via `DATABRICKS_RETRY_INTERVAL`)
- **Backoff Coefficient**: 2.0 (5 min → 10 min → 20 min)
- **Maximum Interval**: 10 minutes

Common retry scenarios:
- MCP server temporarily unavailable
- Databricks workspace rate limiting
- Network connectivity issues
- Temporary DBFS storage issues

## Monitoring

### 1. Temporal UI

Access the Temporal Web UI at `http://localhost:8233` (development) to:

- View running workflows
- Inspect workflow history
- Check retry attempts
- Debug failed workflows
- View activity logs

Search for workflows by ID pattern: `dataset-sync-{dataset_id}`

### 2. Application Logs

**API Server Logs**:
```bash
docker-compose logs -f api | grep "Databricks sync"
```

**Worker Logs**:
```bash
docker-compose logs -f databricks-worker
```

**MCP Server Logs** (if accessible):
```bash
docker-compose logs -f mcp-server
```

### 3. Dataset Metadata

Query dataset metadata directly:

```bash
GET /api/v1/datasets/{dataset_id}
```

Check the `metadata` field for sync details:
```json
{
  "metadata": {
    "databricks_enabled": true,
    "sync_status": "synced",
    "bronze_table": "...",
    "silver_table": "...",
    "last_sync_at": "2025-11-06T14:30:00Z",
    "last_sync_error": null,
    "row_count_databricks": 1000
  }
}
```

## Troubleshooting

### Dataset Stuck in "pending" Status

**Symptoms**: Dataset remains in "pending" status for more than 5 minutes.

**Possible Causes**:
1. Temporal worker not running
2. Temporal server unreachable
3. Task queue misconfiguration

**Solutions**:
```bash
# Check worker status
docker-compose ps databricks-worker

# View worker logs
docker-compose logs databricks-worker

# Restart worker
docker-compose restart databricks-worker

# Verify Temporal server
curl http://localhost:8233
```

### Sync Fails Repeatedly

**Symptoms**: Dataset status shows "failed" with error message.

**Possible Causes**:
1. MCP server not running or unreachable
2. Databricks credentials invalid
3. Unity Catalog permissions issue
4. Network connectivity problem

**Solutions**:
```bash
# Check MCP server health
curl http://localhost:8085/health

# Verify MCP server logs
docker-compose logs mcp-server

# Check Databricks credentials in MCP server .env
# Verify workspace URL, token, and catalog permissions

# Test network connectivity from API container
docker-compose exec api curl http://mcp-server:8085/health
```

### File Not Found Error from MCP Server

**Symptoms**: MCP server returns 404 when downloading Parquet file.

**Possible Causes**:
1. Internal file endpoint not accessible
2. MCP_API_KEY mismatch
3. File deleted before sync completed
4. Docker network issue

**Solutions**:
```bash
# Test internal endpoint (from API container)
docker-compose exec api curl -H "Authorization: Bearer ${MCP_API_KEY}" \
  http://localhost:8001/internal/storage/datasets/test.parquet

# Verify MCP_API_KEY matches in both services
grep MCP_API_KEY apps/api/.env
grep MCP_API_KEY mcp-server/.env  # if applicable

# Check file exists
docker-compose exec api ls -la /app/storage/datasets/
```

### Bronze Table Created but Silver Fails

**Symptoms**: Bronze table exists in Unity Catalog but Silver table creation fails.

**Possible Causes**:
1. Transformation logic error
2. Data type incompatibility
3. Insufficient Databricks compute resources

**Solutions**:
```bash
# Check workflow details in Temporal UI
# Navigate to failed workflow → Activity: transform_to_silver → Stack Trace

# Verify Bronze table in Databricks
# Run: DESCRIBE TABLE catalog.bronze.table_name

# Check MCP server transformation logs
docker-compose logs mcp-server | grep "transform_to_silver"
```

### Workflow Timing Out

**Symptoms**: Workflow fails with timeout error after 5-10 minutes.

**Possible Causes**:
1. Large dataset (> 100MB)
2. Slow network connection
3. Databricks cluster cold start

**Solutions**:
```bash
# Increase activity timeout in workflow definition
# Edit: apps/api/app/workflows/dataset_sync.py
# Update: start_to_close_timeout=timedelta(minutes=10)

# Monitor dataset size before upload
# Consider chunking large datasets

# Use Databricks serverless SQL warehouse for faster cold starts
```

### Local Query Works but Databricks Query Fails

**Symptoms**: DuckDB query succeeds but Databricks SQL fails.

**Possible Causes**:
1. Table not fully synced
2. Databricks warehouse not running
3. SQL syntax differences

**Solutions**:
```sql
-- Verify table exists
SHOW TABLES IN catalog.bronze;
SHOW TABLES IN catalog.silver;

-- Check table schema
DESCRIBE TABLE catalog.bronze.table_name;

-- Verify row counts match
SELECT COUNT(*) FROM catalog.bronze.table_name;
SELECT COUNT(*) FROM catalog.silver.table_name;
```

## Security Considerations

### Internal File Endpoint

The `/internal/storage/datasets/{file_name}` endpoint is protected by:

1. **Authentication**: Requires `MCP_API_KEY` in `Authorization: Bearer` header
2. **Path Validation**: Prevents directory traversal attacks (`..`, `/`, `\`)
3. **File Type Validation**: Only serves files from `datasets/` subdirectory
4. **Network Isolation**: Should NOT be exposed via Nginx (internal only)

**Production Configuration**:

Ensure Nginx configuration does NOT proxy `/internal/*` paths:

```nginx
# In /etc/nginx/sites-available/servicetsunami.com
location /api/ {
    proxy_pass http://localhost:8001/api/;
    # ... other directives
}

# DO NOT add this (intentionally omitted):
# location /internal/ {
#     proxy_pass http://localhost:8001/internal/;
# }
```

### MCP API Key Management

The `MCP_API_KEY` is a shared secret between ServiceTsunami and MCP server:

```bash
# Generate secure random key
openssl rand -hex 32

# Set in both .env files
# apps/api/.env
MCP_API_KEY=abc123xyz456...

# mcp-server/.env (if separate deployment)
MCP_API_KEY=abc123xyz456...
```

Rotate keys periodically in production environments.

### Multi-tenant Isolation

Databricks catalogs are created per tenant:

```
catalog_tenant_{tenant_id}
  ├── bronze
  │   └── {dataset_name}
  └── silver
      └── {dataset_name}_clean
```

Unity Catalog permissions ensure tenants cannot access each other's data.

## Performance Optimization

### Dataset Upload (Local)

- **Target**: < 2 seconds for datasets up to 10,000 rows
- **Optimization**: Parquet compression (snappy), columnar storage
- **Monitoring**: Track `dataset.created_at` to `workflow.started_at` delta

### Databricks Sync

- **Target**: < 60 seconds for datasets up to 100,000 rows
- **Bronze Creation**: ~30 seconds (file upload + external table DDL)
- **Silver Creation**: ~20 seconds (CTAS query + type inference)

**Optimization Tips**:
1. Use Databricks Serverless SQL for faster cold starts
2. Pre-warm Bronze schema with common column types
3. Batch multiple small datasets into single workflow run
4. Use Unity Catalog volumes for faster DBFS access

### Query Performance

| Query Type | Latency | Use Case |
|------------|---------|----------|
| Local DuckDB | 10-100ms | Single dataset, exploratory queries |
| Databricks Bronze | 1-5s | Raw data access, initial exploration |
| Databricks Silver | 500ms-2s | Clean data, production analytics |

## Development Setup

### Prerequisites

1. Docker and Docker Compose installed
2. Temporal server running (via `docker-compose up temporal`)
3. MCP server configured with Databricks credentials
4. PostgreSQL database initialized

### Local Testing

**1. Start services**:
```bash
docker-compose up -d db temporal mcp-server
docker-compose up --build api databricks-worker
```

**2. Verify worker started**:
```bash
docker-compose logs databricks-worker | grep "started successfully"
```

**3. Upload test dataset**:
```bash
curl -X POST http://localhost:8001/api/v1/datasets/ingest \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Dataset",
    "records": [{"id": 1, "value": 100}]
  }'
```

**4. Monitor workflow**:
- Open Temporal UI: `http://localhost:8233`
- Search for workflow: `dataset-sync-*`
- Watch activity progress

**5. Check sync status**:
```bash
curl http://localhost:8001/api/v1/datasets/{dataset_id}/databricks/status \
  -H "Authorization: Bearer ${TOKEN}"
```

### Running Tests

**Unit Tests**:
```bash
cd apps/api
pytest tests/test_config.py -v
pytest tests/test_mcp_client.py -v
pytest tests/test_internal_endpoints.py -v
```

**Workflow Tests**:
```bash
cd apps/api
pytest tests/workflows/test_dataset_sync_workflow.py -v
```

**Integration Tests** (requires MCP server):
```bash
cd apps/api
pytest tests/integration/test_databricks_sync_e2e.py -v -s
```

Skip integration tests if MCP not available:
```bash
MCP_ENABLED=false pytest tests/integration/ -v
```

## Production Deployment

### Pre-Deployment Checklist

- [ ] MCP server deployed and accessible from API network
- [ ] Databricks workspace configured with Unity Catalog
- [ ] Databricks service principal created with catalog permissions
- [ ] MCP_API_KEY generated and set in both services
- [ ] Temporal server running (included in `deploy.sh`)
- [ ] DATA_STORAGE_PATH has sufficient disk space

### Deployment Steps

**1. Update environment variables**:
```bash
# On production server: apps/api/.env
MCP_SERVER_URL=http://mcp-server:8085
MCP_API_KEY=<production-key>
DATABRICKS_SYNC_ENABLED=true
DATABRICKS_AUTO_SYNC=true
```

**2. Run deployment script**:
```bash
./deploy.sh
```

The script will:
- Build and start all services including `databricks-worker`
- Configure Nginx (internal endpoint NOT exposed)
- Run E2E tests to verify deployment

**3. Verify worker started**:
```bash
docker-compose ps databricks-worker
docker-compose logs databricks-worker | grep "started successfully"
```

**4. Test with sample dataset**:
```bash
# Upload via web UI or API
# Monitor in Temporal UI: http://production-ip:8233
```

### Monitoring in Production

**Key Metrics to Track**:

1. **Workflow Success Rate**: Track `sync_status=synced` vs `sync_status=failed`
2. **Sync Duration**: Monitor time from upload to sync completion
3. **Retry Rate**: Count workflows requiring > 1 attempt
4. **Worker Health**: Ensure `databricks-worker` container stays running

**Alerting Recommendations**:

```yaml
# Example Prometheus alerts
- alert: DatabricksSyncFailureRate
  expr: rate(datasets_sync_failed_total[5m]) > 0.1
  annotations:
    summary: "High Databricks sync failure rate"

- alert: DatabricksWorkerDown
  expr: up{job="databricks-worker"} == 0
  annotations:
    summary: "Databricks worker is down"
```

## Limitations

### Current Limitations

1. **Dataset Size**: Optimal for datasets up to 100,000 rows; larger datasets may timeout
2. **File Format**: Only Parquet files supported (CSV/Excel converted on upload)
3. **Schema Evolution**: Changing dataset schema requires re-upload
4. **Concurrent Uploads**: No batching; each dataset triggers separate workflow
5. **Delete Sync**: Deleting dataset from ServiceTsunami doesn't delete Databricks tables

### Future Enhancements

- [ ] Incremental sync for append operations
- [ ] Batch workflow for multiple datasets
- [ ] Gold layer with business logic transformations
- [ ] Schema evolution handling
- [ ] Databricks table deletion on dataset delete
- [ ] Query federation (join local + Databricks tables)
- [ ] Delta Lake format support
- [ ] Streaming sync for real-time data

## FAQ

**Q: What happens if Databricks is unavailable during upload?**

A: Dataset upload completes successfully (local storage). Temporal workflow retries automatically every 5 minutes (3 attempts). If all retries fail, dataset remains local-only.

**Q: Can I disable Databricks sync for specific datasets?**

A: Currently, sync is controlled globally via `DATABRICKS_AUTO_SYNC`. Future enhancement: per-dataset sync flag.

**Q: How do I delete Databricks tables?**

A: Currently manual (delete via Databricks UI or SQL). Future endpoint: `DELETE /api/v1/datasets/{id}/databricks`.

**Q: Can I query across multiple datasets in Databricks?**

A: Yes! Silver tables support standard SQL joins:
```sql
SELECT a.*, b.category
FROM catalog_tenant_xyz.silver.sales_data_clean a
JOIN catalog_tenant_xyz.silver.product_catalog_clean b
  ON a.product_id = b.id
```

**Q: What's the cost impact of Databricks sync?**

A: Bronze tables (external) incur minimal cost (DBFS storage only). Silver tables (managed) incur compute cost during CTAS query. Use Databricks cost monitoring to track per-tenant usage.

**Q: How do I test without Databricks?**

A: Set `DATABRICKS_SYNC_ENABLED=false` or `MCP_ENABLED=false`. All features work locally with DuckDB.

## Additional Resources

- **Implementation Plan**: `docs/plans/2025-11-06-databricks-dataset-sync-implementation.md`
- **MCP Integration Architecture**: `SERVICETSUNAMI_MCP_INTEGRATION.md`
- **Temporal Workflows Guide**: `apps/api/app/workflows/README.md` (if exists)
- **Databricks Unity Catalog Docs**: https://docs.databricks.com/data-governance/unity-catalog/

## Support

For issues or questions:

1. Check troubleshooting section above
2. Review workflow logs in Temporal UI
3. Inspect MCP server logs
4. File GitHub issue with:
   - Dataset size and row count
   - Workflow ID from Temporal
   - Error message from sync status endpoint
   - Relevant container logs
