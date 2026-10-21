import pytest
from sqlalchemy import delete, select

from app.models import Chapter, ChapterEvaluation, ChapterOutline, ChapterVersion, Prompt, SystemConfig


async def test_outline_continuation_preserves_existing_chapters(client, auth, ready_project, llm, database):
    project = ready_project
    outlines = [{"chapter_number": number, "title": f"线索{number}", "summary": f"找到第{number}条线索"} for number in [2, 3]]
    llm.reply("outline_generation", {"chapters": outlines})
    response = await client.post(f"/api/writer/novels/{project}/chapters/outline", headers=auth, json={"start_chapter": 2, "num_chapters": 2})
    assert response.status_code == 200
    assert response.json()["id"] == project
    assert [row["chapter_number"] for row in response.json()["blueprint"]["chapter_outline"]] == [1, 2, 3]
    fresh = (await client.get(f"/api/novels/{project}", headers=auth)).json()
    assert [row["chapter_number"] for row in fresh["blueprint"]["chapter_outline"]] == [1, 2, 3]
    prompt = llm.calls[0][1]["messages"][-1].content
    assert "旧馆" in prompt and "第 2 章" in prompt
    async with database() as session:
        records = (await session.scalars(select(ChapterOutline).order_by(ChapterOutline.chapter_number))).all()
        assert [(row.chapter_number, row.title, row.summary) for row in records][1:] == [(row["chapter_number"], row["title"], row["summary"]) for row in outlines]


@pytest.mark.parametrize("count", [1, 2])
async def test_generation_versions_and_selection_state(client, auth, ready_project, llm, database, count, wait_job):
    project = ready_project
    base = f"/api/writer/novels/{project}/chapters"
    before = (await client.get(f"/api/novels/{project}/chapters/1", headers=auth)).json()
    assert before["generation_status"] == "not_generated"
    async with database() as session:
        session.add(SystemConfig(key="writer.chapter_versions", value=str(count)))
        await session.commit()

    async def assert_generating():
        async with database() as session:
            chapter = (await session.scalars(select(Chapter))).one()
            assert chapter.status == "generating"
            assert chapter.selected_version_id is None

    llm.reply("chapter_plan", {"macro_beat": "调查", "allowed_new_characters": ["林川"]})
    contents = [f"一位调查员林川走进旧馆，找到第{number + 1}页缺失的目录。" for number in range(count)]
    for content in contents:
        llm.reply("writing_v2", {"content": content}, before=assert_generating)
    if count > 1:
        llm.reply("editor_review", {"best_version_index": 1, "scores": {"pacing": 9}, "overall_evaluation": "第二版更紧凑"})
    response = await client.post(f"{base}/generate", headers=auth, json={"chapter_number": 1, "writing_notes": "调查旧馆"})
    assert response.status_code == 202
    assert list(response.json()) == ["job_id"]
    job = await wait_job(response.json()["job_id"])
    assert job["status"] == "succeeded", job["error"]
    assert job["progress"] == 100
    assert job["result"]["version_count"] == count
    detail = (await client.get(f"/api/novels/{project}/chapters/1", headers=auth)).json()
    assert detail["generation_status"] == "waiting_for_confirm"
    assert detail["versions"] == contents
    assert detail["content"] is None
    async with database() as session:
        versions = (await session.scalars(select(ChapterVersion).order_by(ChapterVersion.id))).all()
        assert [version.version_label for version in versions] == [f"v{index + 1}" for index in range(count)]
        assert [version.content for version in versions] == contents
        assert all(version.metadata["guardrail"]["passed"] for version in versions)
        if count > 1:
            assert [version.metadata["ai_review"]["is_best"] for version in versions] == [False, True]
    invalid = await client.post(f"{base}/select", headers=auth, json={"chapter_number": 1, "version_index": count})
    assert invalid.status_code == 400
    selected = await client.post(f"{base}/select", headers=auth, json={"chapter_number": 1, "version_index": count - 1})
    assert selected.status_code == 200
    fresh = (await client.get(f"/api/novels/{project}/chapters/1", headers=auth)).json()
    assert (fresh["generation_status"], fresh["content"]) == ("successful", contents[-1])
    async with database() as session:
        chapter = (await session.scalars(select(Chapter))).one()
        chosen = await session.get(ChapterVersion, chapter.selected_version_id)
        assert chosen.content == contents[-1]
        assert chapter.word_count == len(contents[-1])


async def test_truncated_generation_marks_chapter_failed(client, auth, ready_project, llm, database, wait_job):
    async with database() as session:
        session.add(SystemConfig(key="writer.chapter_versions", value="1"))
        await session.commit()
    llm.reply("chapter_plan", {"allowed_new_characters": []})
    for _ in range(3):
        llm.reply("writing_v2", "不应被保存的半章内容", finish_reason="length")
    response = await client.post(f"/api/writer/novels/{ready_project}/chapters/generate", headers=auth, json={"chapter_number": 1})
    assert response.status_code == 202
    job = await wait_job(response.json()["job_id"])
    assert job["status"] == "failed"
    assert job["error"]["status_code"] == 500
    assert "长度限制被截断" in job["error"]["detail"]["failed_versions"][0]["detail"]
    async with database() as session:
        chapter = (await session.scalars(select(Chapter))).one()
        assert chapter.status == "failed"
        assert chapter.selected_version_id is None
        assert (await session.scalars(select(ChapterVersion))).all() == []


async def test_missing_outline_does_not_create_chapter(client, auth, project, database):
    response = await client.post(f"/api/writer/novels/{project}/chapters/generate", headers=auth, json={"chapter_number": 99})
    assert response.status_code == 404
    assert response.json()["detail"] == "蓝图中未找到对应章节纲要"
    async with database() as session:
        assert (await session.scalars(select(Chapter))).all() == []


async def test_bad_outline_json_preserves_existing_outline(client, auth, ready_project, llm, database):
    llm.reply("outline_generation", "invalid outline JSON")
    response = await client.post(f"/api/writer/novels/{ready_project}/chapters/outline", headers=auth, json={"start_chapter": 2, "num_chapters": 2})
    assert response.status_code == 500
    assert response.json()["detail"].startswith("大纲生成失败:")
    async with database() as session:
        assert [row.chapter_number for row in (await session.scalars(select(ChapterOutline))).all()] == [1]


@pytest.mark.parametrize("configured", [True, False])
async def test_evaluation_returns_to_confirmation_and_records_decision(client, auth, ready_project, llm, database, configured):
    async with database() as session:
        chapter = Chapter(project_id=ready_project, chapter_number=1, status="waiting_for_confirm")
        session.add(chapter)
        await session.flush()
        session.add(ChapterVersion(chapter_id=chapter.id, content="林川发现新的线索。", version_label="v1"))
        if not configured:
            await session.execute(delete(Prompt).where(Prompt.name == "evaluation"))
        await session.commit()

    async def assert_evaluating():
        async with database() as session:
            assert (await session.scalars(select(Chapter))).one().status == "evaluating"

    if configured:
        llm.reply("evaluation", "线索明确，节奏紧凑。", before=assert_evaluating)
    response = await client.post(f"/api/writer/novels/{ready_project}/chapters/evaluate", headers=auth, json={"chapter_number": 1})
    assert response.status_code == 200
    async with database() as session:
        assert (await session.scalars(select(Chapter))).one().status == "waiting_for_confirm"
        evaluation = (await session.scalars(select(ChapterEvaluation))).one()
        assert evaluation.version_id is not None
        assert evaluation.decision == ("reviewed" if configured else "skipped")
        assert evaluation.feedback == ("线索明确，节奏紧凑。" if configured else "未配置评审提示词")
