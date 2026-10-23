<template>
  <section class="space-y-4 my-6" aria-label="分卷规划">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h3 class="text-xl font-semibold">分卷规划 · 计划 {{ blueprint.total_chapters || maximum }} 章</h3>
      <div v-if="editable && projectId" class="flex gap-2">
        <button v-if="legacy" class="md-btn md-btn-outlined" :disabled="busy" @click="smartSplit">智能分卷</button>
        <button class="md-btn md-btn-tonal" :disabled="busy || !groups.length" @click="run(jobs.generateAll)">生成全部剩余卷</button>
      </div>
    </div>
    <p v-if="error || jobs.error.value" class="text-red-600" role="alert">{{ error || jobs.error.value }}</p>
    <button v-if="jobs.error.value" class="md-btn md-btn-text" @click="run(jobs.recover)">重新获取任务状态</button>
    <div v-for="job in jobs.jobs.value" :key="job.id" class="p-3 rounded-lg bg-gray-50" aria-live="polite">
      <p>{{ planningStage(job.stage) }} · {{ job.progress }}%</p>
      <p v-if="job.status === 'failed'" class="text-red-600">{{ jobError(job) }}；已保存的进度可继续生成。</p>
      <button v-if="job.status === 'queued' || job.status === 'running'" class="underline" :disabled="jobs.cancelling.value" @click="run(() => jobs.cancel(job.id))">取消生成</button>
    </div>
    <details v-for="group in groups" :key="group.volume_number" open class="border rounded-xl p-4">
      <summary class="cursor-pointer font-medium">{{ group.volume_number ? `第 ${group.volume_number} 卷 · ` : '' }}{{ group.title }} · {{ group.written }} 已写 / {{ group.outlines.length }} 已有大纲 / {{ group.planned }} 计划章数</summary>
      <p v-if="group.volume_number && !group.outlines.length" class="text-amber-800 bg-amber-50 rounded p-3 mt-3" role="status">第 {{ group.volume_number }} 卷大纲尚未生成，请生成本卷大纲；失败后可续跑已保存的批次。</p>
      <p class="mt-3 whitespace-pre-line">{{ group.summary || '尚无卷梗概' }}</p>
      <dl v-if="group.plan" class="text-sm space-y-2 mt-3">
        <template v-for="(label, key) in planLabels" :key="key">
          <div v-if="group.plan[key]"><dt class="font-medium">{{ label }}</dt><dd class="whitespace-pre-line">{{ group.plan[key] }}</dd></div>
        </template>
      </dl>
      <p class="text-sm text-gray-500">第 {{ group.start_chapter }}–{{ group.end_chapter }} 章</p>
      <template v-if="editable && projectId && group.volume_number && drafts[group.volume_number]">
        <div class="grid gap-3 mt-4">
          <label>卷名<input v-model="drafts[group.volume_number]!.title" :disabled="busy" class="md-text-field-input w-full" maxlength="255" /></label>
          <label>卷梗概<textarea v-model="drafts[group.volume_number]!.summary" :disabled="busy" class="md-text-field-input w-full" rows="3" maxlength="10000"></textarea></label>
          <div class="flex gap-3">
            <label>起始章<input v-model.number="drafts[group.volume_number]!.start_chapter" :disabled="busy" type="number" min="1" max="10000" class="md-text-field-input w-28" /></label>
            <label>结束章<input v-model.number="drafts[group.volume_number]!.end_chapter" :disabled="busy" type="number" min="1" max="10000" class="md-text-field-input w-28" /></label>
          </div>
          <p class="text-xs text-gray-500">区间不能与相邻卷重叠，已有正文的章节须保留在原卷。</p>
          <div class="flex gap-2">
            <button class="md-btn md-btn-outlined" :disabled="busy" @click="save(group.volume_number)">保存卷规划</button>
            <button class="md-btn md-btn-tonal" :disabled="busy || group.status === 'outlined'" @click="run(() => jobs.generate(group.volume_number))">{{ group.status === 'outlined' ? '本卷大纲完整' : '生成本卷大纲 / 续跑' }}</button>
          </div>
        </div>
      </template>
    </details>
    <p v-if="!groups.length" class="text-gray-500">尚无卷规划，请先生成蓝图。</p>
  </section>
</template>

<script setup lang="ts">
import { errorMessage } from '@/utils/errors'
import { computed, ref, watch } from 'vue'
import { NovelAPI, formatApiDetail, type Blueprint, type Chapter, type NovelProject, type NovelVolume } from '@/api/novel'
import { globalAlert } from '@/composables/useAlert'
import { useUnsavedChanges } from '@/composables/useUnsavedChanges'
import { useVolumeJobs } from '@/composables/useVolumeJobs'
import { jobError, planningStage, volumeGroups } from '@/utils/volumes'

const props = defineProps<{ blueprint: Blueprint; projectId?: string; chapters?: Chapter[]; editable?: boolean }>()
const emit = defineEmits<{ updated: [project: NovelProject] }>()
const error = ref('')
const planLabels = { main_plot: '主线', core_conflict: '核心冲突', climax: '高潮', ending_hook: '卷末钩子', foreshadowing: '伏笔安排' }
const submitting = ref(false)
const replanning = ref(false)
const groups = computed(() => volumeGroups(props.blueprint, props.chapters || []))
const maximum = computed(() => Math.max(0, ...groups.value.map(row => row.end_chapter)))
const legacy = computed(() => !props.blueprint.volumes?.length || props.blueprint.volumes.every(row => row.is_virtual))
const jobs = useVolumeJobs(() => props.editable ? props.projectId : undefined, (project, job) => {
  if (replanning.value || job.kind === 'volume_smart_split') {
    resetDrafts(volumeGroups(project.blueprint, project.chapters))
    replanning.value = false
  }
  emit('updated', project)
})
const busy = computed(() => submitting.value || replanning.value || jobs.activeJobs.value.length > 0)
const drafts = ref<Record<number, Pick<NovelVolume, 'title' | 'summary' | 'start_chapter' | 'end_chapter'>>>({})
const baseline = ref<Record<number, Pick<NovelVolume, 'title' | 'summary' | 'start_chapter' | 'end_chapter'>>>({})
const dirty = (number: number) => JSON.stringify(drafts.value[number]) !== JSON.stringify(baseline.value[number])
useUnsavedChanges(() => Object.keys(drafts.value).some(number => dirty(Number(number))))
watch(() => props.projectId, () => { drafts.value = {}; baseline.value = {}; replanning.value = false })
watch(groups, values => {
  for (const row of values) {
    const value = { title: row.title, summary: row.summary, start_chapter: row.start_chapter, end_chapter: row.end_chapter }
    if (!dirty(row.volume_number)) drafts.value[row.volume_number] = { ...value }
    baseline.value[row.volume_number] = value
  }
}, { immediate: true })

function resetDrafts(values: ReturnType<typeof volumeGroups>) {
  baseline.value = Object.fromEntries(values.map(row => [row.volume_number, { title: row.title, summary: row.summary, start_chapter: row.start_chapter, end_chapter: row.end_chapter }]))
  drafts.value = Object.fromEntries(Object.entries(baseline.value).map(([number, value]) => [number, { ...value }]))
}

async function smartSplit() {
  if (busy.value) return
  if (!await globalAlert.showConfirm('将根据已有大纲重新规划第 1–' + maximum.value + ' 章的分卷归属。大纲内容和正文保留，未保存的卷计划修改将丢失。确定继续吗？', '重新规划分卷确认')) return
  if (busy.value) return
  resetDrafts(groups.value)
  await run(async () => {
    replanning.value = true
    try { await jobs.smartSplit() }
    catch (reason) { replanning.value = false; throw reason }
  })
}


async function run(action: () => Promise<unknown>) {
  if (submitting.value) return
  submitting.value = true
  error.value = ''
  try { await action() }
  catch (reason) { error.value = errorMessage(reason, '操作失败，请重试') }
  finally { submitting.value = false }
}

async function save(number: number) {
  if (busy.value || !props.projectId || !drafts.value[number]) return
  const value = { ...drafts.value[number]! }
  const previous = baseline.value[number]
  if (previous && (value.start_chapter !== previous.start_chapter || value.end_chapter !== previous.end_chapter)
    && !await globalAlert.showConfirm(`将调整第 ${number} 卷的章号区间为 ${value.start_chapter}–${value.end_chapter}。已有正文必须保留在原卷，大纲归属将更新。确定保存吗？`, '修改卷区间确认')) return
  await run(async () => {
    const project = await NovelAPI.updateVolume(props.projectId!, number, value)
    baseline.value[number] = value
    emit('updated', project)
  })
}
</script>
