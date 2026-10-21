from sqlalchemy import select

from app.models import NovelProject, User


async def test_login_and_authentication(client, password, database):
    assert (await client.get("/api/auth/users/me")).status_code == 401
    invalid = await client.get("/api/auth/users/me", headers={"Authorization": "Bearer invalid"})
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "无效的凭证"
    denied = await client.post("/api/auth/token", data={"username": "reader", "password": "wrong"})
    assert denied.status_code == 401
    assert denied.json()["detail"] == "用户名或密码错误"
    response = await client.post("/api/auth/token", data={"username": "reader", "password": password})
    assert response.status_code == 200
    token = response.json()
    assert token["token_type"] == "bearer"
    assert token["must_change_password"] is False
    current = await client.get("/api/auth/users/me", headers={"Authorization": f"Bearer {token['access_token']}"})
    assert current.json()["username"] == "reader"
    assert "hashed_password" not in current.json()
    async with database() as session:
        user = await session.get(User, 1)
        assert user.hashed_password != password


async def test_create_project_and_owner_isolation(client, auth, password, database):
    payload = {"title": "新故事", "initial_prompt": "一个寻找记忆的故事"}
    assert (await client.post("/api/novels", json=payload)).status_code == 401
    response = await client.post("/api/novels", headers=auth, json=payload)
    assert response.status_code == 201
    data = response.json()
    assert (data["title"], data["initial_prompt"], data["user_id"]) == (payload["title"], payload["initial_prompt"], 1)
    async with database() as session:
        records = (await session.scalars(select(NovelProject))).all()
        assert len(records) == 1
        assert (records[0].id, records[0].status) == (data["id"], "draft")
    other = await client.post("/api/auth/token", data={"username": "other", "password": password})
    headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    forbidden = await client.get(f"/api/novels/{data['id']}", headers=headers)
    assert forbidden.status_code == 403
    assert (await client.get("/api/novels", headers=headers)).json() == []
