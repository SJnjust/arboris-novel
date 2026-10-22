import asyncio

import httpx
from fastapi import APIRouter, FastAPI
from starlette.responses import StreamingResponse

from app.core.config import settings
from app.core.request_deadline import RequestDeadlineRoute, request_deadline
from app.main import app
from test_concept_blueprint import CONCEPT


def test_api_deadlines_are_explicit_and_limited_to_task_routes():
    api = [route for route in app.routes if route.path.startswith('/api/')]
    limited = {(method, route.path) for route in api for method in route.methods
               if getattr(route.endpoint, 'request_deadline', False)}
    assert limited == {
        ('POST', '/api/writer/novels/{project_id}/chapters/generate'),
        ('GET', '/api/jobs'), ('GET', '/api/jobs/{job_id}'),
        ('POST', '/api/jobs/{job_id}/cancel'),
        ('POST', '/api/novels/{project_id}/blueprint/generate'),
        ('GET', '/api/novels/{project_id}/volumes'),
        ('PATCH', '/api/novels/{project_id}/volumes/{number}'),
        ('POST', '/api/novels/{project_id}/volumes/{number}/outline'),
        ('POST', '/api/novels/{project_id}/volumes/outline-all'),
        ('POST', '/api/novels/{project_id}/volumes/smart-split'),
    }
    assert all(isinstance(route, RequestDeadlineRoute) for route in api
               if getattr(route.endpoint, 'request_deadline', False))
    assert not any(middleware.cls.__name__ == 'RequestDeadlineMiddleware' for middleware in app.user_middleware)


async def test_legacy_sync_generation_can_outlive_short_route_timeout(client, auth, project, llm, monkeypatch):
    monkeypatch.setattr(settings, 'http_request_deadline_seconds', 0.01)
    llm.reply('concept', CONCEPT, before=lambda: asyncio.sleep(0.04))
    response = await client.post(f'/api/novels/{project}/concept/converse', headers=auth,
                                 json={'user_input': {'value': '测试'}, 'conversation_state': {}})
    assert response.status_code == 200
    assert response.json()['ai_message'] == CONCEPT['ai_message']


async def test_started_stream_is_not_cancelled_by_route_deadline(monkeypatch):
    monkeypatch.setattr(settings, 'http_request_deadline_seconds', 0.01)
    router = APIRouter(route_class=RequestDeadlineRoute)
    finished = asyncio.Event()

    @router.get('/api/stream')
    @request_deadline
    async def stream():
        async def events():
            yield 'event: ping\ndata: start\n\n'
            await asyncio.sleep(0.04)
            yield 'event: final\ndata: done\n\n'
            finished.set()
        return StreamingResponse(events(), media_type='text/event-stream')

    isolated = FastAPI()
    isolated.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated), base_url='http://test') as client:
        response = await client.get('/api/stream')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')
    assert response.text == 'event: ping\ndata: start\n\nevent: final\ndata: done\n\n'
    assert finished.is_set()
