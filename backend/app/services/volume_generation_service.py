"""蓝图与卷大纲任务；每个完整批次和 cursor 在同一事务中提交。"""
import json
import math

from fastapi import HTTPException
from sqlalchemy import select, update

from ..core.config import settings
from ..models import ChapterOutline, NovelBlueprint, NovelVolume
from ..schemas.novel import Blueprint, Volume
from ..utils.json_utils import parse_llm_json
from .llm_service import LLMService
from .novel_service import NovelService
from .prompt_service import PromptService
from .usage_service import UsageService
from .volume_planning import blueprint_summary, integer, normalize_outline_batch, normalize_volume_plan, outline_context, validate_core_lengths
from .volume_service import VolumeService, cursor_for, serialize_volumes, written_numbers


class PlanningRun:
    def __init__(self, context):
        self.context = context
        self.config = None
        self.prompts = {}
        self.usage = 0

    def note_usage(self):
        self.usage += 1

    async def call(self, name, content):
        ctx = self.context
        ctx.check_cancelled()
        if self.config is None:
            async with ctx.session_factory() as session:
                self.config = dict(await LLMService(session)._resolve_llm_config(ctx.user_id))
                service = PromptService(session)
                self.prompts = {key: await service.get_prompt(key) for key in ("screenwriting", "volume_planning", "volume_outline")}
        prompt = self.prompts.get(name)
        if not prompt:
            raise HTTPException(status_code=500, detail=f"缺少 {name} 提示词，请联系管理员配置")
        if name == "screenwriting":
            prompt += "\n[R3 输出契约] 只输出核心设定与 target_chapter_range（整数 min/max），不要输出 chapter_outline 或 volumes；标题≤80字，一句话梗概≤200字，full_synopsis≤2000字，核心角色≤12名、关系≤24条。"
        if name == "volume_planning":
            prompt += "\n[输出长度约束] 卷名≤60字、梗概≤240字、main_plot/core_conflict/climax/ending_hook/foreshadowing各≤160字；超过6卷时分别收紧为40/120/80字。用精练因果句，禁止扩写正文或逐章大纲。"
        elif name == "volume_outline":
            prompt += f"\n[本批输出契约] 仅生成本批任务闭区间，单批最多{settings.outline_batch_size}章；标题≤80字，summary≤300字，必须完整返回全部章号。"
        async with ctx.session_factory() as session:
            raw = await LLMService(session, config=self.config, record_usage=False, usage_callback=self.note_usage).get_llm_response(
                system_prompt=prompt, conversation_history=[{"role": "user", "content": content}],
                user_id=ctx.user_id, temperature=0.3, timeout=settings.llm_job_deadline_seconds, structured=True,
            )
        try:
            result = parse_llm_json(raw, allow_lossy=False)
            if not isinstance(result, dict):
                raise ValueError("需要 JSON 对象")
            return result
        except (ValueError, TypeError) as error:
            raise HTTPException(status_code=500, detail="AI 返回的内容格式不正确（输出不完整或结构无效），请重试任务") from error

    async def settle(self):
        if self.config is None:
            return
        async with self.context.session_factory() as session:
            usage = UsageService(session)
            quota = self.config.get("_quota")
            if self.usage:
                await usage.increment("api_request_count", self.usage, quota=quota)
            elif quota:
                await usage.refund_reservation(quota)

    async def core(self):
        ctx = self.context
        async with ctx.session_factory() as session:
            novel = NovelService(session)
            project = await novel.ensure_project_owner(ctx.project_id, ctx.user_id)
            saved = project.blueprint.generation_state or {}
            if ctx.payload.get("resume", True) and saved.get("core"):
                return saved
            if not ctx.payload.get("resume", True) and saved:
                project.blueprint.generation_state = None
                await session.commit()
            records = project.conversations
            if not records:
                raise HTTPException(status_code=400, detail="缺少对话历史，请先完成概念对话后再生成蓝图")
            state = {}
            history = []
            for item in records:
                try:
                    data = json.loads(item.content)
                except (json.JSONDecodeError, TypeError):
                    continue
                if item.role == "assistant" and isinstance(data, dict):
                    state = (item.metadata or {}).get("conversation_state", data.get("conversation_state", {}))
                    history.append({"role": "assistant", "content": str(data.get("ai_message", ""))[:1000]})
                elif item.role == "user":
                    history.append({"role": "user", "content": str(data.get("value", data) if isinstance(data, dict) else data)[:1000]})
            if not history:
                raise HTTPException(status_code=400, detail="无法从历史对话中提取有效内容，请检查对话历史格式或重新进行概念对话")
        await ctx.advance("stage_core", 5)
        raw = await self.call("screenwriting", "[已收集信息]\n" + json.dumps(state, ensure_ascii=False)[:6000] + "\n[最近对话]\n" + json.dumps(history[-12:], ensure_ascii=False))
        try:
            validate_core_lengths(raw)
            target = raw.get("target_chapter_range")
            if not isinstance(target, dict):
                raise ValueError("缺少目标章数范围")
            low, high = integer(target.get("min")), integer(target.get("max"))
            if not 1 <= low <= high <= 10000:
                raise ValueError("目标章数范围须在 1–10000 内")
            core = Blueprint(**{**raw, "chapter_outline": [], "volumes": [], "total_chapters": 0}).model_dump()
        except (ValueError, TypeError) as error:
            raise HTTPException(status_code=500, detail="核心蓝图缺少完整设定或有效目标章数范围，请重试") from error
        saved = {"core": core, "target_range": {"min": low, "max": high}, "stage": "core"}
        async with ctx.session_factory() as session:
            await NovelService(session).ensure_project_owner(ctx.project_id, ctx.user_id)
            record = await session.get(NovelBlueprint, ctx.project_id)
            record.generation_state = saved
            await session.commit()
        return saved

    async def blueprint(self):
        ctx = self.context
        saved = await self.core()
        if saved.get("stage") != "plan":
            await ctx.advance("stage_volume_plan", 25)
            async with ctx.session_factory() as session:
                project = await NovelService(session).ensure_project_owner(ctx.project_id, ctx.user_id)
                protected = written_numbers(project)
                constraints = [{"volume_number": row.volume_number, "written_min": min(numbers), "written_max": max(numbers)}
                               for row in project.volumes if (numbers := [item.chapter_number for item in project.outlines if item.chapter_number in protected and item.volume_number == row.volume_number])]
            raw = await self.call("volume_planning", "[核心设定]\n" + blueprint_summary(saved["core"]) + "\n[目标章数范围]\n" + json.dumps(saved["target_range"]) + "\n[已有正文的卷边界约束]\n" + json.dumps(constraints, ensure_ascii=False))
            try:
                total = integer(raw.get("total_chapters"))
                if not saved["target_range"]["min"] <= total <= saved["target_range"]["max"]:
                    raise ValueError("总章数超出核心设定的目标范围")
                volumes = [Volume(**item) for item in normalize_volume_plan(raw, total)]
            except (ValueError, TypeError) as error:
                raise HTTPException(status_code=500, detail=f"卷规划不完整或区间无效：{error}") from error
            saved = {**saved, "stage": "plan"}
            async with ctx.session_factory() as session:
                novel = NovelService(session)
                project = await novel.ensure_project_owner(ctx.project_id, ctx.user_id)
                if max(written_numbers(project), default=0) > total:
                    raise HTTPException(status_code=409, detail="新规划总章数小于已有正文的最大章号，请保留已有章节范围")
                await novel.replace_blueprint(ctx.project_id, Blueprint(**{**saved["core"], "volumes": volumes, "total_chapters": total}), checkpoint=saved)
        await self.outline([1], base=40)
        return {"project_id": ctx.project_id, "first_volume": 1}

    async def outline(self, numbers=None, base=5):
        ctx = self.context
        async with ctx.session_factory() as session:
            project = await NovelService(session).ensure_project_owner(ctx.project_id, ctx.user_id)
            if not project.volumes:
                legacy = serialize_volumes(project)
                if not legacy:
                    raise HTTPException(status_code=400, detail="尚无卷规划，请先生成蓝图")
                await VolumeService(session).apply_plan(project, legacy)
                await session.commit()
                project = await NovelService(session).ensure_project_owner(ctx.project_id, ctx.user_id)
            selected = [row for row in serialize_volumes(project) if numbers is None or row.volume_number in numbers]
            if numbers and len(selected) != len(numbers):
                raise HTTPException(status_code=404, detail="卷不存在")
            total_batches = sum(math.ceil((row.end_chapter - row.outline_cursor) / settings.outline_batch_size) for row in selected)
        done = 0
        for volume in selected:
            while True:
                ctx.check_cancelled()
                async with ctx.session_factory() as session:
                    novel = NovelService(session)
                    project = await novel.ensure_project_owner(ctx.project_id, ctx.user_id)
                    row = next(item for item in project.volumes if item.volume_number == volume.volume_number)
                    cursor = cursor_for(row.start_chapter, row.end_chapter, {item.chapter_number for item in project.outlines})
                    row.outline_cursor = cursor
                    if cursor >= row.end_chapter:
                        row.status = "outlined"
                        await session.commit()
                        break
                    row.status = "outlining"
                    start, end = cursor + 1, min(cursor + settings.outline_batch_size, row.end_chapter)
                    current = Volume(volume_number=row.volume_number, title=row.title, summary=row.summary or "", start_chapter=row.start_chapter,
                                     end_chapter=row.end_chapter, plan=row.plan or {})
                    recent = sorted((item for item in project.outlines if item.chapter_number < start), key=lambda item: item.chapter_number)[-10:]
                    previous = next((item.summary for item in project.volumes if item.volume_number == row.volume_number - 1), "")
                    content = outline_context(novel._build_blueprint_schema(project).model_dump(), current.model_dump(), previous, recent, start, end)
                    await session.commit()
                batch = (start - current.start_chapter) // settings.outline_batch_size + 1
                batches = math.ceil((current.end_chapter - current.start_chapter + 1) / settings.outline_batch_size)
                await ctx.advance(f"volume_{current.volume_number}_batch_{batch}_of_{batches}", base + int((94 - base) * done / max(1, total_batches)))
                raw = await self.call("volume_outline", content)
                try:
                    outlines = normalize_outline_batch(raw, start, end)
                except (ValueError, TypeError) as error:
                    raise HTTPException(status_code=500, detail=f"卷大纲批次无效，已保留之前进度：{error}") from error
                async with ctx.session_factory() as session:
                    novel = NovelService(session)
                    project = await novel.ensure_project_owner(ctx.project_id, ctx.user_id)
                    protected = written_numbers(project)
                    for item in outlines:
                        if item["chapter_number"] not in protected:
                            await novel.update_or_create_outline(ctx.project_id, **item, volume_number=current.volume_number)
                    row = await session.get(NovelVolume, (ctx.project_id, current.volume_number))
                    row.outline_cursor = end
                    row.status = "outlined" if end == row.end_chapter else "outlining"
                    ctx.check_cancelled()
                    await session.commit()
                done += 1
        return {"project_id": ctx.project_id, "completed_volumes": [row.volume_number for row in selected]}

    async def smart_split(self):
        ctx = self.context
        async with ctx.session_factory() as session:
            project = await NovelService(session).ensure_project_owner(ctx.project_id, ctx.user_id)
            outlines = sorted(project.outlines, key=lambda item: item.chapter_number)
            if not outlines:
                raise HTTPException(status_code=400, detail="没有已有大纲可供分卷")
            total = max(item.chapter_number for item in outlines)
            step = max(1, math.ceil(len(outlines) / 100))
            samples = [{"start_chapter": group[0].chapter_number, "end_chapter": group[-1].chapter_number,
                        "titles": [group[0].title[:100], group[-1].title[:100]],
                        "summaries": [(group[0].summary or "")[:150], (group[-1].summary or "")[:150]]}
                       for index in range(0, len(outlines), step) if (group := outlines[index:index + step])]
        await ctx.advance("stage_smart_split", 10)
        raw = await self.call("volume_planning", "只根据已有大纲标题摘要划分卷，不重写大纲或正文。\n[总章数]\n" + str(total) + "\n[已有大纲目录]\n" + json.dumps(samples, ensure_ascii=False))
        try:
            volumes = [Volume(**item) for item in normalize_volume_plan(raw, total)]
        except (ValueError, TypeError) as error:
            raise HTTPException(status_code=500, detail=f"智能分卷区间无效：{error}") from error
        async with ctx.session_factory() as session:
            project = await NovelService(session).ensure_project_owner(ctx.project_id, ctx.user_id)
            await VolumeService(session).apply_plan(project, volumes)
            project.blueprint.total_chapters = total
            project.blueprint.generation_state = None
            ctx.check_cancelled()
            await session.commit()
        return {"project_id": ctx.project_id, "volume_count": len(volumes)}


class VolumeGenerationService:
    def __init__(self, kind):
        self.kind = kind

    async def run(self, context):
        run = PlanningRun(context)
        try:
            if self.kind == "blueprint_generate":
                return await run.blueprint()
            if self.kind == "volume_smart_split":
                return await run.smart_split()
            return await run.outline([context.payload["volume_number"]] if self.kind == "volume_outline" else None)
        finally:
            await run.settle()

    async def cleanup(self, context, status):
        async with context.session_factory() as session:
            await session.execute(update(NovelVolume).where(NovelVolume.project_id == context.project_id, NovelVolume.status == "outlining").values(status="planned"))
            await session.commit()
