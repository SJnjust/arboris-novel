"""卷规划与批次校验；绝不从不完整 JSON 猜测业务数据。"""
import json
import math


def integer(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("章号与章数必须是整数")
    return int(value)


def normalize_volume_plan(data, total_chapters):
    total = integer(total_chapters)
    if not 1 <= total <= 10000:
        raise ValueError("总章数必须在 1–10000 之间")
    raw = data.get("volumes") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw or any(not isinstance(item, dict) for item in raw):
        raise ValueError("卷规划必须包含非空 volumes 数组")
    if len(raw) < math.ceil(total / 120):
        raise ValueError("卷规划数量不足：每卷最多 120 章，请重新规划")
    count = min(len(raw), max(1, total // 20))
    groups = [raw[index * len(raw) // count:(index + 1) * len(raw) // count] for index in range(count)]
    prepared = []
    for index, group in enumerate(groups):
        if any(not isinstance(item.get("title"), str) or not item["title"].strip() or not isinstance(item.get("summary"), str) or not item["summary"].strip() for item in group):
            raise ValueError("每卷必须提供卷名与梗概")
        plan = {key: "\n".join(str(item[key]) for item in group if item.get(key)) for key in (
            "main_plot", "core_conflict", "climax", "ending_hook", "foreshadowing",
        )}
        summary = "\n".join(item["summary"] for item in group)
        if len(summary) > 10000 or len(group[0]["title"]) > 255:
            raise ValueError("卷名或合并后的卷梗概过长，请重新规划")
        prepared.append({"volume_number": index + 1, "title": group[0]["title"],
                         "summary": summary, "plan": plan,
                         "start_chapter": group[0].get("start_chapter"), "end_chapter": group[-1].get("end_chapter")})
    cursor = 1
    valid = count == len(raw)
    for item in prepared:
        try:
            start, end = integer(item["start_chapter"]), integer(item["end_chapter"])
            valid = valid and start == cursor and end >= start and (20 <= end - start + 1 <= 120 or total < 20)
            cursor = end + 1
        except (ValueError, TypeError):
            valid = False
    valid = valid and cursor == total + 1
    cursor = 1
    for index, item in enumerate(prepared):
        size = total // count + int(index < total % count)
        if not valid:
            item["start_chapter"], item["end_chapter"] = cursor, cursor + size - 1
        else:
            item["start_chapter"], item["end_chapter"] = integer(item["start_chapter"]), integer(item["end_chapter"])
        item["outline_cursor"] = item["start_chapter"] - 1
        cursor = item["end_chapter"] + 1
    return prepared


def normalize_outline_batch(data, start, end):
    if end < start or end - start + 1 > 15:
        raise ValueError("大纲批次必须为连续的 1–15 章")
    raw = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise ValueError("大纲响应缺少 chapters 数组")
    result = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("大纲条目必须是对象")
        number = integer(item.get("chapter_number"))
        if number < start or number > end or number in result:
            continue
        if not isinstance(item.get("title"), str) or not item["title"].strip() or not isinstance(item.get("summary"), str) or not item["summary"].strip():
            raise ValueError(f"第 {number} 章缺少完整标题或摘要")
        result[number] = {"chapter_number": number, "title": item["title"][:255], "summary": item["summary"]}
    missing = [str(number) for number in range(start, end + 1) if number not in result]
    if missing:
        raise ValueError("大纲批次不完整，缺少章号：" + ",".join(missing))
    return [result[number] for number in range(start, end + 1)]


def blueprint_summary(blueprint):
    data = {key: blueprint.get(key) for key in ("title", "genre", "style", "tone", "one_sentence_summary", "full_synopsis", "world_setting", "characters")}
    return json.dumps(data, ensure_ascii=False)[:2000]


def outline_context(blueprint, volume, previous, recent, start, end):
    return "\n\n".join((
        "[蓝图摘要]\n" + blueprint_summary(blueprint),
        "[本卷计划]\n" + json.dumps(volume, ensure_ascii=False)[:6000],
        "[上一卷摘要]\n" + (previous or "无")[:2000],
        "[最近10条大纲]\n" + json.dumps([{ "chapter_number": item.chapter_number, "title": item.title[:200], "summary": (item.summary or "")[:600]} for item in recent[-10:]], ensure_ascii=False),
        f"[本批任务]\n请从第 {start} 章开始，生成至第 {end} 章，逐章完整返回。\n" + json.dumps({"start_chapter": start, "end_chapter": end}, ensure_ascii=False),
    ))
