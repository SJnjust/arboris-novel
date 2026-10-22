import type { Blueprint, Chapter, ChapterOutline, NovelVolume } from '@/api/novel'

export const nextOutlineNumber = (outlines: ChapterOutline[] = []) => Math.max(0, ...outlines.map(row => row.chapter_number)) + 1

export function volumeGroups(blueprint: Blueprint | undefined, chapters: Chapter[] = []) {
  const outlines = [...(blueprint?.chapter_outline || [])].sort((a, b) => a.chapter_number - b.chapter_number)
  const maximum = Math.max(0, ...outlines.map(row => row.chapter_number), ...chapters.map(row => row.chapter_number))
  const plans: NovelVolume[] = blueprint?.volumes?.length ? blueprint.volumes : maximum ? [{
    volume_number: 1, title: '第 1 卷', summary: '', start_chapter: 1, end_chapter: maximum,
    status: 'planned', outline_cursor: 0, is_virtual: true
  }] : []
  const used = new Set<number>()
  const groups = plans.map(volume => {
    const items = outlines.filter(row => row.chapter_number >= volume.start_chapter && row.chapter_number <= volume.end_chapter &&
      (row.volume_number == null || row.volume_number === volume.volume_number))
    items.forEach(row => used.add(row.chapter_number))
    const written = chapters.filter(row => row.chapter_number >= volume.start_chapter && row.chapter_number <= volume.end_chapter &&
      (row.content?.trim() || row.versions?.some(text => text.trim()) || row.generation_status === 'successful')).length
    return { ...volume, outlines: items, written, planned: volume.end_chapter - volume.start_chapter + 1 }
  })
  const unassigned = outlines.filter(row => !used.has(row.chapter_number))
  if (unassigned.length) groups.push({ volume_number: 0, title: '未分卷章节', summary: '', start_chapter: unassigned[0]!.chapter_number,
    end_chapter: unassigned.at(-1)!.chapter_number, status: 'planned', outline_cursor: 0, outlines: unassigned, written: 0, planned: unassigned.length })
  return groups
}

export function planningStage(stage: string) {
  if (stage === 'stage_core') return '核心设定'
  if (stage === 'stage_volume_plan') return '分卷规划'
  if (stage === 'stage_smart_split') return '分析已有大纲并划分卷'
  const batch = /^volume_(\d+)_batch_(\d+)_of_(\d+)$/.exec(stage)
  if (batch) return `第 ${batch[1]} 卷大纲 · ${batch[2]}/${batch[3]} 批`
  return ({ queued: '排队中', starting: '正在准备', succeeded: '已完成', failed: '生成失败', cancelled: '已取消' } as Record<string, string>)[stage] || '正在处理'
}
