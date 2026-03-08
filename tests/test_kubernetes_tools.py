import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock, mock_open

from tools.kubernetes_tools import get_current_image, run_ephemeral_test_pod, _get_namespace

@pytest.fixture
def mock_k8s_secrets():
    with patch("builtins.open", mock_open(read_data="fake-secret")) as m:
        yield m

@pytest.mark.asyncio
@patch('os.environ.get')
@patch('tools.kubernetes_tools._get_k8s_client')
async def test_get_current_image(mock_get_client, mock_env, mock_k8s_secrets):
    mock_env.return_value = "my-pod-123"
    
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "spec": {
            "containers": [{"image": "my-image:latest"}]
        }
    }
    mock_client.get.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    
    mock_get_client.return_value = mock_client
    
    result = await get_current_image()
    assert result["status"] == "success"
    assert result["image"] == "my-image:latest"

@pytest.mark.asyncio
@patch('tools.kubernetes_tools._get_k8s_client')
async def test_run_ephemeral_test_pod(mock_get_client, mock_k8s_secrets):
    mock_client = AsyncMock()
    
    # Mock post pod
    post_resp = MagicMock()
    post_resp.status_code = 201
    
    # Mock get pod status
    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = {"status": {"phase": "Succeeded"}}
    
    # Mock logs
    logs_resp = MagicMock()
    logs_resp.status_code = 200
    logs_resp.text = "test passed"
    
    mock_client.post.return_value = post_resp
    mock_client.get.side_effect = [get_resp, logs_resp]
    mock_client.__aenter__.return_value = mock_client
    
    mock_get_client.return_value = mock_client
    
    result = await run_ephemeral_test_pod("test-pod", "my-image:latest", ["pytest", "tests/"])
    assert result["status"] == "success"
    assert result["phase"] == "Succeeded"
    assert result["logs"] == "test passed"

