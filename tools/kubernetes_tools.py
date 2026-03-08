"""Kubernetes tools for the tester agent to spin up ephemeral microservice pods.

Provides capabilities to run isolated unit tests and integrated smoke tests
in ephemeral Kubernetes pods before opening Pull Requests.
"""
import httpx
import logging
import asyncio
import os
from typing import Optional

logger = logging.getLogger(__name__)

def _get_k8s_client() -> httpx.AsyncClient:
    with open("/var/run/secrets/kubernetes.io/serviceaccount/token", "r") as f:
        token = f.read().strip()
    return httpx.AsyncClient(
        base_url="https://kubernetes.default.svc",
        headers={"Authorization": f"Bearer {token}"},
        verify="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
        timeout=60.0,
    )

def _get_namespace() -> str:
    with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
        return f.read().strip()

async def get_current_image() -> dict:
    """Gets the container image of the current pod to use for ephemeral testing."""
    try:
        namespace = _get_namespace()
        hostname = os.environ.get("HOSTNAME")
        if not hostname:
            return {"status": "error", "detail": "HOSTNAME env var not set."}
            
        async with _get_k8s_client() as client:
            resp = await client.get(f"/api/v1/namespaces/{namespace}/pods/{hostname}")
            if resp.status_code == 200:
                pod_info = resp.json()
                containers = pod_info.get("spec", {}).get("containers", [])
                if containers:
                    return {"status": "success", "image": containers[0].get("image")}
            return {"status": "error", "detail": resp.text}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

async def run_ephemeral_test_pod(
    pod_name: str,
    image: str,
    command: list[str],
) -> dict:
    """Spins up an ephemeral pod to run unit tests and returns the logs."""
    try:
        namespace = _get_namespace()
        
        pod_manifest = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "labels": {"role": "ephemeral-test"}
            },
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "test-container",
                        "image": image,
                        "command": command,
                    }
                ]
            }
        }
        
        async with _get_k8s_client() as client:
            # Create Pod
            resp = await client.post(f"/api/v1/namespaces/{namespace}/pods", json=pod_manifest)
            if resp.status_code not in (200, 201, 202):
                return {"status": "error", "step": "create_pod", "detail": resp.text}
                
            # Wait for pod to complete
            phase = "Pending"
            for _ in range(60):
                await asyncio.sleep(2)
                pod_resp = await client.get(f"/api/v1/namespaces/{namespace}/pods/{pod_name}")
                if pod_resp.status_code == 200:
                    phase = pod_resp.json().get("status", {}).get("phase")
                    if phase in ("Succeeded", "Failed"):
                        break
            
            # Get Logs
            logs_resp = await client.get(f"/api/v1/namespaces/{namespace}/pods/{pod_name}/log")
            logs = logs_resp.text if logs_resp.status_code == 200 else f"Could not fetch logs. Status: {logs_resp.status_code}"
            
            # Delete Pod
            await client.delete(f"/api/v1/namespaces/{namespace}/pods/{pod_name}")
            
            return {
                "status": "success" if phase == "Succeeded" else "failed",
                "phase": phase,
                "logs": logs
            }
    except Exception as e:
        return {"status": "error", "detail": str(e)}
