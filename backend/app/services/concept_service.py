"""灵感对话的共享流程：有界历史、重放、增量显示和最终提交。"""
import asyncio
import json
import logging
from contextlib import aclosing, asynccontextmanager

from anyio import CancelScope
from fastapi import HTTPException
from pydantic import ValidationError

from ..core.config import settings
from ..schemas.novel import ConverseResponse
from ..utils.concept_stream import MessageDeltas
from ..utils.json_utils import parse_llm_json
from .llm_service import LLMService
from .novel_service import NovelService
from .prompt_service import PromptService
from .usage_service import UsageService

logger = logging.getLogger(__name__)
_LOCKS = {}
JSON_RESPONSE_INSTRUCTION = """
IMPORTANT: 你的回复必须是合法的 JSON 对象，包含 ai_message（面向用户的字符串）、
ui_control（type/options/placeholder）、conversation_state（完整已收集信息）和 is_complete（布尔值）。
不要输出额外的文本或解释。
"""


@asynccontextmanager
async def _project_lock(project_id):
    if project_id not in _LOCKS:
        if len(_LOCKS) >= 256:
            raise HTTPException(status_code=429, detail="当前灵感对话较多，请稍后重试")
        _LOCKS[project_id] = [asyncio.Lock(), 0]
    entry = _LOCKS[project_id]
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if not entry[1]:
            _LOCKS.pop(project_id, None)


def _response(parsed):
    if not isinstance(parsed, dict):
        raise ValueError("AI 回复必须是 JSON 对象")
    parsed = dict(parsed)
    parsed.setdefault("conversation_state", {})
    if parsed.get("is_complete"):
        parsed = {**parsed, "ready_for_blueprint": True}
    return ConverseResponse(**parsed)


def _assistant(record):
    try:
        parsed = parse_llm_json(record.content, allow_lossy=False)
        return parsed.get("ai_message", ""), parsed.get("conversation_state", {})
    except (json.JSONDecodeError, AttributeError):
        text = record.content if not record.content.lstrip().startswith(("{", "[")) else "[历史回复无法解析]"
        return text, {}


class ConceptService:
    def __init__(self, session):
        self.session = session
        self.novel = NovelService(session)

    async def _run(self, project_id, user_id, request, emit):
        async with _project_lock(project_id):
            await self.novel.ensure_project_owner(project_id, user_id)
            records = await self.novel.list_conversations(project_id)
            message_id = request.client_msg_id
            user_content = json.dumps(request.user_input, ensure_ascii=False)
            user_record = None
            if message_id:
                matching = [row for row in records if (row.metadata or {}).get("client_msg_id") == message_id]
                user_record = next((row for row in matching if row.role == "user"), None)
                if user_record and user_record.content != user_content:
                    raise HTTPException(status_code=409, detail="本轮消息标识已用于其他输入，请发起新一轮")
                completed = next((row for row in matching if row.role == "assistant"), None)
                if completed:
                    response = _response(parse_llm_json(completed.content, allow_lossy=False))
                    await self.session.rollback()
                    await emit("delta", {"text": response.ai_message})
                    await emit("final", response.model_dump())
                    return
            state = request.conversation_state
            history = []
            for record in records:
                if user_record and record.id == user_record.id:
                    continue
                if record.role == "assistant":
                    text, old_state = _assistant(record)
                    state = (record.metadata or {}).get("conversation_state", old_state)
                    history.append({"role": "assistant", "content": text})
                elif record.role == "user":
                    history.append({"role": "user", "content": record.content})
            history.append({"role": "user", "content": user_content})
            prompt = await PromptService(self.session).get_prompt("concept")
            if not prompt:
                raise HTTPException(status_code=500, detail="未配置名为 concept 的提示词，请联系管理员")
            prompt += "\n" + JSON_RESPONSE_INSTRUCTION + "\n[已收集信息]\n" + json.dumps(state, ensure_ascii=False)
            if message_id and not user_record:
                await self.novel.append_conversation(project_id, "user", user_content, {"client_msg_id": message_id})
            llm = LLMService(self.session)
            config = await llm._resolve_llm_config(user_id)
            await self.session.rollback()
            completed_calls = 0
            def note_usage():
                nonlocal completed_calls
                completed_calls += 1
            llm = LLMService(self.session, config=config, record_usage=False, usage_callback=note_usage)
            decoder = MessageDeltas()
            parts = []
            try:
                async with aclosing(llm.stream_text(
                    [{"role": "system", "content": prompt}, *history[-12:]], temperature=0.8,
                    user_id=user_id, timeout=240.0, response_format="json_object",
                )) as stream:
                    async for chunk in stream:
                        parts.append(chunk)
                        delta = decoder.feed(chunk)
                        if delta:
                            await emit("delta", {"text": delta})
            finally:
                # Starlette 断连会取消整个 body scope；结算仍需完成且不写 assistant。
                with CancelScope(shield=True):
                    usage = UsageService(self.session)
                    if completed_calls:
                        await usage.increment("api_request_count", completed_calls, quota=config.get("_quota"))
                    elif config.get("_quota"):
                        await usage.refund_reservation(config["_quota"])
            try:
                parsed = parse_llm_json("".join(parts), allow_lossy=False)
                response = _response(parsed)
                if response.ai_message.startswith(decoder.text):
                    remaining = response.ai_message[len(decoder.text):]
                    if remaining:
                        await emit("delta", {"text": remaining})
                else:
                    raise ValueError("增量回复与完整回复不一致")
            except (json.JSONDecodeError, ValidationError, ValueError, AttributeError) as error:
                raise HTTPException(status_code=500, detail="概念对话失败，AI 返回的内容格式不正确。请重试或联系管理员。") from error
            metadata = {"client_msg_id": message_id, "conversation_state": response.conversation_state}
            # 没带消息 ID 的旧同步调用保留失败时不写 user 的行为。
            if not message_id:
                await self.novel.append_conversation(project_id, "user", user_content)
            await self.novel.append_conversation(project_id, "assistant", json.dumps(parsed, ensure_ascii=False), metadata)
            await emit("final", response.model_dump())

    async def events(self, project_id, user_id, request):
        queue = asyncio.Queue(maxsize=16)

        async def emit(kind, data):
            await queue.put({"event": kind, "data": data})

        async def produce():
            try:
                await self._run(project_id, user_id, request, emit)
            except Exception as error:
                logger.exception("灵感对话失败: project=%s", project_id)
                code = error.status_code if isinstance(error, HTTPException) else 500
                detail = error.detail if isinstance(error, HTTPException) else "灵感对话失败，请重试本轮"
                await queue.put({"event": "error", "data": {"detail": detail, "retryable": code >= 500 or code == 429}, "status_code": code})

        task = asyncio.create_task(produce(), name=f"concept:{project_id}")
        loop = asyncio.get_running_loop()
        next_ping = loop.time() + settings.concept_ping_seconds
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), max(0, next_ping - loop.time()))
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": {}}
                    next_ping = loop.time() + settings.concept_ping_seconds
                    continue
                yield item
                if item["event"] in ("final", "error"):
                    return
        finally:
            with CancelScope(shield=True):
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await self.session.rollback()

    async def converse(self, project_id, user_id, request):
        async with aclosing(self.events(project_id, user_id, request)) as events:
            async for item in events:
                if item["event"] == "error":
                    raise HTTPException(status_code=item["status_code"], detail=item["data"]["detail"])
                if item["event"] == "final":
                    return ConverseResponse(**item["data"])
