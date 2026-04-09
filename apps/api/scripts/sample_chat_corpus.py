"""Sample 100 chat_messages from tenant 0f134606 for commitment-classifier gold-set labeling.

Pulls a stratified random sample (mix of user + assistant turns, message
length 20-600 chars) from the production tenant's chat_messages table.
Output: apps/api/tests/fixtures/commitment_gold_set_unlabeled.jsonl with
label=null on every row — Simon hand-labels next.

Usage:
    DATABASE_URL=postgresql://postgres:postgres@localhost:8003/servicetsunami \\
      python apps/api/scripts/sample_chat_corpus.py

Env:
    DATABASE_URL  required
    SAMPLE_TENANT  default 0f134606-3906-44a5-9e88-6c2020f0f776
    SAMPLE_N       default 100
"""
import json
import os
import sys

from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    sys.stderr.write("ERROR: DATABASE_URL env var required.\n")
    sys.exit(2)

TENANT = os.environ.get("SAMPLE_TENANT", "0f134606-3906-44a5-9e88-6c2020f0f776")
N = int(os.environ.get("SAMPLE_N", "100"))
OUT_PATH = "apps/api/tests/fixtures/commitment_gold_set_unlabeled.jsonl"

QUERY = text(
    """
    SELECT
        cm.id,
        cm.role,
        cm.content,
        cm.created_at
    FROM chat_messages cm
    JOIN chat_sessions cs ON cs.id = cm.session_id
    WHERE cs.tenant_id = CAST(:t AS uuid)
      AND char_length(cm.content) BETWEEN 20 AND 600
    ORDER BY random()
    LIMIT :n
    """
)


def main() -> int:
    engine = create_engine(DATABASE_URL)
    with engine.connect() as c:
        rows = c.execute(QUERY, {"t": TENANT, "n": N}).fetchall()

    if not rows:
        sys.stderr.write(f"ERROR: no rows returned for tenant {TENANT}\n")
        return 1

    with open(OUT_PATH, "w") as f:
        for r in rows:
            f.write(
                json.dumps(
                    {
                        "text": r.content,
                        "role": r.role,
                        "label": None,
                        "title": None,
                        "due_at": None,
                        "type": None,
                        "source": "real",
                        "message_id": str(r.id),
                        "created_at": r.created_at.isoformat(),
                        "labeled_by": None,
                    }
                )
                + "\n"
            )

    print(f"wrote {len(rows)} rows to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
