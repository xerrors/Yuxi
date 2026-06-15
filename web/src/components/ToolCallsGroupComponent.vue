<template>
  <div v-if="normalizedToolCalls.length > 0" class="tool-calls-container">
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
        <component :is="areToolCallsExpanded ? ChevronDown : ChevronRight" size="14" />
      </span>
    </button>

    <div v-if="!shouldCollapseToolCalls || areToolCallsExpanded" class="tool-calls-panel">
      <div
        v-for="(toolCall, index) in normalizedToolCalls"
        :key="toolCall.id || `${getToolCallId(toolCall)}-${index}`"
        class="tool-call-container"
      >
        <ToolCallRenderer :tool-call="toolCall" appearance="timeline" :default-expanded="false" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, inject } from 'vue'
import { ChevronDown, ChevronRight, Atom } from 'lucide-vue-next'
import { ToolCallRenderer } from '@/components/ToolCallingResult'
import { getToolCallId, normalizeToolCalls } from '@/components/ToolCallingResult/toolRegistry'

const activeSubagentToolCallIds = inject('activeSubagentToolCallIds', null)

// task 工具结果不随流式返回，不能用 tool_call_result 判断运行中：只有「活跃」的 task 才算运行中。
const toolRunState = (toolCall) => {
  if (toolCall.status === 'error') return 'error'
  if (toolCall.tool_call_result || toolCall.status === 'success') return 'completed'
  if (getToolCallId(toolCall) === 'task') {
    return activeSubagentToolCallIds?.value?.has(String(toolCall.id)) ? 'running' : 'completed'
  }
  return 'running'
}

const props = defineProps({
  toolCalls: {
    type: Array,
    default: () => []
  },
  isActive: {
    type: Boolean,
    default: false
  }
})

const normalizedToolCalls = computed(() => normalizeToolCalls(props.toolCalls))

const shouldCollapseToolCalls = computed(() => normalizedToolCalls.value.length > 0)
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

const getToolCallLabel = (toolCall) => {
  const displayLabel = String(toolCall?.display_label || '').trim()
  if (displayLabel) return displayLabel

  const rawName = getToolCallId(toolCall)
  const name = typeof rawName === 'string' ? rawName.replaceAll('_', ' ') : 'tool'
  return name.charAt(0).toUpperCase() + name.slice(1)
}

const toolCallsSummaryTitle = computed(() => {
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
  const states = normalizedToolCalls.value.map(toolRunState)
  const successCount = states.filter((state) => state === 'completed').length
  const runningCount = states.filter((state) => state === 'running').length
  const errorCount = states.filter((state) => state === 'error').length

  const parts = []
  if (successCount > 0 && successCount === normalizedToolCalls.value.length) {
    return '已完成'
  }
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
    transition: all 0.2s ease;
    user-select: none;
    background: transparent;

    &:hover {
      color: var(--gray-800);
    }

    &.is-expanded {
      color: var(--gray-800);
      margin-bottom: 4px;
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
      padding: 0px 4px;
      // background: var(--gray-25);
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
  }

  .tool-calls-panel {
    border-top: 1px solid var(--gray-100);
    padding-top: 4px;
    margin-top: 4px;
    margin-bottom: 8px;
  }

  .tool-call-container {
    margin-bottom: 4px;
    &:last-child {
      margin-bottom: 0;
    }
  }
}
</style>
