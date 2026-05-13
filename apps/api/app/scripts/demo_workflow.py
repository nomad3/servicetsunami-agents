"""Run an end-to-end demo workflow for the seeded demo tenant.

Steps:
1. Authenticate with the demo credentials.
2. Use the access token to ingest a synthetic dataset via the REST API.
3. Start the MorningRoutineWorkflow in Temporal for the demo tenant.
4. Print status and Temporal identifiers for verification.

Usage:
    poetry run python -m app.scripts.demo_workflow \
        --api-base http://localhost:8000/api/v1 \
        --temporal https://localhost:7233 \
        --email test@example.com \
        --password DemoPass123!

Note: the demo account is only seeded when ENVIRONMENT ∈ {local, dev}
(see init_db.py). Production deploys fail-closed (no demo seed).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Dict, Tuple

import requests

from app.services import workflows


DEMO_EMAIL = "test@example.com"
# Must match the seed in apps/api/app/db/init_db.py:seed_demo_data().
# Bumped 2026-05-10 to satisfy the password-complexity validator
# (12+ chars, ≥3 of {upper, lower, digit, symbol}).
DEMO_PASSWORD = "DemoPass123!"
DEFAULT_DATASET_PAYLOAD = {
    "name": "Workflow Demo",
    "description": "Synthetic dataset ingested during demo workflow run.",
    "records": [
        {"date": "2024-10-01", "metric": "energy", "value": 78},
        {"date": "2024-10-02", "metric": "energy", "value": 82},
        {"date": "2024-10-03", "metric": "energy", "value": 76},
    ],
}


class DemoRunner:
    def __init__(self, api_base: str, temporal_address: str, email: str, password: str):
        self.api_base = api_base.rstrip("/")
        self.temporal_address = temporal_address
        self.email = email
        self.password = password

    def login(self) -> str:
        token_url = f"{self.api_base}/auth/login"
        response = requests.post(
            token_url,
            data={"username": self.email, "password": self.password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise RuntimeError("No access token returned by login endpoint.")
        return token

    def ingest_dataset(self, token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        datasets_url = f"{self.api_base}/datasets/ingest"
        response = requests.post(
            datasets_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    async def start_workflow(self, tenant_id: str, dataset_id: str) -> Tuple[str, str]:
        handle = await workflows.start_workflow(
            workflow_type="MorningRoutineWorkflow",
            tenant_id=tenant_id,
            task_queue="agentprovision-lifeops",
            arguments={"dataset_id": dataset_id},
            memo={"source": "demo_script"},
        )
        return handle.id, handle.first_execution_run_id


async def run_demo(args: argparse.Namespace) -> None:
    runner = DemoRunner(
        api_base=args.api_base,
        temporal_address=args.temporal,
        email=args.email,
        password=args.password,
    )

    print("Authenticating demo user...")
    token = runner.login()
    print("Authenticated. Token acquired.")

    print("Ingesting synthetic dataset...")
    dataset_response = runner.ingest_dataset(token, DEFAULT_DATASET_PAYLOAD)
    dataset_id = dataset_response.get("id")
    tenant_id = dataset_response.get("tenant_id")
    if not dataset_id or not tenant_id:
        raise RuntimeError("Dataset response missing id or tenant_id.")
    print(f"Dataset created: {dataset_id} (tenant {tenant_id})")

    if args.skip_workflow:
        print("Skipping Temporal workflow start as requested.")
        return

    print("Starting MorningRoutineWorkflow in Temporal...")
    workflow_id, run_id = await runner.start_workflow(tenant_id=tenant_id, dataset_id=dataset_id)
    print("Workflow dispatched.")
    print(f"Workflow ID: {workflow_id}")
    print(f"Run ID: {run_id}")

    if args.describe:
        print("Fetching workflow status...")
        description = await workflows.describe_workflow(workflow_id=workflow_id, run_id=run_id)
        print(json.dumps(description, indent=2))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AgentProvision demo workflow.")
    parser.add_argument("--api-base", default="http://localhost:8000/api/v1", help="API base URL")
    parser.add_argument("--temporal", default="temporal:7233", help="Temporal address")
    parser.add_argument("--email", default=DEMO_EMAIL, help="Demo user email")
    parser.add_argument("--password", default=DEMO_PASSWORD, help="Demo user password")
    parser.add_argument("--skip-workflow", action="store_true", help="Only ingest dataset, skip workflow start")
    parser.add_argument("--describe", action="store_true", help="Print Temporal workflow description after dispatch")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv or sys.argv[1:])
    try:
        asyncio.run(run_demo(args))
    except Exception as exc:  # noqa: BLE001
        print(f"Demo execution failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
