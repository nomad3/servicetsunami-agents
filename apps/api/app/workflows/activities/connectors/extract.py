"""
Data extraction activities for the data source sync workflow.

These activities handle:
1. Extracting data from various connector types
2. Staging data to cloud storage
3. Loading to Databricks Bronze/Silver layers
4. Updating sync metadata
"""

from temporalio import activity
from typing import Dict, Any, List
from datetime import datetime
import os
import tempfile
import uuid

from app.db.session import SessionLocal
from app.models.connector import Connector
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _get_connector_config(db, connector_id: str, tenant_id: str) -> Dict[str, Any]:
    """Fetch connector configuration from database."""
    connector = db.query(Connector).filter(
        Connector.id == connector_id,
        Connector.tenant_id == tenant_id
    ).first()

    if not connector:
        raise ValueError(f"Connector {connector_id} not found for tenant {tenant_id}")

    return {
        "type": connector.type,
        "config": connector.config,
        "name": connector.name
    }


async def _extract_from_snowflake(config: Dict, sync_config: Dict) -> Dict[str, Any]:
    """Extract data from Snowflake."""
    try:
        import snowflake.connector
        import pandas as pd

        conn = snowflake.connector.connect(
            account=config.get("account"),
            user=config.get("user"),
            password=config.get("password"),
            warehouse=config.get("warehouse"),
            database=config.get("database"),
            schema=config.get("schema", "PUBLIC"),
        )

        table_name = sync_config.get("table_name")
        mode = sync_config.get("mode", "full")
        watermark_column = sync_config.get("watermark_column")
        last_watermark = sync_config.get("last_watermark")

        # Build query
        if mode == "incremental" and watermark_column and last_watermark:
            query = f"SELECT * FROM {table_name} WHERE {watermark_column} > '{last_watermark}'"
        else:
            query = f"SELECT * FROM {table_name}"

        cursor = conn.cursor()
        cursor.execute(query)

        # Get schema
        columns = [desc[0] for desc in cursor.description]
        schema = [{"name": col, "type": "string"} for col in columns]

        # Fetch data
        rows = cursor.fetchall()
        df = pd.DataFrame(rows, columns=columns)

        # Get new watermark if incremental
        new_watermark = None
        if watermark_column and len(df) > 0:
            new_watermark = str(df[watermark_column].max())

        cursor.close()
        conn.close()

        # Save to temp parquet file
        temp_dir = tempfile.mkdtemp()
        parquet_path = os.path.join(temp_dir, f"{uuid.uuid4()}.parquet")
        df.to_parquet(parquet_path, index=False)

        return {
            "success": True,
            "staging_path": parquet_path,
            "row_count": len(df),
            "schema": schema,
            "new_watermark": new_watermark
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


async def _extract_from_postgres(config: Dict, sync_config: Dict) -> Dict[str, Any]:
    """Extract data from PostgreSQL."""
    try:
        import psycopg2
        import pandas as pd

        conn = psycopg2.connect(
            host=config.get("host"),
            port=config.get("port", 5432),
            database=config.get("database"),
            user=config.get("user"),
            password=config.get("password"),
        )

        table_name = sync_config.get("table_name")
        mode = sync_config.get("mode", "full")
        watermark_column = sync_config.get("watermark_column")
        last_watermark = sync_config.get("last_watermark")

        # Build query
        if mode == "incremental" and watermark_column and last_watermark:
            query = f"SELECT * FROM {table_name} WHERE {watermark_column} > %s"
            df = pd.read_sql(query, conn, params=[last_watermark])
        else:
            query = f"SELECT * FROM {table_name}"
            df = pd.read_sql(query, conn)

        # Get schema
        schema = [{"name": col, "type": str(df[col].dtype)} for col in df.columns]

        # Get new watermark if incremental
        new_watermark = None
        if watermark_column and len(df) > 0:
            new_watermark = str(df[watermark_column].max())

        conn.close()

        # Save to temp parquet file
        temp_dir = tempfile.mkdtemp()
        parquet_path = os.path.join(temp_dir, f"{uuid.uuid4()}.parquet")
        df.to_parquet(parquet_path, index=False)

        return {
            "success": True,
            "staging_path": parquet_path,
            "row_count": len(df),
            "schema": schema,
            "new_watermark": new_watermark
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


async def _extract_from_mysql(config: Dict, sync_config: Dict) -> Dict[str, Any]:
    """Extract data from MySQL."""
    try:
        import mysql.connector
        import pandas as pd

        conn = mysql.connector.connect(
            host=config.get("host"),
            port=config.get("port", 3306),
            database=config.get("database"),
            user=config.get("user"),
            password=config.get("password"),
        )

        table_name = sync_config.get("table_name")
        mode = sync_config.get("mode", "full")
        watermark_column = sync_config.get("watermark_column")
        last_watermark = sync_config.get("last_watermark")

        # Build query
        if mode == "incremental" and watermark_column and last_watermark:
            query = f"SELECT * FROM {table_name} WHERE {watermark_column} > %s"
            df = pd.read_sql(query, conn, params=[last_watermark])
        else:
            query = f"SELECT * FROM {table_name}"
            df = pd.read_sql(query, conn)

        schema = [{"name": col, "type": str(df[col].dtype)} for col in df.columns]

        new_watermark = None
        if watermark_column and len(df) > 0:
            new_watermark = str(df[watermark_column].max())

        conn.close()

        temp_dir = tempfile.mkdtemp()
        parquet_path = os.path.join(temp_dir, f"{uuid.uuid4()}.parquet")
        df.to_parquet(parquet_path, index=False)

        return {
            "success": True,
            "staging_path": parquet_path,
            "row_count": len(df),
            "schema": schema,
            "new_watermark": new_watermark
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


async def _extract_from_s3(config: Dict, sync_config: Dict) -> Dict[str, Any]:
    """Extract data from S3."""
    try:
        import boto3
        import pandas as pd

        s3 = boto3.client(
            "s3",
            region_name=config.get("region", "us-east-1"),
            aws_access_key_id=config.get("access_key"),
            aws_secret_access_key=config.get("secret_key"),
        )

        bucket = config.get("bucket")
        prefix = sync_config.get("prefix", config.get("prefix", ""))
        _file_pattern = sync_config.get("file_pattern", "*.parquet")  # noqa: F841

        # List objects
        response = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)

        dfs = []
        for obj in response.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".parquet"):
                temp_file = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
                s3.download_file(bucket, key, temp_file.name)
                df = pd.read_parquet(temp_file.name)
                dfs.append(df)
                os.unlink(temp_file.name)

        if not dfs:
            return {"success": True, "staging_path": None, "row_count": 0, "schema": [], "new_watermark": None}

        combined_df = pd.concat(dfs, ignore_index=True)
        schema = [{"name": col, "type": str(combined_df[col].dtype)} for col in combined_df.columns]

        temp_dir = tempfile.mkdtemp()
        parquet_path = os.path.join(temp_dir, f"{uuid.uuid4()}.parquet")
        combined_df.to_parquet(parquet_path, index=False)

        return {
            "success": True,
            "staging_path": parquet_path,
            "row_count": len(combined_df),
            "schema": schema,
            "new_watermark": None
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


async def _extract_from_gcs(config: Dict, sync_config: Dict) -> Dict[str, Any]:
    """Extract data from Google Cloud Storage."""
    try:
        from google.cloud import storage
        import pandas as pd

        client = storage.Client(project=config.get("project_id"))
        bucket = client.bucket(config.get("bucket"))
        prefix = sync_config.get("prefix", config.get("prefix", ""))

        blobs = bucket.list_blobs(prefix=prefix)

        dfs = []
        for blob in blobs:
            if blob.name.endswith(".parquet"):
                temp_file = tempfile.NamedTemporaryFile(suffix=".parquet", delete=False)
                blob.download_to_filename(temp_file.name)
                df = pd.read_parquet(temp_file.name)
                dfs.append(df)
                os.unlink(temp_file.name)

        if not dfs:
            return {"success": True, "staging_path": None, "row_count": 0, "schema": [], "new_watermark": None}

        combined_df = pd.concat(dfs, ignore_index=True)
        schema = [{"name": col, "type": str(combined_df[col].dtype)} for col in combined_df.columns]

        temp_dir = tempfile.mkdtemp()
        parquet_path = os.path.join(temp_dir, f"{uuid.uuid4()}.parquet")
        combined_df.to_parquet(parquet_path, index=False)

        return {
            "success": True,
            "staging_path": parquet_path,
            "row_count": len(combined_df),
            "schema": schema,
            "new_watermark": None
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


async def _extract_from_api(config: Dict, sync_config: Dict) -> Dict[str, Any]:
    """Extract data from REST API."""
    try:
        import httpx
        import pandas as pd

        base_url = config.get("base_url")
        endpoint = sync_config.get("endpoint", "")
        auth_type = config.get("auth_type", "none")

        headers = {}
        if auth_type == "api_key":
            header_name = config.get("api_key_header", "Authorization")
            headers[header_name] = config.get("api_key")
        elif auth_type == "bearer":
            headers["Authorization"] = f"Bearer {config.get('bearer_token')}"

        url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}" if endpoint else base_url

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=60.0)
            response.raise_for_status()
            data = response.json()

        # Handle different response structures
        if isinstance(data, list):
            df = pd.DataFrame(data)
        elif isinstance(data, dict):
            # Try common patterns
            for key in ["data", "results", "items", "records"]:
                if key in data and isinstance(data[key], list):
                    df = pd.DataFrame(data[key])
                    break
            else:
                df = pd.DataFrame([data])
        else:
            return {"success": False, "error": "Unexpected API response format"}

        schema = [{"name": col, "type": str(df[col].dtype)} for col in df.columns]

        temp_dir = tempfile.mkdtemp()
        parquet_path = os.path.join(temp_dir, f"{uuid.uuid4()}.parquet")
        df.to_parquet(parquet_path, index=False)

        return {
            "success": True,
            "staging_path": parquet_path,
            "row_count": len(df),
            "schema": schema,
            "new_watermark": None
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


# Extraction registry
EXTRACTORS = {
    "snowflake": _extract_from_snowflake,
    "postgres": _extract_from_postgres,
    "mysql": _extract_from_mysql,
    "s3": _extract_from_s3,
    "gcs": _extract_from_gcs,
    "api": _extract_from_api,
}


@activity.defn
async def extract_from_connector(
    connector_id: str,
    connector_type: str,
    tenant_id: str,
    sync_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Extract data from a connector.

    Args:
        connector_id: UUID of the connector
        connector_type: Type of connector
        tenant_id: UUID of tenant
        sync_config: Sync configuration

    Returns:
        Dict with staging_path, row_count, schema, new_watermark
    """
    activity.logger.info(f"Extracting from {connector_type} connector {connector_id}")

    db = SessionLocal()
    try:
        connector_data = _get_connector_config(db, connector_id, tenant_id)
        config = connector_data["config"]

        extractor = EXTRACTORS.get(connector_type)
        if not extractor:
            return {"success": False, "error": f"Unsupported connector type: {connector_type}"}

        result = await extractor(config, sync_config)
        return result

    except Exception as e:
        activity.logger.error(f"Extraction failed: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@activity.defn
async def load_to_bronze(
    tenant_id: str,
    dataset_name: str,
    staging_path: str,
    schema: List[Dict[str, str]]
) -> Dict[str, Any]:
    """
    Load staged data to Databricks Bronze layer.

    Args:
        tenant_id: UUID of tenant
        dataset_name: Name for the target dataset
        staging_path: Path to staged parquet file
        schema: Schema of the data

    Returns:
        Dict with bronze_table name
    """
    activity.logger.info(f"Loading to Bronze: {dataset_name}")

    if not staging_path:
        return {"bronze_table": None, "row_count": 0}

    # For now, just return the expected table name
    # In production, this would upload to DBFS and create the table
    table_name = f"bronze_{dataset_name.replace('-', '_').replace(' ', '_').lower()}"
    catalog = f"servicetsunami_{tenant_id.replace('-', '_')}"

    bronze_table = f"{catalog}.default.{table_name}"

    activity.logger.info(f"Bronze table would be: {bronze_table}")

    return {"bronze_table": bronze_table, "staging_path": staging_path}


@activity.defn
async def load_to_silver(
    tenant_id: str,
    bronze_table: str
) -> Dict[str, Any]:
    """
    Transform Bronze to Silver layer.

    Args:
        tenant_id: UUID of tenant
        bronze_table: Bronze table name

    Returns:
        Dict with silver_table name
    """
    activity.logger.info(f"Loading to Silver from: {bronze_table}")

    if not bronze_table:
        return {"silver_table": None}

    silver_table = bronze_table.replace("bronze_", "silver_")

    activity.logger.info(f"Silver table would be: {silver_table}")

    return {"silver_table": silver_table}


@activity.defn
async def update_sync_metadata(
    connector_id: str,
    tenant_id: str,
    metadata: Dict[str, Any]
) -> None:
    """
    Update connector sync metadata.

    Args:
        connector_id: UUID of the connector
        tenant_id: UUID of tenant
        metadata: Sync metadata to update
    """
    activity.logger.info(f"Updating sync metadata for connector {connector_id}")

    db = SessionLocal()
    try:
        connector = db.query(Connector).filter(
            Connector.id == connector_id,
            Connector.tenant_id == tenant_id
        ).first()

        if connector:
            # Merge metadata
            if not connector.config:
                connector.config = {}

            connector.config["sync_metadata"] = metadata
            connector.status = "active"
            connector.last_test_at = datetime.utcnow()
            connector.last_test_error = None

            db.commit()
            activity.logger.info(f"Sync metadata updated for {connector_id}")

    except Exception as e:
        activity.logger.error(f"Failed to update sync metadata: {e}")
        db.rollback()
        raise
    finally:
        db.close()
