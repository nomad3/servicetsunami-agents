#!/usr/bin/env python3
"""Backfill missing embeddings on knowledge_entities and knowledge_observations.

Usage:
    docker exec servicetsunami-agents-api-1 python /app/scripts/backfill_embeddings.py

Uses the embedding_service already loaded in the API container.
"""
import os
import sys

# Add the app to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine, text
from sentence_transformers import SentenceTransformer

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@db:5432/servicetsunami")
BATCH_SIZE = 50

# Fix driver prefix
db_url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")


def get_model():
    print("Loading nomic-embed-text-v1.5...")
    model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)
    print("Model loaded.")
    return model


def embed_text(model, text_val):
    prefixed = f"search_document: {text_val[:8000]}"
    embedding = model.encode(prefixed, normalize_embeddings=True)
    return embedding.tolist()


def backfill_entities(engine, model):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, name, description FROM knowledge_entities WHERE embedding IS NULL"
        )).fetchall()

    total = len(rows)
    print(f"Entities missing embeddings: {total}")
    if total == 0:
        return

    for i in range(0, total, BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        with engine.begin() as conn:
            for row in batch:
                text_val = f"{row.name} {row.description or ''}"
                emb = embed_text(model, text_val)
                conn.execute(
                    text("UPDATE knowledge_entities SET embedding = CAST(:emb AS vector) WHERE id = :id"),
                    {"emb": str(emb), "id": str(row.id)},
                )
        done = min(i + BATCH_SIZE, total)
        print(f"  Entities: {done}/{total} ({done * 100 // total}%)")

    print(f"Entities backfill complete: {total} embeddings generated.")


def backfill_observations(engine, model):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, observation_text FROM knowledge_observations WHERE embedding IS NULL"
        )).fetchall()

    total = len(rows)
    print(f"Observations missing embeddings: {total}")
    if total == 0:
        return

    for i in range(0, total, BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        with engine.begin() as conn:
            for row in batch:
                emb = embed_text(model, row.observation_text or "")
                conn.execute(
                    text("UPDATE knowledge_observations SET embedding = CAST(:emb AS vector) WHERE id = :id"),
                    {"emb": str(emb), "id": str(row.id)},
                )
        done = min(i + BATCH_SIZE, total)
        print(f"  Observations: {done}/{total} ({done * 100 // total}%)")

    print(f"Observations backfill complete: {total} embeddings generated.")


if __name__ == "__main__":
    engine = create_engine(db_url)
    model = get_model()
    backfill_entities(engine, model)
    backfill_observations(engine, model)
    print("Done.")
