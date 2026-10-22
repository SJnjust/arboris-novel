// AIMETA P=小说状态_当前小说数据管理|R=currentNovel_chapters_fetch|NR=不含API调用|E=store:novel|X=internal|A=useNovelStore|D=pinia|S=none|RD=./README.ai
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { NovelProject, NovelProjectSummary, ConverseResponse, ConceptStreamOptions, Blueprint, DeleteNovelsResponse, ChapterOutline, JobSubmitted, RequestOptions } from '@/api/novel'
import { NovelAPI } from '@/api/novel'

export const useNovelStore = defineStore('novel', () => {
  // State
  const projects = ref<NovelProjectSummary[]>([])
  const currentProject = ref<NovelProject | null>(null)
  const currentConversationState = ref<any>({})
  const isLoading = ref(false)
  const error = ref<string | null>(null)
  const pendingChapterEdits = new Map<string, string>()

  // Getters
  const projectsCount = computed(() => projects.value.length)
  const hasCurrentProject = computed(() => currentProject.value !== null)

  // Actions
  async function loadProjects() {
    isLoading.value = true
    error.value = null
    try {
      projects.value = await NovelAPI.getAllNovels()
    } catch (err) {
      error.value = err instanceof Error ? err.message : '加载项目失败'
    } finally {
      isLoading.value = false
    }
  }

  async function createProject(title: string, initialPrompt: string) {
    isLoading.value = true
    error.value = null
    try {
      const project = await NovelAPI.createNovel(title, initialPrompt)
      currentProject.value = project
      currentConversationState.value = {}
      return project
    } catch (err) {
      error.value = err instanceof Error ? err.message : '创建项目失败'
      throw err
    } finally {
      isLoading.value = false
    }
  }

  async function loadProject(projectId: string, silent: boolean = false) {
    if (!silent) {
      isLoading.value = true
    }
    error.value = null
    try {
      currentProject.value = await NovelAPI.getNovel(projectId)
    } catch (err) {
      error.value = err instanceof Error ? err.message : '加载项目失败'
    } finally {
      if (!silent) {
        isLoading.value = false
      }
    }
  }

  async function loadChapter(chapterNumber: number) {
    error.value = null
    try {
      if (!currentProject.value) {
        throw new Error('没有当前项目')
      }
      const chapter = await NovelAPI.getChapter(currentProject.value.id, chapterNumber)
      const project = currentProject.value
      if (!Array.isArray(project.chapters)) {
        project.chapters = []
      }
      const index = project.chapters.findIndex(ch => ch.chapter_number === chapterNumber)
      if (index >= 0) {
        project.chapters.splice(index, 1, chapter)
      } else {
        project.chapters.push(chapter)
      }
      project.chapters.sort((a, b) => a.chapter_number - b.chapter_number)
      return chapter
    } catch (err) {
      error.value = err instanceof Error ? err.message : '加载章节失败'
      throw err
    }
  }

  async function sendConversation(userInput: any, options: Partial<ConceptStreamOptions> = {}): Promise<ConverseResponse> {
    isLoading.value = true
    error.value = null
    try {
      if (!currentProject.value) {
        throw new Error('没有当前项目')
      }
      const projectId = currentProject.value.id
      const response = await NovelAPI.converseConceptStream(
        projectId,
        userInput,
        currentConversationState.value,
        { ...options, clientMsgId: options.clientMsgId || crypto.randomUUID() }
      )
      if (!options.signal?.aborted && currentProject.value?.id === projectId) currentConversationState.value = response.conversation_state
      return response
    } catch (err) {
      error.value = err instanceof Error ? err.message : '对话失败'
      throw err
    } finally {
      isLoading.value = false
    }
  }

  async function generateBlueprint(key?: string, options: RequestOptions & { resume?: boolean } = {}): Promise<JobSubmitted> {
    // Generate blueprint from conversation history
    isLoading.value = true
    error.value = null
    try {
      if (!currentProject.value) {
        throw new Error('没有当前项目')
      }
      return await NovelAPI.generateBlueprint(currentProject.value.id, key, options)
    } catch (err) {
      error.value = err instanceof Error ? err.message : '生成蓝图失败'
      throw err
    } finally {
      isLoading.value = false
    }
  }

  async function saveBlueprint(blueprint: Blueprint) {
    isLoading.value = true
    error.value = null
    try {
      if (!currentProject.value) {
        throw new Error('没有当前项目')
      }
      if (!blueprint) {
        throw new Error('缺少蓝图数据')
      }
      currentProject.value = await NovelAPI.saveBlueprint(currentProject.value.id, blueprint)
    } catch (err) {
      error.value = err instanceof Error ? err.message : '保存蓝图失败'
      throw err
    } finally {
      isLoading.value = false
    }
  }

  async function generateChapter(chapterNumber: number, key: string, options: RequestOptions = {}): Promise<JobSubmitted> {
    error.value = null
    if (!currentProject.value) throw new Error('没有当前项目')
    // 提交只返回任务 ID；任务失败保留工作区，由任务面板显示原因。
    return NovelAPI.generateChapter(currentProject.value.id, chapterNumber, key, options)
  }

  async function evaluateChapter(chapterNumber: number): Promise<NovelProject> {
    error.value = null
    try {
      if (!currentProject.value) {
        throw new Error('没有当前项目')
      }
      const updatedProject = await NovelAPI.evaluateChapter(currentProject.value.id, chapterNumber)
      currentProject.value = updatedProject
      return updatedProject
    } catch (err) {
      error.value = err instanceof Error ? err.message : '评估章节失败'
      throw err
    }
  }

  async function selectChapterVersion(chapterNumber: number, versionIndex: number) {
    // 不设置全局 isLoading，让调用方处理局部加载状态
    error.value = null
    try {
      if (!currentProject.value) {
        throw new Error('没有当前项目')
      }
      const updatedProject = await NovelAPI.selectChapterVersion(
        currentProject.value.id,
        chapterNumber,
        versionIndex
      )
      currentProject.value = updatedProject // 更新 store
    } catch (err) {
      error.value = err instanceof Error ? err.message : '选择章节版本失败'
      throw err
    }
  }

  async function deleteProjects(projectIds: string[]): Promise<DeleteNovelsResponse> {
    isLoading.value = true
    error.value = null
    try {
      const response = await NovelAPI.deleteNovels(projectIds)
      
      // 从本地项目列表中移除已删除的项目
      projects.value = projects.value.filter(project => !projectIds.includes(project.id))
      
      // 如果当前项目被删除，清空当前项目
      if (currentProject.value && projectIds.includes(currentProject.value.id)) {
        currentProject.value = null
        currentConversationState.value = {}
      }
      
      return response
    } catch (err) {
      error.value = err instanceof Error ? err.message : '删除项目失败'
      throw err
    } finally {
      isLoading.value = false
    }
  }

  async function updateChapterOutline(chapterOutline: ChapterOutline) {
    // 不设置全局 isLoading，让调用方处理局部加载状态
    error.value = null
    try {
      if (!currentProject.value) {
        throw new Error('没有当前项目')
      }
      const updatedProject = await NovelAPI.updateChapterOutline(
        currentProject.value.id,
        chapterOutline
      )
      currentProject.value = updatedProject // 更新 store
    } catch (err) {
      error.value = err instanceof Error ? err.message : '更新章节大纲失败'
      throw err
    }
  }

  async function deleteChapter(chapterNumbers: number | number[]) {
    error.value = null
    try {
      if (!currentProject.value) {
        throw new Error('没有当前项目')
      }
      const numbersToDelete = Array.isArray(chapterNumbers) ? chapterNumbers : [chapterNumbers]
      const updatedProject = await NovelAPI.deleteChapter(
        currentProject.value.id,
        numbersToDelete
      )
      currentProject.value = updatedProject // 更新 store
    } catch (err) {
      error.value = err instanceof Error ? err.message : '删除章节失败'
      throw err
    }
  }

  async function generateChapterOutline(startChapter: number, numChapters: number) {
    error.value = null
    try {
      if (!currentProject.value) {
        throw new Error('没有当前项目')
      }
      const updatedProject = await NovelAPI.generateChapterOutline(
        currentProject.value.id,
        startChapter,
        numChapters
      )
      currentProject.value = updatedProject // 更新 store
    } catch (err) {
      error.value = err instanceof Error ? err.message : '生成大纲失败'
      throw err
    }
  }

  async function editChapterContent(projectId: string, chapterNumber: number, content: string) {
    error.value = null
    const requestKey = `${projectId}:${chapterNumber}`
    pendingChapterEdits.set(requestKey, content)
    const project = currentProject.value
    let previousContent: string | null = null
    let previousWordCount: number | undefined
    let versionIndex = -1
    if (project) {
      const chapter = project.chapters.find(ch => ch.chapter_number === chapterNumber)
      if (chapter) {
        previousContent = chapter.content ?? null
        previousWordCount = chapter.word_count
        chapter.content = content
        chapter.generation_status = 'successful'
        chapter.word_count = content.length
        if (Array.isArray(chapter.versions) && previousContent !== null) {
          versionIndex = chapter.versions.findIndex(v => v === previousContent)
          if (versionIndex >= 0) {
            chapter.versions.splice(versionIndex, 1, content)
          }
        }
      }
    }
    try {
      const updatedChapter = await NovelAPI.editChapterContent(projectId, chapterNumber, content)
      if (pendingChapterEdits.get(requestKey) !== content) {
        return
      }
      if (project) {
        const chapters = project.chapters
        const index = chapters.findIndex(ch => ch.chapter_number === chapterNumber)
        if (index >= 0) {
          chapters.splice(index, 1, updatedChapter)
        } else {
          chapters.push(updatedChapter)
          chapters.sort((a, b) => a.chapter_number - b.chapter_number)
        }
      }
      pendingChapterEdits.delete(requestKey)
    } catch (err) {
      if (pendingChapterEdits.get(requestKey) === content) {
        pendingChapterEdits.delete(requestKey)
        if (project) {
          const chapter = project.chapters.find(ch => ch.chapter_number === chapterNumber)
          if (chapter) {
            chapter.content = previousContent
            chapter.word_count = previousWordCount
            if (Array.isArray(chapter.versions) && versionIndex >= 0 && previousContent !== null) {
              chapter.versions.splice(versionIndex, 1, previousContent)
            }
          }
        }
      }
      error.value = err instanceof Error ? err.message : '编辑章节内容失败'
      throw err
    }
  }

  function clearError() {
    error.value = null
  }

  function setCurrentProject(project: NovelProject | null) {
    currentProject.value = project
  }

  return {
    // State
    projects,
    currentProject,
    currentConversationState,
    isLoading,
    error,
    // Getters
    projectsCount,
    hasCurrentProject,
    // Actions
    loadProjects,
    createProject,
    loadProject,
    loadChapter,
    sendConversation,
    generateBlueprint,
    saveBlueprint,
    generateChapter,
    evaluateChapter,
    selectChapterVersion,
    deleteProjects,
    updateChapterOutline,
    deleteChapter,
    generateChapterOutline,
    editChapterContent,
    clearError,
    setCurrentProject
  }
})
