# AIMETA P=LLM服务_大模型调用封装|R=API调用_流式生成|NR=不含业务逻辑|E=LLMService|X=internal|A=服务类|D=openai,httpx|S=net|RD=./README.ai
import asyncio
import logging
import os
from contextlib import aclosing
from datetime import date
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException, status
from openai import APIConnectionError, APITimeoutError, InternalServerError, BadRequestError

from ..core.config import settings
from ..core.llm_policy import generation_deadline, read_timeout
from ..core.llm_options import reasoning_effort
from ..repositories.llm_config_repository import LLMConfigRepository
from ..repositories.system_config_repository import SystemConfigRepository
from ..repositories.user_repository import UserRepository
from ..services.admin_setting_service import AdminSettingService
from ..services.prompt_service import PromptService
from ..services.usage_service import UsageService
from ..utils.llm_tool import ChatMessage, LLMClient, openai_client

logger = logging.getLogger(__name__)

try:  # pragma: no cover - 运行环境未安装时兼容
    from ollama import AsyncClient as OllamaAsyncClient
except ImportError:  # pragma: no cover - Ollama 为可选依赖
    OllamaAsyncClient = None


class LLMService:
    """封装与大模型交互的所有逻辑，包括配额控制与配置选择。"""

    def __init__(self, session, *, config=None, record_usage=True, usage_callback=None):
        self.session = session
        self._fixed_config = config
        self._record_usage = record_usage
        self._usage_callback = usage_callback
        self.llm_repo = LLMConfigRepository(session)
        self.system_config_repo = SystemConfigRepository(session)
        self.user_repo = UserRepository(session)
        self.admin_setting_service = AdminSettingService(session)
        self.usage_service = UsageService(session)
        self._embedding_dimensions: Dict[str, int] = {}

    async def get_llm_response(
        self,
        system_prompt: str,
        conversation_history: List[Dict[str, str]],
        *,
        temperature: float = 0.7,
        user_id: Optional[int] = None,
        timeout: float = 300.0,
        response_format: Optional[str] = "json_object",
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        structured: bool = False,
    ) -> str:
        messages = [{"role": "system", "content": system_prompt}, *conversation_history]
        return await self._stream_and_collect(
            messages,
            temperature=temperature,
            user_id=user_id,
            timeout=timeout,
            response_format=response_format,
            max_tokens=max_tokens,
            top_p=top_p, structured=structured,
        )

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        user_id: Optional[int] = None,
        timeout: float = 300.0,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        top_p: Optional[float] = None,
        structured: bool = False,
    ) -> str:
        """兼容旧版接口的文本生成入口，统一走 get_llm_response。"""
        return await self.get_llm_response(
            system_prompt=system_prompt or "你是一位专业写作助手。",
            conversation_history=[{"role": "user", "content": prompt}],
            temperature=temperature,
            user_id=user_id,
            timeout=timeout,
            response_format=response_format,
            max_tokens=max_tokens,
            top_p=top_p, structured=structured,
        )

    async def get_summary(
        self,
        chapter_content: str,
        *,
        temperature: float = 0.2,
        user_id: Optional[int] = None,
        timeout: float = 180.0,
        system_prompt: Optional[str] = None,
    ) -> str:
        if not system_prompt:
            prompt_service = PromptService(self.session)
            system_prompt = await prompt_service.get_prompt("extraction")
        if not system_prompt:
            logger.error("未配置名为 'extraction' 的摘要提示词，无法生成章节摘要")
            raise HTTPException(status_code=500, detail="未配置摘要提示词，请联系管理员配置 'extraction' 提示词")
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": chapter_content},
        ]
        return await self._stream_and_collect(messages, temperature=temperature, user_id=user_id, timeout=timeout)

    async def _stream_and_collect(
        self, messages: List[Dict[str, str]], *, temperature: float,
        user_id: Optional[int], timeout: float, response_format: Optional[str] = None,
        max_tokens: Optional[int] = None, top_p: Optional[float] = None, structured: bool = False,
    ) -> str:
        async def operation():
            config = self._fixed_config or await self._resolve_llm_config(user_id)
            if structured:
                config = {**config, "model": config.get("structured_model") or config.get("model"),
                          "reasoning_effort": config.get("structured_reasoning_effort") or config.get("reasoning_effort")}
            return await self._collect(messages, config=config, temperature=temperature,
                                       user_id=user_id, timeout=timeout, response_format=response_format,
                                       max_tokens=max_tokens, top_p=top_p)

        try:
            return await asyncio.wait_for(operation(), timeout=generation_deadline(timeout))
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=504, detail="AI 生成超过总时限，请稍后重试或缩小生成范围") from exc

    async def stream_text(
        self, messages: List[Dict[str, str]], *, temperature: float = 0.7,
        user_id: Optional[int] = None, timeout: float = 300.0,
        response_format: Optional[str] = None, max_tokens: Optional[int] = None,
        top_p: Optional[float] = None, config=None,
    ):
        """逐段返回文本；配置、续写与流关闭均在同一个总 deadline 内。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + generation_deadline(timeout)
        try:
            resolved = config or self._fixed_config
            if resolved is None:
                resolved = await asyncio.wait_for(self._resolve_llm_config(user_id), max(0, deadline - loop.time()))
            async with aclosing(self._stream_body(
                messages, config=resolved, temperature=temperature, user_id=user_id,
                timeout=timeout, response_format=response_format, max_tokens=max_tokens, top_p=top_p,
            )) as stream:
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    try:
                        text = await asyncio.wait_for(anext(stream), remaining)
                    except StopAsyncIteration:
                        return
                    # yield 时不在 timeout context 中，避免取消消费者自身的工作。
                    yield text
        except asyncio.TimeoutError as exc:
            raise HTTPException(status_code=504, detail="AI 生成超过总时限，请稍后重试或缩小生成范围") from exc

    async def _collect(self, messages, *, config, temperature, user_id, timeout,
                       response_format, max_tokens, top_p):
        parts = []
        async with aclosing(self.stream_text(
            messages, config=config, temperature=temperature, user_id=user_id, timeout=timeout,
            response_format=response_format, max_tokens=max_tokens, top_p=top_p,
        )) as stream:
            async for text in stream:
                parts.append(text)
        return "".join(parts)

    async def _stream_body(self, messages, *, config, temperature, user_id, timeout,
                       response_format, max_tokens, top_p):
        timeout = generation_deadline(timeout)
        client = LLMClient(api_key=config["api_key"], base_url=config.get("base_url"))
        full_response = ""
        finish_reason = None
        completed_calls = 0
        history = list(messages)
        try:
            for attempt in range(3):
                segment = ""
                finish_reason = None
                stream = client.stream_chat(
                    messages=[ChatMessage(role=msg["role"], content=msg["content"]) for msg in history],
                    model=config.get("model"), temperature=temperature, timeout=timeout, read_timeout=read_timeout(),
                    reasoning_effort=reasoning_effort(config.get("reasoning_effort")),
                    response_format=response_format, max_tokens=max_tokens, top_p=top_p,
                )
                async with aclosing(stream):
                    async for part in stream:
                        text = part.get("content") or ""
                        segment += text
                        if text:
                            yield text
                        if part.get("finish_reason"):
                            finish_reason = part["finish_reason"]
                completed_calls += 1
                if self._usage_callback:
                    self._usage_callback()
                full_response += segment
                if finish_reason != "length":
                    break
                if response_format:
                    raise HTTPException(status_code=500, detail="AI 响应因长度限制被截断：JSON 输出不支持自动续写，请缩小生成范围")
                if attempt == 2:
                    raise HTTPException(status_code=500, detail="AI 响应因长度限制被截断，续写 2 次后仍未完成，请缩小生成范围")
                history = [*messages, {"role": "assistant", "content": full_response},
                           {"role": "user", "content": "请从上一段最后一个字符之后继续，只返回后续正文，不要重复已生成的内容。"}]
        except BadRequestError as exc:
            logger.exception("模型请求参数被上游拒绝")
            detail = "AI 服务拒绝请求，请检查模型与参数设置后重试"
            if config.get("reasoning_effort") and (getattr(exc, "param", None) == "reasoning_effort" or "reasoning_effort" in str(getattr(exc, "body", ""))):
                detail = "当前中转或模型不支持 reasoning_effort，请清空推理设置（含全局及结构化覆盖），或更换支持该参数的中转/模型"
            raise HTTPException(status_code=400, detail=detail) from exc
        except InternalServerError as exc:
            detail = "AI 服务内部错误，请稍后重试"
            logger.error(
                "LLM stream internal error: model=%s user_id=%s detail=%s",
                config.get("model"),
                user_id,
                detail,
                exc_info=exc,
            )
            raise HTTPException(status_code=503, detail=detail)
        except (httpx.RemoteProtocolError, httpx.ReadTimeout, APIConnectionError, APITimeoutError) as exc:
            if isinstance(exc, httpx.RemoteProtocolError):
                detail = "AI 服务连接被意外中断，请稍后重试"
            elif isinstance(exc, (httpx.ReadTimeout, APITimeoutError)):
                detail = "AI 服务响应超时，请稍后重试"
            else:
                detail = "无法连接到 AI 服务，请稍后重试"
            logger.error(
                "LLM stream failed: model=%s user_id=%s detail=%s",
                config.get("model"),
                user_id,
                detail,
                exc_info=exc,
            )
            raise HTTPException(status_code=503, detail=detail) from exc


        if not full_response:
            raise HTTPException(status_code=500, detail=f"AI 未返回有效内容（结束原因: {finish_reason or '未知'}），请稍后重试或联系管理员")
        if self._record_usage:
            await self.usage_service.increment("api_request_count", completed_calls, quota=config.get("_quota"))

    async def _resolve_llm_config(self, user_id: Optional[int]) -> Dict[str, Any]:
        personal = await self.llm_repo.get_by_user(user_id) if user_id else None
        api_key = (personal.llm_provider_api_key if personal else None) or await self._get_config_value("llm.api_key") or settings.openai_api_key
        if not api_key:
            raise HTTPException(status_code=500, detail="未配置默认 LLM API Key，请联系管理员配置系统默认 API Key 或在个人设置中配置自定义 API Key")
        own_key = bool(personal and personal.llm_provider_api_key)
        base_url = personal.llm_provider_url if own_key else (await self._get_config_value("llm.base_url") or (str(settings.openai_base_url) if settings.openai_base_url else None))
        model = (personal.llm_provider_model if personal else None) or await self._get_config_value("llm.model") or settings.openai_model_name
        options = {}
        for name in ("reasoning_effort", "structured_model", "structured_reasoning_effort"):
            # 有个人配置时可明确留空，不能被全局强度重新填回而无法关闭参数。
            options[name] = getattr(personal, name, None) if personal is not None else (await self._get_config_value("llm." + name) or getattr(settings, "llm_" + name))
        # 个人参数可覆盖默认参数；只有自带 key 时才免系统每日额度。
        quota = {"user_id": user_id, "date": await self._enforce_daily_limit(user_id)} if user_id and not own_key else None
        return {"api_key": api_key, "base_url": base_url, "model": model, "_quota": quota, **options}

    async def get_embedding(
        self,
        text: str,
        *,
        user_id: Optional[int] = None,
        model: Optional[str] = None,
    ) -> List[float]:
        """生成文本向量，用于章节 RAG 检索，支持 openai 与 ollama 双提供方。"""
        provider = await self._get_config_value("embedding.provider") or "openai"
        default_model = (
            await self._get_config_value("ollama.embedding_model") or "nomic-embed-text:latest"
            if provider == "ollama"
            else await self._get_config_value("embedding.model") or "text-embedding-3-large"
        )
        target_model = model or default_model

        if provider == "ollama":
            if OllamaAsyncClient is None:
                logger.error("未安装 ollama 依赖，无法调用本地嵌入模型。")
                raise HTTPException(status_code=500, detail="缺少 Ollama 依赖，请先安装 ollama 包。")

            base_url = (
                await self._get_config_value("ollama.embedding_base_url")
                or await self._get_config_value("embedding.base_url")
            )
            client = OllamaAsyncClient(host=base_url)
            try:
                response = await client.embeddings(model=target_model, prompt=text)
            except Exception as exc:  # pragma: no cover - 本地服务调用失败
                logger.error(
                    "Ollama 嵌入请求失败: model=%s base_url=%s error=%s",
                    target_model,
                    base_url,
                    exc,
                    exc_info=True,
                )
                return []
            embedding: Optional[List[float]]
            if isinstance(response, dict):
                embedding = response.get("embedding")
            else:
                embedding = getattr(response, "embedding", None)
            if not embedding:
                logger.warning("Ollama 返回空向量: model=%s", target_model)
                return []
            if not isinstance(embedding, list):
                embedding = list(embedding)
        else:
            config = self._fixed_config or await self._resolve_llm_config(user_id)
            api_key = await self._get_config_value("embedding.api_key") or config["api_key"]
            base_url = await self._get_config_value("embedding.base_url") or config.get("base_url")
            try:
                async with openai_client(api_key, base_url, read_timeout=read_timeout()) as client:
                    response = await asyncio.wait_for(client.embeddings.create(input=text, model=target_model), generation_deadline(settings.llm_deadline_seconds))
            except Exception as exc:  # pragma: no cover - 网络或鉴权失败
                logger.error(
                    "OpenAI 嵌入请求失败: model=%s base_url=%s user_id=%s error=%s",
                    target_model,
                    base_url,
                    user_id,
                    exc,
                    exc_info=True,
                )
                return []
            if not response.data:
                logger.warning("OpenAI 嵌入请求返回空数据: model=%s user_id=%s", target_model, user_id)
                return []
            embedding = response.data[0].embedding

        if not isinstance(embedding, list):
            embedding = list(embedding)

        dimension = len(embedding)
        if not dimension:
            vector_size_str = await self._get_config_value("embedding.model_vector_size")
            if vector_size_str:
                dimension = int(vector_size_str)
        if dimension:
            self._embedding_dimensions[target_model] = dimension
        if self._usage_callback:
            self._usage_callback("embedding")
        return embedding

    async def get_embedding_dimension(self, model: Optional[str] = None) -> Optional[int]:
        """获取嵌入向量维度，优先返回缓存结果，其次读取配置。"""
        provider = await self._get_config_value("embedding.provider") or "openai"
        default_model = (
            await self._get_config_value("ollama.embedding_model") or "nomic-embed-text:latest"
            if provider == "ollama"
            else await self._get_config_value("embedding.model") or "text-embedding-3-large"
        )
        target_model = model or default_model
        if target_model in self._embedding_dimensions:
            return self._embedding_dimensions[target_model]
        vector_size_str = await self._get_config_value("embedding.model_vector_size")
        return int(vector_size_str) if vector_size_str else None

    async def _enforce_daily_limit(self, user_id: int) -> str:
        limit_str = await self.admin_setting_service.get("daily_request_limit", "100")
        limit = int(limit_str or 10)
        day = date.today()
        if not await self.user_repo.reserve_daily_request(user_id, limit, day):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="今日请求次数已达上限，请明日再试或设置自定义 API Key。",
            )
        await self.session.commit()
        return day.isoformat()

    async def _get_config_value(self, key: str) -> Optional[str]:
        if self._fixed_config is not None and "_settings" in self._fixed_config:
            return self._fixed_config["_settings"].get(key)
        record = await self.system_config_repo.get_by_key(key)
        if record:
            return record.value
        # 兼容环境变量，首次迁移时无需立即写入数据库
        env_key = key.upper().replace(".", "_")
        return os.getenv(env_key)
