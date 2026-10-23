from operation_helpers import complete_operation
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.main import app
from app.models import NovelConversation, SystemConfig, UserDailyRequest
from app.services.llm_service import LLMService
from app.services.usage_service import UsageService
from app.services import llm_service
from app.utils.concept_stream import MessageDeltas
from test_concept_blueprint import CONCEPT


def model(monkeypatch, stream):
    monkeypatch.setattr(llm_service, 'LLMClient', lambda **kwargs: SimpleNamespace(stream_chat=stream))


def frames(text):
    result = []
    for block in text.split('\n\n'):
        if not block.strip():
            continue
        lines = block.splitlines()
        result.append((lines[0].removeprefix('event: '), json.loads(lines[1].removeprefix('data: '))))
    return result


async def test_sse_deltas_ping_final_and_sync_replay_share_contract(client, auth, project, database, monkeypatch):
    monkeypatch.setattr(settings, 'concept_ping_seconds', 0.01)
    calls = []
    text = '你好\n"继续"😀'
    async def stream(**kwargs):
        calls.append(kwargs)
        yield {'content': '{"conversation_state":{"ai_message":"不得推送"},"ai_message":"你好'}
        await asyncio.sleep(0.04)
        yield {'content': '\\n\\"继续\\"\\ud83d'}
        yield {'content': '\\ude00","ui_control":{"type":"text_input"},"is_complete":false}', 'finish_reason': 'stop'}
    model(monkeypatch, stream)
    body = {'user_input': {'value': '新灵感'}, 'conversation_state': {}, 'client_msg_id': 'turn-1'}
    async with client.stream('POST', f'/api/novels/{project}/concept/converse/stream', headers=auth, json=body) as response:
        assert response.status_code == 200
        assert response.headers['x-accel-buffering'] == 'no'
        assert response.headers['cache-control'] == 'no-cache'
        assert response.headers['content-type'].startswith('text/event-stream')
        data = frames((await response.aread()).decode())
    first_delta = next(index for index, (kind, _) in enumerate(data) if kind == 'delta')
    assert data[first_delta] == ('delta', {'text': '你好'})
    assert any(kind == 'ping' for kind, _ in data[first_delta + 1:-1])
    assert ''.join(item['text'] for kind, item in data if kind == 'delta') == text
    final = data[-1][1]
    assert data[-1][0] == 'final' and final['ai_message'] == text
    assert final['conversation_state'] == {'ai_message': '不得推送'}
    replay = await client.post(f'/api/novels/{project}/concept/converse', headers=auth, json=body)
    replay = await complete_operation(client, auth, replay)
    assert replay.status_code == 200 and replay.json() == final
    assert len(calls) == 1
    async with database() as session:
        rows = (await session.scalars(select(NovelConversation).order_by(NovelConversation.seq))).all()
        assert [row.role for row in rows] == ['user', 'assistant']
        assert all(row.metadata['client_msg_id'] == 'turn-1' for row in rows)
        assert rows[1].metadata['conversation_state'] == final['conversation_state']


async def test_error_retry_reuses_user_and_completed_turn_replays(client, auth, project, database, monkeypatch):
    calls = 0
    async def stream(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {'content': '{"ai_message":"保留部分'}
            raise HTTPException(status_code=503, detail='模拟上游暂时不可用')
        yield {'content': json.dumps(CONCEPT), 'finish_reason': 'stop'}
    model(monkeypatch, stream)
    body = {'user_input': {'value': '保留我的输入'}, 'conversation_state': {}, 'client_msg_id': 'stable-turn'}
    url = f'/api/novels/{project}/concept/converse/stream'
    failed = frames((await client.post(url, headers=auth, json=body)).text)
    assert failed[-1] == ('error', {'detail': '模拟上游暂时不可用', 'retryable': True})
    async with database() as session:
        assert [row.role for row in (await session.scalars(select(NovelConversation))).all()] == ['user']
    responses = await asyncio.gather(*(client.post(url, headers=auth, json=body) for _ in range(2)))
    assert all(frames(response.text)[-1][0] == 'final' for response in responses)
    assert calls == 2
    async with database() as session:
        assert [row.role for row in (await session.scalars(select(NovelConversation).order_by(NovelConversation.seq))).all()] == ['user', 'assistant']
    wrong = await client.post(url, headers=auth, json={**body, 'user_input': {'value': '另一个输入'}})
    assert frames(wrong.text)[-1] == ('error', {'detail': '本轮消息标识已用于其他输入，请发起新一轮', 'retryable': False})
    assert calls == 2


@pytest.mark.parametrize('raw', ['{"ai_message":"未完成","ui_control":', json.dumps([['ai_message', '不接受数组'], ['ui_control', {'type': 'text_input'}], ['conversation_state', {}]])])
async def test_truncated_json_sse_errors_without_assistant(client, auth, project, database, monkeypatch, raw):
    async def stream(**kwargs):
        yield {'content': raw, 'finish_reason': 'stop'}
    model(monkeypatch, stream)
    response = await client.post(f'/api/novels/{project}/concept/converse/stream', headers=auth,
                                 json={'user_input': {}, 'conversation_state': {}, 'client_msg_id': 'bad-json'})
    data = frames(response.text)
    assert data[-1][0] == 'error' and data[-1][1]['retryable']
    assert 'AI 返回的内容格式不正确' in data[-1][1]['detail']
    assert not any(kind == 'final' for kind, _ in data)
    async with database() as session:
        assert [row.role for row in (await session.scalars(select(NovelConversation))).all()] == ['user']


async def test_history_is_twelve_messages_and_legacy_assistant_json_is_projected(client, auth, project, database, monkeypatch):
    async with database() as session:
        for number in range(30):
            assistant = number % 2 == 1
            content = json.dumps({**CONCEPT, 'ai_message': f'回复{number}', 'conversation_state': {'old': number}}) if assistant else f'用户{number}'
            session.add(NovelConversation(project_id=project, seq=number + 1, role='assistant' if assistant else 'user',
                                           content=content, metadata={'conversation_state': {'genre': '最新悬疑'}} if number == 29 else None))
        await session.commit()
    seen = []
    async def stream(**kwargs):
        seen.extend(kwargs['messages'])
        yield {'content': json.dumps(CONCEPT), 'finish_reason': 'stop'}
    model(monkeypatch, stream)
    response = await client.post(f'/api/novels/{project}/concept/converse/stream', headers=auth,
                                 json={'user_input': {'value': '继续'}, 'conversation_state': {'genre': '伪造旧值'}, 'client_msg_id': 'new-turn'})
    assert frames(response.text)[-1][0] == 'final'
    assert len(seen) == 13
    assert '[已收集信息]\n{"genre": "最新悬疑"}' in seen[0].content
    assert '伪造旧值' not in seen[0].content
    assert all(message.content.startswith('回复') and '{' not in message.content for message in seen if message.role == 'assistant')
    assert all('用户0' != message.content for message in seen)


async def test_disconnect_cancels_upstream_and_never_saves_assistant(client, auth, project, database, monkeypatch):
    from conftest import REAL_RESOLVE_CONFIG
    monkeypatch.setattr(LLMService, '_resolve_llm_config', REAL_RESOLVE_CONFIG)
    async with database() as session:
        session.add(SystemConfig(key='llm.api_key', value='offline-cancellation-fixture'))
        await session.commit()
    ended = asyncio.Event()
    async def stream(**kwargs):
        try:
            yield {'content': '{"ai_message":"已经开始'}
            await asyncio.sleep(30)
        finally:
            ended.set()
    model(monkeypatch, stream)
    disconnected = asyncio.Event()
    first = True
    payload = json.dumps({'user_input': {'value': '取消这轮'}, 'conversation_state': {}, 'client_msg_id': 'disconnect'}).encode()
    async def receive():
        nonlocal first
        if first:
            first = False
            return {'type': 'http.request', 'body': payload, 'more_body': False}
        await disconnected.wait()
        return {'type': 'http.disconnect'}
    async def send(message):
        if message['type'] == 'http.response.body' and b'event: delta' in message.get('body', b''):
            disconnected.set()
    path = f'/api/novels/{project}/concept/converse/stream'
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'}, 'method': 'POST', 'scheme': 'http',
             'path': path, 'raw_path': path.encode(), 'root_path': '', 'query_string': b'', 'http_version': '1.1',
             'server': ('test', 80), 'client': ('127.0.0.1', 1234),
             'headers': [(b'content-type', b'application/json'), *[(key.lower().encode(), value.encode()) for key, value in auth.items()]]}
    await asyncio.wait_for(app(scope, receive, send), 3)
    assert ended.is_set()
    assert disconnected.is_set()
    async with database() as session:
        assert [row.role for row in (await session.scalars(select(NovelConversation))).all()] == ['user']
        assert (await session.scalars(select(UserDailyRequest))).one().request_count == 0
        assert await UsageService(session).get_value('api_request_count') == 0
    assert not any(task.get_name().startswith('concept:') for task in asyncio.all_tasks())


@pytest.mark.parametrize('width', [1, 2, 7, 999])
def test_ai_message_decoder_handles_escapes_without_other_fields(width):
    raw = '<think>{"ai_message":"内部思考"}</think>```json\n' + json.dumps({
        'hidden': {'ai_message': '不展示'}, 'ai_message': '问候😀\n"你"\\继续', 'ui_control': {'ai_message': '不展示'}
    }, ensure_ascii=True) + '\n```'
    decoder = MessageDeltas()
    result = ''.join(decoder.feed(raw[index:index + width]) for index in range(0, len(raw), width))
    assert result == '问候😀\n"你"\\继续'


async def test_ping_is_periodic_even_with_continuous_deltas(client, auth, project, monkeypatch):
    monkeypatch.setattr(settings, 'concept_ping_seconds', 0.01)
    async def stream(**kwargs):
        yield {'content': '{"ai_message":"'}
        for _ in range(15):
            yield {'content': '字'}
            await asyncio.sleep(0.003)
        yield {'content': '","ui_control":{"type":"text_input"},"conversation_state":{}}', 'finish_reason': 'stop'}
    model(monkeypatch, stream)
    response = await client.post(f'/api/novels/{project}/concept/converse/stream', headers=auth,
                                 json={'user_input': {}, 'conversation_state': {}, 'client_msg_id': 'frequent'})
    data = frames(response.text)
    assert data[-1][0] == 'final'
    assert len([kind for kind, _ in data if kind == 'ping']) >= 2
    assert ''.join(item['text'] for kind, item in data if kind == 'delta') == '字' * 15


async def test_lossless_quote_repair_keeps_sync_and_stream_contract(client, auth, project, monkeypatch):
    async def stream(**kwargs):
        yield {'content': '{"ai_message":"他说"你好"","ui_control":{"type":"text_input"},"conversation_state":{}}', 'finish_reason': 'stop'}
    model(monkeypatch, stream)
    body = {'user_input': {}, 'conversation_state': {}, 'client_msg_id': 'quotes'}
    streamed = frames((await client.post(f'/api/novels/{project}/concept/converse/stream', headers=auth, json=body)).text)
    assert streamed[-1][0] == 'final'
    assert ''.join(item['text'] for kind, item in streamed if kind == 'delta') == '他说"你好"'
    replay = await client.post(f'/api/novels/{project}/concept/converse', headers=auth, json=body)
    replay = await complete_operation(client, auth, replay)
    assert replay.json() == streamed[-1][1]


@pytest.mark.parametrize('control, expected', [
    (None, {'type': 'text_input', 'options': None, 'placeholder': None}),
    ({'type': 'text'}, {'type': 'text_input', 'options': None, 'placeholder': None}),
    ({'type': 'single_choice', 'options': []}, {'type': 'text_input', 'options': None, 'placeholder': None}),
    ({'type': 'multiple_choice', 'options': ['悬疑', {'text': '奇幻'}, '']},
     {'type': 'single_choice', 'options': [{'id': '1', 'label': '悬疑'}, {'id': '2', 'label': '奇幻'}], 'placeholder': None}),
    ({'type': 'text_input', 'options': [{'id': 'a', 'label': '忽略'}], 'placeholder': '说说主角'},
     {'type': 'text_input', 'options': None, 'placeholder': '说说主角'}),
])
async def test_ui_control_is_normalized_so_the_input_always_renders(client, auth, project, database, monkeypatch, control, expected):
    payload = {'ai_message': '继续说说吧', 'conversation_state': {}, 'is_complete': False}
    if control is not None:
        payload['ui_control'] = control
    async def stream(**kwargs):
        yield {'content': json.dumps(payload, ensure_ascii=False), 'finish_reason': 'stop'}
    model(monkeypatch, stream)
    body = {'user_input': {'value': '灵感'}, 'conversation_state': {}, 'client_msg_id': 'ui-turn'}
    async with client.stream('POST', f'/api/novels/{project}/concept/converse/stream', headers=auth, json=body) as response:
        data = frames((await response.aread()).decode())
    assert data[-1][0] == 'final' and data[-1][1]['ui_control'] == expected
    async with database() as session:
        stored = (await session.scalars(select(NovelConversation).where(NovelConversation.role == 'assistant'))).one()
        assert json.loads(stored.content)['ui_control'] == {key: value for key, value in expected.items() if value is not None}
