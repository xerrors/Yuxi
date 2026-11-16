<template>
  <BaseToolCall
    :tool-call="toolCall"
    :status="baseStatus"
    :force-show-result="hasDisplayResult"
  >
    <template #header>
      <div class="sep-header">
        <span class="note">{{ subagentDisplayName }}</span>
        <span v-if="runStatusLabel" class="run-status" :class="runStatusClass">
          {{ runStatusLabel }}
        </span>
        <span class="separator" v-if="headerDetail">|</span>
        <span class="description" :class="{ 'is-live': isRunning && liveStep }" v-if="headerDetail">
          {{ headerDetail }}
        </span>
      </div>
    </template>

    <template #params>
      <div v-if="description" class="task-description">{{ description }}</div>
    </template>

    <template #result>
      <div class="task-result">
        <MarkdownPreview compact :content="String(displayResult)" class="md-preview-wrapper" />
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed, inject } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import { MessageProcessor } from '@/utils/messageProcessor'
import { getToolCallDisplayStatus, parseToolCallArgs } from '../toolRegistry'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  }
})

const getThreadOngoingMessages = inject('getThreadOngoingMessages', null)
const getSubagentThreadIdByToolCall = inject('getSubagentThreadIdByToolCall', null)
const activeSubagentToolCallIds = inject('activeSubagentToolCallIds', null)

const parsedArgs = computed(() => parseToolCallArgs(props.toolCall))

const subagentRun = computed(() => props.toolCall.subagent_run || null)
const subagentDisplayName = computed(
  () => subagentRun.value?.subagent_name || props.toolCall.display_label || '子智能体'
)
const description = computed(
  () => parsedArgs.value.description || subagentRun.value?.description || ''
)
const childThreadId = computed(
  () =>
    subagentRun.value?.child_thread_id ||
    parsedArgs.value.thread_id ||
    (getSubagentThreadIdByToolCall ? getSubagentThreadIdByToolCall(props.toolCall.id) : '') ||
    ''
)
const runStatus = computed(() => {
  const status = getToolCallDisplayStatus(props.toolCall, activeSubagentToolCallIds?.value)
  return status === 'error' ? 'failed' : status
})
const runStatusLabel = computed(() => {
  if (runStatus.value === 'completed') return '已完成'
  if (runStatus.value === 'failed') return '失败'
  if (runStatus.value === 'running') return '运行中'
  return ''
})
const runStatusClass = computed(() => ({
  'is-running': runStatus.value === 'running',
  'is-completed': runStatus.value === 'completed',
  'is-failed': runStatus.value === 'failed'
}))
// 映射到 BaseToolCall 的图标状态（failed → error）
const baseStatus = computed(() => (runStatus.value === 'failed' ? 'error' : runStatus.value))
// ongoing 期间 task 结果不流式：只展示工具结果，状态摘要不承载后端预览文本。
const displayResult = computed(() => {
  return props.toolCall.tool_call_result?.content ?? props.toolCall.result ?? ''
})
const hasDisplayResult = computed(() => displayResult.value !== '')
const shortDescription = computed(() => {
  const desc = description.value
  if (!desc) return ''
  return desc.length > 50 ? desc.slice(0, 50) + '...' : desc
})

const isRunning = computed(() => runStatus.value === 'running')

const truncate = (text, limit = 50) => {
  const value = String(text || '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!value) return ''
  return value.length > limit ? value.slice(0, limit) + '...' : value
}

const formatToolCall = (toolCall) => {
  const name = toolCall?.name || toolCall?.function?.name || 'tool'
  const rawArgs = toolCall?.args ?? toolCall?.function?.arguments
  const args =
    rawArgs && typeof rawArgs === 'object' ? JSON.stringify(rawArgs) : String(rawArgs ?? '')
  return `Call(${name}): ${args}`
}

// 子线程实时轨迹：最新一条 ongoing 消息——优先展示工具调用，否则展示正文。
const liveStep = computed(() => {
  if (!isRunning.value || !childThreadId.value || typeof getThreadOngoingMessages !== 'function') {
    return ''
  }
  const messages = getThreadOngoingMessages(childThreadId.value)
  const last = messages[messages.length - 1]
  if (!last) return ''
  const toolCalls = last.tool_calls
  if (Array.isArray(toolCalls) && toolCalls.length) {
    return truncate(formatToolCall(toolCalls[toolCalls.length - 1]), 80)
  }
  const { content, reasoningContent } = MessageProcessor.parseAssistantMessageBody(last)
  const body = content || reasoningContent
  if (body) return truncate(body)
  return ''
})

const headerDetail = computed(() => {
  if (isRunning.value && liveStep.value) return liveStep.value
  return shortDescription.value
})
</script>

<style lang="less" scoped>
.sep-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  width: 100%;
  overflow: hidden;
}

.run-status {
  flex-shrink: 0;
  border-radius: 4px;
  padding: 0 4px;
  font-size: 11px;
  background: var(--gray-25);
  color: var(--gray-600);

  &.is-running {
    color: var(--color-primary-700);
    background: var(--color-primary-50);
  }

  &.is-completed {
    color: var(--color-success-700);
    background: var(--color-success-50);
  }

  &.is-failed {
    color: var(--color-error-700);
    background: var(--color-error-50);
  }
}

.sep-header .description {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;

  &.is-live {
    color: var(--color-primary-700);
  }
}

.task-description {
  border-radius: 8px;
  font-size: 13px;
  color: var(--gray-800);
  padding: 6px 8px;
  background: var(--gray-50);
}

.task-result {
  border-radius: 8px;
  padding: 0 12px;
  max-height: min(600px, 40vh);
  overflow: auto;

  .md-preview-wrapper {
    color: var(--gray-800);
  }
}
</style>
