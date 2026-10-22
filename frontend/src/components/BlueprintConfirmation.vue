<!-- AIMETA P=蓝图确认_蓝图确认对话框|R=确认操作|NR=不含编辑功能|E=component:BlueprintConfirmation|X=internal|A=确认对话框|D=vue|S=dom|RD=./README.ai -->
<template>
  <div class="p-8 bg-white rounded-2xl shadow-2xl fade-in">
    <h2 class="text-3xl font-bold text-center text-gray-800 mb-6">信息收集完成！</h2>

    <div class="text-center mb-8">
      <div 
        class="prose prose-lg prose-gray max-w-none mx-auto mb-4 text-gray-600"
        v-html="renderedAiMessage"
      ></div>
      <p class="text-sm text-gray-500">
        我们已经收集了足够的信息来为您创建详细的小说蓝图。点击下方按钮开始生成您的专属故事大纲。
      </p>
    </div>

    <!-- 高级加载状态 -->
    <div v-if="isGenerating" class="text-center py-12">
      <!-- 主加载动画 -->
      <div class="relative mx-auto mb-8 w-24 h-24">
        <!-- 外圆环 -->
        <div
          class="absolute inset-0 border-4 rounded-full transition-colors duration-500"
          :class="progress >= 100 ? 'border-green-100' : 'border-indigo-100'"
        ></div>
        <!-- 旋转的渐变圆环 -->
        <div
          class="absolute inset-0 border-4 border-transparent rounded-full transition-colors duration-500"
          :class="[
            progress >= 100
              ? 'border-t-green-500 border-r-green-400'
              : 'border-t-indigo-500 border-r-indigo-400',
            progress < 100 ? 'animate-spin' : ''
          ]"
        ></div>
        <!-- 内部脉冲圆 -->
        <div
          class="absolute inset-3 rounded-full animate-pulse opacity-20 transition-colors duration-500"
          :class="progress >= 100 ? 'bg-green-500' : 'bg-indigo-500'"
        ></div>
        <!-- 中心图标 -->
        <div
          class="absolute inset-6 rounded-full flex items-center justify-center transition-colors duration-500"
          :class="progress >= 100 ? 'bg-green-500' : 'bg-indigo-500'"
        >
          <svg
            v-if="progress >= 100"
            class="w-6 h-6 text-white"
            fill="currentColor"
            viewBox="0 0 20 20"
          >
            <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"></path>
          </svg>
          <svg
            v-else
            class="w-6 h-6 text-white animate-pulse"
            fill="currentColor"
            viewBox="0 0 20 20"
          >
            <path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
          </svg>
        </div>
      </div>

      <!-- 加载文本和进度 -->
      <div class="space-y-4">
        <h3 class="text-xl font-semibold text-gray-800 animate-pulse">{{ loadingText }}</h3>
        <p class="text-gray-600">{{ progress }}% · 已完成的批次会保存，离开或刷新后可恢复任务。</p>
        <progress class="w-full max-w-md" :value="progress" max="100" aria-label="蓝图生成进度"></progress>
        <button class="md-btn md-btn-outlined" :disabled="jobs.cancelling.value" @click="cancel">取消生成</button>

        <!-- 温馨提示 -->
        <div class="mt-6 p-4 bg-blue-50 rounded-lg border border-blue-200">
          <p class="text-sm text-blue-800">
            <svg class="inline w-4 h-4 mr-2" fill="currentColor" viewBox="0 0 20 20">
              <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd"></path>
            </svg>
            AI正在分析您的创意偏好，生成过程需要一些时间，请耐心等待...
          </p>
        </div>
      </div>
    </div>

    <p v-if="failure" role="alert" class="text-red-600 my-4">{{ failure }} 已保存的完整数据不会丢失。</p>

    <!-- 操作按钮 -->
    <div v-else class="text-center space-x-4">
      <!-- <button
        @click="$emit('back')"
        class="bg-gray-200 text-gray-700 font-bold py-3 px-8 rounded-full hover:bg-gray-300 transition-all duration-300 transform hover:scale-105"
      >
        返回对话
      </button> -->
      <button
        @click="generateBlueprint()"
        :disabled="isGenerating"
        class="bg-gradient-to-r from-indigo-500 to-purple-600 text-white font-bold py-3 px-8 rounded-full hover:from-indigo-600 hover:to-purple-700 transition-all duration-300 transform hover:scale-105 shadow-lg hover:shadow-xl disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none"
      >
        <span class="flex items-center justify-center">
          <svg class="w-5 h-5 mr-2" fill="currentColor" viewBox="0 0 20 20">
            <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"></path>
          </svg>
          {{ latestJob?.status === 'failed' || latestJob?.status === 'cancelled' ? '继续生成蓝图' : '开始创建蓝图' }}
        </span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { errorMessage } from '@/utils/errors'
import { ref, computed } from 'vue'
import { marked } from 'marked'
import { useNovelStore } from '@/stores/novel'
import { globalAlert } from '@/composables/useAlert'
import { NovelAPI, formatApiDetail } from '@/api/novel'
import { useJob } from '@/composables/useJob'
import { planningStage } from '@/utils/volumes'

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
const failure = computed(() => latestJob.value?.status === 'failed' ? formatApiDetail(latestJob.value.error?.detail) : latestJob.value?.status === 'cancelled' ? '已取消；再次生成会从已保存的进度继续。' : jobs.error.value)

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
@keyframes shimmer {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(100%);
  }
}

.animate-shimmer {
  animation: shimmer 2s infinite;
}

/* 自定义动画增强 */
@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.fade-in {
  animation: fadeIn 0.6s ease-out;
}

/* 按钮悬停效果增强 */
.transform {
  transition: transform 0.2s ease-in-out;
}

.hover\:scale-105:hover {
  transform: scale(1.05);
}

/* 禁用状态样式 */
.disabled\:transform-none:disabled {
  transform: none !important;
}

/* Markdown 内容样式优化 */
.prose {
  text-align: left;
}

.prose strong {
  color: #374151;
  font-weight: 700;
}

.prose em {
  color: #4b5563;
  font-style: italic;
}

.prose p {
  margin-bottom: 0.75rem;
}

.prose p:last-child {
  margin-bottom: 0;
}

.prose a {
  color: #6366f1;
  text-decoration: none;
  transition: color 0.2s;
}

.prose a:hover {
  color: #4f46e5;
  text-decoration: underline;
}

.prose code {
  background-color: #f3f4f6;
  padding: 0.125rem 0.375rem;
  border-radius: 0.25rem;
  font-size: 0.875em;
  color: #1f2937;
}

.prose ul, .prose ol {
  margin-left: 1.5rem;
  margin-bottom: 0.75rem;
}

.prose li {
  margin-bottom: 0.25rem;
}
</style>