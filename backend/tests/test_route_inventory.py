import pytest


@pytest.mark.parametrize("path", [
    "/api/writer/advanced/generate",
    "/api/writer/chapters/1/finalize",
    "/enhanced/api/analytics/projects/example/story-trajectory",
])
async def test_removed_routes_are_not_registered(client, auth, path):
    response = await client.request("GET" if path.startswith("/enhanced/") else "POST", path, headers=auth, json={})
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


async def test_current_routes_and_documentation_remain_available(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/writer/novels/{project_id}/chapters/generate" in paths
    assert "/api/analytics/{project_id}/emotion-curve" in paths
    assert "/api/review/consistency" in paths
    assert "/api/projects/{project_id}/memory" in paths
    assert not any(path.startswith("/enhanced/") or "/advanced/" in path or path.endswith("/finalize") for path in paths)
    docs = await client.get("/docs")
    assert docs.status_code == 200
    assert "SwaggerUIBundle" in docs.text
