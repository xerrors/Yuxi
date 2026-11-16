<template>
  <div v-if="displayEntries.length > 0" class="tool-calls-container">
    <button
      v-if="shouldCollapseToolCalls"
      type="button"
      class="tool-calls-summary"
      :class="{ 'is-expanded': areToolCallsExpanded }"
      :aria-expanded="areToolCallsExpanded"
      @click="toggleToolCallsExpanded"
    >
      <span class="summary-leading">
        <Atom size="14" />
      </span>
      <span class="summary-content">
        <span class="summary-title">{{ toolCallsSummaryTitle }}</span>
        <span class="summary-separator" v-if="normalizedToolCalls.length > 1 && toolCallsNamesMeta"
          >·</span
        >
        <span class="summary-meta" v-if="normalizedToolCalls.length > 1 && toolCallsNamesMeta">{{
          toolCallsNamesMeta
        }}</span>
        <span class="summary-status-tag" v-if="statusSummary">{{ statusSummary }}</span>
      </span>
      <span class="summary-trailing">
        <ChevronDown
          :size="14"
          class="summary-chevron"
          :class="{ 'is-collapsed': !areToolCallsExpanded }"
        />
      </span>
    </button>

    <div
      class="tool-calls-collapse-panel"
      :class="{ 'is-expanded': !shouldCollapseToolCalls || areToolCallsExpanded }"
    >
      <div class="tool-calls-collapse-inner">
        <div class="tool-calls-panel">
          <div v-for="entry in displayEntries" :key="entry.key" class="tool-call-container">
            <ReasoningBlockComponent
              v-if="entry.type === 'reasoning'"
              :content="entry.content"
              :is-active="isActive && entry === displayEntries[displayEntries.length - 1]"
            />
            <ToolCallRenderer
              v-else
              :tool-call="entry.toolCall"
              appearance="timeline"
              :default-expanded="false"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, inject } from 'vue'
import { ChevronDown, Atom } from '@lucide/vue'
import { storeToRefs } from 'pinia'
import { useAgentStore } from '@/stores/agent'
import ReasoningBlockComponent from '@/components/ReasoningBlockComponent.vue'
import { ToolCallRenderer } from '@/components/ToolCallingResult'
import {
  getToolCallId,
  getToolCallDisplayStatus,
  getToolName,
  findToolInList,
  normalizeToolCalls
} from '@/components/ToolCallingResult/toolRegistry'

const agentStore = useAgentStore()
const { availableTools, toolMetadata } = storeToRefs(agentStore)

const activeSubagentToolCallIds = inject('activeSubagentToolCallIds', null)

const props = defineProps({
  toolCalls: {
    type: Array,
    default: () => []
  },
  entries: {
    type: Array,
    default: () => []
  },
  isActive: {
    type: Boolean,
    default: false
  }
})

const normalizedToolCalls = computed(() => normalizeToolCalls(props.toolCalls))

const displayEntries = computed(() =>
  props.entries.length
    ? props.entries
    : normalizedToolCalls.value.map((toolCall, index) => ({
        type: 'tool',
        key: toolCall.id || `${getToolCallId(toolCall)}-${index}`,
        toolCall
      }))
)
const hasReasoning = computed(() =>
  displayEntries.value.some((entry) => entry.type === 'reasoning')
)
const shouldCollapseToolCalls = computed(() => displayEntries.value.length > 0)
const areToolCallsExpanded = ref(false)

watch(
  [() => normalizedToolCalls.value.length, () => props.isActive],
  ([, isActive], [, previousActive]) => {
    // 如果是活跃状态，强制展开
    if (isActive) {
      areToolCallsExpanded.value = true
      return
    }

    // 从活跃转为非活跃（例如：正文开始输出了），则收起
    if (previousActive === true && isActive === false) {
      areToolCallsExpanded.value = false
      return
    }

    // 初始化或非活跃状态下，默认保持收起
    if (!previousActive && !isActive) {
      areToolCallsExpanded.value = false
    }
  },
  { immediate: true }
)

// 工具名称展示优先级：display_label > 完整工具元数据中的 display name > 前端兜底名称映射 > 工具 id
const getToolCallLabel = (toolCall) => {
  const displayLabel = String(toolCall?.display_label || '').trim()
  if (displayLabel) return displayLabel

  const toolId = getToolCallId(toolCall)
  const toolsList = toolMetadata.value.length
    ? toolMetadata.value
    : availableTools.value
      ? Object.values(availableTools.value)
      : []
  const tool = findToolInList(toolId, toolsList)
  return tool ? tool.name : getToolName(toolId)
}

const toolCallsSummaryTitle = computed(() => {
  if (normalizedToolCalls.value.length === 0) return props.isActive ? 'Thinking...' : '推理过程'
  if (hasReasoning.value) return `推理与工具调用 · ${normalizedToolCalls.value.length} 个工具`
  if (normalizedToolCalls.value.length === 1) {
    return `调用: ${getToolCallLabel(normalizedToolCalls.value[0])}`
  }
  return `已调用 ${normalizedToolCalls.value.length} 个工具`
})

const toolCallsNamesMeta = computed(() => {
  const names = normalizedToolCalls.value.map(getToolCallLabel).filter(Boolean)
  const uniqueNames = [...new Set(names)]
  const visibleNames = uniqueNames.slice(0, 3)

  if (visibleNames.length === 0) return ''

  return `${visibleNames.join(' · ')}${uniqueNames.length > visibleNames.length ? ` +${uniqueNames.length - visibleNames.length}` : ''}`
})

const statusSummary = computed(() => {
  const states = normalizedToolCalls.value.map((toolCall) =>
    getToolCallDisplayStatus(toolCall, activeSubagentToolCallIds?.value)
  )
  const runningCount = states.filter((state) => state === 'running').length
  const errorCount = states.filter((state) => state === 'error').length

  const parts = []
  if (errorCount > 0) parts.push(`${errorCount} 失败`)
  if (runningCount > 0) parts.push(`${runningCount} 进行中`)

  return parts.join(' · ')
})

const toggleToolCallsExpanded = () => {
  if (!shouldCollapseToolCalls.value) return
  areToolCallsExpanded.value = !areToolCallsExpanded.value
}
</script>

<style lang="less" scoped>
.tool-calls-container {
  width: 100%;
  padding: 0;
  display: flex;
  flex-direction: column;

  .tool-calls-summary {
    appearance: none;
    width: auto;
    max-width: 100%;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    color: var(--gray-700);
    text-align: left;
    cursor: pointer;
    outline: none;
    border: none;
    padding: 0;
    transition: color 0.15s ease;
    user-select: none;
    background: transparent;

    &:focus-visible {
      outline: 2px solid var(--main-300);
      outline-offset: 2px;
    }

    &:hover {
      color: var(--gray-800);

      .summary-chevron {
        color: var(--gray-700);
      }
    }

    &.is-expanded {
      color: var(--gray-800);
    }

    .summary-leading {
      display: inline-flex;
      align-items: center;
      color: var(--gray-700);
      flex-shrink: 0;
    }

    .summary-content {
      min-width: 0;
      display: flex;
      align-items: center;
      gap: 6px;
      flex: 1;
      font-size: 13px;
    }

    .summary-title {
      font-weight: 400;
      white-space: nowrap;
    }

    .summary-separator {
      color: var(--gray-500);
      flex-shrink: 0;
    }

    .summary-meta {
      color: var(--gray-600);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .summary-status-tag {
      margin-left: 4px;
      font-size: 11px;
      padding: 0 4px;
      color: var(--gray-600);
      border-radius: 4px;
      white-space: nowrap;
      font-weight: normal;
    }

    .summary-trailing {
      display: inline-flex;
      align-items: center;
      color: var(--gray-500);
      flex-shrink: 0;
    }

    .summary-chevron {
      flex-shrink: 0;
      color: var(--gray-400);
      transition:
        transform 0.22s cubic-bezier(0.16, 1, 0.3, 1),
        color 0.18s ease;

      &.is-collapsed {
        transform: rotate(-90deg);
      }
    }
  }

  .tool-calls-collapse-panel {
    display: grid;
    grid-template-rows: 0fr;
    transition:
      grid-template-rows 0.24s cubic-bezier(0.16, 1, 0.3, 1),
      visibility 0.24s ease;
    visibility: hidden;
    min-width: 0;

    &.is-expanded {
      grid-template-rows: 1fr;
      visibility: visible;
    }
  }

  .tool-calls-collapse-inner {
    overflow: hidden;
    min-height: 0;
    opacity: 0;
    transform: translateY(-4px);
    transition:
      opacity 0.2s ease,
      transform 0.2s cubic-bezier(0.16, 1, 0.3, 1);
  }

  .tool-calls-collapse-panel.is-expanded .tool-calls-collapse-inner {
    opacity: 1;
    transform: translateY(0);
  }

  .tool-calls-panel {
    border-top: 1px solid var(--gray-100);
    padding-top: 6px;
    margin-top: 6px;
    margin-bottom: 8px;
  }

  .tool-call-container {
    margin-bottom: 4px;
    &:last-child {
      margin-bottom: 0;
    }
  }
}

@media (prefers-reduced-motion: reduce) {
  .tool-calls-container {
    .tool-calls-summary .summary-chevron,
    .tool-calls-collapse-panel,
    .tool-calls-collapse-inner {
      transition: none;
    }
  }
}
</style>
