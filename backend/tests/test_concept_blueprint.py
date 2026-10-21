import json

import pytest
from sqlalchemy import select

from app.models import BlueprintCharacter, BlueprintRelationship, ChapterOutline, NovelBlueprint, NovelConversation, NovelProject


CONCEPT = {"ai_message": "你希望调查从哪里开始？", "ui_control": {"type": "text_input", "placeholder": "输入线索"}, "conversation_state": {"genre": "悬疑"}, "is_complete": True}


async def converse(client, auth, project, llm):
    llm.reply("concept", CONCEPT)
    return await client.post(f"/api/novels/{project}/concept/converse", headers=auth, json={"user_input": {"value": "从失踪目录开始"}, "conversation_state": {}})


async def test_concept_round_persists_ordered_conversation(client, auth, project, llm, database):
    response = await converse(client, auth, project, llm)
    assert response.status_code == 200
    assert response.json() == {**CONCEPT, "ui_control": {**CONCEPT["ui_control"], "options": None}, "ready_for_blueprint": True}
    async with database() as session:
        records = (await session.scalars(select(NovelConversation).order_by(NovelConversation.seq))).all()
        assert [row.role for row in records] == ["user", "assistant"]
        assert records[0].seq < records[1].seq
        assert json.loads(records[0].content) == {"value": "从失踪目录开始"}
        assert json.loads(records[1].content) == CONCEPT
    call = llm.calls[0][1]
    assert call["timeout"] == 240
    assert call["messages"][-1].role == "user"


async def test_blueprint_generation_and_replacement_are_persistent(client, auth, project, llm, blueprint, database):
    assert (await converse(client, auth, project, llm)).status_code == 200
    llm.reply("screenwriting", blueprint)
    response = await client.post(f"/api/novels/{project}/blueprint/generate", headers=auth)
    assert response.status_code == 200
    assert response.json()["blueprint"]["chapter_outline"] == blueprint["chapter_outline"]
    async with database() as session:
        stored = await session.get(NovelProject, project)
        assert (stored.title, stored.status) == (blueprint["title"], "blueprint_ready")
        assert (await session.get(NovelBlueprint, project)).world_setting == blueprint["world_setting"]
        characters = (await session.scalars(select(BlueprintCharacter).order_by(BlueprintCharacter.position))).all()
        assert [row.name for row in characters] == ["林川", "许宁"]
        assert characters[0].extra == {"habit": "记录线索"}
        relation = (await session.scalars(select(BlueprintRelationship))).one()
        assert (relation.character_from, relation.character_to, relation.description) == ("林川", "许宁", "同事")
    replacement = {**blueprint, "title": "新标题", "characters": [{"name": "新主角"}], "relationships": [], "chapter_outline": [{"chapter_number": 2, "title": "新起点", "summary": "重新调查"}]}
    saved = await client.post(f"/api/novels/{project}/blueprint/save", headers=auth, json=replacement)
    assert saved.status_code == 200
    fresh = (await client.get(f"/api/novels/{project}", headers=auth)).json()
    assert fresh["title"] == "新标题"
    assert fresh["blueprint"]["chapter_outline"] == replacement["chapter_outline"]
    async with database() as session:
        assert [row.name for row in (await session.scalars(select(BlueprintCharacter))).all()] == ["新主角"]
        assert (await session.scalars(select(BlueprintRelationship))).all() == []
        assert [row.chapter_number for row in (await session.scalars(select(ChapterOutline))).all()] == [2]


async def test_blueprint_requires_conversation(client, auth, project):
    response = await client.post(f"/api/novels/{project}/blueprint/generate", headers=auth)
    assert response.status_code == 400
    assert response.json()["detail"] == "缺少对话历史，请先完成概念对话后再生成蓝图"


@pytest.mark.parametrize("stage", ["concept", "screenwriting"])
async def test_invalid_json_keeps_existing_state(client, auth, project, llm, database, stage):
    if stage == "screenwriting":
        await converse(client, auth, project, llm)
    llm.reply(stage, "this is not JSON")
    endpoint = "concept/converse" if stage == "concept" else "blueprint/generate"
    response = await client.post(f"/api/novels/{project}/{endpoint}", headers=auth, **({"json": {"user_input": {"value": "test"}, "conversation_state": {}}} if stage == "concept" else {}))
    assert response.status_code == 500
    assert "AI 返回的内容格式不正确" in response.json()["detail"]
    async with database() as session:
        stored = await session.get(NovelBlueprint, project)
        # create_project already inserts an empty blueprint; failure must not fill it.
        assert stored.title is None and stored.full_synopsis is None
        assert (await session.get(NovelProject, project)).status == "draft"
        assert len((await session.scalars(select(NovelConversation))).all()) == (2 if stage == "screenwriting" else 0)


async def test_length_finish_reason_is_an_error_not_partial_json(client, auth, project, llm, database):
    llm.reply("concept", CONCEPT, finish_reason="length")
    response = await client.post(f"/api/novels/{project}/concept/converse", headers=auth, json={"user_input": {"value": "test"}, "conversation_state": {}})
    assert response.status_code == 500
    assert "AI 响应因长度限制被截断" in response.json()["detail"]
    async with database() as session:
        assert (await session.scalars(select(NovelConversation))).all() == []
