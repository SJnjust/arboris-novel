# -*- coding: utf-8 -*-
# AIMETA P=LLM工具_大模型调用辅助|R=请求构建_响应解析|NR=不含业务逻辑|E=LLMTool|X=internal|A=工具类|D=httpx|S=net|RD=./README.ai
"""OpenAI 兼容型 LLM 工具封装，保持与旧项目一致的接口体验。"""

import os
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import AsyncGenerator, Dict, List, Optional

from openai import AsyncOpenAI
import httpx


@dataclass
class _ClientEntry:
    client: AsyncOpenAI
    users: int = 0


_CLIENTS = OrderedDict()
_CLIENT_LIMIT = 64


@asynccontextmanager
async def openai_client(api_key, base_url=None):
    """按凭据/地址复用连接池；只淘汰空闲实例，不关闭在用流。"""
    key = (api_key, str(base_url) if base_url else None)
    entry = _CLIENTS.get(key)
    evicted = None
    if entry is None:
        if len(_CLIENTS) >= _CLIENT_LIMIT:
            idle = next((name for name, value in _CLIENTS.items() if value.users == 0), None)
            if idle is not None:
                evicted = _CLIENTS.pop(idle)
        entry = _ClientEntry(AsyncOpenAI(
            api_key=api_key, base_url=key[1], max_retries=1,
            timeout=httpx.Timeout(connect=10, read=90, write=30, pool=10),
        ))
        if len(_CLIENTS) < _CLIENT_LIMIT:
            _CLIENTS[key] = entry
    cached = _CLIENTS.get(key) is entry
    if cached:
        _CLIENTS.move_to_end(key)
    entry.users += 1
    try:
        if evicted:
            await evicted.client.close()
        yield entry.client
    finally:
        entry.users -= 1
        if not cached:
            await entry.client.close()


async def close_openai_clients():
    entries = list(_CLIENTS.values())
    _CLIENTS.clear()
    for entry in entries:
        await entry.client.close()


@dataclass
class ChatMessage:
    role: str
    content: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


class LLMClient:
    """异步流式调用封装，兼容 OpenAI SDK。"""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("缺少 OPENAI_API_KEY 配置，请在数据库或环境变量中补全。")

        self._key = key
        self._base_url = base_url or os.environ.get("OPENAI_API_BASE")

    async def stream_chat(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        response_format: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 120,
        **kwargs,
    ) -> AsyncGenerator[Dict[str, str], None]:
        payload = {
            "model": model or os.environ.get("MODEL", "gpt-3.5-turbo"),
            "messages": [msg.to_dict() for msg in messages],
            "stream": True,
            **kwargs,
        }
        if response_format:
            payload["response_format"] = {"type": response_format}
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        # timeout 是调用层总 deadline；不覆盖连接池的四项传输超时。
        async with openai_client(self._key, self._base_url) as client:
            stream = await client.chat.completions.create(**payload)
            try:
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    yield {"content": choice.delta.content, "finish_reason": choice.finish_reason}
            finally:
                await stream.close()
