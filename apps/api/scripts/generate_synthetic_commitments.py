"""Generate 100 synthetic edge-case examples for the commitment classifier gold set.

50 explicit commitments + 50 hard negatives, drawn from Gemma4 via the
local Ollama instance. Output: commit_gold_set_synthetic.jsonl with
labels pre-set (1 for commitments, 0 for negatives).

Why synthetic: real chat data is dominated by easy cases (greetings,
simple questions). The classifier's hard work is on the edges —
third-person descriptions, meta-discussion, hedged intent, past tense
that LOOKS forward. We generate those explicitly so the gold set
covers them.

Usage:
    python apps/api/scripts/generate_synthetic_commitments.py

Env:
    OLLAMA_URL  default http://localhost:11434
    GEMMA_MODEL  default gemma3:12b
"""
import json
import os
import sys

import requests

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("GEMMA_MODEL", "gemma4:latest")
OUT_PATH = "apps/api/tests/fixtures/commitment_gold_set_synthetic.jsonl"

PROMPT_COMMITMENT = """Generate exactly 50 short messages (20-200 chars each) where someone makes a clear COMMITMENT to a future action with an explicit or implicit deadline.

Mix these styles:
- First-person: "I'll send the report by Friday"
- Directives to assistants: "Luna, follow up with Ray tomorrow"
- First-person plural: "We need to ship before the freeze"
- Explicit promises: "I promise I'll review the PR tonight"
- Meeting confirmations: "Confirmed for 3pm Thursday"
- Obligation acknowledgments: "I owe you that doc by EOD"
- Spanish: "Voy a llamar al cliente mañana", "Te mando el reporte el viernes"

Output ONE message per line. No numbering, no quotes, no bullet points, no commentary. Just 50 plain lines."""

PROMPT_NEGATIVE = """Generate exactly 50 short messages (20-200 chars each) that look like they MIGHT be commitments but are NOT. These should be the hard cases that fool a regex-based classifier.

Mix these patterns:
- Third-person descriptions: "Ray usually sends reports on Fridays"
- Past tense: "I sent the report yesterday"
- Hypotheticals: "What if we shipped on Friday?"
- Questions: "Should I review the PR tonight?"
- Meta-discussion of features: "The commitment-tracking system has a stakes dimension"
- Describing data: "There are 47 open commitments in the database"
- Soft intent without deadline: "I'm thinking about reviewing the PR"
- Hedged: "Maybe I'll get to it later", "I might send it tomorrow"
- Spanish equivalents: "Ray suele mandar reportes los viernes", "Quizás mañana"

Output ONE message per line. No numbering, no quotes, no bullet points, no commentary. Just 50 plain lines."""


def gen(prompt: str) -> list[str]:
    r = requests.post(
        f"{OLLAMA}/api/generate",
        json={
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.9, "num_predict": 2000},
        },
        timeout=300,
    )
    r.raise_for_status()
    text = r.json().get("response", "")
    lines = [l.strip().lstrip("-•*0123456789. ").strip('"\'') for l in text.splitlines()]
    return [l for l in lines if 15 <= len(l) <= 250][:50]


def main() -> int:
    sys.stderr.write(f"Generating commitment positives via {MODEL}...\n")
    positives = gen(PROMPT_COMMITMENT)
    sys.stderr.write(f"  got {len(positives)} positives\n")
    sys.stderr.write(f"Generating commitment negatives via {MODEL}...\n")
    negatives = gen(PROMPT_NEGATIVE)
    sys.stderr.write(f"  got {len(negatives)} negatives\n")

    if len(positives) < 30 or len(negatives) < 30:
        sys.stderr.write(
            f"ERROR: gemma returned too few examples — {len(positives)} pos, "
            f"{len(negatives)} neg. Re-run, or check Ollama logs.\n"
        )
        return 1

    with open(OUT_PATH, "w") as f:
        for txt in positives:
            f.write(
                json.dumps(
                    {
                        "text": txt,
                        "role": "user",
                        "label": 1,
                        "title": txt[:80],
                        "due_at": None,
                        "type": "action",
                        "source": "synthetic",
                        "labeled_by": "gemma3-12b",
                    }
                )
                + "\n"
            )
        for txt in negatives:
            f.write(
                json.dumps(
                    {
                        "text": txt,
                        "role": "user",
                        "label": 0,
                        "title": None,
                        "due_at": None,
                        "type": None,
                        "source": "synthetic",
                        "labeled_by": "gemma3-12b",
                    }
                )
                + "\n"
            )

    print(f"wrote {len(positives)} positives + {len(negatives)} negatives to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
