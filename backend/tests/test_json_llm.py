from operation_helpers import complete_operation
import asyncio
import json

import pytest
from fastapi import HTTPException

from app.services.llm_service import LLMService
from app.utils.json_utils import parse_llm_json
from app.utils.llm_tool import close_openai_clients, openai_client


@pytest.mark.parametrize("source,expected", [
    ('{"a":1}', {"a": 1}),
    ('<think>思考 {伪数据}</think>\n{"a":true}', {"a": True}),
    ('```JSON\n{"a":1}\n```', {"a": 1}),
    ('结果如下：{"a":1} 完毕。', {"a": 1}),
    ('{"a":[1,2,],}', {"a": [1, 2]}),
    ('{"a":",}","b":2,}', {"a": ",}", "b": 2}),
    ('{"a":"第一行\n第二行"}', {"a": "第一行\n第二行"}),
    ('{"a":"他说"你好""}', {"a": '他说"你好"'}),
    ('{"a":"escaped \\\" quote"}', {"a": 'escaped " quote'}),
    ('null', None),
    ('false', False),
    ('42', 42),
    ('["甲","乙"]', ["甲", "乙"]),
    ('{"code":"```保留正文```"}', {"code": "```保留正文```"}),
    ('```json\n{"code":"```保留正文```"}\n```', {"code": "```保留正文```"}),
])
def test_llm_json_repairs(source, expected):
    assert parse_llm_json(source) == expected


@pytest.mark.parametrize("source", ["", "not JSON", "{invalid}", '{"a":1}, {"b":', '说明：{"a":[{"b":2}]'])
def test_invalid_json_still_fails(source):
    with pytest.raises(json.JSONDecodeError):
        parse_llm_json(source)


@pytest.mark.parametrize("source,partial", [
    ('{"a":[1,2', {"a": [1, 2]}),
    ('```json\n{"a":"未闭合', {"a": "未闭合"}),
    ('{"a":1,', {"a": 1}),
    ('{"items":[{"a":1},{"b":', {"items": [{"a": 1}]}),
    ('[1,2,unclosed', [1, 2]),
])
def test_truncation_requires_explicit_lossy_opt_in(source, partial):
    with pytest.raises(json.JSONDecodeError, match="输出不完整"):
        parse_llm_json(source)
    with pytest.raises(json.JSONDecodeError):
        parse_llm_json(source, allow_lossy=False)
    assert parse_llm_json(source, allow_lossy=True) == partial


async def test_text_length_continues_with_accumulated_context(llm):
    llm.reply("text", "第一段。", finish_reason="length")
    llm.reply("text", "第二段。", finish_reason="length")
    llm.reply("text", "结尾。")
    response = await LLMService(None, record_usage=False).get_llm_response(
        system_prompt="fixture:text", conversation_history=[], response_format=None)
    assert response == "第一段。第二段。结尾。"
    assert len(llm.calls) == 3
    assert llm.calls[2][1]["messages"][-2].content == "第一段。第二段。"
    assert "继续" in llm.calls[2][1]["messages"][-1].content


async def test_json_length_does_not_continue(llm):
    llm.reply("json", '{"a":1}', finish_reason="length")
    with pytest.raises(HTTPException) as raised:
        await LLMService(None, record_usage=False).get_llm_response(system_prompt="fixture:json", conversation_history=[])
    assert raised.value.status_code == 500
    assert "JSON 输出不支持自动续写" in raised.value.detail
    assert len(llm.calls) == 1


async def test_collector_deadline_is_504_and_cancels_stream(llm):
    ended = asyncio.Event()

    async def slow():
        try:
            await asyncio.sleep(10)
        finally:
            ended.set()

    llm.reply("slow", "too late", before=slow)
    with pytest.raises(HTTPException) as raised:
        await LLMService(None, record_usage=False).get_llm_response(system_prompt="fixture:slow", conversation_history=[], timeout=0.01)
    assert raised.value.status_code == 504
    assert "总时限" in raised.value.detail
    assert ended.is_set()


async def test_stream_deadline_does_not_cancel_the_consumer_between_chunks(llm):
    llm.reply('paced', '逐段正文')
    service = LLMService(None, record_usage=False)
    stream = service.stream_text([{'role': 'system', 'content': 'fixture:paced'}], timeout=0.01)
    try:
        assert await anext(stream) == '逐段'
        # 消费者自身的等待不得被异步生成器里悬挂的 timeout scope 取消。
        await asyncio.sleep(0.03)
        with pytest.raises(HTTPException) as raised:
            await anext(stream)
        assert raised.value.status_code == 504
        assert '总时限' in raised.value.detail
    finally:
        await stream.aclose()


async def test_sync_api_deadline_returns_readable_504(client, auth, project, llm, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "llm_deadline_seconds", 0.01)
    llm.reply("concept", "late", before=lambda: asyncio.sleep(1))
    response = await client.post(f"/api/novels/{project}/concept/converse", headers=auth,
                                 json={"user_input": {"value": "test"}, "conversation_state": {}})
    response = await complete_operation(client, auth, response)
    assert response.status_code == 504
    assert "总时限" in response.json()["detail"]


async def test_http_deadline_only_limits_explicit_short_routes(client, auth, job_runner, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "http_request_deadline_seconds", 0.02)
    ended = asyncio.Event()

    async def slow(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            ended.set()

    monkeypatch.setattr(job_runner, "list", slow)
    response = await client.get("/api/jobs", headers={**auth, "Origin": "http://fixture.test"})
    assert response.status_code == 504
    assert response.json()["detail"] == "请求处理超时，请稍后重试"
    assert response.headers.get("access-control-allow-origin")
    assert ended.is_set()


async def test_sdk_pool_reuses_credentials_and_has_exact_timeouts():
    try:
        async with openai_client("offline-test", "https://fixture.invalid") as first:
            assert first.max_retries == 1
            assert (first.timeout.connect, first.timeout.read, first.timeout.write, first.timeout.pool) == (10, 90, 30, 10)
        async with openai_client("offline-test", "https://fixture.invalid") as second:
            assert first is second
        async with openai_client("offline-test", "https://other.invalid") as other:
            assert first is not other
        await close_openai_clients()
        assert first.is_closed() and other.is_closed()
    finally:
        await close_openai_clients()


async def test_pool_does_not_evict_clients_with_active_streams(monkeypatch):
    from app.utils import llm_tool
    monkeypatch.setattr(llm_tool, "_CLIENT_LIMIT", 2)
    try:
        async with openai_client("offline-1") as one, openai_client("offline-2") as two:
            async with openai_client("offline-3") as temporary:
                assert not one.is_closed() and not two.is_closed()
            assert temporary.is_closed()
            assert len(llm_tool._CLIENTS) == 2
        async with openai_client("offline-4"):
            assert one.is_closed()
            assert not two.is_closed()
    finally:
        await close_openai_clients()
