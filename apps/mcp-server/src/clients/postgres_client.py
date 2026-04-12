"""
PostgreSQL Client for MCP Server

Handles direct database operations for:
- Executing analytical queries
- Managing bronze/silver/gold data layers
- Table discovery and schema inspection
"""
import logging
from typing import Any, Dict, List, Optional
import asyncpg

from src.config import settings

logger = logging.getLogger(__name__)


class PostgreSQLClient:
    """
    Client for PostgreSQL database operations.
    Used for analytical queries and data warehouse management.
    """

    def __init__(self):
        self.dsn = settings.DATABASE_URL
        self._pool: Optional[asyncpg.Pool] = None

    async def _get_pool(self) -> asyncpg.Pool:
        """Get or create connection pool"""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self.dsn,
                min_size=1,
                max_size=10,
                timeout=30.0
            )
        return self._pool

    async def close(self):
        """Close connection pool"""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def execute(self, query: str, *args) -> str:
        """Execute a command (INSERT, UPDATE, DELETE, etc.)"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args) -> List[Dict[str, Any]]:
        """Fetch multiple rows as dictionaries"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return [dict(r) for r in rows]

    async def fetchrow(self, query: str, *args) -> Optional[Dict[str, Any]]:
        """Fetch a single row as a dictionary"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            return dict(row) if row else None

    async def fetchval(self, query: str, *args) -> Any:
        """Fetch a single value"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    # ==================== Warehouse Management ====================

    async def ensure_schema(self, schema_name: str):
        """Ensure a schema exists"""
        await self.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")

    async def list_tables(self, schema: str = "public") -> List[str]:
        """List tables in a schema"""
        query = """
            SELECT tablename 
            FROM pg_catalog.pg_tables 
            WHERE schemaname = $1
        """
        rows = await self.fetch(query, schema)
        return [r["tablename"] for r in rows]

    async def describe_table(self, table_name: str, schema: str = "public") -> List[Dict[str, Any]]:
        """Get column information for a table"""
        query = """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            ORDER BY ordinal_position
        """
        return await self.fetch(query, schema, table_name)

    async def create_table_from_parquet(
        self,
        catalog: str,
        schema: str,
        table_name: str,
        parquet_data: bytes,
        mode: str = "overwrite"
    ):
        """Create or append to a table from parquet data."""
        from src.utils.parquet import parquet_to_dataframe
        from sqlalchemy import create_engine
        
        df = parquet_to_dataframe(parquet_data)
        
        # In plain Postgres we ignore catalog (it's the DB name in the DSN)
        full_table_name = table_name
        if schema:
            await self.ensure_schema(schema)
            full_table_name = f"{schema}.{table_name}"
            
        # Use sqlalchemy engine for pandas to_sql
        # Convert asyncpg DSN to sqlalchemy format if needed
        # (Assuming DATABASE_URL is already postgresql://...)
        engine = create_engine(self.dsn.replace("postgresql://", "postgresql+psycopg2://"))
        
        try:
            df.to_sql(
                name=table_name,
                con=engine,
                schema=schema,
                if_exists="replace" if mode == "overwrite" else "append",
                index=False
            )
        finally:
            engine.dispose()
