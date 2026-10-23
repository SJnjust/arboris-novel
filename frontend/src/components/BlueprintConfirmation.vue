<!-- AIMETA P=蓝图确认_蓝图确认对话框|R=确认操作|NR=不含编辑功能|E=component:BlueprintConfirmation|X=internal|A=确认对话框|D=vue|S=dom|RD=./README.ai -->
<template>
  <section class="blueprint-confirmation p-5 sm:p-8 bg-white rounded-2xl shadow-2xl">
    <h2 class="text-2xl sm:text-3xl font-bold text-center text-gray-800">信息收集完成！</h2>
    <div class="blueprint-explanation space-y-4" data-testid="blueprint-explanation">
      <div class="prose prose-gray max-w-none break-words" v-html="renderedAiMessage"></div>
      <p class="text-sm text-gray-600">我们已经收集了足够的信息。接下来依次生成核心设定、分卷规划和第 1 卷大纲，完整批次会自动保存。</p>
    </div>
    <div v-if="isGenerating" class="blueprint-progress space-y-4" aria-live="polite">
      <p class="font-semibold text-indigo-700">{{ loadingText }}</p>
      <p class="text-sm text-gray-600">{{ progress }}% · 可离开或刷新后恢复任务</p>
      <progress class="w-full" :value="progress" max="100" aria-label="蓝图生成进度"></progress>
      <button class="md-btn md-btn-outlined" :disabled="jobs.cancelling.value" @click="cancel">取消生成</button>
    </div>
    <div v-if="failure" class="blueprint-failure rounded-xl border border-red-200 bg-red-50 p-4 space-y-3" role="alert" data-testid="blueprint-failure">
      <p class="text-red-700 break-words whitespace-pre-line">{{ failure }}</p>
      <p class="text-sm text-gray-700">已保存的设定、分卷和完整批次保留，可以从未完成的位置继续。</p>
    </div>
    <p v-else-if="hasCheckpoint && !isGenerating" class="text-sm text-indigo-700">规划已保存，第 1 卷大纲尚未完成，可以继续生成。</p>
    <div v-if="!isGenerating" class="flex flex-wrap justify-center gap-3">
      <button class="md-btn md-btn-filled" @click="generateBlueprint()">{{ failure || hasCheckpoint ? '从这里继续' : '开始创建蓝图' }}</button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { errorMessage } from '@/utils/errors'
import { ref, computed } from 'vue'
import { marked } from 'marked'
import { useNovelStore } from '@/stores/novel'
import { globalAlert } from '@/composables/useAlert'
import { NovelAPI, formatApiDetail } from '@/api/novel'
import { useJob } from '@/composables/useJob'
import { planningStage, jobError } from '@/utils/volumes'

// 配置 marked
marked.setOptions({
  gfm: true,           // 启用 GitHub 风格语法
  breaks: true         // 将单个换行视为 <br>
})

interface Props {
  aiMessage: string
  regenerate?: boolean
}

const props = defineProps<Props>()

const emit = defineEmits<{
  blueprintGenerated: [response: any]
  back: []
}>()

const novelStore = useNovelStore()
const submitting = ref(false)
const submittedOnce = ref(false)
const jobs = useJob(() => novelStore.currentProject?.id, {
  kind: 'blueprint_generate',
  async onTerminal(job, signal) {
    const project = await NovelAPI.getNovel(job.project_id, { signal })
    if (signal.aborted || novelStore.currentProject?.id !== job.project_id) return
    novelStore.setCurrentProject(project)
    if (job.status === 'succeeded' && project.blueprint) {
      // 先让 useJob 清除已消费的 ID，再切换父界面，避免重新生成时重放旧任务。
      return () => emit('blueprintGenerated', { blueprint: project.blueprint, ai_message: '核心蓝图、分卷规划和第 1 卷大纲已完成，可继续生成后续卷。' })
    }
  }
})
const latestJob = computed(() => jobs.jobs.value[0])
const isGenerating = computed(() => submitting.value || jobs.activeJobs.value.length > 0)
const progress = computed(() => latestJob.value?.progress || 0)
const renderedAiMessage = computed(() => marked.parse(props.aiMessage))
const loadingText = computed(() => planningStage(latestJob.value?.stage || 'starting'))
const hasCheckpoint = computed(() => !!novelStore.currentProject?.blueprint?.volumes?.length)
const failure = computed(() => latestJob.value?.status === 'failed' ? jobError(latestJob.value) : latestJob.value?.status === 'cancelled' ? '已取消；再次生成会从已保存的进度继续。' : jobs.error.value)

const generateBlueprint = async (resume = true) => {
  if (isGenerating.value) return
  submitting.value = true
  try {
    await jobs.submit('blueprint', (key, signal) => novelStore.generateBlueprint(key, { signal, resume: resume && !(props.regenerate && !submittedOnce.value) }))
    submittedOnce.value = true
  } catch (error) {
    globalAlert.showError(errorMessage(error, '提交失败，请重试'), '蓝图生成')
  } finally {
    submitting.value = false
  }
}
const cancel = async () => {
  const job = jobs.activeJobs.value[0]
  if (!job) return
  try { await jobs.cancel(job.id) }
  catch (error) { globalAlert.showError(errorMessage(error, '取消失败'), '取消生成') }
}
</script>

<style scoped>
.blueprint-confirmation { display: flex; flex-direction: column; gap: 1.5rem; min-width: 0; }
.blueprint-explanation, .blueprint-failure, .blueprint-progress { position: static; min-width: 0; flex-shrink: 0; overflow-wrap: anywhere; }
.blueprint-explanation :deep(p) { margin: 0 0 0.75rem; }
</style>
