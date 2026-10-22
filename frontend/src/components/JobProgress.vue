<template>
  <section v-if="jobs.length || error" class="space-y-3 mb-4" aria-live="polite" aria-label="后台任务">
    <p v-if="error" role="alert">{{ errorMessage(error) }}</p>
    <div v-for="job in jobs" :key="job.id" class="border rounded-xl p-3 bg-white min-w-0">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <span>{{ labels[job.kind] || 'AI 任务' }} · {{ stages[job.stage] || (active(job) ? '处理中' : statuses[job.status]) }} · {{ job.progress }}%</span>
        <button v-if="active(job)" class="md-btn md-btn-text" @click="$emit('cancel', job.id)">取消任务</button>
        <button v-else-if="job.status === 'failed' || job.status === 'cancelled'" class="md-btn md-btn-text" @click="$emit('retry', job)">重试</button>
      </div>
      <progress v-if="active(job)" :value="job.progress" max="100" class="w-full" aria-label="任务进度"></progress>
      <p v-if="job.status === 'failed'" class="text-red-700 break-words" role="alert">{{ formatApiDetail(job.error?.detail) }}</p>
      <p v-else-if="job.status === 'cancelled'" class="text-gray-600">任务已取消，已保存的内容保留。</p>
    </div>
  </section>
</template>
<script setup lang="ts">
import { errorMessage, formatApiDetail, type AIJob } from '@/api/novel'
defineProps<{ jobs: AIJob[]; error?: string | null }>()
defineEmits<{ cancel: [id: string]; retry: [job: AIJob] }>()
const active = (job: AIJob) => job.status === 'queued' || job.status === 'running'
const labels: Record<string, string> = { chapter_evaluate: '章节评审', outline_extend: '大纲续写', chapter_optimize: '正文优化', novel_import: '小说导入', emotion_analyze: '情感分析', six_dimension_review: '六维审查', consistency_review: '一致性审查' }
const stages: Record<string, string> = { queued: '等待执行', preparing: '准备上下文', persisted: '保存结果', import_characters: '识别角色', import_analysis: '分析设定', import_saving: '保存章节' }
const statuses: Record<string, string> = { succeeded: '已完成', failed: '失败', cancelled: '已取消' }
</script>
