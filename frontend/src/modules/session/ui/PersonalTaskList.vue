<script setup>
import { computed } from 'vue'
import { FileText, ChevronRight, RefreshCw } from '@lucide/vue'

const props = defineProps({ sessions: { type: Array, default: () => [] }, error: { type: String, default: '' } })
defineEmits(['open', 'refresh'])
const visibleSessions = computed(() => props.sessions.filter(session => session.waiting_for || session.has_pending_input || (session.turn_status || session.run_status) !== 'completed').toReversed())
const status = (session) => session.waiting_for ? '需要处理'
  : session.has_pending_input ? '排队中'
    : ({ running: '进行中', pending: '排队中', completed: '已完成', failed: '失败', cancelled: '已取消' }[session.turn_status || session.run_status] || '已就绪')
</script>

<template>
  <section v-if="visibleSessions.length || error" class="personal-tasks" aria-label="委派任务">
    <div class="task-heading"><span>委派任务</span><button type="button" class="lucide-icon-btn" aria-label="刷新委派任务" @click="$emit('refresh')"><RefreshCw :size="14" /></button></div>
    <p v-if="error" class="task-error" role="alert">{{ error }}</p>
    <div class="task-list">
    <button v-for="session in visibleSessions" :key="session.session_id" type="button" class="task-row" :data-session-id="session.session_id" :aria-label="`查看任务轨迹：${session.title || session.name}`" @click="$emit('open', session.session_id, session.title || session.name)">
      <FileText :size="16" aria-hidden="true" />
      <span class="task-title">{{ session.title || session.name || '委派任务' }}</span>
      <span class="task-status">{{ status(session) }}</span>
      <ChevronRight :size="14" aria-hidden="true" />
    </button>
    </div>
  </section>
</template>

<style scoped>
.personal-tasks { display: flex; flex-direction: column; gap: 2px; margin-top: 16px; border-top: 1px solid var(--border-surface); padding-top: 12px; }
.task-heading { display: flex; align-items: center; justify-content: space-between; padding: 0 8px 4px; color: var(--color-text-secondary); font-size: 12px; }
.task-list { max-height: min(240px, 40vh); overflow-y: auto; }
.task-heading button { display: grid; place-items: center; width: 26px; height: 26px; padding: 0; border: 0; border-radius: 6px; background: transparent; color: inherit; cursor: pointer; }
.task-heading button:hover { background: var(--gray-50); color: var(--color-text); }
.task-heading button:focus-visible { outline: 2px solid var(--main-color); outline-offset: 1px; }
.task-row { display: flex; align-items: center; gap: 10px; width: 100%; min-height: 38px; padding: 8px; border: 0; border-radius: 8px; background: transparent; color: var(--color-text); cursor: pointer; text-align: left; }
.task-row:hover { background: var(--gray-50); }
.task-row:focus-visible { outline: 2px solid var(--main-color); outline-offset: 1px; }
.task-title { flex: 1; min-width: 0; overflow-wrap: anywhere; font-size: 13px; }
.task-status { flex: none; color: var(--color-text-secondary); font-size: 12px; }
.task-error { margin: 4px 8px; color: var(--color-error-500); font-size: 12px; }
</style>
