// AIMETA P=小说API客户端_小说和章节接口|R=小说CRUD_章节管理_生成|NR=不含UI逻辑|E=api:novel|X=internal|A=novelApi对象|D=axios|S=net|RD=./README.ai
import { useAuthStore } from '@/stores/auth'
import router from '@/router'

// API 配置
// 在生产环境中使用相对路径，在开发环境中使用绝对路径
export const API_BASE_URL = import.meta.env.MODE === 'production' ? '' : 'http://127.0.0.1:8000'
export const API_PREFIX = '/api'

// 统一的请求处理函数
export type RequestOptions = RequestInit & { timeoutMs?: number | null }

export function redactSensitiveText(text: string): string {
  return text.replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[已隐藏]@')
    .replace(/\bBearer\s+(?!\[已隐藏\])[^\s"'<>;,，；}]+/gi, 'Bearer [已隐藏]')
    .replace(/\bsk-[A-Za-z0-9_-]+/g, '[已隐藏]')
    .replace(/(["']?(?:api[_-]?key|access[_-]?token|secret[_-]?key|authorization|password|passwd|token)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;，；}]+)/gi, '$1[已隐藏]')
}

export function formatApiDetail(detail: unknown): string {
  if (typeof detail === 'string') return redactSensitiveText(detail)
  if (Array.isArray(detail)) {
    return detail.map(item => {
      if (item && typeof item === 'object' && 'msg' in item) {
        const location = Array.isArray(item.loc) ? item.loc.filter((part: unknown) => !['body', 'query', 'path'].includes(String(part))).join('.') : ''
        return redactSensitiveText(`${location ? `${location}：` : ''}${String(item.msg)}`)
      }
      return formatApiDetail(item)
    }).join('；')
  }
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const versions = 'failed_versions' in detail && Array.isArray(detail.failed_versions)
      ? detail.failed_versions.map(item => `版本 ${Number(item.index) + 1}：${formatApiDetail(item.detail)}`) : []
    return redactSensitiveText([String(detail.message), ...versions].join('；'))
  }
  return '请求失败，请稍后重试'
}

export class ApiError extends Error {
  constructor(message: string, public status: number, public detail: unknown) {
    super(message)
  }

  get jobId(): string | undefined {
    return this.detail && typeof this.detail === 'object' && 'job_id' in this.detail ? String(this.detail.job_id) : undefined
  }
}

export const request = async (url: string, options: RequestOptions = {}) => {
  const { timeoutMs = 30000, signal, ...init } = options
  const controller = new AbortController()
  const abort = () => controller.abort(signal?.reason)
  if (signal?.aborted) abort()
  else signal?.addEventListener('abort', abort, { once: true })
  let timedOut = false
  const timer = timeoutMs === null ? undefined : setTimeout(() => { timedOut = true; controller.abort() }, timeoutMs)
  const authStore = useAuthStore()
  const headers = new Headers(options.headers)
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')

  // 如果 body 是 FormData，删除 Content-Type header，让浏览器自动设置（包含 boundary）
  if (options.body instanceof FormData) {
    headers.delete('Content-Type')
  }

  if (authStore.isAuthenticated && authStore.token) {
    headers.set('Authorization', `Bearer ${authStore.token}`)
  }

  try {
    const response = await fetch(url, { ...init, headers, signal: controller.signal })

    if (response.status === 401) {
      // Token 失效或未授权
      authStore.logout()
      router.push('/login')
      throw new Error('会话已过期，请重新登录')
    }

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new ApiError(errorData.detail ? formatApiDetail(errorData.detail) : `请求失败，状态码: ${response.status}`, response.status, errorData.detail)
    }

    return await response.json()
  } catch (error) {
    if (timedOut) throw new Error('请求超时，请重试')
    throw error
  } finally {
    if (timer !== undefined) clearTimeout(timer)
    signal?.removeEventListener('abort', abort)
  }
}

export interface JobSubmitted { job_id: string }

export interface AIJob {
  id: string
  project_id: string
  kind: string
  target: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  stage: string
  progress: number
  payload: { chapter_number?: number; writing_notes?: string }
  result: { version_count?: number; failed_versions?: Array<{ index: number; detail: unknown }> } | null
  error: { detail: unknown; status_code: number } | null
  created_at: string
  updated_at: string
  heartbeat_at: string
}

// 类型定义
export interface NovelProject {
  id: string
  title: string
  initial_prompt: string
  blueprint?: Blueprint
  chapters: Chapter[]
  conversation_history: ConversationMessage[]
}

export interface NovelProjectSummary {
  id: string
  title: string
  genre: string
  last_edited: string
  completed_chapters: number
  total_chapters: number
}

export interface Blueprint {
  title?: string
  target_audience?: string
  genre?: string
  style?: string
  tone?: string
  one_sentence_summary?: string
  full_synopsis?: string
  world_setting?: any
  characters?: Character[]
  relationships?: any[]
  chapter_outline?: ChapterOutline[]
}

export interface Character {
  name: string
  description: string
  identity?: string
  personality?: string
  goals?: string
  abilities?: string
  relationship_to_protagonist?: string
}

export interface ChapterOutline {
  chapter_number: number
  title: string
  summary: string
}

export interface ChapterVersion {
  content: string
  style?: string
}

export interface Chapter {
  chapter_number: number
  title: string
  summary: string
  content: string | null
  versions: string[] | null  // versions是字符串数组，不是对象数组
  evaluation: string | null
  generation_status: 'not_generated' | 'generating' | 'evaluating' | 'selecting' | 'failed' | 'evaluation_failed' | 'waiting_for_confirm' | 'successful'
  word_count?: number  // 字数统计
}

export interface ConversationMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ConverseResponse {
  ai_message: string
  ui_control: UIControl
  conversation_state: any
  is_complete: boolean
  ready_for_blueprint?: boolean  // 新增：表示准备生成蓝图
}

export interface BlueprintGenerationResponse {
  blueprint: Blueprint
  ai_message: string
}

export interface UIControl {
  type: 'single_choice' | 'text_input'
  options?: Array<{ id: string; label: string }>
  placeholder?: string
}

export interface ChapterGenerationResponse {
  versions: ChapterVersion[] // Renamed from chapter_versions for consistency
  evaluation: string | null
  ai_message: string
  chapter_number: number
}

export interface DeleteNovelsResponse {
  status: string
  message: string
}

// 内容型Section（对应后端NovelSectionType枚举）
export type NovelSectionType = 'overview' | 'world_setting' | 'characters' | 'relationships' | 'chapter_outline' | 'chapters'

// 分析型Section（不属于NovelSectionType，使用独立的analytics API）
export type AnalysisSectionType = 'emotion_curve' | 'foreshadowing'

// 所有Section的联合类型
export type AllSectionType = NovelSectionType | AnalysisSectionType

export interface NovelSectionResponse {
  section: NovelSectionType
  data: Record<string, any>
}

// API 函数
const NOVELS_BASE = `${API_BASE_URL}${API_PREFIX}/novels`
const WRITER_PREFIX = '/api/writer'
const WRITER_BASE = `${API_BASE_URL}${WRITER_PREFIX}/novels`

export class NovelAPI {
  static async createNovel(title: string, initialPrompt: string): Promise<NovelProject> {
    return request(NOVELS_BASE, {
      method: 'POST',
      body: JSON.stringify({ title, initial_prompt: initialPrompt })
    })
  }

  static async importNovel(file: File): Promise<{ id: string }> {
    const formData = new FormData()
    formData.append('file', file)
    return request(`${NOVELS_BASE}/import`, {
      timeoutMs: null,
      method: 'POST',
      body: formData,
      headers: {
        // 让 browser 自动设置 Content-Type 为 multipart/form-data，不手动设置
      }
    })
  }

  static async getNovel(projectId: string, options: RequestOptions = {}): Promise<NovelProject> {
    return request(`${NOVELS_BASE}/${projectId}`, options)
  }

  static async getChapter(projectId: string, chapterNumber: number): Promise<Chapter> {
    return request(`${NOVELS_BASE}/${projectId}/chapters/${chapterNumber}`)
  }

  static async getSection(projectId: string, section: NovelSectionType): Promise<NovelSectionResponse> {
    return request(`${NOVELS_BASE}/${projectId}/sections/${section}`)
  }

  static async converseConcept(
    projectId: string,
    userInput: any,
    conversationState: any = {}
  ): Promise<ConverseResponse> {
    const formattedUserInput = userInput || { id: null, value: null }
    return request(`${NOVELS_BASE}/${projectId}/concept/converse`, {
      timeoutMs: null,
      method: 'POST',
      body: JSON.stringify({
        user_input: formattedUserInput,
        conversation_state: conversationState
      })
    })
  }

  static async generateBlueprint(projectId: string): Promise<BlueprintGenerationResponse> {
    return request(`${NOVELS_BASE}/${projectId}/blueprint/generate`, {
      timeoutMs: null,
      method: 'POST'
    })
  }

  static async saveBlueprint(projectId: string, blueprint: Blueprint): Promise<NovelProject> {
    return request(`${NOVELS_BASE}/${projectId}/blueprint/save`, {
      method: 'POST',
      body: JSON.stringify(blueprint)
    })
  }

  static async generateChapter(projectId: string, chapterNumber: number, key: string, options: RequestOptions = {}): Promise<JobSubmitted> {
    return request(`${WRITER_BASE}/${projectId}/chapters/generate`, {
      ...options,
      method: 'POST',
      headers: { 'Idempotency-Key': key },
      body: JSON.stringify({ chapter_number: chapterNumber })
    })
  }

  static async getJob(jobId: string, options: RequestOptions = {}): Promise<AIJob> {
    return request(`${API_BASE_URL}/api/jobs/${jobId}`, options)
  }

  static async getJobs(projectId: string, options: RequestOptions = {}): Promise<AIJob[]> {
    return request(`${API_BASE_URL}/api/jobs?project_id=${encodeURIComponent(projectId)}&active=1`, options)
  }

  static async cancelJob(jobId: string, options: RequestOptions = {}): Promise<AIJob> {
    return request(`${API_BASE_URL}/api/jobs/${jobId}/cancel`, { ...options, method: 'POST' })
  }

  static async evaluateChapter(projectId: string, chapterNumber: number): Promise<NovelProject> {
    return request(`${WRITER_BASE}/${projectId}/chapters/evaluate`, {
      timeoutMs: null,
      method: 'POST',
      body: JSON.stringify({ chapter_number: chapterNumber })
    })
  }

  static async selectChapterVersion(
    projectId: string,
    chapterNumber: number,
    versionIndex: number
  ): Promise<NovelProject> {
    return request(`${WRITER_BASE}/${projectId}/chapters/select`, {
      timeoutMs: null,
      method: 'POST',
      body: JSON.stringify({
        chapter_number: chapterNumber,
        version_index: versionIndex
      })
    })
  }

  static async getAllNovels(): Promise<NovelProjectSummary[]> {
    return request(NOVELS_BASE)
  }

  static async deleteNovels(projectIds: string[]): Promise<DeleteNovelsResponse> {
    return request(NOVELS_BASE, {
      method: 'DELETE',
      body: JSON.stringify(projectIds)
    })
  }

  static async updateChapterOutline(
    projectId: string,
    chapterOutline: ChapterOutline
  ): Promise<NovelProject> {
    return request(`${WRITER_BASE}/${projectId}/chapters/update-outline`, {
      method: 'POST',
      body: JSON.stringify(chapterOutline)
    })
  }

  static async deleteChapter(
    projectId: string,
    chapterNumbers: number[]
  ): Promise<NovelProject> {
    return request(`${WRITER_BASE}/${projectId}/chapters/delete`, {
      method: 'POST',
      body: JSON.stringify({ chapter_numbers: chapterNumbers })
    })
  }

  static async generateChapterOutline(
    projectId: string,
    startChapter: number,
    numChapters: number
  ): Promise<NovelProject> {
    return request(`${WRITER_BASE}/${projectId}/chapters/outline`, {
      timeoutMs: null,
      method: 'POST',
      body: JSON.stringify({
        start_chapter: startChapter,
        num_chapters: numChapters
      })
    })
  }

  static async updateBlueprint(projectId: string, data: Record<string, any>): Promise<NovelProject> {
    return request(`${NOVELS_BASE}/${projectId}/blueprint`, {
      method: 'PATCH',
      body: JSON.stringify(data)
    })
  }

  static async editChapterContent(
    projectId: string,
    chapterNumber: number,
    content: string
  ): Promise<Chapter> {
    return request(`${WRITER_BASE}/${projectId}/chapters/edit-fast`, {
      method: 'POST',
      body: JSON.stringify({
        chapter_number: chapterNumber,
        content: content
      })
    })
  }
}


// 优化相关类型定义
export interface EmotionBeat {
  primary_emotion: string
  intensity: number
  curve: {
    start: number
    peak: number
    end: number
  }
  turning_point: string
}

export interface OptimizeRequest {
  project_id: string
  chapter_number: number
  dimension: 'dialogue' | 'environment' | 'psychology' | 'rhythm'
  additional_notes?: string
}

export interface OptimizeResponse {
  optimized_content: string
  optimization_notes: string
  dimension: string
}

// 优化API
const OPTIMIZER_BASE = `${API_BASE_URL}${API_PREFIX}/optimizer`

export class OptimizerAPI {
  /**
   * 对章节内容进行分层优化
   */
  static async optimizeChapter(optimizeReq: OptimizeRequest): Promise<OptimizeResponse> {
    return request(`${OPTIMIZER_BASE}/optimize`, {
      timeoutMs: null,
      method: 'POST',
      body: JSON.stringify(optimizeReq)
    })
  }

  /**
   * 应用优化后的内容到章节
   */
  static async applyOptimization(
    projectId: string,
    chapterNumber: number,
    optimizedContent: string
  ): Promise<{ status: string; message: string }> {
    const params = new URLSearchParams({
      project_id: projectId,
      chapter_number: chapterNumber.toString(),
      optimized_content: optimizedContent
    })
    return request(`${OPTIMIZER_BASE}/apply-optimization?${params}`, {
      method: 'POST'
    })
  }
}
