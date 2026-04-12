"""
PostgreSQL MCP Tools

Tools for connecting to and extracting data from PostgreSQL databases.
"""
import asyncpg
from typing import Dict, Any

from src.clients.api_client import AgentProvisionAPI

api = AgentProvisionAPI()


async def connect_postgres(
    name: str,
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    tenant_id: str
) -> Dict[str, Any]:
    """
    Register a PostgreSQL database connection.

    Credentials are encrypted and stored securely in the API.
    Returns connection_id for use in other tools.

    Args:
        name: Display name for this connection
        host: Database host
        port: Database port (usually 5432)
        database: Database name
        user: Username
        password: Password
        tenant_id: Tenant identifier

    Returns:
        connection_id, status, message
    """
    result = await api.create_data_source(
        tenant_id=tenant_id,
        name=name,
        source_type="postgresql",
        config={
            "host": host,
            "port": port,
            "database": database,
            "user": user,
            "password": password,
        }
    )

    return {
        "connection_id": result["id"],
        "name": name,
        "status": "created",
        "message": f"Connection '{name}' registered. Use verify_connection to test."
    }


async def verify_connection(connection_id: str) -> Dict[str, Any]:
    """
    Verify if a PostgreSQL connection is working.

    Fetches credentials from API and attempts to connect.
    Returns success status and any error details.

    Args:
        connection_id: The data source ID to verify

    Returns:
        status, message, database_version (if successful)
    """
    source = await api.get_data_source(connection_id)
    config = source["config"]

    try:
        conn = await asyncpg.connect(
            host=config["host"],
            port=config["port"],
            database=config["database"],
            user=config["user"],
            password=config["password"],
            timeout=10
        )

        version = await conn.fetchval("SELECT version()")
        await conn.close()

        return {
            "status": "success",
            "connection_id": connection_id,
            "database_version": version,
            "message": "Connection successful"
        }

    except asyncpg.InvalidPasswordError:
        return {"status": "error", "connection_id": connection_id, "error": "Invalid username or password"}
    except asyncpg.InvalidCatalogNameError:
        return {"status": "error", "connection_id": connection_id, "error": f"Database '{config['database']}' not found"}
    except OSError as e:
        return {"status": "error", "connection_id": connection_id, "error": f"Cannot reach host: {e}"}
    except Exception as e:
        return {"status": "error", "connection_id": connection_id, "error": str(e)}


async def list_source_tables(connection_id: str) -> Dict[str, Any]:
    """
    List all tables available in the connected PostgreSQL database.

    Returns table names, row counts, and column information.

    Args:
        connection_id: The data source ID

    Returns:
        database, table_count, tables (with columns)
    """
    source = await api.get_data_source(connection_id)
    config = source["config"]

    conn = await asyncpg.connect(
        host=config["host"],
        port=config["port"],
        database=config["database"],
        user=config["user"],
        password=config["password"]
    )

    try:
        # Get tables with row counts
        tables = await conn.fetch("""
            SELECT
                schemaname || '.' || tablename as table_name,
                schemaname,
                tablename,
                n_live_tup as row_count
            FROM pg_stat_user_tables
            ORDER BY schemaname, tablename
        """)

        result = []
        for t in tables:
            # Get columns for each table
            columns = await conn.fetch("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
            """, t["schemaname"], t["tablename"])

            result.append({
                "table_name": t["table_name"],
                "row_count": t["row_count"],
                "columns": [
                    {
                        "name": c["column_name"],
                        "type": c["data_type"],
                        "nullable": c["is_nullable"] == "YES"
                    }
                    for c in columns
                ]
            })

        return {
            "connection_id": connection_id,
            "database": config["database"],
            "table_count": len(result),
            "tables": result
        }

    finally:
        await conn.close()
