"""
Generic Data Source Sync Workflow

This workflow handles syncing data from any connector type to the datalake.
It orchestrates extraction, staging, and loading activities.
"""

from temporalio import workflow
from datetime import timedelta
from typing import Dict, Any


@workflow.defn(sandboxed=False)
class DataSourceSyncWorkflow:
    """
    Generic workflow for syncing any data source to the datalake.

    Steps:
    1. Connect and validate source
    2. Extract data (full or incremental)
    3. Stage to cloud storage (GCS/S3)
    4. Load to Databricks (Bronze → Silver)
    5. Update sync metadata

    Supports:
    - Full refresh sync
    - Incremental sync (with watermark column)
    - Schema detection
    """

    @workflow.run
    async def run(
        self,
        connector_id: str,
        connector_type: str,
        tenant_id: str,
        sync_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute data source sync workflow.

        Args:
            connector_id: UUID of the connector
            connector_type: Type of connector (snowflake, postgres, etc.)
            tenant_id: UUID of tenant for isolation
            sync_config: Configuration for this sync run:
                - mode: "full" or "incremental"
                - table_name: Source table/query to sync
                - watermark_column: Column for incremental sync (optional)
                - last_watermark: Last sync watermark value (optional)
                - target_dataset: Name for the target dataset

        Returns:
            Dict with status, rows_synced, target tables, and new watermark
        """
        workflow.logger.info(f"Starting data source sync for connector {connector_id}")

        sync_mode = sync_config.get("mode", "full")
        table_name = sync_config.get("table_name")
        target_dataset = sync_config.get("target_dataset", table_name)

        # Step 1: Extract data from source
        workflow.logger.info(f"Extracting data from {connector_type}:{table_name}")

        extract_result = await workflow.execute_activity(
            "extract_from_connector",
            args=[connector_id, connector_type, tenant_id, sync_config],
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=workflow.RetryPolicy(
                maximum_attempts=3,
                initial_interval=timedelta(seconds=30),
                maximum_interval=timedelta(minutes=5),
                backoff_coefficient=2.0
            )
        )

        if not extract_result.get("success"):
            raise Exception(f"Extraction failed: {extract_result.get('error')}")

        staging_path = extract_result.get("staging_path")
        row_count = extract_result.get("row_count", 0)
        schema = extract_result.get("schema", [])
        new_watermark = extract_result.get("new_watermark")

        workflow.logger.info(f"Extracted {row_count} rows to {staging_path}")

        # Step 2: Load to Databricks Bronze
        workflow.logger.info("Loading to Databricks Bronze layer")

        bronze_result = await workflow.execute_activity(
            "load_to_bronze",
            args=[tenant_id, target_dataset, staging_path, schema],
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=workflow.RetryPolicy(
                maximum_attempts=3,
                initial_interval=timedelta(minutes=1)
            )
        )

        bronze_table = bronze_result.get("bronze_table")
        workflow.logger.info(f"Bronze table created: {bronze_table}")

        # Step 3: Transform to Silver layer
        workflow.logger.info("Transforming to Silver layer")

        silver_result = await workflow.execute_activity(
            "load_to_silver",
            args=[tenant_id, bronze_table],
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=workflow.RetryPolicy(
                maximum_attempts=3,
                initial_interval=timedelta(minutes=1)
            )
        )

        silver_table = silver_result.get("silver_table")
        workflow.logger.info(f"Silver table created: {silver_table}")

        # Step 4: Update sync metadata
        await workflow.execute_activity(
            "update_sync_metadata",
            args=[connector_id, tenant_id, {
                "last_sync_at": workflow.now().isoformat(),
                "last_sync_status": "success",
                "rows_synced": row_count,
                "bronze_table": bronze_table,
                "silver_table": silver_table,
                "new_watermark": new_watermark
            }],
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=workflow.RetryPolicy(maximum_attempts=5)
        )

        workflow.logger.info(f"Data source sync complete for connector {connector_id}")

        return {
            "status": "success",
            "rows_synced": row_count,
            "bronze_table": bronze_table,
            "silver_table": silver_table,
            "new_watermark": new_watermark,
            "sync_mode": sync_mode
        }


@workflow.defn(sandboxed=False)
class ScheduledSyncWorkflow:
    """
    Parent workflow for scheduled syncs.

    This workflow is triggered by the scheduler and orchestrates
    multiple table syncs for a single connector.
    """

    @workflow.run
    async def run(
        self,
        connector_id: str,
        connector_type: str,
        tenant_id: str,
        tables: list[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Run scheduled sync for multiple tables.

        Args:
            connector_id: UUID of the connector
            connector_type: Type of connector
            tenant_id: UUID of tenant
            tables: List of table configs to sync

        Returns:
            Summary of sync results
        """
        workflow.logger.info(f"Starting scheduled sync for {len(tables)} tables")

        results = []
        errors = []

        for table_config in tables:
            try:
                # Run child workflow for each table
                result = await workflow.execute_child_workflow(
                    DataSourceSyncWorkflow.run,
                    args=[connector_id, connector_type, tenant_id, table_config],
                    id=f"sync-{connector_id}-{table_config.get('table_name')}-{workflow.now().timestamp()}",
                    task_queue="servicetsunami-databricks"
                )
                results.append({
                    "table": table_config.get("table_name"),
                    "status": "success",
                    "rows_synced": result.get("rows_synced", 0)
                })
            except Exception as e:
                workflow.logger.error(f"Failed to sync {table_config.get('table_name')}: {e}")
                errors.append({
                    "table": table_config.get("table_name"),
                    "status": "failed",
                    "error": str(e)
                })

        return {
            "status": "completed" if not errors else "partial",
            "tables_synced": len(results),
            "tables_failed": len(errors),
            "results": results,
            "errors": errors
        }
