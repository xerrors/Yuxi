<template>
  <div class="agent-view" :class="{ 'sessions-pinned': sidebarCollapsed && sessionsPinned }">
    <SessionWorkspace
      ref="workspaceRef"
      class="agent-session-workspace"
      :single-mode="false"
      :initial-project-id="draftProjectId"
      :is-new-session="!threadId"
      @thread-change="handleThreadChange"
    >
      <template #input-actions-left="{ hasActiveThread, isCreatingThread, agentId: sessionAgentId }">
        <SessionAgentPicker
          :agent-id="sessionAgentId"
          :has-active-thread="hasActiveThread"
          :is-creating-thread="isCreatingThread"
          :start-new-session="startNewSession"
        />
      </template>
    </SessionWorkspace>
  </div>
</template>

<script setup>
import { computed, onActivated, onBeforeUnmount, onDeactivated, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useChatUIStore } from '@/modules/session'
import SessionWorkspace from '@/modules/session/ui/SessionWorkspace.vue'
import SessionAgentPicker from '@/modules/agents/ui/SessionAgentPicker.vue'
import { useAgentStore } from '@/modules/agents/model/agent'
import { handleChatError } from '@/shared/lib/errorHandler'
import { createThreadRouteCoordinator } from './agent/threadRouteCoordinator'

const props = defineProps({
  memberSessionId: { type: String, default: '' },
  threadId: { type: String, default: '' },
  agentId: { type: String, default: '' },
  projectId: { type: String, default: '' }
})
const emit = defineEmits(['session-created'])
const router = useRouter()
const agentStore = useAgentStore()
const { sidebarCollapsed, sessionsPinned } = storeToRefs(useChatUIStore())
const workspaceRef = ref(null)
const active = ref(true)
const draftProjectId = computed(() => (props.threadId ? '' : props.projectId))
const ownsCurrentRoute = computed(() => {
  const route = router.currentRoute.value
  return (
    active.value &&
    ['AgentComp', 'AgentCompWithThreadId'].includes(route.name) &&
    (route.params.thread_id || '') === props.threadId
  )
})

const coordinator = createThreadRouteCoordinator({
  initializeAgents: async () => {
    if (!agentStore.isInitialized) await agentStore.initialize()
  },
  selectAgent: (id) => agentStore.selectAgent(id),
  rejectThread: () => router.replace({ name: 'AgentComp' }),
  consumeAgent: () => {
    const query = { ...router.currentRoute.value.query }
    delete query.agent_id
    return router.replace({ name: 'AgentComp', query })
  },
  onError: (error) => handleChatError(error, 'load')
})

watch(
  [workspaceRef, () => props.threadId, () => props.agentId, () => props.memberSessionId, ownsCurrentRoute],
  ([selection, threadId, agentId, memberSessionId, ownsRoute]) => {
    void coordinator.sync({ threadId, agentId, memberSessionId }, ownsRoute ? selection : null)
  },
  { immediate: true }
)
onActivated(() => {
  active.value = true
})
onDeactivated(() => {
  active.value = false
})
onBeforeUnmount(() => coordinator.dispose())

/** 智能体创建后的新会话准备由页面协调。 */
async function startNewSession() {
  if (!ownsCurrentRoute.value) return
  if (props.threadId) return router.push({ name: 'AgentComp' })
  return workspaceRef.value?.selectThreadFromRoute('')
}

/** 将工作区的新会话身份同步到路由。 */
function handleThreadChange(id) {
  if (!ownsCurrentRoute.value || coordinator.isSyncing()) return
  const nextThreadId = id || ''
  if (props.threadId === nextThreadId) return
  if (!props.threadId && nextThreadId) {
    emit('session-created', nextThreadId)
    return
  }
  const location = nextThreadId
    ? { name: 'AgentCompWithThreadId', params: { thread_id: nextThreadId } }
    : { name: 'AgentComp' }
  void router.replace(location)
}
</script>

<style scoped>
.agent-view {
  display: flex;
  position: relative;
  flex-direction: row;
  min-width: 0;
  width: 100%;
  height: 100vh;
  overflow: hidden;
}
.agent-session-workspace {
  flex: 1;
  min-width: 0;
  width: auto;
  margin-left: 0;
  transition: margin-left 0.2s ease;
}
.agent-view.sessions-pinned > .agent-session-workspace {
  margin-left: 250px;
}
@media (prefers-reduced-motion: reduce) {
  .agent-session-workspace {
    transition: none;
  }
}
</style>
