import re


def job_step(stage):
    batch = re.fullmatch(r"volume_(\d+)_batch_(\d+)_of_(\d+)", stage)
    if batch:
        return f"第 {batch[1]} 卷第 {batch[2]}/{batch[3]} 批大纲"
    return {"queued": "等待执行", "starting": "准备任务", "summary": "回补摘要", "context": "准备上下文",
            "plan": "章节规划", "rag": "检索上下文", "versions": "生成正文版本", "review": "比较版本", "persist": "保存正文",
            "stage_core": "核心设定", "stage_volume_plan": "分卷规划", "stage_smart_split": "智能分卷",
            "chapter_evaluate": "章节评审", "outline_extend": "大纲续写", "chapter_optimize": "正文优化",
            "novel_import": "导入分析", "import_analysis": "导入分析", "import_characters": "导入角色筛选"}.get(stage, stage)
