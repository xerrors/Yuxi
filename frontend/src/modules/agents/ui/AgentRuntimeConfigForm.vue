<template>
  <div class="agent-runtime-config-form">
    <div class="runtime-config-content">
      <div class="agent-info">
        <div class="config-segment" v-if="props.showSegmented && !isEmptyConfig">
          <a-segmented v-model:value="currentSegment" :options="segmentOptions" block />
        </div>

        <div
          v-if="configurableItems"
          class="config-form-content"
          :class="{ 'is-readonly': isReadOnlyConfig }"
        >
          <!-- 配置表单 -->
          <a-form :model="agentConfig" layout="vertical" class="config-form">
            <a-alert
              v-if="isEmptyConfig"
              type="warning"
              message="该智能体没有配置项"
              show-icon
              class="config-alert"
            />
            <!-- 统一显示所有配置项 -->
            <a-empty v-if="isCurrentSegmentEmpty" description="暂无配置项" class="config-empty" />
            <template v-for="(value, key) in filteredConfigurableItems" :key="key">
              <a-form-item :label="getConfigLabel(key, value)" :name="key" class="config-item">
                <p v-if="value.description" class="config-description">{{ value.description }}</p>

                <!-- <div>{{ value }}</div> -->
                <!-- 模型选择 -->
                <div
                  v-if="value.kind === 'llm'"
                  class="model-selector"
                  :class="{ 'is-readonly': isReadOnlyConfig }"
                >
                  <ModelSelectorComponent
                    @select-model="(spec) => handleModelChange(key, spec)"
                    :model_spec="agentConfig[key] || ''"
                    :disabled="isReadOnlyConfig"
                    :placeholder="value.placeholder || '使用系统默认'"
                    clearable
                  />
                </div>

                <!-- 系统提示词 -->
                <div v-else-if="value.kind === 'prompt'" class="system-prompt-container">
                  <div class="system-prompt-display" @click="openSystemPromptModal(key)">
                    <div
                      class="system-prompt-content"
                      :class="{ 'is-placeholder': !agentConfig[key] }"
                    >
                      {{ agentConfig[key] || getPlaceholder(key, value) }}
                    </div>
                    <div class="edit-hint">
                      {{ isReadOnlyConfig ? '查看' : '点击查看并编辑' }}
                    </div>
                  </div>
                </div>

                <!-- 布尔类型 -->
                <a-switch
                  v-else-if="typeof agentConfig[key] === 'boolean'"
                  :checked="agentConfig[key]"
                  :disabled="isReadOnlyConfig"
                  @update:checked="(val) => updateConfigValue(key, val)"
                />

                <!-- 单选 -->
                <a-select
                  v-else-if="isSingleSelectAgentConfig(value)"
                  :value="agentConfig[key]"
                  :disabled="isReadOnlyConfig"
                  @update:value="(val) => updateConfigValue(key, val)"
                  class="config-select"
                >
                  <a-select-option
                    v-for="option in getConfigOptions(value)"
                    :key="getOptionValue(option)"
                    :value="getOptionValue(option)"
                  >
                    {{ getOptionLabel(option) }}
                  </a-select-option>
                </a-select>

                <!-- 多选 / 工具列表 (统一处理) -->
                <div v-else-if="value?.type === 'list'" class="list-config-container">
                  <div
                    v-if="getHiddenSelection(key).length"
                    class="hidden-selection-note"
                    role="status"
                  >
                    另有
                    {{ getHiddenSelection(key).length }} 项当前不在可选范围，修改可见选择时会保留。
                    <template v-if="!isReadOnlyConfig"> 清空全部会移除这些选择。 </template>
                  </div>
                  <div class="selection-toolbar">
                    <div class="selection-summary">
                      <span class="selection-count" role="status">
                        <template v-if="isAllSelected(key)">已选择全部（含新增）</template>
                        <template v-else>
                          已选择 {{ getSelectedCount(key) }} 项｜共
                          {{ getSelectionOptions(key, value).length }} 项
                        </template>
                      </span>
                      <a-tooltip v-if="!isReadOnlyConfig && canResetSelection(key)" title="清空全部">
                        <a-button
                          type="text"
                          size="small"
                          class="clear-btn lucide-icon-btn"
                          aria-label="清空全部"
                          @click="clearSelection(key)"
                        >
                          <X :size="14" aria-hidden="true" />
                        </a-button>
                      </a-tooltip>
                    </div>
                    <div v-if="!isReadOnlyConfig" class="selection-actions">
                      <a-button
                        v-if="supportsAllAgentResources(value) && !isAllSelected(key)"
                        type="link"
                        size="small"
                        class="select-all-btn"
                        @click="setAllSelection(key)"
                      >
                        选择全部
                      </a-button>
                      <template
                        v-if="getSelectionOptions(key, value).length <= 5 && isResourceConfigKind(value.kind)"
                      >
                        <a-button
                          type="text"
                          size="small"
                          @click="refreshConfigOptions(key, value.kind)"
                          class="inline-action-btn lucide-icon-btn"
                        >
                          <RotateCw :size="14" aria-hidden="true" />
                          刷新
                        </a-button>
                        <a-button
                          type="text"
                          size="small"
                          @click="navigateToConfigPage(value.kind)"
                          class="inline-action-btn lucide-icon-btn"
                        >
                          <Settings :size="14" aria-hidden="true" />
                          配置
                        </a-button>
                      </template>
                      <slot name="resource-actions" :field="key" :kind="value.kind" />
                      <a-button
                        v-if="getSelectionOptions(key, value).length > 5"
                        size="small"
                        class="selection-trigger-btn"
                        @click="openSelectionModal(key)"
                      >
                        选择...
                      </a-button>
                    </div>
                  </div>

                  <div
                    v-if="getSelectionOptions(key, value).length <= 5"
                    class="multi-select-cards"
                  >
                    <div class="options-grid">
                      <div
                        v-for="option in isReadOnlyConfig
                          ? getSelectionOptions(key, value).filter((opt) =>
                              isOptionSelected(key, getOptionValue(opt))
                            )
                          : getSelectionOptions(key, value)"
                        :key="getOptionValue(option)"
                        class="option-card"
                        :class="{
                          selected: isOptionSelected(key, getOptionValue(option)),
                          unselected: !isOptionSelected(key, getOptionValue(option)),
                          readonly: isReadOnlyConfig
                        }"
                        @click="!isReadOnlyConfig && toggleOption(key, getOptionValue(option))"
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
                    <!-- Selected Preview Tags -->

                    <div v-if="getSelectedCount(key) > 0" class="selection-preview">
                      <a-tag
                        v-for="val in ensureArray(key)"
                        :key="val"
                        :closable="!isReadOnlyConfig"
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
                  v-else-if="
                    value?.type === 'number' || value?.type === 'int' || value?.type === 'float'
                  "
                  :value="agentConfig[key]"
                  :disabled="isReadOnlyConfig"
                  @update:value="(val) => updateConfigValue(key, val)"
                  :placeholder="getPlaceholder(key, value)"
                  class="config-input-number"
                />

                <!-- 滑块 -->
                <a-slider
                  v-else-if="value?.type === 'slider'"
                  :value="agentConfig[key]"
                  :disabled="isReadOnlyConfig"
                  @update:value="(val) => updateConfigValue(key, val)"
                  :min="value.min"
                  :max="value.max"
                  :step="value.step"
                  class="config-slider"
                />

                <!-- 其他类型 -->
                <a-input
                  v-else
                  :value="agentConfig[key]"
                  :disabled="isReadOnlyConfig"
                  @update:value="(val) => updateConfigValue(key, val)"
                  :placeholder="getPlaceholder(key, value)"
                  class="config-input"
                />
                <div
                  v-if="shouldShowKnowledgeSkillWarning(key)"
                  class="knowledge-skill-warning"
                  role="status"
                  aria-live="polite"
                >
                  <AlertTriangle :size="14" />
                  <span>
                    已启用知识库，但未选择 knowledge-base Skill。Agent
                    可能无法调用知识库检索、打开文档等工具。
                  </span>
                </div>
              </a-form-item>
            </template>
          </a-form>
        </div>
      </div>
    </div>

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
          <template v-if="!isReadOnlyConfig && isResourceConfigKind(currentConfigKind)">
            <a-button
              type="link"
              size="small"
              @click="refreshConfigOptions(currentConfigKey, currentConfigKind)"
              class="inline-action-btn lucide-icon-btn"
              title="刷新列表"
            >
              <RotateCw :size="12" />
              刷新
            </a-button>
            <a-button
              type="link"
              size="small"
              @click="navigateToConfigPage(currentConfigKind)"
              class="inline-action-btn lucide-icon-btn"
              title="跳转配置"
            >
              <Settings :size="12" />
              配置
            </a-button>
            <slot name="resource-actions" :field="currentConfigKey" :kind="currentConfigKind" />
          </template>
        </div>

        <div class="selection-list">
          <div
            v-for="option in filteredOptions"
            :key="getOptionValue(option)"
            class="selection-item"
            :class="{ selected: tempSelectedValues.includes(getOptionValue(option)) }"
            @click="!isReadOnlyConfig && toggleModalSelection(getOptionValue(option))"
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

            <a-button v-if="!isReadOnlyConfig" @click="confirmSelection">
              确认
            </a-button>
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
          :disabled="isReadOnlyConfig"
          :placeholder="systemPromptModalPlaceholder"
          class="system-prompt-modal-input"
        />
      </div>

      <template #footer>
        <div class="system-prompt-modal-footer">
          <a-button
            v-if="!isReadOnlyConfig && hasSystemPromptDefault"
            class="restore-default-btn"
            :disabled="isSystemPromptDefault"
            @click="restoreSystemPromptDefault"
          >
            <template #icon>
              <RotateCcw :size="14" />
            </template>
            恢复默认
          </a-button>
          <div class="system-prompt-modal-actions">
            <a-button @click="closeSystemPromptModal">{{
              isReadOnlyConfig ? '关闭' : '取消'
            }}</a-button>
            <a-button v-if="!isReadOnlyConfig" type="primary" @click="saveSystemPrompt">
              保存
            </a-button>
          </div>
        </div>
      </template>
    </a-modal>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { AlertTriangle, Check, Plus, Search, RotateCw, RotateCcw, Settings, X } from '@lucide/vue'
import ModelSelectorComponent from '@/modules/agents/ui/ModelSelectorComponent.vue'
import {
  getAgentConfigOptionDescription as getOptionDescription,
  getAgentConfigOptionLabel as getOptionLabel,
  getAgentConfigOptions as getConfigOptions,
  getAgentConfigOptionValue as getOptionValue,
  getAgentResourceSelectionOptions,
  isSingleSelectAgentConfig,
  isAllAgentResourceSelection,
  supportsAllAgentResources,
  mergeVisibleAgentResourceSelection,
  getVisibleAgentResourceSelection
} from '@/modules/agents/model/agentConfigUtils'

const props = defineProps({
  modelValue: { type: Object, required: true },
  configurableItems: { type: Object, required: true },
  readonly: { type: Boolean, default: false },
  creatableResourceKinds: { type: Array, default: () => [] },
  segment: {
    type: String,
    default: 'model'
  },
  showSegmented: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['update:modelValue', 'refresh', 'manage-resource'])
const configurableItems = computed(() => props.configurableItems)
/** 默认值只用于展示，未修改的配置不变成持久覆盖。 */
const agentConfig = computed(() => {
  const values = Object.fromEntries(Object.entries(configurableItems.value)
    .filter(([, item]) => item.default !== undefined)
    .map(([key, item]) => [key, item.default]))
  return { ...values, ...props.modelValue }
})
const isReadOnlyConfig = computed(() => props.readonly)
const updateConfig = (patch) => {
  if (!isReadOnlyConfig.value) emit('update:modelValue', { ...props.modelValue, ...patch })
}

// 本地状态
const selectionModalOpen = ref(false)
const currentConfigKey = ref(null)
const tempSelectedValues = ref([])
const selectionSearchText = ref('')
const systemPromptModalOpen = ref(false)
const currentSystemPromptKey = ref(null)
const systemPromptDraft = ref('')
const currentSegment = ref('model')
const segmentOptions = [
  { label: '模型', value: 'model' },
  { label: '工具', value: 'tools' },
  { label: '其他', value: 'other' }
]
const activeSegment = computed(() => (props.showSegmented ? currentSegment.value : props.segment))
const RESOURCE_CONFIG_KINDS = new Set(['tools', 'knowledges', 'mcps', 'skills'])
/** 判断配置项是否属于可管理的资源分组。 */
const isResourceConfigKind = (kind) => RESOURCE_CONFIG_KINDS.has(kind)
const KNOWLEDGE_BASE_SKILL_SLUG = 'knowledge-base'

/** 读取当前表单有效的资源候选项。 */
const getSelectionOptions = (key, item) =>
  getAgentResourceSelectionOptions(key, item, agentConfig.value, configurableItems.value)

const isEmptyConfig = computed(() => {
  return Object.keys(configurableItems.value).length === 0
})

const segmentConfigKeys = computed(() => {
  const keys = Object.keys(configurableItems.value)
  return {
    all: keys,
    model: keys.filter((key) => {
      const meta = configurableItems.value[key]?.kind
      return meta === 'llm' || meta === 'prompt'
    }),
    tools: keys.filter((key) => {
      const meta = configurableItems.value[key]?.kind
      return isResourceConfigKind(meta)
    }),
    other: keys.filter((key) => {
      const meta = configurableItems.value[key]?.kind
      return meta !== 'llm' && meta !== 'prompt' && !isResourceConfigKind(meta)
    })
  }
})

/** 空资源保留创建入口和已有不可见选择的清理入口。 */
const hasSelectableOptions = (key, value) => {
  if (value?.type !== 'list') return true
  if (props.creatableResourceKinds.includes(value.kind)) return true
  return getSelectionOptions(key, value).length > 0 || getHiddenSelection(key).length > 0
}

const filteredConfigurableItems = computed(() => {
  if (isEmptyConfig.value) return {}
  const keys = segmentConfigKeys.value[activeSegment.value] || []
  const filtered = {}
  keys.forEach((key) => {
    const value = configurableItems.value[key]
    if (!hasSelectableOptions(key, value)) return
    filtered[key] = value
  })
  return filtered
})

const isCurrentSegmentEmpty = computed(
  () => !isEmptyConfig.value && Object.keys(filteredConfigurableItems.value).length === 0
)

const refreshConfigOptions = () => {
  if (!isReadOnlyConfig.value) emit('refresh')
}
const navigateToConfigPage = (kind) => {
  if (!isReadOnlyConfig.value) emit('manage-resource', kind)
}

const isDefaultEnabledResourceValue = (value) => value === 'all' || value === undefined

const isResourceEnabled = (value) => {
  if (Array.isArray(value)) return value.length > 0
  return isDefaultEnabledResourceValue(value)
}

const isKnowledgeBaseSkillEnabled = computed(() => {
  const skills = agentConfig.value?.skills
  if (Array.isArray(skills)) return skills.includes(KNOWLEDGE_BASE_SKILL_SLUG)
  return isDefaultEnabledResourceValue(skills)
})

const shouldShowKnowledgeSkillWarning = (key) => {
  if (key !== 'knowledges') return false
  return isResourceEnabled(agentConfig.value?.knowledges) && !isKnowledgeBaseSkillEnabled.value
}

const currentConfigKind = computed(() => {
  if (!currentConfigKey.value) return null
  return configurableItems.value[currentConfigKey.value]?.kind
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

const currentSystemPromptItem = computed(() => {
  if (!currentSystemPromptKey.value) return null
  return configurableItems.value[currentSystemPromptKey.value] || null
})

const hasSystemPromptDefault = computed(() => {
  const currentItem = currentSystemPromptItem.value
  return !!currentItem && Object.prototype.hasOwnProperty.call(currentItem, 'default')
})

const systemPromptDefaultValue = computed(() => {
  if (!hasSystemPromptDefault.value) return ''
  const defaultValue = currentSystemPromptItem.value.default
  return defaultValue == null ? '' : String(defaultValue)
})

const isSystemPromptDefault = computed(
  () => hasSystemPromptDefault.value && systemPromptDraft.value === systemPromptDefaultValue.value
)

const filteredOptions = computed(() => {
  if (!currentConfigKey.value) return []
  const key = currentConfigKey.value
  const configItem = configurableItems.value[key]
  const options = getSelectionOptions(key, configItem)

  if (!selectionSearchText.value) return options

  const search = selectionSearchText.value.toLowerCase()
  return options.filter((opt) => {
    const label = String(getOptionLabel(opt)).toLowerCase()
    const desc = String(getOptionDescription(opt) || '').toLowerCase()
    return label.includes(search) || desc.includes(search)
  })
})

// 方法
const updateConfigValue = (key, value) => {
  if (isReadOnlyConfig.value) return
  updateConfig({
    [key]: value
  })
}

const getConfigLabel = (key, value) => value?.name || key

const getPlaceholder = (_key, value) => {
  return `（默认: ${value.default}）`
}

const handleModelChange = (key, spec) => {
  if (isReadOnlyConfig.value) return
  if (typeof spec !== 'string') return
  updateConfig({
    [key]: spec
  })
}

// 多选相关方法
const ensureArray = (key) => {
  const configItem = configurableItems.value[key]
  return getVisibleAgentResourceSelection(
    agentConfig.value[key],
    configItem,
    getSelectionOptions(key, configItem).map(getOptionValue)
  )
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
  if (isReadOnlyConfig.value) return
  const currentOptions = [...ensureArray(key)]
  const index = currentOptions.indexOf(option)

  if (index > -1) {
    currentOptions.splice(index, 1)
  } else {
    currentOptions.push(option)
  }

  updateConfig({
    [key]: mergeVisibleAgentResourceSelection(
      agentConfig.value[key],
      getSelectionOptions(key, configurableItems.value[key]).map(getOptionValue),
      currentOptions
    )
  })
}

const clearSelection = (key) => {
  if (isReadOnlyConfig.value) return
  updateConfig({
    [key]: []
  })
}

// 统一选择弹窗相关方法
const getOptionLabelFromValue = (key, val) => {
  const options = getSelectionOptions(key, configurableItems.value[key])
  const option = options.find((opt) => getOptionValue(opt) === val)
  return option ? getOptionLabel(option) : val
}

const openSelectionModal = (key) => {
  if (isReadOnlyConfig.value) return
  currentConfigKey.value = key
  tempSelectedValues.value = [...ensureArray(key)]
  selectionModalOpen.value = true
}

const toggleModalSelection = (optionValue) => {
  if (isReadOnlyConfig.value) return
  const index = tempSelectedValues.value.indexOf(optionValue)
  if (index > -1) {
    tempSelectedValues.value.splice(index, 1)
  } else {
    tempSelectedValues.value.push(optionValue)
  }
}

const confirmSelection = () => {
  if (isReadOnlyConfig.value) {
    closeSelectionModal()
    return
  }
  if (currentConfigKey.value) {
    updateConfig({
      [currentConfigKey.value]: mergeVisibleAgentResourceSelection(
        agentConfig.value[currentConfigKey.value],
        getSelectionOptions(
          currentConfigKey.value,
          configurableItems.value[currentConfigKey.value]
        ).map(getOptionValue),
        tempSelectedValues.value
      )
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

const restoreSystemPromptDefault = () => {
  if (isReadOnlyConfig.value || !hasSystemPromptDefault.value) return
  systemPromptDraft.value = systemPromptDefaultValue.value
}

const saveSystemPrompt = () => {
  if (isReadOnlyConfig.value) return
  if (!currentSystemPromptKey.value) return
  updateConfig({
    [currentSystemPromptKey.value]: systemPromptDraft.value
  })
  closeSystemPromptModal()
}

/** 不可访问的既有选择仅用于保留，不作为当前用户可用选项。 */
const getHiddenSelection = (key) =>
  mergeVisibleAgentResourceSelection(
    agentConfig.value[key],
    getSelectionOptions(key, configurableItems.value[key]).map(getOptionValue),
    []
  )

/** 默认全部与显式全部使用相同选中状态。 */
const isAllSelected = (key) =>
  isAllAgentResourceSelection(agentConfig.value[key], configurableItems.value[key])

const canResetSelection = (key) =>
  isAllSelected(key) ||
  getSelectedCount(key) > 0 ||
  getHiddenSelection(key).length > 0

/** 全选保存动态全部意图，状态区说明包含新增资源。 */
const setAllSelection = (key) => {
  if (isReadOnlyConfig.value) return
  updateConfig({ [key]: 'all' })
}
defineExpose({ closeSelectionModal })
</script>

<style lang="less" scoped>
.agent-runtime-config-form {
  background: var(--gray-0);

  .hidden-selection-note {
    margin-bottom: 8px;
    color: var(--color-text-secondary);
    font-size: 12px;
  }

  .runtime-config-content {
    flex: 1;
    overflow-y: auto;
    padding: 10px 12px 8px;
    min-width: 360px;

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

    .config-segment {
      margin: 0 auto;
      margin-bottom: 6px;
      padding: 4px 0;
      width: 80%;
    }

    .config-form-content {
      margin-bottom: 20px;

      &.is-readonly {
        .config-item {
          .model-selector.is-readonly {
            opacity: 0.78;
            pointer-events: none;
          }

          .system-prompt-display {
            cursor: default;

            &:hover {
              border-color: var(--gray-200);
              background: transparent;

              .edit-hint {
                opacity: 1;
              }
            }

            .edit-hint {
              color: var(--gray-500);
              opacity: 1;
            }
          }

          .option-card.readonly {
            cursor: default;

            &:hover {
              border-color: var(--gray-300);
              background: var(--gray-0);
            }

            &.selected:hover {
              border-color: var(--runtime-selection-border, var(--main-color));
              background: var(--runtime-selection-bg, var(--main-10));
            }
          }
        }
      }

      .config-form {
        .config-alert,
        .config-empty {
          margin-bottom: 16px;
        }

        .config-item {
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

          .knowledge-skill-warning {
            display: flex;
            align-items: flex-start;
            gap: 6px;
            margin-top: 8px;
            padding: 8px 10px;
            border: 1px solid var(--color-warning-100);
            border-radius: 6px;
            background: var(--color-warning-50);
            color: var(--color-warning-900);
            font-size: 12px;
            line-height: 1.5;

            svg {
              flex: 0 0 auto;
              margin-top: 2px;
              color: var(--color-warning-500);
            }

            span {
              min-width: 0;
              overflow-wrap: anywhere;
            }
          }
        }
      }
    }
  }
}

// 选择器样式
.selection-container {
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
  .options-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr));
    gap: 8px;
  }

  .option-card {
    min-width: 0;
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
      border-color: var(--runtime-selection-border, var(--main-color));
      background: var(--runtime-selection-bg, var(--main-10));

      .option-indicator {
        color: var(--runtime-selection-accent, var(--main-color));
      }

      .option-text {
        color: var(--runtime-selection-text, var(--main-color));
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
      min-width: 0;

      .option-text {
        flex: 1;
        min-width: 0;
        font-size: 13px;
        line-height: 1.4;
        overflow-wrap: anywhere;
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
          border-color: var(--gray-600);
          box-shadow: 0 0 0 2px var(--gray-200);

          .search-icon {
            color: var(--gray-900);
          }
        }

        &:hover {
          border-color: var(--gray-400);
        }
      }
    }

    .selection-list {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      max-height: 60vh;
      overflow-y: auto;
      border-radius: 8px;
      margin-bottom: 16px;

      // 在小屏幕下调整为单列布局
      @media (max-width: 480px) {
        grid-template-columns: 1fr;
      }

      .selection-item {
        min-width: 0;
        padding: 12px 16px;
        cursor: pointer;
        transition: all 0.2s ease;
        border-radius: 8px;
        background: var(--gray-0);
        border: 1px solid var(--gray-200);

        &:hover {
          border-color: var(--gray-300);
          background: var(--gray-25);
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
              min-width: 0;
              overflow-wrap: anywhere;
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
          background: var(--gray-50);
          border-color: var(--gray-600);

          .selection-item-content {
            .selection-item-name {
              color: var(--gray-900);
            }
            .selection-item-indicator {
              color: var(--gray-900);
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
          height: 36px;
          font-size: 14px;
          padding: 0 16px;

          &:hover, &:focus-visible {
            color: var(--gray-900);
            border-color: var(--gray-600);
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

  .system-prompt-modal-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    width: 100%;

    .restore-default-btn {
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }

    .system-prompt-modal-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-left: auto;
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

.selection-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;
  min-height: 28px;
  margin-bottom: 10px;
}

.selection-count {
  flex: 0 1 auto;
  color: var(--gray-600);
  font-size: 12px;
  line-height: 1.5;
}

.selection-summary {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.selection-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-left: auto;
}

.select-all-btn {
  height: 28px;
  padding: 0 6px;
  font-size: 12px;
  color: var(--main-700);
}

.selection-trigger-btn {
  height: 28px;
  border-radius: var(--radius-control);
  font-size: 12px;
}

.clear-btn {
  width: 28px;
  height: 28px;
  padding: 0;
  color: var(--gray-500);

  &:hover {
    color: var(--gray-900);
  }
}

.inline-action-btn, :deep(.inline-action-btn) {
  gap: 3px;
  padding: 0 6px;
  height: 28px;
  line-height: 1;
  font-size: 12px;
  color: var(--gray-600);
  white-space: nowrap;

  &:hover {
    color: var(--gray-900);
  }
}

.selection-search .inline-action-btn, .selection-search :deep(.inline-action-btn) {
  font-size: 13px;
}

:global(:root.dark) .agent-runtime-config-form {
  --runtime-selection-bg: color-mix(in srgb, var(--main-color) 12%, var(--gray-25));
  --runtime-selection-border: var(--main-600);
  --runtime-selection-accent: var(--main-700);
  --runtime-selection-text: var(--gray-900);
}

@media (max-width: 600px) {
  .selection-modal .selection-modal-content .selection-search {
    flex-wrap: wrap;
    .search-input { flex: 0 0 100%; min-width: 0; }
    :deep(.ant-btn) { min-height: 40px; }
  }
}
</style>
