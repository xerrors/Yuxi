<template>
  <div class="agent-config-sidebar" :class="{ open: isOpen }">
    <!-- 侧边栏头部 -->
    <div class="sidebar-header">
      <div class="header-center">
        <a-segmented v-model:value="activeTab" :options="segmentedOptions" />
      </div>
      <a-button type="text" size="small" @click="closeSidebar" class="close-btn">
        <X :size="16" />
      </a-button>
    </div>

    <!-- 侧边栏内容 -->
    <div class="sidebar-content">
      <div class="agent-info" v-if="selectedAgent">
        <div class="agent-basic-info">
          <p class="agent-description">{{ selectedAgent.description }}</p>
        </div>

        <!-- <a-divider /> -->

        <div v-if="selectedAgentId && configurableItems" class="config-form-content">
          <!-- 配置表单 -->
          <a-form :model="agentConfig" layout="vertical" class="config-form">
            <a-alert
              v-if="isEmptyConfig"
              type="warning"
              message="该智能体没有配置项"
              show-icon
              class="config-alert"
            />
            <a-alert
              v-if="!selectedAgent.has_checkpointer"
              type="error"
              message="该智能体没有配置 Checkpointer，功能无法正常使用"
              show-icon
              class="config-alert"
            />

            <!-- 统一显示所有配置项 -->
            <template v-for="(value, key) in configurableItems" :key="key">
              <a-form-item
                v-if="shouldShowConfig(key, value)"
                :label="getConfigLabel(key, value)"
                :name="key"
                class="config-item"
              >
                <p v-if="value.description" class="config-description">{{ value.description }}</p>

                <!-- <div>{{ value }}</div> -->
                <!-- 模型选择 -->
                <div v-if="value.template_metadata.kind === 'llm'" class="model-selector">
                  <ModelSelectorComponent
                    @select-model="(spec) => handleModelChange(key, spec)"
                    :model_spec="agentConfig[key] || ''"
                  />
                </div>

                <!-- 系统提示词 -->
                <div
                  v-else-if="value.template_metadata.kind === 'prompt'"
                  class="system-prompt-container"
                >
                  <div class="system-prompt-display" @click="openSystemPromptModal(key)">
                    <div
                      class="system-prompt-content"
                      :class="{ 'is-placeholder': !agentConfig[key] }"
                    >
                      {{ agentConfig[key] || getPlaceholder(key, value) }}
                    </div>
                    <div class="edit-hint">点击查看并编辑</div>
                  </div>
                </div>

                <!-- 工具选择 -->
                <!-- <div v-else-if="value.template_metadata.kind === 'tools'" class="tools-selector">
                  <div class="tools-summary">
                    <div class="tools-summary-info">
                      <span class="tools-count">已选择 {{ getSelectedCount(key) }} 个工具</span>
                      <a-button
                        type="link"
                        size="small"
                        @click="clearSelection(key)"
                        v-if="getSelectedCount(key) > 0"
                        class="clear-btn"
                      >
                        清空
                      </a-button>
                    </div>
                    <a-button
                      type="primary"
                      @click="openToolsModal"
                      class="select-tools-btn"
                      size="small"
                    >
                      选择工具
                    </a-button>
                  </div>
                  <div v-if="getSelectedCount(key) > 0" class="selected-tools-preview">
                    <a-tag
                      v-for="toolId in agentConfig[key]"
                      :key="toolId"
                      closable
                      @close="removeSelectedTool(toolId)"
                      class="tool-tag"
                    >
                      {{ getToolNameById(toolId) }}
                    </a-tag>
                  </div>
                </div> -->

                <!-- 布尔类型 -->
                <a-switch
                  v-else-if="typeof agentConfig[key] === 'boolean'"
                  :checked="agentConfig[key]"
                  @update:checked="(val) => agentStore.updateAgentConfig({ [key]: val })"
                />

                <!-- 单选 -->
                <a-select
                  v-else-if="
                    value?.options.length > 0 && (value?.type === 'str' || value?.type === 'select')
                  "
                  :value="agentConfig[key]"
                  @update:value="(val) => agentStore.updateAgentConfig({ [key]: val })"
                  class="config-select"
                >
                  <a-select-option v-for="option in value.options" :key="option" :value="option">
                    {{ option.label || option }}
                  </a-select-option>
                </a-select>

                <!-- 多选 / 工具列表 (统一处理) -->
                <div v-else-if="isListConfig(key, value)" class="list-config-container">
                  <!-- Case 1: <= 5 options, inline list -->
                  <div v-if="getConfigOptions(value).length <= 5" class="multi-select-cards">
                    <div class="multi-select-label">
                      <span>已选择 {{ getSelectedCount(key) }} 项</span>
                      <div class="label-actions">
                        <a-button
                          type="link"
                          size="small"
                          class="clear-btn"
                          @click="clearSelection(key)"
                          v-if="getSelectedCount(key) > 0"
                        >
                          清空
                        </a-button>
                        <template v-if="isToolsKind(value.template_metadata?.kind)">
                          <a-divider type="vertical" />
                          <a-button
                            type="link"
                            size="small"
                            @click="refreshConfigOptions(key, value.template_metadata.kind)"
                            class="action-btn"
                          >
                            <RotateCw :size="12" />
                            刷新
                          </a-button>
                          <a-button
                            type="link"
                            size="small"
                            @click="navigateToConfigPage(value.template_metadata.kind)"
                            class="action-btn"
                          >
                            <Settings :size="12" />
                            配置
                          </a-button>
                        </template>
                      </div>
                    </div>

                    <div class="options-grid">
                      <div
                        v-for="option in getConfigOptions(value)"
                        :key="getOptionValue(option)"
                        class="option-card"
                        :class="{
                          selected: isOptionSelected(key, getOptionValue(option)),
                          unselected: !isOptionSelected(key, getOptionValue(option))
                        }"
                        @click="toggleOption(key, getOptionValue(option))"
                      >
                        <div class="option-content">
                          <span class="option-text">{{ getOptionLabel(option) }}</span>
                          <div class="option-indicator">
                            <Check
                              v-if="isOptionSelected(key, getOptionValue(option))"
                              :size="16"
                            />
                            <Plus v-else :size="16" />
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>

                  <!-- Case 2: > 5 options, Modal trigger -->

                  <div v-else class="selection-container">
                    <div class="selection-summary">
                      <div class="selection-summary-info">
                        <span class="selection-count">已选择 {{ getSelectedCount(key) }} 项</span>

                        <a-button
                          type="link"
                          size="small"
                          class="clear-btn"
                          @click="clearSelection(key)"
                          v-if="getSelectedCount(key) > 0"
                        >
                          清空
                        </a-button>
                      </div>

                      <a-button
                        type="primary"
                        size="small"
                        class="selection-trigger-btn"
                        @click="openSelectionModal(key)"
                      >
                        选择...
                      </a-button>
                    </div>

                    <!-- Selected Preview Tags -->

                    <div v-if="getSelectedCount(key) > 0" class="selection-preview">
                      <a-tag
                        v-for="val in agentConfig[key]"
                        :key="val"
                        closable
                        @close="toggleOption(key, val)"
                        class="selection-tag"
                      >
                        {{ getOptionLabelFromValue(key, val) }}
                      </a-tag>
                    </div>
                  </div>
                </div>

                <!-- 数字 -->
                <a-input-number
                  v-else-if="value?.type === 'number'"
                  :value="agentConfig[key]"
                  @update:value="(val) => agentStore.updateAgentConfig({ [key]: val })"
                  :placeholder="getPlaceholder(key, value)"
                  class="config-input-number"
                />

                <!-- 滑块 -->
                <a-slider
                  v-else-if="value?.type === 'slider'"
                  :value="agentConfig[key]"
                  @update:value="(val) => agentStore.updateAgentConfig({ [key]: val })"
                  :min="value.min"
                  :max="value.max"
                  :step="value.step"
                  class="config-slider"
                />

                <!-- 其他类型 -->
                <a-input
                  v-else
                  :value="agentConfig[key]"
                  @update:value="(val) => agentStore.updateAgentConfig({ [key]: val })"
                  :placeholder="getPlaceholder(key, value)"
                  class="config-input"
                />
              </a-form-item>
            </template>
          </a-form>
        </div>
      </div>
    </div>

    <!-- 固定在底部的操作按钮 -->
    <div class="sidebar-footer" v-if="!isEmptyConfig && userStore.isAdmin">
      <div class="form-actions">
        <a-button
          type="primary"
          @click="saveConfig"
          class="save-btn"
          :class="{ changed: agentStore.hasConfigChanges }"
          :disabled="isSavingConfig"
        >
          保存
        </a-button>

        <a-tooltip :title="isCurrentDefault ? '当前已是默认配置' : '设为默认配置'">
          <a-button type="text" shape="circle" class="icon-btn" @click="setAsDefault">
            <Star
              :size="18"
              :fill="isCurrentDefault ? 'currentColor' : 'none'"
              :class="{ 'is-default': isCurrentDefault }"
            />
          </a-button>
        </a-tooltip>

        <a-tooltip title="删除配置">
          <a-button
            type="text"
            shape="circle"
            danger
            class="icon-btn"
            @click="confirmDeleteConfig"
            :disabled="isDeletingConfig"
          >
            <Trash2 :size="18" />
          </a-button>
        </a-tooltip>
      </div>
    </div>

    <!-- 通用选择弹窗 -->

    <a-modal
      v-model:open="selectionModalOpen"
      :title="`选择${configurableItems[currentConfigKey]?.name || '项目'}`"
      :width="800"
      :footer="null"
      :maskClosable="false"
      class="selection-modal"
    >
      <div class="selection-modal-content">
        <div class="selection-search">
          <a-input
            v-model:value="selectionSearchText"
            placeholder="搜索..."
            allow-clear
            class="search-input"
          >
            <template #prefix>
              <Search :size="16" class="search-icon" />
            </template>
          </a-input>
          <template v-if="isToolsKind(currentConfigKind)">
            <a-button
              type="text"
              size="small"
              @click="refreshConfigOptions(currentConfigKey, currentConfigKind)"
              class="modal-action-btn"
              title="刷新列表"
            >
              <RotateCw :size="14" />
              刷新
            </a-button>
            <a-button
              type="text"
              size="small"
              @click="navigateToConfigPage(currentConfigKind)"
              class="modal-action-btn"
              title="跳转配置"
            >
              <Settings :size="14" />
              配置
            </a-button>
          </template>
        </div>

        <div class="selection-list">
          <div
            v-for="option in filteredOptions"
            :key="getOptionValue(option)"
            class="selection-item"
            :class="{ selected: tempSelectedValues.includes(getOptionValue(option)) }"
            @click="toggleModalSelection(getOptionValue(option))"
          >
            <div class="selection-item-content">
              <div class="selection-item-header">
                <span class="selection-item-name">{{ getOptionLabel(option) }}</span>

                <div class="selection-item-indicator">
                  <Check v-if="tempSelectedValues.includes(getOptionValue(option))" :size="16" />

                  <Plus v-else :size="16" />
                </div>
              </div>

              <div v-if="getOptionDescription(option)" class="selection-item-description">
                {{ getOptionDescription(option) }}
              </div>
            </div>
          </div>
        </div>

        <div class="selection-modal-footer">
          <div class="selected-count">已选择 {{ tempSelectedValues.length }} 项</div>

          <div class="modal-actions">
            <a-button @click="closeSelectionModal">取消</a-button>

            <a-button type="primary" @click="confirmSelection">确认</a-button>
          </div>
        </div>
      </div>
    </a-modal>

    <a-modal
      v-model:open="systemPromptModalOpen"
      :title="systemPromptModalTitle"
      :width="620"
      :maskClosable="false"
      @cancel="closeSystemPromptModal"
      class="system-prompt-modal"
    >
      <div class="system-prompt-modal-content">
        <a-textarea
          v-model:value="systemPromptDraft"
          :rows="14"
          :placeholder="systemPromptModalPlaceholder"
          class="system-prompt-modal-input"
        />
      </div>

      <template #footer>
        <a-button @click="closeSystemPromptModal">取消</a-button>
        <a-button type="primary" @click="saveSystemPrompt">保存</a-button>
      </template>
    </a-modal>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import { X, Trash2, Check, Plus, Search, Star, RotateCw, Settings } from 'lucide-vue-next'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
import { useAgentStore } from '@/stores/agent'
import { useUserStore } from '@/stores/user'
import { useDatabaseStore } from '@/stores/database'
import { skillApi } from '@/apis/skill_api'
import { subagentApi } from '@/apis/subagent_api'
import { toolApi } from '@/apis/tool_api'
import { storeToRefs } from 'pinia'

// Props
const props = defineProps({
  isOpen: {
    type: Boolean,
    default: false
  },
  disabled: {
    type: Boolean,
    default: false
  }
})

// Emits
const emit = defineEmits(['close'])

// Store 管理
const agentStore = useAgentStore()
const userStore = useUserStore()
const databaseStore = useDatabaseStore()
const router = useRouter()

watch(
  () => props.isOpen,
  async (val) => {
    if (val) {
      databaseStore.loadDatabases().catch(() => {})
      loadLiveSkillOptions().catch(() => {})
      loadSubagentOptions().catch(() => {})
      loadToolOptions().catch(() => {})
      if (selectedAgentId.value) {
        try {
          await agentStore.fetchAgentDetail(selectedAgentId.value, true)
        } catch (error) {
          console.error('刷新智能体配置项失败:', error)
        }
      }
    }
  }
)

const {
  availableTools,
  selectedAgent,
  selectedAgentId,
  selectedAgentConfigId,
  selectedConfigSummary,
  agentConfigs,
  agentConfig,
  configurableItems
} = storeToRefs(agentStore)

// console.log(availableTools.value)

// 本地状态
const selectionModalOpen = ref(false)
const currentConfigKey = ref(null)
const tempSelectedValues = ref([])
const selectionSearchText = ref('')
const systemPromptModalOpen = ref(false)
const currentSystemPromptKey = ref(null)
const systemPromptDraft = ref('')
const activeTab = ref('basic')
const liveSkillOptions = ref([])
const liveSubagentOptions = ref([])
const toolOptionsFromApi = ref([])

const isEmptyConfig = computed(() => {
  return !selectedAgentId.value || Object.keys(configurableItems.value).length === 0
})

const isCurrentDefault = computed(() => {
  return !!selectedConfigSummary.value?.is_default
})

const isSavingConfig = ref(false)
const isDeletingConfig = ref(false)

const hasOtherConfigs = computed(() => {
  if (isEmptyConfig.value) return false
  return Object.entries(configurableItems.value).some(([, value]) => {
    const isBasic =
      value.template_metadata?.kind === 'prompt' || value.template_metadata?.kind === 'llm'
    const isTools =
      value.template_metadata?.kind === 'mcps' ||
      value.template_metadata?.kind === 'knowledges' ||
      value.template_metadata?.kind === 'tools' ||
      value.template_metadata?.kind === 'skills' ||
      value.template_metadata?.kind === 'subagents'

    return !isBasic && !isTools
  })
})

const segmentedOptions = computed(() => {
  const options = [
    { label: '基础', value: 'basic' },
    { label: '工具', value: 'tools' }
  ]

  if (hasOtherConfigs.value) {
    options.push({ label: '其他', value: 'other' })
  }

  return options
})

const loadLiveSkillOptions = async (force = false) => {
  if (!userStore.isAdmin) {
    liveSkillOptions.value = []
    return
  }
  // 如果不是强制刷新且已有数据，则跳过
  if (!force && liveSkillOptions.value.length > 0) {
    return
  }
  try {
    const result = await skillApi.listSkills()
    const rows = result?.data || []
    liveSkillOptions.value = rows.map((item) => ({
      id: item.slug,
      name: item.slug,
      description: item.description || ''
    }))
  } catch (error) {
    console.warn('加载实时 Skills 列表失败:', error)
  }
}

const loadToolOptions = async (force = false) => {
  if (!userStore.isAdmin) {
    toolOptionsFromApi.value = []
    return
  }
  // 如果不是强制刷新且已有数据，则跳过
  if (!force && toolOptionsFromApi.value.length > 0) {
    return
  }
  try {
    const result = await toolApi.getTools()
    toolOptionsFromApi.value = (result?.data || []).map((item) => ({
      id: item.id,
      name: item.name || item.id,
      description: item.description || ''
    }))
  } catch (error) {
    console.warn('加载工具列表失败:', error)
  }
}

const loadSubagentOptions = async (force = false) => {
  if (!userStore.isAdmin) {
    liveSubagentOptions.value = []
    return
  }
  if (!force && liveSubagentOptions.value.length > 0) {
    return
  }
  try {
    const result = await subagentApi.getSubAgents()
    const rows = result?.data || []
    liveSubagentOptions.value = rows.map((item) => ({
      id: item.name,
      name: item.name,
      description: item.description || ''
    }))
  } catch (error) {
    console.warn('加载 Subagents 列表失败:', error)
  }
}

// 判断是否为需要跳转的配置类型
const isToolsKind = (kind) => {
  return ['knowledges', 'tools', 'mcps', 'skills', 'subagents'].includes(kind)
}

// 强制刷新对应配置项的选项列表
const refreshConfigOptions = async (key, kind) => {
  try {
    switch (kind) {
      case 'knowledges':
        await databaseStore.loadDatabases(true)
        message.success('知识库列表已刷新')
        break
      case 'tools':
        await loadToolOptions(true)
        message.success('工具列表已刷新')
        break
      case 'skills':
        await loadLiveSkillOptions(true)
        message.success('Skills 列表已刷新')
        break
      case 'subagents':
        await loadSubagentOptions(true)
        message.success('Subagents 列表已刷新')
        break
      case 'mcps':
        // MCP 没有前端 store，提示用户刷新页面
        message.info('请在 MCP 管理页面刷新')
        break
    }
  } catch (error) {
    console.error('刷新配置选项失败:', error)
    message.error('刷新失败')
  }
}

// 跳转到对应管理页面
const navigateToConfigPage = (kind) => {
  // 先关闭选择弹窗
  closeSelectionModal()
  // 延迟跳转，确保弹窗先关闭
  setTimeout(() => {
    switch (kind) {
      case 'knowledges':
        router.push('/database')
        break
      case 'tools':
        router.push({ path: '/extensions', query: { tab: 'tools' } })
        break
      case 'mcps':
        router.push({ path: '/extensions', query: { tab: 'mcp' } })
        break
      case 'skills':
        router.push({ path: '/extensions', query: { tab: 'skills' } })
        break
      case 'subagents':
        router.push({ path: '/extensions', query: { tab: 'subagents' } })
        break
    }
  }, 100)
}

// 通用选项获取与处理
const getConfigOptions = (value) => {
  if (value?.template_metadata?.kind === 'tools') {
    // 优先使用从 API 获取的工具列表，否则回退到 configurableItems 中的选项
    return toolOptionsFromApi.value.length > 0
      ? toolOptionsFromApi.value
      : availableTools.value
        ? Object.values(availableTools.value)
        : []
  }
  if (value?.template_metadata?.kind === 'knowledges') {
    return databaseStore.databases || []
  }
  if (value?.template_metadata?.kind === 'skills') {
    return liveSkillOptions.value.length > 0 ? liveSkillOptions.value : value?.options || []
  }
  if (value?.template_metadata?.kind === 'subagents') {
    return liveSubagentOptions.value.length > 0 ? liveSubagentOptions.value : value?.options || []
  }
  return value?.options || []
}

const isListConfig = (key, value) => {
  const isTools = value?.template_metadata?.kind === 'tools'
  const isList = value?.type === 'list'
  return isTools || isList || key === 'skills' || key === 'subagents'
}

const getOptionValue = (option) => {
  if (typeof option === 'object' && option !== null) {
    return option.id || option.value || option.name || option.db_id || option.slug
  }
  return option
}

const getOptionLabel = (option) => {
  if (typeof option === 'object' && option !== null) {
    return option.name || option.label || option.id || option.db_id || option.slug
  }
  return option
}

const getOptionDescription = (option) => {
  if (typeof option === 'object' && option !== null) {
    return option.description || '暂无描述'
  }
  return null
}

const currentConfigKind = computed(() => {
  if (!currentConfigKey.value) return null
  return configurableItems.value[currentConfigKey.value]?.template_metadata?.kind
})

const systemPromptModalTitle = computed(() => {
  if (!currentSystemPromptKey.value) return 'System Prompt'
  return configurableItems.value[currentSystemPromptKey.value]?.name || currentSystemPromptKey.value
})

const systemPromptModalPlaceholder = computed(() => {
  if (!currentSystemPromptKey.value) return '请输入系统提示词'
  const currentItem = configurableItems.value[currentSystemPromptKey.value]
  if (!currentItem) return '请输入系统提示词'
  return getPlaceholder(currentSystemPromptKey.value, currentItem)
})

const filteredOptions = computed(() => {
  if (!currentConfigKey.value) return []
  const key = currentConfigKey.value
  const configItem = configurableItems.value[key]
  const options = getConfigOptions(configItem)

  if (!selectionSearchText.value) return options

  const search = selectionSearchText.value.toLowerCase()
  return options.filter((opt) => {
    const label = String(getOptionLabel(opt)).toLowerCase()
    const desc = String(getOptionDescription(opt) || '').toLowerCase()
    return label.includes(search) || desc.includes(search)
  })
})

// 方法
const shouldShowConfig = (key, value) => {
  const isBasic =
    value.template_metadata?.kind === 'prompt' || value.template_metadata?.kind === 'llm'
  const isTools =
    value.template_metadata?.kind === 'mcps' ||
    value.template_metadata?.kind === 'knowledges' ||
    value.template_metadata?.kind === 'tools' ||
    value.template_metadata?.kind === 'skills' ||
    value.template_metadata?.kind === 'subagents' ||
    key === 'skills' ||
    key === 'subagents'

  if (activeTab.value === 'basic') {
    // 基础：System Prompt, LLM Model
    return isBasic
  } else if (activeTab.value === 'tools') {
    // 工具：Tools, MCPs, Knowledges
    return isTools
  } else {
    // 其他：剩余所有配置
    return !isBasic && !isTools
  }
}

const closeSidebar = () => {
  emit('close')
}

const getConfigLabel = (key, value) => {
  // console.log(configurableItems)
  if (value.description && value.name !== key) {
    return `${value.name}`
    // return `${value.name}（${key}）`;
  }
  return key
}

const getPlaceholder = (key, value) => {
  return `（默认: ${value.default}）`
}

const handleModelChange = (key, spec) => {
  if (typeof spec !== 'string' || !spec) return
  agentStore.updateAgentConfig({
    [key]: spec
  })
}

// 多选相关方法
const ensureArray = (key) => {
  const config = agentConfig.value || {}
  if (!config[key] || !Array.isArray(config[key])) {
    return []
  }
  return config[key]
}

const isOptionSelected = (key, option) => {
  const currentOptions = ensureArray(key)
  return currentOptions.includes(option)
}

const getSelectedCount = (key) => {
  const currentOptions = ensureArray(key)
  return currentOptions.length
}

const toggleOption = (key, option) => {
  const currentOptions = [...ensureArray(key)]
  const index = currentOptions.indexOf(option)

  if (index > -1) {
    currentOptions.splice(index, 1)
  } else {
    currentOptions.push(option)
  }

  agentStore.updateAgentConfig({
    [key]: currentOptions
  })
}

const clearSelection = (key) => {
  agentStore.updateAgentConfig({
    [key]: []
  })
}

// 统一选择弹窗相关方法
const getOptionLabelFromValue = (key, val) => {
  const options = getConfigOptions(configurableItems.value[key])
  const option = options.find((opt) => getOptionValue(opt) === val)
  return option ? getOptionLabel(option) : val
}

const openSelectionModal = async (key) => {
  currentConfigKey.value = key
  // 如果是工具，从 API 刷新工具列表
  if (configurableItems.value[key]?.template_metadata?.kind === 'tools') {
    await loadToolOptions()
  }
  // 如果是知识库，需要获取知识库列表
  if (configurableItems.value[key]?.template_metadata?.kind === 'knowledges') {
    try {
      await databaseStore.loadDatabases()
    } catch (error) {
      console.error('加载知识库列表失败:', error)
    }
  }
  if (configurableItems.value[key]?.template_metadata?.kind === 'skills') {
    await loadLiveSkillOptions()
  }
  if (configurableItems.value[key]?.template_metadata?.kind === 'subagents') {
    await loadSubagentOptions()
  }
  const currentValues = agentConfig.value[key] || []
  tempSelectedValues.value = [...currentValues]
  selectionModalOpen.value = true
}

const toggleModalSelection = (optionValue) => {
  const index = tempSelectedValues.value.indexOf(optionValue)
  if (index > -1) {
    tempSelectedValues.value.splice(index, 1)
  } else {
    tempSelectedValues.value.push(optionValue)
  }
}

const confirmSelection = () => {
  if (currentConfigKey.value) {
    agentStore.updateAgentConfig({
      [currentConfigKey.value]: [...tempSelectedValues.value]
    })
  }
  closeSelectionModal()
}

const closeSelectionModal = () => {
  selectionModalOpen.value = false
  currentConfigKey.value = null
  tempSelectedValues.value = []
  selectionSearchText.value = ''
}

// 系统提示词弹窗编辑相关方法
const openSystemPromptModal = (key) => {
  currentSystemPromptKey.value = key
  systemPromptDraft.value = agentConfig.value[key] || ''
  systemPromptModalOpen.value = true
}

const closeSystemPromptModal = () => {
  systemPromptModalOpen.value = false
  currentSystemPromptKey.value = null
  systemPromptDraft.value = ''
}

const saveSystemPrompt = () => {
  if (!currentSystemPromptKey.value) return
  agentStore.updateAgentConfig({
    [currentSystemPromptKey.value]: systemPromptDraft.value
  })
  closeSystemPromptModal()
}

// 验证和过滤配置项
const validateAndFilterConfig = () => {
  const validatedConfig = { ...agentConfig.value }
  const configItems = configurableItems.value

  // 遍历所有配置项
  Object.keys(configItems).forEach((key) => {
    const configItem = configItems[key]
    const currentValue = validatedConfig[key]

    if (
      Array.isArray(currentValue) &&
      (configItem.template_metadata?.kind === 'tools' || configItem.type === 'list')
    ) {
      const options = getConfigOptions(configItem)
      const validValues = new Set(options.map((opt) => String(getOptionValue(opt))))
      if (validValues.size === 0) return

      validatedConfig[key] = currentValue.filter((value) => validValues.has(String(value)))
      if (validatedConfig[key].length !== currentValue.length) {
        console.warn(`配置项 ${key} 中包含无效选项，已自动过滤`)
      }
    }
  })

  return validatedConfig
}

// 配置保存和重置
const saveConfig = async () => {
  if (!selectedAgentId.value) {
    message.error('没有选择智能体')
    return
  }

  if (!agentStore.hasConfigChanges) return

  try {
    isSavingConfig.value = true
    // 验证和过滤配置
    const validatedConfig = validateAndFilterConfig()

    // 如果配置有变化，先更新到store
    if (JSON.stringify(validatedConfig) !== JSON.stringify(agentConfig.value)) {
      agentStore.updateAgentConfig(validatedConfig)
      message.info('检测到无效配置项，已自动过滤')
    }

    await agentStore.saveAgentConfig()
    message.success('配置已保存到服务器')
  } catch (error) {
    console.error('保存配置到服务器出错:', error)
    message.error('保存配置到服务器失败')
  } finally {
    isSavingConfig.value = false
  }
}

const setAsDefault = async () => {
  if (!selectedAgentId.value || !selectedAgentConfigId.value) return
  try {
    await agentStore.setSelectedAgentConfigDefault()
    message.success('已设为默认配置')
  } catch (error) {
    console.error('设置默认配置出错:', error)
    message.error('设置默认配置失败')
  }
}

const confirmDeleteConfig = async () => {
  if (!selectedAgentId.value || !selectedAgentConfigId.value) return

  const currentName = selectedConfigSummary.value?.name || '当前配置'
  const list = agentConfigs.value[selectedAgentId.value] || []
  const content =
    list.length <= 1
      ? `将删除「${currentName}」。删除后系统会自动创建一个新的默认配置。`
      : `将删除「${currentName}」。`

  Modal.confirm({
    title: '确认删除配置？',
    content,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      isDeletingConfig.value = true
      try {
        await agentStore.deleteSelectedAgentConfigProfile()
        message.success('配置已删除')
      } catch (error) {
        console.error('删除配置出错:', error)
        message.error('删除配置失败')
      } finally {
        isDeletingConfig.value = false
      }
    }
  })
}
</script>

<style lang="less" scoped>
@padding-bottom: 0px;
.agent-config-sidebar {
  position: relative;
  width: 0;
  height: 100vh;
  background: var(--gray-0);
  border-left: 1px solid var(--gray-200);
  transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;

  &.open {
    width: 400px;
  }

  .sidebar-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 20px;
    border-bottom: 1px solid var(--gray-150);
    background: var(--gray-0);
    flex-shrink: 0;
    min-width: 400px;
    height: var(--header-height);

    .header-center {
      flex: 1;
      display: flex;
      justify-content: center;
    }

    .close-btn {
      color: var(--gray-600);
      border: none;
      padding: 4px;

      &:hover {
        color: var(--gray-900);
        background: var(--gray-100);
      }
    }
  }

  .sidebar-content {
    flex: 1;
    overflow-y: auto;
    padding: 8px 12px;
    min-width: 400px;
    padding-bottom: @padding-bottom;

    .agent-info {
      .agent-basic-info {
        .agent-description {
          margin: 0 0 12px 0;
          font-size: 14px;
          color: var(--gray-700);
          line-height: 1.5;
        }
      }
    }

    .config-form-content {
      margin-bottom: 20px;
      .config-form {
        .config-alert {
          margin-bottom: 16px;
        }

        .config-item {
          background-color: var(--gray-25);
          padding: 12px;
          border-radius: 8px;
          border: 1px solid var(--gray-100);
          // box-shadow: 0px 0px 2px var(--shadow-3);

          :deep(.ant-form-item-label > label) {
            font-weight: 600;
          }

          :deep(label.form_item_model) {
            font-weight: 600;
          }

          .config-description {
            margin: 4px 0 8px 0;
            font-size: 12px;
            color: var(--gray-600);
            line-height: 1.4;
          }

          .model-selector {
            width: 100%;
          }

          .system-prompt-container {
            width: 100%;
          }

          .system-prompt-display {
            min-height: 60px;
            border: 1px solid var(--gray-200);
            padding: 10px 12px;
            border-radius: 6px;
            cursor: pointer;
            position: relative;
            transition: all 0.2s ease;

            &:hover {
              border-color: var(--main-color);
              background: var(--gray-25);

              .edit-hint {
                opacity: 1;
              }
            }

            .system-prompt-content {
              white-space: pre-line;
              word-break: break-word;
              line-height: 1.5;
              color: var(--gray-900);
              font-size: 13px;
              display: -webkit-box;
              line-clamp: 4;
              -webkit-line-clamp: 4;
              -webkit-box-orient: vertical;
              overflow: hidden;

              &.is-placeholder {
                color: var(--gray-400);
                font-style: italic;
              }

              &:empty::before {
                content: attr(data-placeholder);
                color: var(--gray-400);
              }
            }

            .edit-hint {
              position: absolute;
              top: -32px;
              right: 0px;
              font-size: 12px;
              color: var(--main-800);
              opacity: 0;
              transition: opacity 0.2s ease;
              background: var(--gray-0);
              padding: 2px 6px;
              border-radius: 4px;
            }
          }

          .config-select,
          .config-input,
          .config-input-number {
            width: 100%;
          }

          .config-slider {
            width: 100%;
          }
        }
      }
    }
  }

  .sidebar-footer {
    padding: 8px 12px;
    border-top: 1px solid var(--gray-100);
    background: var(--gray-0);
    // min-width: 400px;
    z-index: 10;
    flex-shrink: 0; // Ensure footer doesn't shrink

    .form-actions {
      display: flex;
      flex-direction: row;
      gap: 12px;
      justify-content: space-between;
      align-items: center;

      .icon-btn {
        width: 36px;
        height: 36px;
        border-radius: 6px;
        color: var(--gray-600);
        border: 1px solid var(--gray-200);
        background: var(--gray-0);
        display: flex;
        justify-content: center;
        align-items: center;

        &:hover:not(:disabled) {
          color: var(--main-600);
          border-color: var(--main-200);
          background: var(--main-10);
        }

        &.is-default {
          // color: var(--main-500);
          color: var(--color-warning-500);
        }

        &[danger]:hover:not(:disabled) {
          color: var(--error-600);
          border-color: var(--error-200);
          background: var(--error-10);
        }

        &:disabled {
          cursor: not-allowed;
          background: transparent;
          color: var(--gray-400);
          border-color: var(--gray-200);

          &.is-default {
            opacity: 1;
          }
        }
      }

      .save-btn {
        flex: 1;
        height: 36px;
        border-radius: 6px;
        font-weight: 500;
        font-size: 14px;
        background-color: var(--gray-100);
        border: 1px solid var(--gray-200);
        color: var(--gray-600);
        transition: all 0.2s ease;

        &.changed {
          background-color: var(--main-color);
          color: var(--gray-0);
          border-color: var(--main-color);
        }

        &:hover:not(:disabled) {
          opacity: 0.9;
        }

        &:disabled {
          cursor: not-allowed;
          background-color: var(--gray-100);
          border-color: var(--gray-200);
          color: var(--gray-400);
        }
      }
    }
  }
}

// 选择器样式
.selection-container {
  .selection-summary {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 10px;
    background: var(--gray-0);
    border-radius: 8px;
    border: 1px solid var(--gray-150);
    margin-bottom: 8px;

    .selection-summary-info {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      color: var(--gray-900);

      .selection-count {
        color: var(--gray-900);
        font-weight: 500;
      }
    }

    .selection-trigger-btn {
      background: var(--main-color);
      border: none;
      border-radius: 4px;
      height: 28px;
      font-size: 12px;
      font-weight: 500;

      &:hover {
        background: var(--main-color);
        opacity: 0.9;
      }
    }
  }

  .selection-preview {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;

    .selection-tag {
      margin: 0;
      padding: 4px 8px;
      border-radius: 8px;
      background: var(--gray-150);
      border: none;
      color: var(--gray-900);
      font-size: 12px;

      :deep(.anticon-close) {
        color: var(--gray-600);
        margin-left: 4px;

        &:hover {
          color: var(--gray-900);
        }
      }
    }
  }
}

// 多选卡片样式
.multi-select-cards {
  .multi-select-label {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
    font-size: 12px;
    color: var(--gray-600);

    .label-actions {
      display: flex;
      align-items: center;
      gap: 4px;

      .action-btn {
        font-size: 12px;
        color: var(--gray-600);
        display: flex;
        align-items: center;
        gap: 2px;
        padding: 2px 6px;
        height: auto;
        line-height: 1;

        &:hover {
          color: var(--main-color);
        }
      }
    }
  }

  .options-grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 8px;
  }

  .option-card {
    border: 1px solid var(--gray-300);
    border-radius: 8px;
    padding: 8px 12px;
    cursor: pointer;
    transition: all 0.2s ease;
    background: var(--gray-0);

    &:hover {
      border-color: var(--main-color);
    }

    &.selected {
      border-color: var(--main-color);
      background: var(--main-10);

      .option-indicator {
        color: var(--main-color);
      }

      .option-text {
        color: var(--main-color);
        font-weight: 500;
      }
    }

    &.unselected {
      .option-indicator {
        color: var(--gray-400);
      }

      .option-text {
        color: var(--gray-700);
      }
    }

    .option-content {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;

      .option-text {
        flex: 1;
        font-size: 13px;
        line-height: 1.4;
      }

      .option-indicator {
        flex-shrink: 0;
        font-size: 14px;
        display: flex;
        align-items: center;
      }
    }
  }
}

// 选择弹窗样式
.selection-modal {
  .selection-modal-content {
    .selection-search {
      margin-bottom: 16px;
      display: flex;
      gap: 8px;
      align-items: center;

      .search-input {
        flex: 1;
        border-radius: 8px;
        border: 1px solid var(--gray-300);
        height: 36px;
        font-size: 14px;
        transition: all 0.2s ease;
        background: var(--gray-0);

        .search-icon {
          color: var(--gray-500);
          font-size: 16px;
        }

        &:focus-within {
          border-color: var(--main-color);
          box-shadow: 0 0 0 2px rgba(var(--main-color-rgb), 0.1);

          .search-icon {
            color: var(--main-color);
          }
        }

        &:hover {
          border-color: var(--gray-400);
        }
      }

      .modal-action-btn {
        display: flex;
        align-items: center;
        gap: 4px;
        font-size: 13px;
        color: var(--gray-600);
        white-space: nowrap;

        &:hover {
          color: var(--main-color);
        }
      }
    }

    .selection-list {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      max-height: max(60vh, 800px);
      overflow-y: auto;
      border-radius: 8px;
      margin-bottom: 16px;

      // 在小屏幕下调整为单列布局
      @media (max-width: 480px) {
        grid-template-columns: 1fr;
      }

      &::-webkit-scrollbar {
        width: 6px;
      }

      &::-webkit-scrollbar-track {
        background: var(--gray-100);
        border-radius: 3px;
      }

      &::-webkit-scrollbar-thumb {
        background: var(--gray-400);
        border-radius: 3px;
      }

      &::-webkit-scrollbar-thumb:hover {
        background: var(--gray-500);
      }

      .selection-item {
        padding: 12px 16px;
        border-bottom: none;
        cursor: pointer;
        transition: all 0.2s ease;
        border-radius: 8px;
        margin-bottom: 4px;
        background: var(--gray-0);
        border: 1px solid var(--gray-200);

        &:hover {
          border-color: var(--gray-300);
          background: var(--gray-20);
        }
        .selection-item-content {
          .selection-item-header {
            display: flex;
            align-items: center;
            gap: 8px;

            .selection-item-name {
              font-size: 14px;
              font-weight: 500;
              color: var(--gray-900);
              line-height: 1.3;
              flex: 1;
            }

            .selection-item-indicator {
              color: var(--gray-400);
              font-size: 16px;
              transition: all 0.2s ease;
              flex-shrink: 0;
              display: flex;
              align-items: center;
            }
          }

          .selection-item-description {
            font-size: 12px;
            color: var(--gray-600);
            line-height: 1.4;
            margin-top: 6px;
            display: -webkit-box;
            line-clamp: 2;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            text-overflow: ellipsis;
          }
        }

        &.selected {
          background: var(--main-10);
          border-color: var(--main-color);

          .selection-item-content {
            .selection-item-name {
              color: var(--main-800);
            }
            .selection-item-indicator {
              color: var(--main-800);
            }
          }
          .selection-item-description {
            color: var(--gray-900);
          }
        }
      }
    }

    .selection-modal-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-top: 16px;
      border-top: 1px solid var(--gray-200);

      .selected-count {
        font-size: 14px;
        color: var(--gray-700);
        font-weight: 500;
        padding: 6px 12px;
        background: var(--gray-50);
        border-radius: 8px;
        border: 1px solid var(--gray-200);
      }

      .modal-actions {
        display: flex;
        gap: 12px;

        :deep(.ant-btn) {
          border-radius: 8px;
          height: 36px;
          font-size: 14px;
          font-weight: 500;
          padding: 0 16px;
          transition: all 0.2s ease;

          &.ant-btn-default {
            border: 1px solid var(--gray-300);
            color: var(--gray-700);
            background: var(--gray-0);

            &:hover {
              border-color: var(--main-color);
              color: var(--main-color);
            }
          }

          &.ant-btn-primary {
            background: var(--main-color);
            border: none;
            color: var(--gray-0);

            &:hover {
              background: var(--main-color);
              opacity: 0.9;
            }
          }
        }
      }
    }
  }
}

.system-prompt-modal {
  .system-prompt-modal-content {
    .system-prompt-modal-input {
      resize: vertical;
      font-size: 13px;
      line-height: 1.6;
      border-radius: 8px;
    }
  }
}

.clear-btn {
  padding: 0;
  height: auto;
  font-size: 12px;
  font-weight: 600;
  color: var(--main-700);

  &:hover {
    color: var(--main-800);
  }
}

// 响应式适配
@media (max-width: 768px) {
  .agent-config-sidebar.open {
    width: 100%;
  }

  .sidebar-header,
  .sidebar-content {
    min-width: 100% !important;
  }
}
</style>
