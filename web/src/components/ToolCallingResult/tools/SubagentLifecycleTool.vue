<template>
  <BaseToolCall
    :tool-call="toolCall"
    :appearance="appearance"
    :default-expanded="defaultExpanded"
    :status="baseStatus"
    :force-show-result="hasParsedResult"
    hide-params
  >
    <template #header>
      <div class="sep-header">
        <span class="note">{{ headerTitle }}</span>
        <span v-if="statusLabel" class="run-status" :class="runStatusClass">
          {{ statusLabel }}
        </span>
        <span class="separator" v-if="headerDetail">|</span>
        <span class="description" v-if="headerDetail">{{ headerDetail }}</span>
      </div>
    </template>

    <template #result>
      <div class="subagent-result">
        <div class="meta-grid" v-if="metaItems.length">
          <div v-for="item in metaItems" :key="item.label" class="meta-item">
            <span class="meta-label">{{ item.label }}</span>
            <span class="meta-value">{{ item.value }}</span>
          </div>
        </div>

        <div v-if="progressMessages.length" class="progress-list">
          <div
            v-for="(message, index) in progressMessages"
            :key="progressMessageKey(message, index)"
            class="progress-row"
          >
            <span class="progress-kind">{{ progressKindLabel(message.kind) }}</span>
            <span class="progress-content">{{ message.content }}</span>
          </div>
        </div>

        <div v-if="events.length" class="events-list">
          <div
            v-for="event in events"
            :key="event.seq || event.event || event.type"
            class="event-row"
          >
            <span class="event-seq">{{ event.seq || '-' }}</span>
            <span class="event-name">{{
              event.event || event.type || event.method || 'event'
            }}</span>
          </div>
        </div>

        <MarkdownPreview
          v-if="resultText"
          compact
          :content="resultText"
          class="md-preview-wrapper"
        />

        <pre v-else-if="fallbackResult">{{ fallbackResult }}</pre>
      </div>
    </template>
  </BaseToolCall>
</template>

<script setup>
import { computed } from 'vue'
import BaseToolCall from '../BaseToolCall.vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import {
  getToolCallId,
  parseToolCallArgs,
  parseToolCallResult,
  getSubagentRunStatus,
  getToolCallDisplayStatus
} from '../toolRegistry'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  },
  appearance: {
    type: String,
    default: 'card'
  },
  defaultExpanded: {
    type: Boolean,
    default: false
  }
})

const TOOL_LABELS = {
  subagent_start: '启动子智能体',
  subagent_status: '查询子智能体',
  subagent_events: '读取子智能体事件',
  subagent_cancel: '取消子智能体',
  subagent_await: '等待子智能体'
}

const PROGRESS_KIND_LABELS = {
  assistant_message: '消息',
  assistant_reasoning: '思考',
  tool_call: '工具',
  tool_call_delta: '工具'
}

const STATUS_LABELS = {
  busy: '忙碌',
  cancelled: '已取消',
  cancel_requested: '取消中',
  completed: '已完成',
  existing: '已存在',
  error: '失败',
  failed: '失败',
  interrupted: '已中断',
  ok: '成功',
  pending: '等待中',
  running: '运行中',
  started: '已启动',
  success: '成功'
}

const failedStatuses = new Set(['error', 'failed', 'cancelled', 'interrupted'])

const toolId = computed(() => getToolCallId(props.toolCall))
const args = computed(() => parseToolCallArgs(props.toolCall))
const parsedResult = computed(() => parseToolCallResult(props.toolCall))
const hasParsedResult = computed(
  () => parsedResult.value !== null && parsedResult.value !== undefined
)
const subagentRun = computed(() => props.toolCall.subagent_run || null)

const headerTitle = computed(() => {
  const name =
    parsedResult.value?.subagent_name ||
    subagentRun.value?.subagent_name ||
    props.toolCall.display_label ||
    args.value.subagent_slug ||
    parsedResult.value?.subagent_slug ||
    subagentRun.value?.subagent_slug ||
    ''
  const label = TOOL_LABELS[toolId.value] || '子智能体'
  return name ? `${label}: ${name}` : label
})

const effectiveStatus = computed(() => getSubagentRunStatus(props.toolCall))

const statusLabel = computed(() => {
  const status = String(effectiveStatus.value || '').trim()
  return STATUS_LABELS[status] || status
})

const runStatusClass = computed(() => ({
  'is-running': effectiveStatus.value === 'running' || effectiveStatus.value === 'pending',
  'is-completed':
    effectiveStatus.value === 'started' ||
    effectiveStatus.value === 'existing' ||
    effectiveStatus.value === 'ok' ||
    effectiveStatus.value === 'success' ||
    effectiveStatus.value === 'completed',
  'is-failed': failedStatuses.has(effectiveStatus.value)
}))

const baseStatus = computed(() => getToolCallDisplayStatus(props.toolCall))

const shortText = (value, limit = 60) => {
  const text = String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!text) return ''
  return text.length > limit ? `${text.slice(0, limit)}...` : text
}

const headerDetail = computed(() => {
  if (parsedResult.value?.message) return shortText(parsedResult.value.message)
  if (args.value.description) return shortText(args.value.description)
  if (parsedResult.value?.run_id || args.value.run_id) {
    return `run: ${shortText(parsedResult.value?.run_id || args.value.run_id, 18)}`
  }
  return ''
})

const metaItems = computed(() => {
  const result =
    parsedResult.value && typeof parsedResult.value === 'object' ? parsedResult.value : {}
  const items = [
    ['run_id', result.run_id || args.value.run_id || result.active_run_id],
    ['thread_id', result.thread_id || args.value.thread_id || subagentRun.value?.child_thread_id],
    [
      'subagent_slug',
      result.subagent_slug || args.value.subagent_slug || subagentRun.value?.subagent_slug
    ],
    ['last_seq', result.last_seq || result.progress?.last_seq],
    ['events', Array.isArray(result.events) ? `${result.events.length} 条` : '']
  ]
  return items
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([label, value]) => ({ label, value: String(value) }))
})

const events = computed(() => {
  const value = parsedResult.value?.events
  return Array.isArray(value) ? value.slice(0, 8) : []
})

const progressMessages = computed(() => {
  const value = parsedResult.value?.progress?.messages
  if (!Array.isArray(value)) return []
  return value
    .filter((message) => String(message?.content || '').trim())
    .slice(0, 3)
    .map((message) => ({
      ...message,
      content: String(message.content || '').trim()
    }))
})

const progressKindLabel = (kind) => PROGRESS_KIND_LABELS[kind] || '进度'

const progressMessageKey = (message, index) =>
  [message?.seq, message?.message_id, message?.tool_call_id, index].filter(Boolean).join(':')

const resultText = computed(() => {
  const result = parsedResult.value?.result
  if (!result) return ''
  if (typeof result === 'string') return result
  if (typeof result?.output === 'string') return result.output
  if (typeof result?.error?.message === 'string') return result.error.message
  return ''
})

const fallbackResult = computed(() => {
  if (
    !hasParsedResult.value ||
    resultText.value ||
    events.value.length ||
    progressMessages.value.length
  )
    return ''
  return JSON.stringify(parsedResult.value, null, 2)
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

.description {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subagent-result {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0 4px;
  color: var(--gray-800);

  pre {
    margin: 0;
    max-height: 240px;
    overflow: auto;
    padding: 8px;
    border-radius: 6px;
    background: var(--gray-25);
    font-size: 12px;
    line-height: 1.45;
    white-space: pre-wrap;
    word-break: break-word;
  }
}

.meta-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 4px;
}

.meta-item {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  gap: 8px;
  font-size: 12px;
  line-height: 1.5;
}

.meta-label {
  color: var(--gray-500);
}

.meta-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  color: var(--gray-700);
}

.progress-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border-radius: 6px;
  background: var(--gray-25);
}

.progress-row {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  min-width: 0;
  font-size: 12px;
  line-height: 1.5;
}

.progress-kind {
  color: var(--gray-500);
}

.progress-content {
  min-width: 0;
  color: var(--gray-800);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.events-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--gray-25);
}

.event-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}

.event-seq {
  flex-shrink: 0;
  color: var(--gray-500);
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
}

.event-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--gray-700);
}
</style>
