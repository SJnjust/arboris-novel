"""卷数据保护、兼容视图和区间编辑；不调用模型。"""
from fastapi import HTTPException
from sqlalchemy import delete, select

from ..models import ChapterOutline, NovelVolume
from ..schemas.novel import Volume


def written_numbers(project):
    return {chapter.chapter_number for chapter in project.chapters if any(version.content and version.content.strip() for version in chapter.versions)}


def cursor_for(start, end, numbers):
    cursor = start - 1
    while cursor < end and cursor + 1 in numbers:
        cursor += 1
    return cursor


def volume_for(number, volumes):
    for item in volumes:
        data = item if isinstance(item, dict) else {"volume_number": item.volume_number, "start_chapter": item.start_chapter, "end_chapter": item.end_chapter}
        if data["start_chapter"] <= number <= data["end_chapter"]:
            return data["volume_number"]
    return None


def serialize_volumes(project):
    numbers = {item.chapter_number for item in project.outlines}
    if not project.volumes:
        maximum = max([*numbers, *(chapter.chapter_number for chapter in project.chapters)], default=0)
        if not maximum:
            return []
        cursor = cursor_for(1, maximum, numbers)
        return [Volume(volume_number=1, title="第 1 卷", summary="", start_chapter=1, end_chapter=maximum,
                       outline_cursor=cursor, status="outlined" if cursor == maximum else "planned", is_virtual=True)]
    result = []
    for item in sorted(project.volumes, key=lambda row: row.volume_number):
        cursor = cursor_for(item.start_chapter, item.end_chapter, numbers)
        status = "outlining" if item.status == "outlining" else ("outlined" if cursor == item.end_chapter else "planned")
        result.append(Volume(volume_number=item.volume_number, title=item.title, summary=item.summary or "", start_chapter=item.start_chapter,
                             end_chapter=item.end_chapter, status=status, outline_cursor=cursor, plan=item.plan or {}))
    return result


def validate_ranges(volumes):
    previous = 0
    seen = set()
    for item in sorted(volumes, key=lambda row: row.start_chapter):
        if item.volume_number in seen or item.start_chapter <= previous or item.end_chapter < item.start_chapter:
            raise HTTPException(status_code=422, detail="卷区间不能重叠，起止章号须有效且卷号不能重复")
        seen.add(item.volume_number)
        previous = item.end_chapter


def protect_assignments(project, volumes):
    protected = written_numbers(project)
    real_plan = bool(project.volumes)
    existing = {item.chapter_number: item.volume_number for item in project.outlines}
    for number in protected:
        assigned = volume_for(number, volumes)
        old = existing.get(number)
        if assigned is None or real_plan and old is not None and assigned != old:
            raise HTTPException(status_code=409, detail=f"第 {number} 章已有正文，不能移出其所在卷；请保留相应区间")


class VolumeService:
    def __init__(self, session):
        self.session = session

    async def apply_plan(self, project, volumes):
        validate_ranges(volumes)
        protect_assignments(project, volumes)
        await self.session.execute(delete(NovelVolume).where(NovelVolume.project_id == project.id))
        outlines = (await self.session.scalars(select(ChapterOutline).where(ChapterOutline.project_id == project.id))).all()
        numbers = {item.chapter_number for item in outlines}
        for outline in outlines:
            outline.volume_number = volume_for(outline.chapter_number, volumes)
        for item in volumes:
            cursor = cursor_for(item.start_chapter, item.end_chapter, numbers)
            self.session.add(NovelVolume(project_id=project.id, volume_number=item.volume_number, title=item.title, summary=item.summary,
                                         start_chapter=item.start_chapter, end_chapter=item.end_chapter, plan=item.plan,
                                         outline_cursor=cursor, status="outlined" if cursor == item.end_chapter else "planned"))
        await self.session.flush()

    async def patch(self, project, number, patch):
        volumes = serialize_volumes(project)
        original = next((item for item in volumes if item.volume_number == number), None)
        if not original:
            raise HTTPException(status_code=404, detail="卷不存在")
        updated = original.model_copy(update=patch.model_dump(exclude_unset=True, exclude_none=True))
        replacement = [updated if item.volume_number == number else item for item in volumes]
        validate_ranges(replacement)
        protect_assignments(project, replacement)
        if {"start_chapter", "end_chapter"} & patch.model_fields_set:
            size = updated.end_chapter - updated.start_chapter + 1
            total = max(item.end_chapter for item in volumes)
            if size > 120 or size < (20 if total >= 20 else 1):
                raise HTTPException(status_code=422, detail="每卷须为 20–120 章；不足 20 章的短篇只保留一卷")
        await self.apply_plan(project, replacement)
        if project.blueprint:
            project.blueprint.total_chapters = max([item.end_chapter for item in replacement] + [row.chapter_number for row in project.outlines])
            project.blueprint.generation_state = None
        await self.session.commit()
