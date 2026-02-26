from typing import List

from sqlalchemy.orm import Session
from sqlalchemy import create_engine, text
import uuid

from app.models.data_source import DataSource
from app.schemas.data_source import DataSourceCreate, DataSourceBase

def get_data_source(db: Session, data_source_id: uuid.UUID) -> DataSource | None:
    return db.query(DataSource).filter(DataSource.id == data_source_id).first()

def get_all_data_sources(db: Session, skip: int = 0, limit: int = 100) -> List[DataSource]:
    return db.query(DataSource).offset(skip).limit(limit).all()

def get_data_sources_by_tenant(db: Session, tenant_id: uuid.UUID, skip: int = 0, limit: int = 100) -> List[DataSource]:
    return db.query(DataSource).filter(DataSource.tenant_id == tenant_id).offset(skip).limit(limit).all()

def create_tenant_data_source(db: Session, *, item_in: DataSourceCreate, tenant_id: uuid.UUID) -> DataSource:
    db_item = DataSource(**item_in.dict(), tenant_id=tenant_id)
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item

def update_data_source(db: Session, *, db_obj: DataSource, obj_in: DataSourceBase) -> DataSource:
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

def delete_data_source(db: Session, *, data_source_id: uuid.UUID) -> DataSource | None:
    data_source = db.query(DataSource).filter(DataSource.id == data_source_id).first()
    if data_source:
        db.delete(data_source)
        db.commit()
    return data_source

def execute_query(db: Session, data_source_id: uuid.UUID, query: str) -> List[dict]:
    data_source = get_data_source(db, data_source_id)
    if not data_source:
        raise ValueError("Data source not found")

    if data_source.type == 'postgres':
        config = data_source.config
        user = config.get('username')
        password = config.get('password')
        host = config.get('host')
        port = config.get('port', 5432)
        dbname = config.get('database')

        # Docker networking convenience
        if host == 'localhost':
            host = 'host.docker.internal'

        url = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
        engine = create_engine(url)

        try:
            with engine.connect() as conn:
                result = conn.execute(text(query))
                keys = result.keys()
                return [dict(zip(keys, row)) for row in result.fetchall()]
        except Exception as e:
            raise ValueError(f"Query execution failed: {str(e)}")

    elif data_source.type == 'databricks':
        try:
            from databricks import sql
        except ImportError:
            raise ImportError("databricks-sql-connector is not installed")

        config = data_source.config
        host = config.get('host')
        http_path = config.get('http_path')
        token = config.get('token')

        # Clean up host if needed (remove https://)
        if host and host.startswith('https://'):
            host = host.replace('https://', '')
        if host and host.endswith('/'):
            host = host[:-1]

        if not all([host, http_path, token]):
             raise ValueError("Missing Databricks configuration (host, http_path, token)")

        try:
            with sql.connect(server_hostname=host, http_path=http_path, access_token=token) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query)
                    # Fetch results
                    if cursor.description:
                        columns = [desc[0] for desc in cursor.description]
                        return [dict(zip(columns, row)) for row in cursor.fetchall()]
                    return []
        except Exception as e:
            raise ValueError(f"Databricks query execution failed: {str(e)}")

    elif data_source.type in ('rest_api', 'api'):
        import httpx

        config = data_source.config
        base_url = config.get('internal_url') or config.get('base_url')
        if not base_url:
            raise ValueError("REST API data source missing base_url")

        auth_type = config.get('auth_type')
        headers = {}

        # JWT authentication
        if auth_type == 'jwt':
            auth_endpoint = config.get('auth_endpoint', '/auth/login')
            creds = config.get('auth_credentials', {})
            with httpx.Client(timeout=30) as client:
                auth_resp = client.post(
                    f"{base_url}{auth_endpoint}",
                    data={"username": creds.get("username"), "password": creds.get("password")},
                )
                auth_resp.raise_for_status()
                token = auth_resp.json().get("access_token")
                headers["Authorization"] = f"Bearer {token}"

        # Use query as search term against configured endpoints
        endpoints = config.get('endpoints', {})
        search_endpoint = endpoints.get('search') or endpoints.get('medications', '/medications')

        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f"{base_url}{search_endpoint}",
                headers=headers,
                params={"q": query, "limit": 20},
            )
            resp.raise_for_status()
            result = resp.json()

        if isinstance(result, list):
            return result[:100]
        elif isinstance(result, dict):
            items = result.get('items') or result.get('results') or result.get('data') or [result]
            return items[:100] if isinstance(items, list) else [items]
        return [result]

    else:
        raise ValueError(f"Unsupported data source type: {data_source.type}")
