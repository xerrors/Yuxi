<template>
  <section class="cooperation" :class="{ 'is-compact': compact }" aria-label="协作会话">
    <div :class="compact ? 'cooperation-overview' : 'list-header'">
      <button
        v-if="compact"
        type="button"
        class="summary-entry"
        aria-label="查看协作会话"
        @click="$emit('open-tree')"
      >
        <span class="summary-heading">
          <span class="summary-title">协作会话</span>
          <ChevronRight :size="15" class="summary-chevron" aria-hidden="true" />
        </span>
      </button>
      <div v-else class="list-heading">
        <h3>
          协作会话 <span class="session-count">{{ sessions.length }}</span>
        </h3>
        <p>{{ summaryText }}</p>
      </div>
      <button
        v-if="!compact && pausedQueueCount"
        type="button"
        class="lucide-icon-btn control-button"
        title="继续队列"
        aria-label="继续队列"
        :disabled="hasActiveTurns || controlling || !currentId || continueDisabled"
        :aria-busy="controlling"
        @click="control(false)"
      >
        <LoaderCircle v-if="controlling" :size="16" class="is-spinning" aria-hidden="true" />
        <Play v-else :size="16" aria-hidden="true" />
        <span class="control-label">继续队列</span>
      </button>
      <button
        v-if="hasStoppableWork"
        type="button"
        class="lucide-icon-btn control-button stop-button"
        title="停止全部协作会话"
        aria-label="停止全部"
        :disabled="controlling || !currentId || !sessions.length"
        :aria-busy="controlling"
        @click="control(true)"
      >
        <LoaderCircle v-if="controlling" :size="16" class="is-spinning" aria-hidden="true" />
        <Square v-else :size="14" aria-hidden="true" />
        <span v-if="!compact" class="control-label">停止全部</span>
      </button>
      <span v-if="compact" class="session-count">{{ sessions.length }}</span>
    </div>
    <p v-if="compact" class="summary-detail">{{ summaryText }}</p>
    <p v-if="error" role="alert" class="cooperation-error">
      {{ error }} <button type="button" @click="$emit('refresh')">重试</button>
    </p>
    <template v-if="!compact">
      <ul v-if="sessionRows.length" class="session-list">
        <li v-for="item in sessionRows" :key="item.session.session_id">
          <button
            type="button"
            class="session-row"
            :style="{ paddingInlineStart: `${12 + Math.min(item.depth, 4) * 12}px` }"
            :aria-label="`打开会话 ${item.name}`"
            :aria-current="item.session.session_id === currentId ? 'true' : undefined"
            :title="item.session.path"
            @click="$emit('open', item.session.session_id)"
          >
            <FallbackAvatar
              :name="item.name"
              :seed="item.session.session_id"
              kind="session"
              :size="18"
              shape="rounded"
              decorative
            />
            <span class="session-content">
              <span class="session-heading">
                <span class="session-name" :title="item.name">{{ item.name }}</span>
                <span
                  v-if="item.session.title && item.session.title !== item.name"
                  class="session-task"
                  :title="item.session.title"
                >
                  {{ item.session.title }}
                </span>
                <span v-if="item.session.has_pending_input" class="queue-hint">
                  {{ item.session.queue_paused ? '后续输入待继续' : '有后续输入排队' }}
                </span>
                <span class="status-label" :class="`status-${item.state.tone}`">{{
                  item.state.label
                }}</span>
              </span>
            </span>
            <ChevronRight :size="16" class="open-icon" aria-hidden="true" />
          </button>
        </li>
      </ul>
      <p v-else class="empty-state">暂无协作会话，派发任务后会显示在这里。</p>
    </template>
  </section>
</template>
<script setup>
import { computed, ref } from 'vue'
import { ChevronRight, LoaderCircle, Play, Square } from '@lucide/vue'
import { message } from 'ant-design-vue'
import { agentApi } from '@/apis'
import FallbackAvatar from '@/shared/ui/FallbackAvatar.vue'
const controlling = ref(false)
const props = defineProps({
  sessions: { type: Array, default: () => [] },
  currentId: { type: String, default: '' },
  error: { type: String, default: '' },
  compact: { type: Boolean, default: false },
  continueDisabled: { type: Boolean, default: false }
})
const emit = defineEmits(['open', 'open-tree', 'refresh', 'control-error'])
const states = {
  approval: { label: '等待审批', tone: 'attention' },
  answer: { label: '等待回答', tone: 'attention' },
  failed: { label: '失败', tone: 'error' },
  running: { label: '执行中', tone: 'active' },
  pending: { label: '等待执行', tone: 'active' },
  cooperation: { label: '等待协作', tone: 'neutral' },
  waiting: { label: '等待中', tone: 'neutral' },
  cancelling: { label: '取消中', tone: 'neutral' },
  completed: { label: '已完成', tone: 'neutral' },
  cancelled: { label: '已取消', tone: 'neutral' },
  idle: { label: '空闲', tone: 'neutral' }
}
/** 等待原因只属于等待中的轮次，不能覆盖最终状态。 */
const sessionStateKey = (session) => {
  if (session.turn_status === 'cancelling') return 'cancelling'
  if (session.run_status === 'pending') return 'pending'
  if (session.turn_status === 'waiting') {
    return ['approval', 'answer', 'cooperation'].includes(session.waiting_for)
      ? session.waiting_for
      : 'waiting'
  }
  return states[session.turn_status] ? session.turn_status : 'idle'
}
const sessionRows = computed(() =>
  props.sessions.map((session) => {
    const depth = Math.max(0, (session.path || '').split('/').filter(Boolean).length - 1)
    const stateKey = sessionStateKey(session)
    return {
      session,
      depth,
      name: depth ? session.name || session.path.split('/').pop() : '主会话',
      stateKey,
      state: states[stateKey]
    }
  })
)
const pausedQueueCount = computed(
  () => props.sessions.filter((session) => session.has_pending_input && session.queue_paused).length
)
const hasActiveTurns = computed(() =>
  props.sessions.some((session) =>
    ['running', 'waiting', 'cancelling'].includes(session.turn_status)
  )
)
const hasStoppableWork = computed(
  () =>
    hasActiveTurns.value ||
    props.sessions.some((session) => session.has_pending_input && !session.queue_paused)
)
const summaryText = computed(() => {
  if (!props.sessions.length) return '暂无协作会话'
  const counts = {}
  for (const item of sessionRows.value) counts[item.stateKey] = (counts[item.stateKey] || 0) + 1
  const attention = [
    'approval',
    'answer',
    'failed',
    'running',
    'pending',
    'cooperation',
    'waiting',
    'cancelling'
  ]
    .filter((key) => counts[key])
    .map((key) => `${counts[key]} ${states[key].label}`)
  if (pausedQueueCount.value) attention.push(`${pausedQueueCount.value} 待继续`)
  if (attention.length) return attention.join(' · ')
  return ['completed', 'cancelled', 'idle']
    .filter((key) => counts[key])
    .map((key) => `${counts[key]} ${states[key].label}`)
    .join(' · ')
})
const control = async (stopped) => {
  const threadId = props.currentId
  if (controlling.value || !threadId || (!stopped && props.continueDisabled)) return
  controlling.value = true
  try {
    const key =
      typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
        ? crypto.randomUUID()
        : `tree-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
    await agentApi.controlSessionTree(threadId, stopped, key)
    emit('refresh')
  } catch (failure) {
    if (!stopped) emit('control-error', threadId, failure)
    message.error(failure.message || '协作树控制失败')
  } finally {
    controlling.value = false
  }
}
</script>
<style scoped lang="less">
.cooperation {
  container-type: inline-size;
  container-name: cooperation;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  color: var(--color-text);
}
.is-compact {
  height: auto;

  .session-count {
    color: var(--gray-500);
  }

  .stop-button {
    width: 28px;
    height: 28px;
    color: var(--gray-500);
  }

  .cooperation-error {
    margin: 6px 0 0;
    padding: 0;
    font-size: 12px;
  }
}
.cooperation-overview,
.list-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.summary-entry {
  flex: 1;
  min-width: 0;
  padding: 4px 0;
  text-align: left;
  border: 0;
  border-radius: 6px;
  color: inherit;
  font: inherit;
  background: transparent;
  cursor: pointer;
}
.session-row:hover {
  background: var(--gray-50);
}
.summary-entry:hover {
  .summary-heading,
  .summary-chevron {
    color: var(--gray-900);
  }
}
.summary-heading {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--gray-800);
}
.summary-title {
  font-weight: 600;
}
.summary-chevron {
  flex-shrink: 0;
  color: var(--gray-400);
}
.session-count {
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 400;
}
.summary-detail {
  margin: 6px 0 0;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.6;
}
.control-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  gap: 6px;
  min-width: 28px;
  height: 28px;
  padding: 0 6px;
  font: inherit;
  font-size: 12px;
  white-space: nowrap;
  border: 0;
  border-radius: 6px;
  color: var(--color-text-secondary);
  background: transparent;
  cursor: pointer;
}
.control-button:hover:not(:disabled) {
  color: var(--color-text);
  background: var(--gray-100);
}
.control-label {
  display: none;
}
@container cooperation (min-width: 440px) {
  .control-label {
    display: inline;
  }
}
.control-button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.summary-entry:focus-visible,
.session-row:focus-visible,
.control-button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: -2px;
}
.list-header {
  flex-shrink: 0;
  padding: 12px;
  border-bottom: 1px solid var(--gray-100);
}
.list-heading {
  flex: 1;
  min-width: 0;
  h3 {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
  }
  p {
    margin: 3px 0 0;
    font-size: 12px;
    color: var(--color-text-secondary);
    line-height: 1.6;
  }
}
.session-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  list-style: none;
  margin: 0;
  padding: 0;
  li + li {
    border-top: 1px solid var(--gray-100);
  }
}
.session-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 9px 12px;
  text-align: left;
  border: 0;
  color: inherit;
  font: inherit;
  background: transparent;
  cursor: pointer;
}
.open-icon {
  flex-shrink: 0;
  margin-top: 3px;
  color: var(--color-text-secondary);
}
.session-content {
  flex: 1;
  min-width: 0;
}
.session-heading {
  display: flex;
  align-items: center;
  gap: 8px;
}
.session-name,
.session-task {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-name {
  flex: 0 1 auto;
  min-width: 0;
  font-size: 14px;
  font-weight: 500;
}
.status-label {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 12px;
  line-height: 20px;
  padding: 0 6px;
  border-radius: 4px;
  color: var(--color-text-secondary);
  background: var(--gray-50);
}
.status-attention {
  color: var(--color-warning-900);
  background: var(--color-warning-50);
}
.status-error {
  color: var(--color-error-700);
  background: var(--color-error-50);
}
.status-active {
  color: var(--color-info-700);
  background: var(--color-info-50);
}
.session-task,
.queue-hint {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-secondary);
}
.session-task {
  flex: 0 1 auto;
}
.queue-hint {
  flex-shrink: 0;
  color: var(--color-warning-900);
}
.cooperation-error,
.empty-state {
  padding: 12px 16px;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.cooperation-error {
  color: var(--color-error-700);
}
.is-spinning {
  animation: cooperation-spin 1s linear infinite;
}
@keyframes cooperation-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (max-width: 767px) {
  .control-button,
  .is-compact .stop-button {
    min-width: 40px;
    height: 40px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .is-spinning {
    animation: none;
  }
}
</style>
