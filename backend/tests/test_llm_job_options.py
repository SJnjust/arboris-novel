import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select, text, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.core.llm_policy import llm_job_context
from app.db.base import Base
from app.db.init_db import _ensure_schema_updates
from app.models import LLMConfig, SystemConfig
from app.services import llm_service
from app.services.llm_service import LLMService
from app.utils import llm_tool


def install_transport(monkeypatch, handler):
    real = llm_tool.AsyncOpenAI
    monkeypatch.setattr(llm_tool, 'AsyncOpenAI', lambda **kwargs: real(**kwargs,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    monkeypatch.setattr(llm_service, 'LLMClient', llm_tool.LLMClient)


def streamed(request):
    event = {'id': 'offline', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'gpt-6.1',
             'choices': [{'index': 0, 'delta': {'content': '{}'}, 'finish_reason': 'stop'}]}
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
        text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n')


@pytest.mark.parametrize('structured,effort,override,expected_model,expected_effort', [
    (False, 'xhigh', 'low', 'gpt-6.1', 'xhigh'),
    (True, 'xhigh', 'low', 'structured-test-model', 'low'),
    (True, 'high', None, 'gpt-6.1', 'high'),
    (False, None, None, 'gpt-6.1', None),
    (False, 'none', None, 'gpt-6.1', 'none'),
])
async def test_real_sdk_payload_for_main_structured_and_blank_efforts(monkeypatch, structured, effort, override, expected_model, expected_effort):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return streamed(request)
    await llm_tool.close_openai_clients()
    install_transport(monkeypatch, handler)
    config = {'api_key': 'offline-fixture', 'base_url': 'http://fixture.invalid/v1', 'model': 'gpt-6.1',
              'reasoning_effort': effort, 'structured_reasoning_effort': override,
              'structured_model': 'structured-test-model' if override else None}
    try:
        assert await LLMService(None, config=config, record_usage=False).get_llm_response('test', [], structured=structured) == '{}'
        payload = requests[0]
        assert payload['model'] == expected_model
        if expected_effort:
            assert payload['reasoning_effort'] == expected_effort
            assert 'temperature' not in payload and 'top_p' not in payload
        else:
            assert 'reasoning_effort' not in payload
            assert 'temperature' in payload
        assert 'structured' not in payload and 'read_timeout' not in payload
    finally:
        await llm_tool.close_openai_clients()


async def test_unsupported_reasoning_parameter_is_clear_400_without_silent_retry(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={'error': {'message': 'provider-diagnostic reasoning_effort unsupported',
            'type': 'invalid_request_error', 'param': 'reasoning_effort'}})
    await llm_tool.close_openai_clients()
    install_transport(monkeypatch, handler)
    try:
        with pytest.raises(HTTPException) as error:
            await LLMService(None, config={'api_key': 'offline', 'reasoning_effort': 'xhigh'}, record_usage=False).get_llm_response('test', [])
        assert error.value.status_code == 400
        assert '不支持 reasoning_effort' in error.value.detail and '清空' in error.value.detail
        assert 'provider-diagnostic' not in error.value.detail
        assert len(calls) == 1
    finally:
        await llm_tool.close_openai_clients()


async def test_job_deadline_and_transport_read_timeout_are_separate_from_foreground(monkeypatch):
    monkeypatch.setattr(settings, 'llm_deadline_seconds', 0.01)
    monkeypatch.setattr(settings, 'llm_job_deadline_seconds', 0.3)
    monkeypatch.setattr(settings, 'llm_job_read_timeout_seconds', 777)
    timeouts = []
    async def handler(request):
        timeouts.append(request.extensions['timeout']['read'])
        await asyncio.sleep(0.035)
        return streamed(request)
    await llm_tool.close_openai_clients()
    install_transport(monkeypatch, handler)
    service = LLMService(None, config={'api_key': 'offline'}, record_usage=False)
    try:
        with llm_job_context():
            assert await service.get_llm_response('test', [], timeout=0.005) == '{}'
        with pytest.raises(HTTPException) as error:
            await service.get_llm_response('test', [], timeout=0.3)
        assert error.value.status_code == 504
        assert timeouts == [777, 90]
        async with llm_tool.openai_client('offline', read_timeout=777) as background, llm_tool.openai_client('offline') as foreground:
            assert background is not foreground
            assert background.timeout.read == 777 and foreground.timeout.read == 90
    finally:
        await llm_tool.close_openai_clients()


async def test_personal_and_global_reasoning_settings_round_trip_and_resolve(client, auth, database, monkeypatch):
    from conftest import REAL_RESOLVE_CONFIG
    monkeypatch.setattr(LLMService, '_resolve_llm_config', REAL_RESOLVE_CONFIG)
    monkeypatch.setattr(LLMService, '_enforce_daily_limit', lambda *args: asyncio.sleep(0, result='2026-10-23'))
    async with database() as session:
        session.add_all([SystemConfig(key='llm.api_key', value='offline-global'), SystemConfig(key='llm.model', value='gpt-6.1'),
            SystemConfig(key='llm.reasoning_effort', value='xhigh'), SystemConfig(key='llm.structured_reasoning_effort', value='low')])
        await session.commit()
        config = await LLMService(session)._resolve_llm_config(1)
        assert config['reasoning_effort'] == 'xhigh' and config['structured_reasoning_effort'] == 'low'
    response = await client.put('/api/llm-config', headers=auth, json={'llm_provider_url': '', 'reasoning_effort': ' HIGH ',
        'structured_model': ' structured-test ', 'structured_reasoning_effort': 'medium'})
    assert response.status_code == 200
    body = (await client.get('/api/llm-config', headers=auth)).json()
    assert body['reasoning_effort'] == 'high' and body['structured_model'] == 'structured-test'
    async with database() as session:
        config = await LLMService(session)._resolve_llm_config(1)
        assert config['reasoning_effort'] == 'high' and config['structured_model'] == 'structured-test'
        assert config['structured_reasoning_effort'] == 'medium'
    invalid = await client.put('/api/llm-config', headers=auth, json={'reasoning_effort': 'invalid'})
    assert invalid.status_code == 422
    cleared = await client.put('/api/llm-config', headers=auth, json={'reasoning_effort': '', 'structured_model': '', 'structured_reasoning_effort': ''})
    assert cleared.status_code == 200 and cleared.json()['reasoning_effort'] is None
    async with database() as session:
        config = await LLMService(session)._resolve_llm_config(1)
        assert config['reasoning_effort'] is None and config['structured_model'] is None and config['structured_reasoning_effort'] is None


async def test_old_llm_config_columns_migrate_idempotently(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "migration.db"}')
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(text('DROP TABLE llm_configs'))
            await connection.execute(text('CREATE TABLE llm_configs (user_id INTEGER PRIMARY KEY, llm_provider_url TEXT, llm_provider_api_key TEXT, llm_provider_model TEXT)'))
        await _ensure_schema_updates(engine)
        await _ensure_schema_updates(engine)
        async with engine.begin() as connection:
            columns = await connection.run_sync(lambda conn: {item['name'] for item in inspect(conn).get_columns('llm_configs')})
        assert {'reasoning_effort', 'structured_model', 'structured_reasoning_effort'} <= columns
    finally:
        await engine.dispose()


def test_deadline_and_batch_environment_ranges_are_validated():
    from app.core.config import Settings
    from pydantic import ValidationError
    config = Settings(_env_file=None, llm_job_deadline_seconds=3600, llm_job_read_timeout_seconds=3600,
        outline_batch_size=20, llm_reasoning_effort=' XHIGH ')
    assert config.llm_job_deadline_seconds == 3600 and config.llm_reasoning_effort == 'xhigh'
    for changes in [{'outline_batch_size': 4}, {'outline_batch_size': 21}, {'llm_job_deadline_seconds': 7201}, {'llm_reasoning_effort': 'invalid'}]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **changes)
