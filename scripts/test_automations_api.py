#!/usr/bin/env python3
"""
Test script to verify Data Pipelines (Automations) API
"""
import requests
import json
import uuid

BASE_URL = "http://localhost:8000"

def test_automations_api():
    print("1. Logging in...")
    login_response = requests.post(
        f"{BASE_URL}/api/v1/auth/login",
        data={
            "username": "test@example.com",
            "password": "password"
        }
    )

    if login_response.status_code != 200:
        print(f"❌ Login failed: {login_response.status_code}")
        return

    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print(f"✅ Login successful")

    # 2. Create a new pipeline
    print("\n2. Creating new automation...")
    pipeline_data = {
        "name": "Test Automation",
        "config": {
            "type": "schedule",
            "frequency": "daily",
            "target": "dataset-123"
        }
    }

    create_response = requests.post(
        f"{BASE_URL}/api/v1/data_pipelines/",
        headers=headers,
        json=pipeline_data
    )

    if create_response.status_code != 201:
        print(f"❌ Create failed: {create_response.status_code}")
        print(create_response.text)
        return

    pipeline = create_response.json()
    pipeline_id = pipeline["id"]
    print(f"✅ Created automation: {pipeline['name']} (ID: {pipeline_id})")

    # 3. List pipelines
    print("\n3. Listing automations...")
    list_response = requests.get(
        f"{BASE_URL}/api/v1/data_pipelines/",
        headers=headers
    )

    if list_response.status_code != 200:
        print(f"❌ List failed: {list_response.status_code}")
        return

    pipelines = list_response.json()
    print(f"✅ Found {len(pipelines)} automations")

    found = False
    for p in pipelines:
        if p["id"] == pipeline_id:
            found = True
            print(f"   - Found created pipeline: {p['name']}")
            break

    if not found:
        print("❌ Created pipeline not found in list")
        return

    # 4. Execute pipeline
    print("\n4. Executing automation...")
    execute_response = requests.post(
        f"{BASE_URL}/api/v1/data_pipelines/{pipeline_id}/execute",
        headers=headers
    )

    if execute_response.status_code != 202:
        print(f"❌ Execution failed: {execute_response.status_code}")
        print(execute_response.text)
    else:
        execution_data = execute_response.json()
        print(f"✅ Execution started: Workflow ID {execution_data.get('workflow_id')}")

    # 5. Delete pipeline
    print("\n5. Deleting automation...")
    delete_response = requests.delete(
        f"{BASE_URL}/api/v1/data_pipelines/{pipeline_id}",
        headers=headers
    )

    if delete_response.status_code != 204:
        print(f"❌ Delete failed: {delete_response.status_code}")
        return

    print(f"✅ Deleted automation")

    # Verify deletion
    verify_response = requests.get(
        f"{BASE_URL}/api/v1/data_pipelines/{pipeline_id}",
        headers=headers
    )

    if verify_response.status_code == 404:
        print("✅ Verification successful: Automation no longer exists")
    else:
        print(f"❌ Verification failed: Expected 404, got {verify_response.status_code}")

if __name__ == "__main__":
    test_automations_api()
