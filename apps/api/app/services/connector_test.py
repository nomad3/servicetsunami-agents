"""
Service for testing connector configurations and establishing connections.
"""
from typing import Dict, Any


async def test_snowflake_connection(config: Dict[str, Any]) -> Dict[str, Any]:
    """Test Snowflake connection."""
    try:
        import snowflake.connector
        conn = snowflake.connector.connect(
            account=config.get("account"),
            user=config.get("user"),
            password=config.get("password"),
            warehouse=config.get("warehouse"),
            database=config.get("database"),
            schema=config.get("schema", "PUBLIC"),
        )
        cursor = conn.cursor()
        cursor.execute("SELECT CURRENT_VERSION()")
        version = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return {
            "success": True,
            "message": "Connected to Snowflake successfully",
            "metadata": {"version": version}
        }
    except Exception as e:
        return {"success": False, "message": str(e), "metadata": None}


async def test_postgres_connection(config: Dict[str, Any]) -> Dict[str, Any]:
    """Test PostgreSQL connection."""
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=config.get("host"),
            port=config.get("port", 5432),
            database=config.get("database"),
            user=config.get("user"),
            password=config.get("password"),
            sslmode=config.get("ssl_mode", "prefer"),
        )
        cursor = conn.cursor()
        cursor.execute("SELECT version()")
        version = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return {
            "success": True,
            "message": "Connected to PostgreSQL successfully",
            "metadata": {"version": version}
        }
    except Exception as e:
        return {"success": False, "message": str(e), "metadata": None}


async def test_mysql_connection(config: Dict[str, Any]) -> Dict[str, Any]:
    """Test MySQL connection."""
    try:
        import mysql.connector
        conn = mysql.connector.connect(
            host=config.get("host"),
            port=config.get("port", 3306),
            database=config.get("database"),
            user=config.get("user"),
            password=config.get("password"),
        )
        cursor = conn.cursor()
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return {
            "success": True,
            "message": "Connected to MySQL successfully",
            "metadata": {"version": version}
        }
    except Exception as e:
        return {"success": False, "message": str(e), "metadata": None}


async def test_s3_connection(config: Dict[str, Any]) -> Dict[str, Any]:
    """Test S3 connection."""
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            region_name=config.get("region", "us-east-1"),
            aws_access_key_id=config.get("access_key"),
            aws_secret_access_key=config.get("secret_key"),
        )
        bucket = config.get("bucket")
        # Try to list first few objects
        response = s3.list_objects_v2(Bucket=bucket, MaxKeys=1, Prefix=config.get("prefix", ""))
        return {
            "success": True,
            "message": f"Connected to S3 bucket '{bucket}' successfully",
            "metadata": {"bucket": bucket, "has_objects": "Contents" in response}
        }
    except Exception as e:
        return {"success": False, "message": str(e), "metadata": None}


async def test_gcs_connection(config: Dict[str, Any]) -> Dict[str, Any]:
    """Test GCS connection using Workload Identity."""
    try:
        from google.cloud import storage
        client = storage.Client(project=config.get("project_id"))
        bucket = client.bucket(config.get("bucket"))
        blobs = list(bucket.list_blobs(max_results=1, prefix=config.get("prefix", "")))
        return {
            "success": True,
            "message": f"Connected to GCS bucket '{config.get('bucket')}' successfully",
            "metadata": {"bucket": config.get("bucket"), "has_objects": len(blobs) > 0}
        }
    except Exception as e:
        return {"success": False, "message": str(e), "metadata": None}


async def test_databricks_connection(config: Dict[str, Any]) -> Dict[str, Any]:
    """Test Databricks connection."""
    try:
        from databricks import sql
        host = config.get("host", "").replace("https://", "").rstrip("/")
        with sql.connect(
            server_hostname=host,
            http_path=config.get("http_path"),
            access_token=config.get("token"),
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_version()")
                version = cursor.fetchone()[0]
        return {
            "success": True,
            "message": "Connected to Databricks successfully",
            "metadata": {"version": version, "host": host}
        }
    except Exception as e:
        return {"success": False, "message": str(e), "metadata": None}


async def test_api_connection(config: Dict[str, Any]) -> Dict[str, Any]:
    """Test REST API connection."""
    try:
        import httpx
        base_url = config.get("base_url")
        auth_type = config.get("auth_type", "none")

        headers = {}
        if auth_type == "api_key":
            header_name = config.get("api_key_header", "Authorization")
            headers[header_name] = config.get("api_key")
        elif auth_type == "bearer":
            headers["Authorization"] = f"Bearer {config.get('bearer_token')}"

        async with httpx.AsyncClient() as client:
            response = await client.get(base_url, headers=headers, timeout=10.0)
            response.raise_for_status()

        return {
            "success": True,
            "message": f"API endpoint reachable: {base_url}",
            "metadata": {"status_code": response.status_code}
        }
    except Exception as e:
        return {"success": False, "message": str(e), "metadata": None}


# Registry of test functions per connector type
CONNECTOR_TESTERS = {
    "snowflake": test_snowflake_connection,
    "postgres": test_postgres_connection,
    "mysql": test_mysql_connection,
    "s3": test_s3_connection,
    "gcs": test_gcs_connection,
    "databricks": test_databricks_connection,
    "api": test_api_connection,
}


async def test_connector(connector_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Test a connector configuration.

    Args:
        connector_type: Type of connector (snowflake, postgres, etc.)
        config: Configuration dictionary for the connector

    Returns:
        Dict with success, message, and optional metadata
    """
    tester = CONNECTOR_TESTERS.get(connector_type)
    if not tester:
        return {
            "success": False,
            "message": f"Unknown connector type: {connector_type}",
            "metadata": None
        }

    try:
        return await tester(config)
    except Exception as e:
        return {
            "success": False,
            "message": f"Connection test failed: {str(e)}",
            "metadata": None
        }
