<!-- AIMETA P=LLM设置_模型配置界面|R=LLM配置表单|NR=不含模型调用|E=component:LLMSettings|X=internal|A=设置组件|D=vue|S=dom,net|RD=./README.ai -->
<template>
  <div class="bg-white/70 backdrop-blur-xl rounded-2xl shadow-lg p-8">
    <h2 class="text-2xl font-bold text-gray-800 mb-6">LLM 配置</h2>
    <h5 class="text-1xl font-bold text-gray-800 mb-6">建议使用自己的中转API和KEY</h5>
    <form @submit.prevent="handleSave" class="space-y-6">
      <div>
        <label for="url" class="block text-sm font-medium text-gray-700">API URL</label>
        <div class="relative mt-1">
          <input
            type="text"
            id="url"
            v-model="config.llm_provider_url"
            class="block w-full px-3 py-2 pr-10 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
            placeholder="https://api.example.com/v1"
          >
          <button
            type="button"
            @click="clearApiUrl"
            class="absolute inset-y-0 right-2 flex items-center px-2 text-gray-400 hover:text-gray-600"
            aria-label="清空 API URL"
          >
            <svg class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
            </svg>
          </button>
        </div>
      </div>
      <div>
        <label for="key" class="block text-sm font-medium text-gray-700">API Key</label>
        <div class="relative mt-1">
          <input
            :type="showApiKey ? 'text' : 'password'"
            id="key"
            v-model="config.llm_provider_api_key"
            class="block w-full px-3 py-2 pr-24 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
            placeholder="留空则使用默认Key"
          >
          <button
            type="button"
            @click="clearApiKey"
            class="absolute inset-y-0 right-2 flex items-center px-2 text-gray-400 hover:text-gray-600"
            aria-label="清空 API Key"
          >
            <svg class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
            </svg>
          </button>
          <button
            type="button"
            @click="toggleApiKeyVisibility"
            class="absolute inset-y-0 right-10 flex items-center px-2 text-gray-400 hover:text-gray-600"
            :aria-label="showApiKey ? '隐藏 API Key' : '显示 API Key'"
          >
            <svg v-if="showApiKey" class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10 5c-4.478 0-8.268 2.943-9.542 7C1.732 16.057 5.522 19 10 19s8.268-2.943 9.542-7C18.268 7.943 14.478 5 10 5zm0 10a5 5 0 110-10 5 5 0 010 10z" fill-opacity="0.2" />
              <path d="M10 7a3 3 0 100 6 3 3 0 000-6z" />
            </svg>
            <svg v-else class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zm13.707 0a4.167 4.167 0 11-8.334 0 4.167 4.167 0 018.334 0z" clip-rule="evenodd" />
              <path d="M10 8a2 2 0 100 4 2 2 0 000-4z" />
            </svg>
          </button>
        </div>
      </div>
      <div>
        <label for="model" class="block text-sm font-medium text-gray-700">Model</label>
        <div class="flex gap-2 mt-1">
          <div class="relative flex-1">
            <input
              type="text"
              id="model"
              v-model="config.llm_provider_model"
              @focus="showModelDropdown = true"
              @blur="hideDropdown"
              class="block w-full px-3 py-2 pr-10 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
              placeholder="留空则使用默认模型"
            >
            <button
              type="button"
              @click="clearApiModel"
              class="absolute inset-y-0 right-2 flex items-center px-2 text-gray-400 hover:text-gray-600"
              aria-label="清空模型名称"
            >
              <svg class="w-5 h-5" viewBox="0 0 20 20" fill="currentColor">
                <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
              </svg>
            </button>
            <!-- 下拉选择提示框 -->
            <div
              v-if="showModelDropdown && availableModels.length > 0"
              class="absolute z-10 w-full mt-1 bg-white border border-gray-300 rounded-md shadow-lg max-h-60 overflow-auto"
            >
              <div
                v-for="model in filteredModels"
                :key="model"
                @mousedown="selectModel(model)"
                class="px-3 py-2 cursor-pointer hover:bg-indigo-50 hover:text-indigo-600 text-sm"
              >
                {{ model }}
              </div>
              <div v-if="filteredModels.length === 0" class="px-3 py-2 text-sm text-gray-500">
                无匹配的模型
              </div>
            </div>
          </div>
          <button
            type="button"
            @click="loadModels"
            :disabled="isLoadingModels"
            class="px-4 py-2 bg-gray-600 text-white rounded-md hover:bg-gray-700 transition-colors disabled:bg-gray-400 disabled:cursor-not-allowed flex items-center gap-2"
          >
            <svg v-if="isLoadingModels" class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span>{{ isLoadingModels ? '加载中...' : '获取模型' }}</span>
          </button>
        </div>
      </div>
      <fieldset class="border rounded-xl p-4 space-y-4">
        <legend class="px-2 font-medium">推理与结构化生成</legend>
        <label class="block text-sm">主模型推理强度
          <select id="reasoning-effort" v-model="config.reasoning_effort" class="block w-full border rounded-md p-2 mt-1">
            <option value="">留空（不发送推理强度参数）</option>
            <option v-for="effort in efforts" :key="effort" :value="effort">{{ effort }}</option>
          </select>
        </label>
        <label class="block text-sm">结构化生成模型（可选）
          <input id="structured-model" v-model="config.structured_model" class="block w-full border rounded-md p-2 mt-1" placeholder="留空使用主模型" />
        </label>
        <label class="block text-sm">结构化生成推理强度（可选）
          <select id="structured-effort" v-model="config.structured_reasoning_effort" class="block w-full border rounded-md p-2 mt-1">
            <option value="">留空（使用主推理强度）</option>
            <option v-for="effort in efforts" :key="effort" :value="effort">{{ effort }}</option>
          </select>
        </label>
        <p class="text-sm text-gray-600">蓝图、分卷和大纲等结构化任务建议使用 low 或 medium；xhigh 可能长时间推理，容易超时。中转需支持 reasoning_effort，具体可选值取决于模型。保存个人配置后以个人字段为准；没有个人配置时使用全局设置。</p>
      </fieldset>
      <div class="flex justify-end space-x-4 pt-4">
        <button type="button" @click="handleDelete" class="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors">删除配置</button>
        <button type="submit" class="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors">保存</button>
      </div>
    </form>
  </div>
</template>

<script setup lang="ts">
import { globalAlert } from '@/composables/useAlert'
import { ref, onMounted, computed } from 'vue';
import { getLLMConfig, createOrUpdateLLMConfig, deleteLLMConfig, getAvailableModels, type LLMConfigCreate } from '@/api/llm';

const config = ref<LLMConfigCreate>({
  llm_provider_url: '',
  llm_provider_api_key: '',
  llm_provider_model: '',
  reasoning_effort: '',
  structured_model: '',
  structured_reasoning_effort: '',
});

const efforts = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'];
const showApiKey = ref(false);
const availableModels = ref<string[]>([]);
const isLoadingModels = ref(false);
const showModelDropdown = ref(false);

// 根据输入过滤模型列表
const filteredModels = computed(() => {
  if (!config.value.llm_provider_model) {
    return availableModels.value;
  }
  const searchTerm = config.value.llm_provider_model.toLowerCase();
  return availableModels.value.filter(model =>
    model.toLowerCase().includes(searchTerm)
  );
});

onMounted(async () => {
  const existingConfig = await getLLMConfig();
  if (existingConfig) {
    config.value = {
      llm_provider_url: existingConfig.llm_provider_url || '',
      llm_provider_api_key: existingConfig.llm_provider_api_key || '',
      llm_provider_model: existingConfig.llm_provider_model || '',
      reasoning_effort: existingConfig.reasoning_effort || '',
      structured_model: existingConfig.structured_model || '',
      structured_reasoning_effort: existingConfig.structured_reasoning_effort || '',
    };
  }
});

const handleSave = async () => {
  try {
    await createOrUpdateLLMConfig(config.value);
    globalAlert.showSuccess('设置已保存！');
  } catch (error) { globalAlert.showError(error, '保存设置失败'); }
};

const handleDelete = async () => {
  if (await globalAlert.showConfirm('确定要删除您的自定义LLM配置吗？删除后将恢复为默认配置。', '删除配置确认')) {
    try {
    await deleteLLMConfig();
    config.value = {
      llm_provider_url: '',
      llm_provider_api_key: '',
      llm_provider_model: '',
  reasoning_effort: '',
  structured_model: '',
  structured_reasoning_effort: '',
    };
    globalAlert.showSuccess('配置已删除！');
    } catch (error) { globalAlert.showError(error, '删除设置失败'); }
  }
};

const toggleApiKeyVisibility = () => {
  showApiKey.value = !showApiKey.value;
};

const clearApiKey = () => {
  config.value.llm_provider_api_key = '';
};

const clearApiUrl = () => {
  config.value.llm_provider_url = '';
};

const clearApiModel = () => {
  config.value.llm_provider_model = '';
};

const loadModels = async () => {
  // 验证表单
  if (!config.value.llm_provider_api_key) {
    globalAlert.showError('请先填写 API Key');
    return;
  }

  isLoadingModels.value = true;
  try {
    const models = await getAvailableModels({
      llm_provider_api_key: config.value.llm_provider_api_key,
      llm_provider_url: config.value.llm_provider_url || undefined,
    });
    availableModels.value = models;
    if (models.length > 0) {
      showModelDropdown.value = true;
    } else {
      globalAlert.showError('未获取到模型列表，请检查API配置是否正确');
    }
  } catch (error) {
    console.error('Failed to load models:', error);
    globalAlert.showError('获取模型列表失败，请检查网络连接和API配置');
  } finally {
    isLoadingModels.value = false;
  }
};

const selectModel = (model: string) => {
  config.value.llm_provider_model = model;
  showModelDropdown.value = false;
};

const hideDropdown = () => {
  // 延迟隐藏，确保点击事件能触发
  setTimeout(() => {
    showModelDropdown.value = false;
  }, 200);
};
</script>
