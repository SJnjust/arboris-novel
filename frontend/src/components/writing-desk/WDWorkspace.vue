<!-- AIMETA P=写作台工作区_主编辑区域|R=章节编辑_生成|NR=不含侧边栏|E=component:WDWorkspace|X=ui|A=工作区|D=vue|S=dom,net|RD=./README.ai -->
<template>
  <div class="flex-1 min-w-0 h-full">
    <div class="md-card md-card-elevated h-full flex flex-col" style="border-radius: var(--md-radius-xl);">
      <!-- 章节工作区头部 -->
      <div v-if="selectedChapterNumber" class="md-card-header flex-shrink-0">
        <div class="flex items-center justify-between">
          <div>
            <div class="flex items-center gap-3 mb-2">
              <h2 class="md-title-large font-semibold">第{{ selectedChapterNumber }}章</h2>
              <span
                :class="[
                  'md-chip',
                  isChapterCompleted(selectedChapterNumber)
                    ? 'm3-chip-success'
                    : 'm3-chip-neutral'
                ]"
              >
                {{ isChapterCompleted(selectedChapterNumber) ? '已完成' : '未完成' }}
              </span>
            </div>
            <h3 class="md-title-medium md-on-surface mb-1">{{ selectedChapterOutline?.title || '未知标题' }}</h3>
            <p class="md-body-small md-on-surface-variant">{{ selectedChapterOutline?.summary || '暂无章节描述' }}</p>
          </div>

          <div class="flex items-center gap-2">
            <button
              v-if="isChapterCompleted(selectedChapterNumber)"
              @click="openEditModal"
              class="md-btn md-btn-tonal md-ripple flex items-center gap-2 whitespace-nowrap"
            >
              <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"></path>
              </svg>
              手动编辑
            </button>
            <button
              @click="confirmRegenerateChapter"
              :disabled="jobActive || generatingChapter === selectedChapterNumber"
              class="md-btn md-btn-filled md-ripple flex items-center gap-2 whitespace-nowrap disabled:opacity-50"
            >
              <svg v-if="generatingChapter === selectedChapterNumber" class="w-4 h-4 animate-spin" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.601 2.566 1 1 0 11-1.885.666A5.002 5.002 0 005.999 7H9a1 1 0 010 2H4a1 1 0 01-1-1V3a1 1 0 011-1zm.008 9.057a1 1 0 011.276.61A5.002 5.002 0 0014.001 13H11a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0v-2.101a7.002 7.002 0 01-11.601-2.566 1 1 0 01.61-1.276z" clip-rule="evenodd"></path>
              </svg>
              <svg v-else class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.601 2.566 1 1 0 11-1.885.666A5.002 5.002 0 005.999 7H9a1 1 0 010 2H4a1 1 0 01-1-1V3a1 1 0 011-1zm.008 9.057a1 1 0 011.276.61A5.002 5.002 0 0014.001 13H11a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0v-2.101a7.002 7.002 0 01-11.601-2.566 1 1 0 01.61-1.276z" clip-rule="evenodd"></path>
              </svg>
              {{ generatingChapter === selectedChapterNumber ? '生成中...' : '重新生成' }}
            </button>
          </div>
        </div>
      </div>

      <div v-if="generationJob" class="px-6 py-3 border-b" aria-live="polite">
        <template v-if="jobActive">
          <div class="flex items-center justify-between gap-3">
            <span>{{ stageLabel }} · {{ generationJob.progress }}%</span>
            <button class="md-btn md-btn-tonal" :disabled="cancellingGeneration" @click="$emit('cancelGeneration')">
              {{ cancellingGeneration ? '正在取消…' : '取消生成' }}
            </button>
          </div>
          <div class="h-2 mt-2 rounded bg-gray-200" role="progressbar" :aria-valuenow="generationJob.progress" aria-valuemin="0" aria-valuemax="100" aria-label="章节生成进度">
            <div class="h-full rounded bg-blue-600" :style="{ width: `${generationJob.progress}%` }"></div>
          </div>
        </template>
        <p v-else-if="generationJob.status === 'failed'">{{ formatApiDetail(generationJob.error?.detail) }}</p>
        <p v-else-if="generationJob.status === 'cancelled'">生成已取消，可以重新生成。</p>
        <p v-else-if="generationJob.result?.failed_versions?.length">部分版本生成失败，已保存其余 {{ generationJob.result.version_count }} 个版本供选择。</p>
        <p v-else>已生成 {{ generationJob.result?.version_count }} 个版本，请选择要采用的内容。</p>
      </div>

      <!-- 章节内容展示区 -->
      <div class="md-card-content flex-1 overflow-y-auto">
        <component
          :is="currentComponent"
          v-bind="currentComponentProps"
          @hideVersionSelector="$emit('hideVersionSelector')"
          @update:selectedVersionIndex="$emit('update:selectedVersionIndex', $event)"
          @showVersionDetail="$emit('showVersionDetail', $event)"
          @confirmVersionSelection="$emit('confirmVersionSelection')"
          @generateChapter="$emit('generateChapter', $event)"
          @showVersionSelector="$emit('showVersionSelector')"
          @regenerateChapter="$emit('regenerateChapter')"
          @evaluateChapter="$emit('evaluateChapter')"
          @showEvaluationDetail="$emit('showEvaluationDetail')"
        />
      </div>
    </div>

    <!-- 编辑章节内容模态框 -->
    <div v-if="showEditModal" class="md-dialog-overlay">
      <div class="md-dialog w-full h-full max-w-5xl m3-editor-dialog">
        <!-- 模态框头部 -->
        <div class="flex items-center justify-between p-6 border-b" style="border-bottom-color: var(--md-outline-variant);">
          <h3 class="md-title-large font-semibold">
            编辑第{{ selectedChapterNumber }}章内容
          </h3>
          <button
            @click="closeEditModal"
            class="md-icon-btn md-ripple"
          >
            <svg class="w-6 h-6" fill="currentColor" viewBox="0 0 20 20">
              <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"></path>
            </svg>
          </button>
        </div>

        <!-- 模态框内容 -->
        <div class="flex-1 p-6 overflow-hidden">
          <div class="flex flex-col h-full">
            <label class="md-text-field-label mb-2">
              章节内容
            </label>
            <textarea
              v-model="editingContent"
              class="md-textarea flex-1 w-full resize-none"
              placeholder="请输入章节内容..."
              :disabled="isSaving"
            ></textarea>
            <div class="md-body-small md-on-surface-variant mt-2">
              字数统计: {{ editingContent.length }}
            </div>
          </div>
        </div>

        <!-- 模态框底部 -->
        <div class="flex items-center justify-end gap-3 p-6 border-t" style="border-top-color: var(--md-outline-variant);">
          <button
            @click="closeEditModal"
            :disabled="isSaving"
            class="md-btn md-btn-outlined md-ripple disabled:opacity-50"
          >
            取消
          </button>
          <button
            @click="saveEditedContent"
            :disabled="isSaving || !editingContent.trim()"
            class="md-btn md-btn-filled md-ripple disabled:opacity-50 flex items-center gap-2"
          >
            <svg v-if="isSaving" class="w-4 h-4 animate-spin" fill="currentColor" viewBox="0 0 20 20">
              <path fill-rule="evenodd" d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.601 2.566 1 1 0 11-1.885.666A5.002 5.002 0 005.999 7H9a1 1 0 010 2H4a1 1 0 01-1-1V3a1 1 0 011-1zm.008 9.057a1 1 0 011.276.61A5.002 5.002 0 0014.001 13H11a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0v-2.101a7.002 7.002 0 01-11.601-2.566 1 1 0 01.61-1.276z" clip-rule="evenodd"></path>
            </svg>
            {{ isSaving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { formatApiDetail } from '@/api/novel'
import { globalAlert } from '@/composables/useAlert'
import type { AIJob, Chapter, ChapterOutline, ChapterGenerationResponse, ChapterVersion, NovelProject } from '@/api/novel'
import WorkspaceInitial from './workspace/WorkspaceInitial.vue'
import ChapterGenerating from './workspace/ChapterGenerating.vue'
import VersionSelector from './workspace/VersionSelector.vue'
import ChapterContent from './workspace/ChapterContent.vue'
import ChapterFailed from './workspace/ChapterFailed.vue'
import ChapterEmpty from './workspace/ChapterEmpty.vue'

interface Props {
  project: NovelProject | null
  selectedChapterNumber: number | null
  generatingChapter: number | null
  evaluatingChapter: number | null
  showVersionSelector: boolean
  chapterGenerationResult: ChapterGenerationResponse | null
  selectedVersionIndex: number
  availableVersions: ChapterVersion[]
  generationJob?: AIJob | null
  cancellingGeneration?: boolean
  isSelectingVersion?: boolean
}

const props = defineProps<Props>()
const stageLabel = computed(() => ({
  queued: '等待生成', starting: '准备生成', backfill_summaries: '回补近期摘要', mission: '规划本章',
  rag: '查找相关内容', versions: '生成候选版本', review: '比较候选版本', persist: '保存内容'
}[props.generationJob?.stage || 'queued'] || '正在生成'))

const emit = defineEmits([
  'regenerateChapter',
  'evaluateChapter',
  'hideVersionSelector',
  'update:selectedVersionIndex',
  'showVersionDetail',
  'confirmVersionSelection',
  'generateChapter',
  'showVersionSelector',
  'showEvaluationDetail',
  'cancelGeneration',
  'editChapter'
])

const confirmRegenerateChapter = async () => {
  const confirmed = await globalAlert.showConfirm('重新生成会覆盖当前章节的现有内容，确定继续吗？', '重新生成确认')
  if (confirmed) {
    emit('regenerateChapter')
  }
}

// 编辑模态框状态
const showEditModal = ref(false)
const editingContent = ref('')
const isSaving = ref(false)

// 清理版本内容的辅助函数
const cleanVersionContent = (content: string): string => {
  if (!content) return ''
  try {
    const parsed = JSON.parse(content)
    const extractContent = (value: any): string | null => {
      if (!value) return null
      if (typeof value === 'string') return value
      if (Array.isArray(value)) {
        for (const item of value) {
          const nested = extractContent(item)
          if (nested) return nested
        }
        return null
      }
      if (typeof value === 'object') {
        for (const key of ['content', 'chapter_content', 'chapter_text', 'text', 'body', 'story']) {
          if (value[key]) {
            const nested = extractContent(value[key])
            if (nested) return nested
          }
        }
      }
      return null
    }
    const extracted = extractContent(parsed)
    if (extracted) {
      content = extracted
    }
  } catch (error) {
    // not a json
  }
  let cleaned = content.replace(/^"|"$/g, '')
  cleaned = cleaned.replace(/\\n/g, '\n')
  cleaned = cleaned.replace(/\\"/g, '"')
  cleaned = cleaned.replace(/\\t/g, '\t')
  cleaned = cleaned.replace(/\\\\/g, '\\')
  return cleaned
}

const openEditModal = () => {
  if (selectedChapter.value?.content) {
    editingContent.value = cleanVersionContent(selectedChapter.value.content)
    showEditModal.value = true
  }
}

const closeEditModal = () => {
  showEditModal.value = false
  editingContent.value = ''
  isSaving.value = false
}

const saveEditedContent = async () => {
  if (!props.selectedChapterNumber || !editingContent.value.trim()) return
  
  isSaving.value = true
  try {
    emit('editChapter', {
      chapterNumber: props.selectedChapterNumber,
      content: editingContent.value
    })
    closeEditModal()
  } catch (error) {
    console.error('保存章节内容失败:', error)
  } finally {
    isSaving.value = false
  }
}

const selectedChapter = computed(() => {
  if (!props.project || props.selectedChapterNumber === null) return null
  return props.project.chapters.find(ch => ch.chapter_number === props.selectedChapterNumber) || null
})

const selectedChapterOutline = computed(() => {
  if (!props.project?.blueprint?.chapter_outline || props.selectedChapterNumber === null) return null
  return props.project.blueprint.chapter_outline.find(ch => ch.chapter_number === props.selectedChapterNumber) || null
})

const isChapterCompleted = (chapterNumber: number) => {
  if (!props.project?.chapters) return false
  const chapter = props.project.chapters.find(ch => ch.chapter_number === chapterNumber)
  return chapter && chapter.generation_status === 'successful'
}

const isChapterGenerating = (chapterNumber: number) => {
  if (!props.project?.chapters) return false
  const chapter = props.project.chapters.find(ch => ch.chapter_number === chapterNumber)
  return chapter && chapter.generation_status === 'generating'
}

const isChapterFailed = (chapterNumber: number) => {
  if (!props.project?.chapters) return false
  const chapter = props.project.chapters.find(ch => ch.chapter_number === chapterNumber)
  return chapter && chapter.generation_status === 'failed'
}

const isChapterEvaluationFailed = (chapterNumber: number) => {
  if (!props.project?.chapters) return false
  const chapter = props.project.chapters.find(ch => ch.chapter_number === chapterNumber)
  return chapter && chapter.generation_status === 'evaluation_failed'
}

const canGenerateChapter = (chapterNumber: number | null) => {
  if (chapterNumber === null || !props.project?.blueprint?.chapter_outline) return false

  const outlines = props.project.blueprint.chapter_outline.sort((a, b) => a.chapter_number - b.chapter_number)
  
  for (const outline of outlines) {
    if (outline.chapter_number >= chapterNumber) break
    
    const chapter = props.project?.chapters.find(ch => ch.chapter_number === outline.chapter_number)
    if (!chapter || chapter.generation_status !== 'successful') {
      return false
    }
  }

  const currentChapter = props.project?.chapters.find(ch => ch.chapter_number === chapterNumber)
  if (currentChapter && currentChapter.generation_status === 'successful') {
    return true
  }

  return true
}

const jobActive = computed(() => props.generationJob?.status === 'queued' || props.generationJob?.status === 'running')

const currentComponent = computed(() => {
  if (!props.selectedChapterNumber) {
    return WorkspaceInitial
  }

  const status = selectedChapter.value?.generation_status
  if (jobActive.value || status === 'generating' || status === 'evaluating' || status === 'selecting') {
    return ChapterGenerating // Use a generic "in-progress" component
  }

  if (status === 'waiting_for_confirm' || status === 'evaluation_failed') {
    return VersionSelector
  }

  if (selectedChapter.value?.content) {
    return ChapterContent
  }
  if (isChapterFailed(props.selectedChapterNumber)) {
    return ChapterFailed
  }
  return ChapterEmpty
})

const currentComponentProps = computed(() => {
  if (!props.selectedChapterNumber) {
    return {}
  }
  const status = selectedChapter.value?.generation_status
  if (jobActive.value || status === 'generating' || status === 'evaluating' || status === 'selecting') {
    return {
      chapterNumber: props.selectedChapterNumber,
      status: jobActive.value ? 'generating' : status
    }
  }

  if (status === 'waiting_for_confirm' || status === 'evaluation_failed') {
    return {
      selectedChapter: selectedChapter.value,
      chapterGenerationResult: props.chapterGenerationResult,
      availableVersions: props.availableVersions,
      selectedVersionIndex: props.selectedVersionIndex,
      isSelectingVersion: props.isSelectingVersion,
      evaluatingChapter: props.evaluatingChapter,
      isEvaluationFailed: isChapterEvaluationFailed(props.selectedChapterNumber)
    }
  }
  if (selectedChapter.value?.content) {
    return { 
      selectedChapter: selectedChapter.value,
      projectId: props.project?.id
    }
  }
  if (isChapterFailed(props.selectedChapterNumber)) {
    return {
      chapterNumber: props.selectedChapterNumber,
      generatingChapter: props.generatingChapter
    }
  }
  return {
    chapterNumber: props.selectedChapterNumber,
    generatingChapter: props.generatingChapter,
    canGenerate: canGenerateChapter(props.selectedChapterNumber)
  }
})
</script>

<style scoped>
.m3-chip-success {
  background-color: var(--md-success-container);
  color: var(--md-on-success-container);
}

.m3-chip-neutral {
  background-color: var(--md-surface-container);
  color: var(--md-on-surface-variant);
}

.m3-editor-dialog {
  max-width: min(1200px, calc(100vw - 32px));
  max-height: calc(100vh - 32px);
  border-radius: var(--md-radius-xl);
}
</style>
