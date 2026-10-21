"""章节生成任务：网络等待与短数据库事务分离。"""
import asyncio
import json
import logging
import os
from typing import Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import HTTPException
from sqlalchemy import exists, select, update

from ..core.config import settings
from ..models import Chapter, ChapterVersion
from ..repositories.system_config_repository import SystemConfigRepository
from ..utils.json_utils import looks_like_json, parse_llm_json, remove_think_tags
from .ai_review_service import AIReviewService
from .chapter_context_service import ChapterContextService
from .chapter_guardrails import ChapterGuardrails
from .llm_service import LLMService
from .novel_service import NovelService
from .prompt_service import PromptService
from .usage_service import UsageService
from .vector_store_service import VectorStoreService
from .writer_context_builder import WriterContextBuilder

logger = logging.getLogger(__name__)


class FrozenPrompts:
    def __init__(self, values):
        self.values = values

    async def get_prompt(self, name):
        return self.values.get(name)


def _extract_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("content", "chapter_content", "chapter_text", "text", "body", "story"):
            text = _extract_text(value.get(key))
            if text:
                return text
    if isinstance(value, list):
        for item in value:
            text = _extract_text(item)
            if text:
                return text
    return None


def _failure(index, error):
    return {
        "index": index,
        "status_code": error.status_code if isinstance(error, HTTPException) else 500,
        "detail": error.detail if isinstance(error, HTTPException) else "该版本生成失败，请重试",
    }


class ChapterGenerationService:
    async def _load(self, context):
        async with context.session_factory() as session:
            novel = NovelService(session)
            project = await novel.ensure_project_owner(context.project_id, context.user_id)
            number = context.payload["chapter_number"]
            outline = await novel.get_outline(context.project_id, number)
            if not outline:
                raise HTTPException(status_code=404, detail="蓝图中未找到对应章节纲要")
            previous = [
                {"id": item.id, "number": item.chapter_number,
                 "version_id": item.selected_version_id, "content": item.selected_version.content,
                 "summary": item.real_summary or ""}
                for item in sorted(project.chapters, key=lambda row: row.chapter_number)
                if item.chapter_number < number and item.selected_version and item.selected_version.content
            ]
            blueprint = (await novel._serialize_project(project)).blueprint.model_dump()
            for relation in blueprint.get("relationships", []):
                relation["from"] = relation.pop("character_from", relation.get("from"))
                relation["to"] = relation.pop("character_to", relation.get("to"))
            data = {"blueprint": blueprint, "previous": previous, "title": outline.title or f"第{number}章", "summary": outline.summary or "暂无摘要"}
            chapter = await novel.get_or_create_chapter(context.project_id, number)
            chapter.status = "generating"
            chapter.real_summary = None
            chapter.selected_version_id = None
            await session.commit()

            # 配置只解析一次；之后每个分支的 LLMService 使用这个不可持久化的快照。
            llm = LLMService(session)
            data["config"] = dict(await llm._resolve_llm_config(context.user_id))
            context.quota = data["config"].get("_quota")
            if settings.vector_store_enabled:
                keys = ("embedding.provider", "embedding.model", "embedding.api_key", "embedding.base_url",
                        "embedding.model_vector_size", "ollama.embedding_model", "ollama.embedding_base_url")
                data["config"]["_settings"] = {key: await llm._get_config_value(key) for key in keys}
            prompts = PromptService(session)
            names = ("chapter_plan", "writing_v2", "writing", "extraction", "rewrite_guardrails", "editor_review")
            data["prompts"] = FrozenPrompts({name: await prompts.get_prompt(name) for name in names})
            data["count"] = await _resolve_version_count(session)
        return data

    def _llm(self, session, data, note_usage):
        return LLMService(session, config=data["config"], record_usage=False, usage_callback=note_usage)

    async def _backfill(self, context, data, note_usage):
        limit = settings.chapter_summary_backfill_limit
        recent = data["previous"][-limit:] if limit else []
        missing = [item for item in recent if not item["summary"]]
        semaphore = asyncio.Semaphore(3)

        async def summarize(item):
            async with semaphore:
                context.check_cancelled()
                # 此会话没有执行 SQL，在网络等待时没有数据库事务/连接。
                async with context.session_factory() as session:
                    llm = self._llm(session, data, note_usage)
                    summary = await llm.get_summary(item["content"], user_id=context.user_id,
                                                    system_prompt=data["prompts"].values.get("extraction"))
                summary = remove_think_tags(summary)
                async with context.session_factory() as session:
                    result = await session.execute(update(Chapter).where(
                        Chapter.id == item["id"], Chapter.selected_version_id == item["version_id"],
                        exists(select(ChapterVersion.id).where(ChapterVersion.id == item["version_id"], ChapterVersion.content == item["content"])),
                    ).values(real_summary=summary))
                    await session.commit()
                    if result.rowcount:
                        item["summary"] = summary

        outcomes = await asyncio.gather(*(summarize(item) for item in missing), return_exceptions=True)
        for result in outcomes:
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                logger.warning("部分历史摘要回补失败: job=%s", context.id, exc_info=result)
        return len([value for value in outcomes if isinstance(value, Exception)])

    async def _prepare(self, context, data, note_usage):
        previous = data["previous"]
        previous_summary = previous[-1]["summary"] if previous else ""
        previous_tail = _extract_tail_excerpt(previous[-1]["content"]) if previous else ""
        notes = context.payload.get("writing_notes") or "无额外写作指令"
        names = [item.get("name") for item in data["blueprint"].get("characters", []) if item.get("name")]
        await context.advance("mission", 25)
        async with context.session_factory() as session:
            llm = self._llm(session, data, note_usage)
            mission = await _generate_chapter_mission(
                llm, data["prompts"], data["blueprint"], previous_summary, previous_tail,
                data["title"], data["summary"], notes, [], names, context.user_id,
            )
        allowed = mission.get("allowed_new_characters", []) if mission else []
        visibility = WriterContextBuilder().build_visibility_context(
            blueprint=data["blueprint"], completed_summaries=[item["summary"] for item in previous],
            previous_tail=previous_tail, outline_title=data["title"], outline_summary=data["summary"],
            writing_notes=notes, allowed_new_characters=allowed,
        )
        await context.advance("rag", 35)
        vector_store = None
        if settings.vector_store_enabled:
            try:
                vector_store = VectorStoreService()
            except RuntimeError:
                logger.warning("向量库不可用，跳过本次检索: job=%s", context.id)
        async with context.session_factory() as session:
            rag = await ChapterContextService(llm_service=self._llm(session, data, note_usage), vector_store=vector_store).retrieve_for_generation(
                project_id=context.project_id, query_text="\n".join((data["title"], data["summary"], notes)), user_id=context.user_id,
            )
        prompt = data["prompts"].values.get("writing_v2") or data["prompts"].values.get("writing")
        if not prompt:
            raise HTTPException(status_code=500, detail="缺少写作提示词，请联系管理员配置")
        sections = [
            ("[世界蓝图](JSON，已裁剪)", json.dumps(visibility["writer_blueprint"], ensure_ascii=False, indent=2)),
            ("[上一章摘要]", previous_summary or "暂无（这是第一章）"),
            ("[上一章结尾]", previous_tail or "暂无（这是第一章）"),
            ("[章节导演脚本](JSON)", json.dumps(mission, ensure_ascii=False, indent=2) if mission else "无导演脚本"),
            ("[检索到的剧情上下文](Markdown)", "\n\n".join(rag.chunk_texts()) if rag.chunks else "未检索到章节片段"),
            ("[检索到的章节摘要](Markdown)", "\n".join(rag.summary_lines()) if rag.summaries else "未检索到章节摘要"),
            ("[当前章节目标]", f"标题：{data['title']}\n摘要：{data['summary']}\n写作要求：{notes}"),
            ("[禁止角色](本章不允许提及)", json.dumps(visibility["forbidden_characters"], ensure_ascii=False)),
        ]
        return {"mission": mission, "allowed": allowed, "forbidden": visibility["forbidden_characters"],
                "prompt": prompt, "input": "\n\n".join(f"{title}\n{text}" for title, text in sections)}

    async def _version(self, context, data, prepared, index, note_usage):
        hints = ["情绪更细腻，节奏更慢，多写内心戏和感官描写", "冲突更强，节奏更快，多写动作和对话", "悬念更重，多埋伏笔，结尾钩子更强"]
        prompt_input = prepared["input"]
        if index < len(hints):
            prompt_input += f"\n\n[版本风格提示]\n{hints[index]}"
        async with context.session_factory() as session:
            llm = self._llm(session, data, note_usage)
            response = await llm.get_llm_response(system_prompt=prepared["prompt"],
                conversation_history=[{"role": "user", "content": prompt_input}],
                temperature=0.9, user_id=context.user_id, timeout=600, response_format=None)
            content = remove_think_tags(response)
            guardrails = ChapterGuardrails()
            guard = guardrails.check(generated_text=content, forbidden_characters=prepared["forbidden"],
                                    allowed_new_characters=prepared["allowed"], pov=(prepared["mission"] or {}).get("pov"))
            violations = [{"type": item.type, "severity": item.severity, "description": item.description} for item in guard.violations]
            rewritten = False
            if not guard.passed:
                revised = await _rewrite_with_guardrails(llm, data["prompts"], content, prepared["mission"], guardrails.format_violations_for_rewrite(guard), context.user_id)
                rewritten = revised != content
                content = revised
        parsed = None
        try:
            parsed = parse_llm_json(content, allow_lossy=False)
        except json.JSONDecodeError as error:
            if looks_like_json(content):
                raise HTTPException(status_code=500, detail="AI 输出不完整或格式不正确，请重试") from error
        text = _extract_text(parsed) or content
        if not text or not text.strip():
            raise HTTPException(status_code=500, detail="AI 未返回有效章节内容")
        return {"content": text, "parsed_json": parsed, "chapter_mission": prepared["mission"],
                "guardrail": {"passed": guard.passed, "violations": violations, "rewritten": rewritten}, "requested_version_index": index}

    async def _versions(self, context, data, prepared, note_usage):
        semaphore = asyncio.Semaphore(2)
        completed = 0

        async def generate(index):
            nonlocal completed
            async with semaphore:
                context.check_cancelled()
                try:
                    return await self._version(context, data, prepared, index, note_usage), None
                except Exception as error:
                    logger.warning("章节版本失败: job=%s index=%s", context.id, index, exc_info=error)
                    return None, _failure(index, error)
                finally:
                    completed += 1
                    await context.advance("versions", 45 + int(35 * completed / data["count"]))

        await context.advance("versions", 45)
        indices = iter(range(data["count"]))
        outcomes = []

        async def worker():
            for index in indices:
                outcomes.append((index, await generate(index)))

        # 固定两个 worker，配置较大的版本数时也不预先分配 N 个等待协程。
        await asyncio.gather(*(worker() for _ in range(min(2, data["count"]))))
        outcomes = [value for _, value in sorted(outcomes, key=lambda item: item[0])]
        return [value for value, _ in outcomes if value], [error for _, error in outcomes if error]

    async def run(self, context):
        usage = 0
        data = None

        def note_usage(kind="text"):
            nonlocal usage
            usage += 1

        try:
            data = await self._load(context)
            await context.advance("backfill_summaries", 5)
            backfill_failures = await self._backfill(context, data, note_usage)
            prepared = await self._prepare(context, data, note_usage)
            versions, failures = await self._versions(context, data, prepared, note_usage)
            if not versions:
                code = 504 if any(item["status_code"] == 504 for item in failures) else failures[0]["status_code"]
                raise HTTPException(status_code=code, detail={"message": "所有章节版本生成失败，请重试", "failed_versions": failures})
            await context.advance("review", 85)
            if len(versions) > 1:
                async with context.session_factory() as session:
                    review = await AIReviewService(self._llm(session, data, note_usage), data["prompts"]).review_versions(
                        [item["content"] for item in versions], prepared["mission"], context.user_id)
                if review:
                    for index, version in enumerate(versions):
                        version["ai_review"] = {"is_best": index == review.best_version_index, "scores": review.scores,
                                                "evaluation": review.overall_evaluation if index == review.best_version_index else None,
                                                "flaws": review.critical_flaws if index == review.best_version_index else None,
                                                "suggestions": review.refinement_suggestions if index == review.best_version_index else None}
            for version in versions:
                version["failed_versions"] = failures
            await context.advance("persist", 95)
            async with context.session_factory() as session:
                novel = NovelService(session)
                await novel.ensure_project_owner(context.project_id, context.user_id)
                chapter = await novel.get_or_create_chapter(context.project_id, context.payload["chapter_number"])
                context.check_cancelled()
                await novel.replace_chapter_versions(chapter, [item["content"] for item in versions], versions)
            return {"project_id": context.project_id, "chapter_number": context.payload["chapter_number"],
                    "version_count": len(versions), "failed_versions": failures, "backfill_failures": backfill_failures}
        finally:
            quota = getattr(context, "quota", None)
            if usage or quota:
                try:
                    async with context.session_factory() as session:
                        service = UsageService(session)
                        if usage:
                            await service.increment("api_request_count", usage, quota=quota)
                        else:
                            await service.refund_reservation(quota)
                except Exception:
                    logger.exception("任务用量记录失败: job=%s", context.id)

    async def cleanup(self, context, status):
        async with context.session_factory() as session:
            await session.execute(update(Chapter).where(
                Chapter.project_id == context.project_id,
                Chapter.chapter_number == context.payload["chapter_number"],
                Chapter.status.in_(("generating", "evaluating", "selecting")),
            ).values(status="failed"))
            await session.commit()


def _extract_tail_excerpt(text: Optional[str], limit: int = 500) -> str:
    """截取章节结尾文本，默认保留 500 字。"""
    if not text:
        return ""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[-limit:]


async def _resolve_version_count(session: AsyncSession) -> int:
    """
    解析章节版本数量配置，优先级：
    1) SystemConfig: writer.chapter_versions
    2) SystemConfig: writer.version_count（兼容旧键）
    3) ENV: WRITER_CHAPTER_VERSION_COUNT / WRITER_CHAPTER_VERSIONS（与 config.py 对齐）
    4) ENV: WRITER_VERSION_COUNT（兼容旧）
    5) settings.writer_chapter_versions（默认=2）
    """
    repo = SystemConfigRepository(session)
    # 1) 新键优先，兼容旧键
    for key in ("writer.chapter_versions", "writer.version_count"):
        record = await repo.get_by_key(key)
        if record and record.value:
            try:
                val = int(record.value)
                if val >= 1:
                    return val
            except ValueError:
                pass
    # 2) 环境变量（与 Settings 对齐）
    for env in ("WRITER_CHAPTER_VERSION_COUNT", "WRITER_CHAPTER_VERSIONS", "WRITER_VERSION_COUNT"):
        v = os.getenv(env)
        if v:
            try:
                val = int(v)
                if val >= 1:
                    return val
            except ValueError:
                pass
    # 3) 默认值
    return int(settings.writer_chapter_versions)


async def _generate_chapter_mission(
    llm_service: LLMService,
    prompt_service: PromptService,
    blueprint_dict: dict,
    previous_summary: str,
    previous_tail: str,
    outline_title: str,
    outline_summary: str,
    writing_notes: str,
    introduced_characters: List[str],
    all_characters: List[str],
    user_id: int,
) -> Optional[dict]:
    """
    L2 Director: 生成章节导演脚本（ChapterMission）
    """
    plan_prompt = await prompt_service.get_prompt("chapter_plan")
    if not plan_prompt:
        logger.warning("未配置 chapter_plan 提示词，跳过导演脚本生成")
        return None

    plan_input = f"""
[上一章摘要]
{previous_summary or "暂无（这是第一章）"}

[上一章结尾]
{previous_tail or "暂无（这是第一章）"}

[当前章节大纲]
标题：{outline_title}
摘要：{outline_summary}

[已登场角色]
{json.dumps(introduced_characters, ensure_ascii=False) if introduced_characters else "暂无"}

[全部角色]
{json.dumps(all_characters, ensure_ascii=False)}

[写作指令]
{writing_notes or "无额外指令"}
"""

    try:
        response = await llm_service.get_llm_response(
            system_prompt=plan_prompt,
            conversation_history=[{"role": "user", "content": plan_input}],
            temperature=0.3,
            user_id=user_id,
            timeout=120.0,
        )
        mission = parse_llm_json(response, allow_lossy=False)
        logger.info("成功生成章节导演脚本: macro_beat=%s", mission.get("macro_beat"))
        return mission
    except Exception as exc:
        logger.warning("生成章节导演脚本失败，将使用默认模式: %s", exc)
        return None


async def _rewrite_with_guardrails(
    llm_service: LLMService,
    prompt_service: PromptService,
    original_text: str,
    chapter_mission: Optional[dict],
    violations_text: str,
    user_id: int,
) -> str:
    """
    使用护栏修复提示词重写违规内容
    """
    rewrite_prompt = await prompt_service.get_prompt("rewrite_guardrails")
    if not rewrite_prompt:
        logger.warning("未配置 rewrite_guardrails 提示词，跳过自动修复")
        return original_text


    rewrite_input = f"""
[原文]
{original_text}

[章节导演脚本]
{json.dumps(chapter_mission, ensure_ascii=False, indent=2) if chapter_mission else "无"}

[违规列表]
{violations_text}
"""

    try:
        response = await llm_service.get_llm_response(
            system_prompt=rewrite_prompt,
            conversation_history=[{"role": "user", "content": rewrite_input}],
            temperature=0.3,
            user_id=user_id,
            timeout=300.0,
            response_format=None,
        )
        cleaned = remove_think_tags(response)
        logger.info("成功修复违规内容")
        return cleaned
    except Exception as exc:
        logger.warning("自动修复失败，返回原文: %s", exc)
        return original_text

