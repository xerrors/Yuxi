<template>
  <BaseToolCall v-if="isList" :tool-call="toolCall" :appearance="appearance" summary-only>
    <template #header>
      <div class="sep-header">
        <span class="note">{{ status === 'completed' ? '查看了' : '查看' }}{{ listScope }}会话</span>
        <span v-if="status === 'completed' && listSessions" class="description">{{ listSessions.length }} 个</span>
      </div>
    </template>
  </BaseToolCall>
  <div v-else class="cooperation-event" :class="{ 'has-error': status === 'error' }">
    <div class="cooperation-event-row">
      <component :is="eventIcon" :size="15" class="event-icon" aria-hidden="true" />
      <span class="event-action">{{ action }}</span>
      <span class="event-targets">
        <template v-for="(target, index) in targets" :key="target.sessionId || index">
          <component
            :is="target.sessionId && cooperationView ? 'button' : 'span'"
            :type="target.sessionId && cooperationView ? 'button' : undefined"
            class="event-target"
            :title="target.name || '会话信息尚未返回'"
            @click="target.sessionId && cooperationView?.openSession(target.sessionId)"
          >
            <FallbackAvatar v-if="target.sessionId" :name="target.name" :seed="target.sessionId" kind="session" :size="18" shape="rounded" decorative />
            <span>{{ target.name || '会话' }}</span>
          </component>
        </template>
      </span>
      <span v-if="status === 'error'" class="event-state event-error">操作失败</span>
      <span v-else-if="status === 'waiting'" class="event-state">等待中</span>
      <span v-else-if="status === 'running'" class="event-state">进行中</span>
      <span v-else-if="status === 'incomplete'" class="event-state">已中断</span>
      <span v-else-if="timedOut" class="event-state">等待超时</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { CirclePlus, Clock, Workflow, Send, Square, CircleAlert } from '@lucide/vue'
import BaseToolCall from '../BaseToolCall.vue'
import FallbackAvatar from '@/shared/ui/FallbackAvatar.vue'
import { getToolCallId, getToolCallStatus, parseToolCallArgs, parseToolCallResult } from '@/modules/session/model/toolCallProjection'
import { COOPERATION_ACTIONS, getCooperationToolTargets } from '@/modules/session/model/cooperationToolView'

const props = defineProps({
  toolCall: { type: Object, required: true },
  toolCalls: { type: Array, default: () => [] },
  appearance: { type: String, default: 'timeline' },
  defaultExpanded: { type: Boolean, default: false },
  cooperationView: { type: Object, default: null }
})
const calls = computed(() => props.toolCalls.length ? props.toolCalls : [props.toolCall])
const toolId = computed(() => getToolCallId(props.toolCall))
const action = computed(() => COOPERATION_ACTIONS[toolId.value])
const status = computed(() => getToolCallStatus(props.toolCall))
const isList = computed(() => toolId.value === 'list_sessions')
const listSessions = computed(() => {
  const result = parseToolCallResult(props.toolCall)
  return Array.isArray(result?.sessions) ? result.sessions : null
})
const listScope = computed(() => {
  const result = parseToolCallResult(props.toolCall)
  // 旧回执没有范围字段，其契约是查询全部历史会话。
  const scope = result?.scope || parseToolCallArgs(props.toolCall).scope || (listSessions.value ? 'all' : 'project')
  return scope === 'all' ? '历史' : '本项目'
})
const timedOut = computed(() => calls.value.some((call) => parseToolCallResult(call)?.wait_timed_out))
const eventIcon = computed(() => {
  if (status.value === 'error') return CircleAlert
  if (toolId.value.startsWith('wait_')) return Clock
  if (toolId.value === 'create_session') return CirclePlus
  if (toolId.value === 'cancel_turn') return Square
  if (['send_message', 'submit_input'].includes(toolId.value)) return Send
  return Workflow
})
const targets = computed(() => {
  const context = props.cooperationView || { sessions: [], tasks: new Map() }
  const items = calls.value.flatMap((call) => getCooperationToolTargets(call, context))
  const unique = items.filter((target, index) => !target.sessionId || items.findIndex((item) => item.sessionId === target.sessionId) === index)
  return unique.length ? unique : [{ name: '' }]
})
</script>

<style scoped lang="less">
.cooperation-event { padding: 4px 0; min-width: 0; color: var(--color-text-secondary); font-size: 13px; }
.cooperation-event-row { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 8px; min-width: 0; min-height: 26px; line-height: 26px; }
.event-icon { flex-shrink: 0; margin-top: 5px; }
.event-action, .event-state { flex-shrink: 0; }
.event-state { font-size: 12px; color: var(--gray-500); }
.event-targets { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 12px; min-width: 0; }
.event-target { display: inline-flex; align-items: center; gap: 6px; min-width: 0; max-width: min(240px, 100%); vertical-align: middle; }
.event-target > span:last-of-type { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.event-target :deep(.fallback-avatar) { flex-shrink: 0; }
.event-target svg { flex-shrink: 0; }
button.event-target {
  font: inherit; color: inherit; line-height: inherit; border: 0; padding: 0; background: transparent; cursor: pointer; text-align: left;
  &:hover { color: var(--color-text); }
  &:focus-visible { outline: 2px solid var(--main-300); outline-offset: 2px; border-radius: 3px; }
}
.event-error { color: var(--color-error-600); }
</style>
