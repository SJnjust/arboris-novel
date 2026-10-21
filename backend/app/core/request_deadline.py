import asyncio

from fastapi.routing import APIRoute
from starlette.responses import JSONResponse

from .config import settings


def request_deadline(endpoint):
    """仅给已迁移的任务提交/查询显式启用短请求时限。"""
    endpoint.request_deadline = True
    return endpoint


class RequestDeadlineRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        if not getattr(self.endpoint, "request_deadline", False):
            return handler

        async def limited(request):
            try:
                # Response 的 ASGI 发送/流迭代发生在 handler 返回后，不受此时限取消。
                return await asyncio.wait_for(handler(request), settings.http_request_deadline_seconds)
            except asyncio.TimeoutError:
                return JSONResponse({"detail": "请求处理超时，请稍后重试"}, status_code=504)

        return limited
