<!-- AIMETA P=蓝图展示_蓝图详细信息|R=蓝图详情展示|NR=不含编辑功能|E=component:BlueprintDisplay|X=internal|A=展示组件|D=vue|S=dom|RD=./README.ai -->
<template>
  <div class="p-8 bg-white rounded-2xl shadow-2xl fade-in">
    <h2 class="text-3xl font-bold text-center text-gray-800 mb-6">你的故事蓝图已生成！</h2>

    <!-- AI消息 -->
    <div v-if="aiMessage" class="mb-6 p-4 bg-blue-50 rounded-lg border border-blue-200">
      <p class="text-blue-800">{{ aiMessage }}</p>
    </div>

    <div class="prose max-w-none p-6 bg-gray-50 rounded-lg border border-gray-200" v-html="formattedBlueprint"></div>

    <VolumePlanning v-if="blueprint" :blueprint="blueprint" :project-id="novelStore.currentProject?.id" :chapters="novelStore.currentProject?.chapters" editable @updated="volumeUpdated" />

    <!-- 加载状态 -->
    <div v-if="isSaving" class="text-center py-8">
      <!-- 保存动画 -->
      <div class="relative mx-auto mb-6 w-16 h-16">
        <!-- 旋转圆环 -->
        <div class="absolute inset-0 border-4 border-green-100 rounded-full"></div>
        <div class="absolute inset-0 border-4 border-transparent border-t-green-500 rounded-full animate-spin"></div>
        <!-- 中心保存图标 -->
        <div class="absolute inset-2 bg-green-500 rounded-full flex items-center justify-center">
          <svg class="w-6 h-6 text-white animate-pulse" fill="currentColor" viewBox="0 0 20 20">
            <path d="M7.707 10.293a1 1 0 10-1.414 1.414l3 3a1 1 0 001.414 0l3-3a1 1 0 00-1.414-1.414L11 11.586V6a1 1 0 10-2 0v5.586l-1.293-1.293z"></path>
            <path d="M5 4a2 2 0 012-2h6a2 2 0 012 2v1a1 1 0 11-2 0V4H7v1a1 1 0 11-2 0V4z"></path>
          </svg>
        </div>
      </div>

      <h3 class="text-lg font-semibold text-gray-800 mb-2 animate-pulse">正在保存蓝图...</h3>
      <p class="text-gray-600">即将跳转到写作工作台，开始您的创作之旅</p>

      <!-- 保存进度指示 -->
      <div class="mt-4 w-32 mx-auto">
        <div class="w-full bg-gray-200 rounded-full h-1">
          <div class="h-1 bg-gradient-to-r from-green-400 to-green-600 rounded-full animate-pulse" style="width: 100%"></div>
        </div>
      </div>
    </div>

    <div v-else class="text-center mt-8 space-x-4">
      <button
        @click="confirmRegenerate"
        class="bg-gray-200 text-gray-700 font-bold py-3 px-8 rounded-full hover:bg-gray-300 transition-all duration-300 transform hover:scale-105"
      >
        <span class="flex items-center justify-center">
          <svg class="w-5 h-5 mr-2" fill="currentColor" viewBox="0 0 20 20">
            <path fill-rule="evenodd" d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.601 2.566 1 1 0 11-1.885.666A5.002 5.002 0 005.999 7H9a1 1 0 010 2H4a1 1 0 01-1-1V3a1 1 0 011-1zm.008 9.057a1 1 0 011.276.61A5.002 5.002 0 0014.001 13H11a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0v-2.101a7.002 7.002 0 01-11.601-2.566 1 1 0 01.61-1.276z" clip-rule="evenodd"></path>
          </svg>
          重新生成
        </span>
      </button>
      <button
        @click="confirmBlueprint"
        :disabled="isSaving"
        class="bg-gradient-to-r from-green-500 to-emerald-600 text-white font-bold py-3 px-8 rounded-full hover:from-green-600 hover:to-emerald-700 transition-all duration-300 transform hover:scale-105 shadow-lg hover:shadow-xl disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none"
      >
        <span class="flex items-center justify-center">
          <svg class="w-5 h-5 mr-2" fill="currentColor" viewBox="0 0 20 20">
            <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"></path>
          </svg>
          确认并开始创作
        </span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { globalAlert } from '@/composables/useAlert'
import type { Blueprint, NovelProject } from '@/api/novel'
import VolumePlanning from '@/components/VolumePlanning.vue'
import { useNovelStore } from '@/stores/novel'

interface DisplayField {
  label: string;
  value: any;
  priority: number;
}

type ExtractedFields = Record<string, DisplayField>;

interface Props {
  blueprint: Blueprint | null
  aiMessage?: string
}

const props = defineProps<Props>()

const emit = defineEmits<{
  confirm: []
  regenerate: []
  'update:blueprint': [blueprint: Blueprint]
}>()

const novelStore = useNovelStore()
const volumeUpdated = (project: NovelProject) => {
  novelStore.setCurrentProject(project)
  if (project.blueprint) emit('update:blueprint', project.blueprint)
}
const isSaving = ref(false)

const confirmRegenerate = async () => {
  const confirmed = await globalAlert.showConfirm('重新规划会更新设定及未写正文的大纲，已有正文对应的大纲会保留。确定继续吗？', '重新生成确认')
  if (confirmed) {
    emit('regenerate')
  }
}

const confirmBlueprint = async () => {
  isSaving.value = true
  try {
    await emit('confirm')
  } finally {
    isSaving.value = false
  }
}

const formattedBlueprint = computed(() => {
  if (!props.blueprint) {
    return '<p class="text-center text-red-500">抱歉，生成大纲失败，未能获取到最终数据。</p>'
  }

  const blueprint = props.blueprint

  // Helper function to safely access nested properties
  const safe = (value: any, fallback = '待补充') => value || fallback

  // Create section with icon and styling
  const createSection = (title: string, content: string, icon: string) => `
    <div class="mb-8 bg-white rounded-xl border border-gray-200 p-6 shadow-sm hover:shadow-md transition-shadow duration-300">
      <div class="flex items-center mb-4">
        <div class="w-8 h-8 bg-indigo-100 rounded-lg flex items-center justify-center mr-3">
          ${icon}
        </div>
        <h3 class="text-xl font-bold text-gray-800">${title}</h3>
      </div>
      <div class="prose max-w-none text-gray-700">
        ${content}
      </div>
    </div>
  `

  // Icons
  const icons = {
    summary: '<svg class="w-5 h-5 text-indigo-600" fill="currentColor" viewBox="0 0 20 20"><path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>',
    story: '<svg class="w-5 h-5 text-indigo-600" fill="currentColor" viewBox="0 0 20 20"><path d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"></path></svg>',
    world: '<svg class="w-5 h-5 text-indigo-600" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM4.332 8.027a6.012 6.012 0 011.912-2.706C6.512 5.73 6.974 6 7.5 6A1.5 1.5 0 019 7.5V8a2 2 0 004 0 2 2 0 011.523-1.943A5.977 5.977 0 0116 10c0 .34-.028.675-.083 1H15a2 2 0 00-2 2v2.197A5.973 5.973 0 0110 16v-2a2 2 0 00-2-2 2 2 0 01-2-2 2 2 0 00-1.668-1.973z" clip-rule="evenodd"></path></svg>',
    characters: '<svg class="w-5 h-5 text-indigo-600" fill="currentColor" viewBox="0 0 20 20"><path d="M9 6a3 3 0 11-6 0 3 3 0 016 0zM17 6a3 3 0 11-6 0 3 3 0 016 0zM12.93 17c.046-.327.07-.66.07-1a6.97 6.97 0 00-1.5-4.33A5 5 0 0119 16v1h-6.07zM6 11a5 5 0 015 5v1H1v-1a5 5 0 015-5z"></path></svg>',
    relationships: '<svg class="w-5 h-5 text-indigo-600" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M3.172 5.172a4 4 0 015.656 0L10 6.343l1.172-1.171a4 4 0 115.656 5.656L10 17.657l-6.828-6.829a4 4 0 010-5.656z" clip-rule="evenodd"></path></svg>',
    chapters: '<svg class="w-5 h-5 text-indigo-600" fill="currentColor" viewBox="0 0 20 20"><path d="M4 4a2 2 0 00-2 2v1h16V6a2 2 0 00-2-2H4zM18 9H2v5a2 2 0 002 2h12a2 2 0 002-2V9zM4 13a1 1 0 011-1h1a1 1 0 110 2H5a1 1 0 01-1-1zm5-1a1 1 0 100 2h1a1 1 0 100-2H9z"></path></svg>'
  }

  // Format characters with enhanced styling - 动态兼容所有字段
  const formatCharacters = (characters: any[]) => {
    if (!characters || characters.length === 0) return '<p class="text-gray-500 italic">暂无角色信息</p>'

    return characters.map(char => {
      if (typeof char === 'object' && char.name) {
        const name = char.name

        // 定义字段映射和图标，支持多种可能的key名称
        const fieldMappings = {
          identity: {
            keys: ['identity_background', 'identity', 'background', '身份背景', '身份'],
            label: '🎭 身份背景',
            priority: 1
          },
          personality: {
            keys: ['personality_traits', 'personality', 'traits', 'character', '性格特质', '性格'],
            label: '🎨 性格特质',
            priority: 2
          },
          goal: {
            keys: ['core_goal', 'goal', 'objectives', 'aims', '核心目标', '目标'],
            label: '🎯 核心目标',
            priority: 3
          },
          abilities: {
            keys: ['abilities_skills', 'abilities', 'skills', 'powers', '能力技能', '能力', '技能'],
            label: '⚡ 能力技能',
            priority: 4
          },
          relationship: {
            keys: ['relationship_with_protagonist', 'relationship_to_protagonist', 'relationship', 'relation', '与主角关系', '关系'],
            label: '🤝 与主角关系',
            priority: 5
          },
          role: {
            keys: ['role', 'character_role', 'story_role', '角色定位', '角色'],
            label: '👤 角色定位',
            priority: 0
          }
        }

        // 提取所有字段
        const extractedFields: ExtractedFields = {}
        const usedKeys = new Set(['name']) // 已使用的key

        // 按优先级提取已知字段
        Object.entries(fieldMappings).forEach(([fieldType, mapping]) => {
          for (const key of mapping.keys) {
            if (char[key] && !usedKeys.has(key)) {
              extractedFields[fieldType] = {
                value: char[key],
                label: mapping.label,
                priority: mapping.priority
              }
              usedKeys.add(key)
              break
            }
          }
        })

        // 提取剩余的未知字段
        Object.entries(char).forEach(([key, value]) => {
          if (!usedKeys.has(key) && value && typeof value === 'string' && value.trim()) {
            // 为未知字段生成友好的标签
            const friendlyLabel = key
              .replace(/_/g, ' ')
              .replace(/([A-Z])/g, ' $1')
              .replace(/^./, str => str.toUpperCase())

            extractedFields[`unknown_${key}`] = {
              value: value,
              label: `📝 ${friendlyLabel}`,
              priority: 99
            }
            usedKeys.add(key)
          }
        })

        // 按优先级排序字段
        const sortedFields = Object.entries(extractedFields).sort(([,a], [,b]) => a.priority - b.priority)

        // 生成HTML
        let fieldsHTML = ''
        sortedFields.forEach(([fieldType, field]) => {
          if (fieldType === 'role') {
            // role字段显示为标签，不在详细信息中
            return
          }

          fieldsHTML += `
            <div class="bg-white/70 rounded-lg p-3">
              <span class="font-medium text-gray-700 block mb-1">${field.label}：</span>
              <span class="text-gray-800">${field.value}</span>
            </div>
          `
        })

        const roleField = extractedFields.role

        return `
          <div class="bg-gradient-to-r from-blue-50 to-indigo-50 border-l-4 border-indigo-400 rounded-lg p-5 mb-4">
            <div class="flex items-center justify-between mb-3">
              <h4 class="text-lg font-bold text-indigo-800 flex items-center">
                <span class="w-2 h-2 bg-indigo-500 rounded-full mr-2"></span>
                ${name}
              </h4>
              ${roleField ? `<span class="bg-indigo-100 text-indigo-700 px-2 py-1 rounded-full text-xs font-medium">${roleField.value}</span>` : ''}
            </div>
            <div class="space-y-3 text-sm">
              ${fieldsHTML}
            </div>
          </div>
        `
      }
      // 处理简单的角色结构 (向后兼容)
      else if (typeof char === 'object' && char.description) {
        const desc = char.description
        const identity = desc.identity || ''
        const personality = desc.personality || ''
        const relationship = desc.relationship_to_protagonist || ''

        return `
          <div class="bg-gradient-to-r from-blue-50 to-indigo-50 border-l-4 border-indigo-400 rounded-lg p-5 mb-4">
            <h4 class="text-lg font-bold text-indigo-800 mb-3 flex items-center">
              <span class="w-2 h-2 bg-indigo-500 rounded-full mr-2"></span>
              ${char.name}
            </h4>
            <div class="space-y-2 text-sm">
              ${identity ? `<div class="flex items-start"><span class="font-medium text-gray-600 min-w-16">身份：</span><span class="text-gray-800">${identity}</span></div>` : ''}
              ${personality ? `<div class="flex items-start"><span class="font-medium text-gray-600 min-w-16">性格：</span><span class="text-gray-800">${personality}</span></div>` : ''}
              ${relationship ? `<div class="flex items-start"><span class="font-medium text-gray-600 min-w-16">关系：</span><span class="text-gray-800">${relationship}</span></div>` : ''}
            </div>
          </div>
        `
      }
      // 处理最简单的结构
      else {
        return `
          <div class="bg-gray-50 border-l-4 border-gray-300 rounded-lg p-4 mb-3">
            <h4 class="font-semibold text-gray-800">${char.name || '未知角色'}</h4>
            <p class="text-gray-600 text-sm mt-1">${char.description || '无描述'}</p>
          </div>
        `
      }
    }).join('')
  }

  // Format world setting with enhanced styling
  const formatWorldSetting = (worldSetting: any) => {
    if (!worldSetting || typeof worldSetting !== 'object') return '<p class="text-gray-500 italic">暂无世界设定信息</p>'

    let html = ''

    if (worldSetting.core_rules) {
      html += `
        <div class="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-4">
          <h4 class="font-semibold text-amber-800 mb-2 flex items-center">
            <svg class="w-4 h-4 mr-2" fill="currentColor" viewBox="0 0 20 20"><path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"></path></svg>
            核心设定
          </h4>
          <p class="text-amber-700">${worldSetting.core_rules}</p>
        </div>
      `
    }

    if (worldSetting.key_locations && worldSetting.key_locations.length > 0) {
      html += `
        <div class="mb-4">
          <h4 class="font-semibold text-gray-800 mb-3 flex items-center">
            <svg class="w-4 h-4 mr-2 text-teal-600" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M5.05 4.05a7 7 0 119.9 9.9L10 18.9l-4.95-4.95a7 7 0 010-9.9zM10 11a2 2 0 100-4 2 2 0 000 4z" clip-rule="evenodd"></path></svg>
            关键地点
          </h4>
          <div class="grid gap-3">
            ${worldSetting.key_locations.map((loc: any) => `
              <div class="bg-teal-50 border-l-3 border-teal-400 p-3 rounded-r-lg">
                <h5 class="font-medium text-teal-800">${loc.name}</h5>
                <p class="text-teal-700 text-sm mt-1">${loc.description}</p>
              </div>
            `).join('')}
          </div>
        </div>
      `
    }

    if (worldSetting.factions && worldSetting.factions.length > 0) {
      html += `
        <div>
          <h4 class="font-semibold text-gray-800 mb-3 flex items-center">
            <svg class="w-4 h-4 mr-2 text-purple-600" fill="currentColor" viewBox="0 0 20 20"><path d="M9 6a3 3 0 11-6 0 3 3 0 016 0zM17 6a3 3 0 11-6 0 3 3 0 016 0zM12.93 17c.046-.327.07-.66.07-1a6.97 6.97 0 00-1.5-4.33A5 5 0 0119 16v1h-6.07zM6 11a5 5 0 015 5v1H1v-1a5 5 0 015-5z"></path></svg>
            主要势力
          </h4>
          <div class="grid gap-3">
            ${worldSetting.factions.map((fac: any) => `
              <div class="bg-purple-50 border-l-3 border-purple-400 p-3 rounded-r-lg">
                <h5 class="font-medium text-purple-800">${fac.name}</h5>
                <p class="text-purple-700 text-sm mt-1">${fac.description}</p>
              </div>
            `).join('')}
          </div>
        </div>
      `
    }

    return html || '<p class="text-gray-500 italic">暂无世界设定详细信息</p>'
  }

  // Format relationships with enhanced styling - 支持新的数据结构
  const formatRelationships = (relationships: any[]) => {
    if (!relationships || relationships.length === 0) return '<p class="text-gray-500 italic">暂无关系设定</p>'

    return `
      <div class="space-y-3">
        ${relationships.map(rel => {
          // 支持新的字段名：character_from, character_to 以及旧的 source, target
          const fromChar = rel.character_from || rel.source || '角色A'
          const toChar = rel.character_to || rel.target || '角色B'
          const description = rel.description || '暂无描述'

          return `
            <div class="bg-rose-50 border border-rose-200 rounded-lg p-4">
              <div class="flex items-center justify-between mb-2">
                <div class="flex items-center">
                  <span class="font-medium text-rose-800 bg-white px-3 py-1 rounded-full text-sm shadow-sm">${fromChar}</span>
                  <svg class="w-5 h-5 mx-3 text-rose-400" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M12.293 5.293a1 1 0 011.414 0l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414-1.414L14.586 11H3a1 1 0 110-2h11.586l-2.293-2.293a1 1 0 010-1.414z" clip-rule="evenodd"></path>
                  </svg>
                  <span class="font-medium text-rose-800 bg-white px-3 py-1 rounded-full text-sm shadow-sm">${toChar}</span>
                </div>
              </div>
              <div class="text-sm text-rose-700 bg-white/50 rounded-lg p-3">
                <span class="font-medium">关系描述：</span>${description}
              </div>
            </div>
          `
        }).join('')}
      </div>
    `
  }

  // Header with title and badges
  const headerHTML = `
    <div class="text-center mb-8 p-6 bg-gradient-to-r from-indigo-500 to-purple-600 rounded-xl text-white">
      <h1 class="text-4xl font-bold mb-4">${safe(blueprint.title, '未知标题')}</h1>
      <div class="flex flex-wrap justify-center gap-3 mb-4">
        <span class="bg-white/20 backdrop-blur-sm px-4 py-2 rounded-full text-sm font-medium">${safe(blueprint.genre, '未指定')}</span>
        <span class="bg-white/20 backdrop-blur-sm px-4 py-2 rounded-full text-sm font-medium">${safe(blueprint.style, '未指定')}</span>
        <span class="bg-white/20 backdrop-blur-sm px-4 py-2 rounded-full text-sm font-medium">${safe(blueprint.tone, '未指定')}</span>
        <span class="bg-white/20 backdrop-blur-sm px-4 py-2 rounded-full text-sm font-medium">${safe(blueprint.target_audience, '未指定')}</span>
      </div>
    </div>
  `

  // Summary section
  const summaryHTML = createSection(
    '故事梗概',
    `
    <div class="bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-200 rounded-lg p-5 mb-4">
      <h4 class="font-semibold text-blue-800 mb-2">一句话总结</h4>
      <p class="text-lg italic text-blue-700">"${safe(blueprint.one_sentence_summary)}"</p>
    </div>
    <div class="prose max-w-none">
      <h4 class="font-semibold text-gray-800 mb-3">完整简介</h4>
      <p class="text-gray-700 leading-relaxed">${safe(blueprint.full_synopsis)}</p>
    </div>
    `,
    icons.summary
  )

  // Chapters section with enhanced styling
  const chaptersHTML = `
    <div class="space-y-4">
      ${(blueprint.chapter_outline || []).map((ch, index) => `
        <div class="group relative overflow-hidden bg-gradient-to-r from-gray-50 to-white border border-gray-200 rounded-lg p-5 hover:shadow-md transition-all duration-300">
          <div class="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-indigo-500 to-purple-600 transform origin-top group-hover:scale-y-110 transition-transform duration-300"></div>
          <div class="flex items-start">
            <div class="flex-shrink-0 w-10 h-10 bg-indigo-100 rounded-lg flex items-center justify-center mr-4">
              <span class="text-indigo-600 font-bold text-sm">${ch.chapter_number}</span>
            </div>
            <div class="flex-1">
              <h4 class="text-lg font-bold text-gray-800 mb-2 group-hover:text-indigo-600 transition-colors duration-300">第 ${ch.chapter_number} 章: ${ch.title}</h4>
              <p class="text-gray-600 leading-relaxed">${ch.summary}</p>
            </div>
          </div>
        </div>
      `).join('')}
    </div>
  `

  return `
    ${headerHTML}
    ${summaryHTML}
    ${createSection('世界设定', formatWorldSetting(blueprint.world_setting), icons.world)}
    ${createSection('主要角色', formatCharacters(blueprint.characters || []), icons.characters)}
    ${createSection('角色关系', formatRelationships(blueprint.relationships || []), icons.relationships)}
    ${createSection('章节大纲', chaptersHTML, icons.chapters)}
  `
})
</script>
