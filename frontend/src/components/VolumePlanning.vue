<template>
  <section class="space-y-4 my-6" aria-label="分卷规划">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h3 class="text-xl font-semibold">分卷规划 · 计划 {{ blueprint.total_chapters || maximum }} 章</h3>
      <div v-if="editable && projectId" class="flex gap-2">
        <button v-if="legacy" class="md-btn md-btn-outlined" :disabled="busy" @click="run(jobs.smartSplit)">智能分卷</button>
        <button class="md-btn md-btn-tonal" :disabled="busy || !groups.length" @click="run(jobs.generateAll)">生成全部剩余卷</button>
      </div>
    </div>
    <p v-if="error || jobs.error.value" class="text-red-600" role="alert">{{ error || jobs.error.value }}</p>
    <div v-for="job in jobs.jobs.value" :key="job.id" class="p-3 rounded-lg bg-gray-50" aria-live="polite">
      <p>{{ planningStage(job.stage) }} · {{ job.progress }}%</p>
      <p v-if="job.status === 'failed'" class="text-red-600">{{ formatApiDetail(job.error?.detail) }}；已保存的进度可继续生成。</p>
      <button v-if="job.status === 'queued' || job.status === 'running'" class="underline" :disabled="jobs.cancelling.value" @click="run(() => jobs.cancel(job.id))">取消生成</button>
    </div>
    <details v-for="group in groups" :key="group.volume_number" open class="border rounded-xl p-4">
      <summary class="cursor-pointer font-medium">{{ group.volume_number ? `第 ${group.volume_number} 卷 · ` : '' }}{{ group.title }} · {{ group.written }} 已写 / {{ group.outlines.length }} 已有大纲 / {{ group.planned }} 计划章数</summary>
      <p class="mt-3 whitespace-pre-line">{{ group.summary || '尚无卷梗概' }}</p>
      <dl v-if="group.plan" class="text-sm space-y-2 mt-3">
        <template v-for="(label, key) in planLabels" :key="key">
          <div v-if="group.plan[key]"><dt class="font-medium">{{ label }}</dt><dd class="whitespace-pre-line">{{ group.plan[key] }}</dd></div>
        </template>
      </dl>
      <p class="text-sm text-gray-500">第 {{ group.start_chapter }}–{{ group.end_chapter }} 章</p>
      <template v-if="editable && projectId && group.volume_number && drafts[group.volume_number]">
        <div class="grid gap-3 mt-4">
          <label>卷名<input v-model="drafts[group.volume_number]!.title" class="md-text-field-input w-full" maxlength="255" /></label>
          <label>卷梗概<textarea v-model="drafts[group.volume_number]!.summary" class="md-text-field-input w-full" rows="3" maxlength="10000"></textarea></label>
          <div class="flex gap-3">
            <label>起始章<input v-model.number="drafts[group.volume_number]!.start_chapter" type="number" min="1" max="10000" class="md-text-field-input w-28" /></label>
            <label>结束章<input v-model.number="drafts[group.volume_number]!.end_chapter" type="number" min="1" max="10000" class="md-text-field-input w-28" /></label>
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
import { computed, ref, watch } from 'vue'
import { NovelAPI, formatApiDetail, type Blueprint, type Chapter, type NovelProject, type NovelVolume } from '@/api/novel'
import { useVolumeJobs } from '@/composables/useVolumeJobs'
import { planningStage, volumeGroups } from '@/utils/volumes'

const props = defineProps<{ blueprint: Blueprint; projectId?: string; chapters?: Chapter[]; editable?: boolean }>()
const emit = defineEmits<{ updated: [project: NovelProject] }>()
const error = ref('')
const planLabels = { main_plot: '主线', core_conflict: '核心冲突', climax: '高潮', ending_hook: '卷末钩子', foreshadowing: '伏笔安排' }
const submitting = ref(false)
const groups = computed(() => volumeGroups(props.blueprint, props.chapters || []))
const maximum = computed(() => Math.max(0, ...groups.value.map(row => row.end_chapter)))
const legacy = computed(() => !props.blueprint.volumes?.length || props.blueprint.volumes.every(row => row.is_virtual))
const jobs = useVolumeJobs(() => props.editable ? props.projectId : undefined, project => emit('updated', project))
const busy = computed(() => submitting.value || jobs.activeJobs.value.length > 0)
const drafts = ref<Record<number, Pick<NovelVolume, 'title' | 'summary' | 'start_chapter' | 'end_chapter'>>>({})
watch(groups, values => { drafts.value = Object.fromEntries(values.map(row => [row.volume_number, { title: row.title, summary: row.summary, start_chapter: row.start_chapter, end_chapter: row.end_chapter }])) }, { immediate: true })

async function run(action: () => Promise<unknown>) {
  if (submitting.value) return
  submitting.value = true
  error.value = ''
  try { await action() }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '操作失败，请重试' }
  finally { submitting.value = false }
}

async function save(number: number) {
  if (!props.projectId || !drafts.value[number]) return
  await run(async () => emit('updated', await NovelAPI.updateVolume(props.projectId!, number, drafts.value[number]!)))
}
</script>
