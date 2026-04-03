from typing import List, Dict, Any

from sqlalchemy.orm import Session
import uuid

from app.models.data_pipeline import DataPipeline
from app.models.pipeline_run import PipelineRun
from app.schemas.data_pipeline import DataPipelineCreate, DataPipelineBase
from app.core.config import settings
from app.services import data_source as data_source_service
from app.services import connectors as connector_service
import requests

def get_data_pipeline(db: Session, data_pipeline_id: uuid.UUID) -> DataPipeline | None:
    return db.query(DataPipeline).filter(DataPipeline.id == data_pipeline_id).first()

def get_data_pipelines_by_tenant(db: Session, tenant_id: uuid.UUID, skip: int = 0, limit: int = 100) -> List[DataPipeline]:
    return db.query(DataPipeline).filter(DataPipeline.tenant_id == tenant_id).offset(skip).limit(limit).all()

def create_tenant_data_pipeline(db: Session, *, item_in: DataPipelineCreate, tenant_id: uuid.UUID) -> DataPipeline:
    db_item = DataPipeline(**item_in.dict(), tenant_id=tenant_id)
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item

def update_data_pipeline(db: Session, *, db_obj: DataPipeline, obj_in: DataPipelineBase) -> DataPipeline:
    if isinstance(obj_in, dict):
        update_data = obj_in
    else:
        update_data = obj_in.dict(exclude_unset=True)

    for field in update_data:
        if hasattr(db_obj, field):
            setattr(db_obj, field, update_data[field])

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def delete_data_pipeline(db: Session, *, data_pipeline_id: uuid.UUID) -> DataPipeline | None:
    data_pipeline = db.query(DataPipeline).filter(DataPipeline.id == data_pipeline_id).first()
    if data_pipeline:
        db.delete(data_pipeline)
        db.commit()
    return data_pipeline

async def execute_pipeline(db: Session, data_pipeline_id: uuid.UUID) -> Dict[str, Any]:
    """
    Execute a data pipeline by triggering the associated Temporal workflow.

    Supports pipeline types:
    - databricks_job: Run a Databricks notebook
    - connector_sync: Sync data from a connector to datalake
    - schedule/sync/alert: Execute an agent kit workflow
    """
    pipeline = get_data_pipeline(db, data_pipeline_id)
    if not pipeline:
        raise ValueError("Data pipeline not found")

    config = pipeline.config or {}
    pipeline_type = config.get("type")

    # Create PipelineRun record
    from datetime import datetime

    run_id = uuid.uuid4()
    pipeline_run = PipelineRun(
        id=run_id,
        pipeline_id=pipeline.id,
        status="running",
        started_at=datetime.utcnow()
    )
    db.add(pipeline_run)
    db.commit()

    try:
        result = {}
        # Route to appropriate handler based on pipeline type
        if pipeline_type == "databricks_job":
            result = await _execute_databricks_job(db, pipeline, config)
        elif pipeline_type == "connector_sync":
            result = await _execute_connector_sync(db, pipeline, config)
        else:
            result = await _execute_agent_kit_workflow(db, pipeline, config)

        # Update PipelineRun with success
        pipeline_run.workflow_id = result.get("workflow_id")
        pipeline_run.output = result
        db.commit()

        return result

    except Exception as e:
        # Update PipelineRun with failure
        pipeline_run.status = "failed"
        pipeline_run.error = str(e)
        pipeline_run.completed_at = datetime.utcnow()
        db.commit()
        raise e


async def _execute_connector_sync(db: Session, pipeline: DataPipeline, config: dict) -> Dict[str, Any]:
    """Execute a connector sync workflow."""
    connector_id = config.get("connector_id")

    if not connector_id:
        raise ValueError("Missing connector_id for connector sync")

    # Get connector to retrieve type
    connector = connector_service.get_connector(db, uuid.UUID(connector_id))
    if not connector:
        raise ValueError("Connector not found")

    # Build sync configuration
    sync_config = {
        "mode": config.get("mode", "full"),
        "table_name": config.get("table_name"),
        "target_dataset": config.get("target_dataset", config.get("table_name")),
        "watermark_column": config.get("watermark_column"),
        "last_watermark": config.get("last_watermark"),
    }

    # Start dynamic workflow
    from app.services.dynamic_workflow_launcher import start_dynamic_workflow_by_name

    temporal_wf_id = await start_dynamic_workflow_by_name(
        "Data Source Sync", str(pipeline.tenant_id),
        input_data={
            "connector_id": str(connector_id),
            "connector_type": connector.type,
            "sync_config": sync_config,
        },
    )

    return {
        "status": "started",
        "workflow_id": temporal_wf_id,
        "connector_type": connector.type,
        "table_name": sync_config.get("table_name")
    }


async def _execute_databricks_job(db: Session, pipeline: DataPipeline, config: dict) -> Dict[str, Any]:
    """Execute a Databricks notebook job."""
    data_source_id = config.get("data_source_id")
    notebook_path = config.get("notebook_path")

    if not data_source_id or not notebook_path:
        raise ValueError("Missing data_source_id or notebook_path for Databricks job")

    data_source = data_source_service.get_data_source(db, uuid.UUID(data_source_id))
    if not data_source:
        raise ValueError("Data source not found")

    ds_config = data_source.config
    host = ds_config.get("host")
    token = ds_config.get("token")
    cluster_id = ds_config.get("cluster_id")

    if not host or not token:
        raise ValueError("Incomplete Databricks configuration (host, token)")

    if not cluster_id:
        raise ValueError("Cluster ID is required in Data Source config to run notebooks")

    # Clean host
    if host.startswith("https://"):
        host = host.replace("https://", "")
    if host.endswith("/"):
        host = host[:-1]

    url = f"https://{host}/api/2.1/jobs/runs/submit"

    payload = {
        "run_name": f"pipeline-{pipeline.name}-{uuid.uuid4()}",
        "existing_cluster_id": cluster_id,
        "notebook_task": {
            "notebook_path": notebook_path
        }
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # We use run_in_executor to avoid blocking the async loop with requests
    import asyncio
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, lambda: requests.post(url, json=payload, headers=headers))

    if response.status_code != 200:
        raise ValueError(f"Databricks API error: {response.text}")

    result = response.json()
    run_id = result.get("run_id")

    return {
        "status": "started",
        "workflow_id": f"databricks-run-{run_id}",
        "run_id": str(run_id),
        "databricks_url": f"https://{host}/#job/runs/{run_id}"
    }
