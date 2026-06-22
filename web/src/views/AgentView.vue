<template>
  <div class="agent-view">
    <div class="agent-view-body">
      <!-- 中间内容区域 -->
      <div class="content">
        <AgentChatComponent
          ref="chatComponentRef"
          :single-mode="false"
          @thread-change="handleThreadChange"
        >
          <template #input-actions-left="{ hasActiveThread }">
            <a-dropdown
              v-if="selectedAgentId"
              v-model:open="agentDropdownOpen"
              :trigger="['click']"
              placement="topLeft"
              overlay-class-name="config-dropdown-overlay"
            >
              <button
                type="button"
                class="input-action-btn config-dropdown-trigger"
                :class="{ disabled: isLoadingConfig }"
                @click.stop
                @mousedown.stop
              >
                <span class="hide-text config-dropdown-text">{{ currentAgentLabel }}</span>
                <ChevronDown size="15" class="config-dropdown-chevron" />
              </button>

              <template #overlay>
                <div class="config-dropdown-panel" @click.stop>
                  <button
                    v-for="agent in agentQuickSwitchOptions"
                    :key="agent.value"
                    type="button"
                    class="config-dropdown-item"
                    :class="{
                      selected: agent.value === selectedAgentId,
                      disabled: hasActiveThread && agent.value !== selectedAgentId
                    }"
                    @click="handleAgentSwitch(agent.value, hasActiveThread)"
                  >
                    <FallbackAvatar
                      class="config-dropdown-item-icon-image"
                      :src="agent.icon"
                      :default-src="agent.defaultIcon"
                      :name="agent.label"
                      :seed="agent.value || agent.label"
                      kind="agent"
                      :size="24"
                      shape="rounded"
                      :alt="`${agent.label}图标`"
                    />
                    <span class="config-dropdown-item-label">{{ agent.label }}</span>
                    <span v-if="agent.isBuiltin" class="config-dropdown-item-badge">内置</span>
                    <Check
                      v-if="agent.value === selectedAgentId"
                      :size="14"
                      class="config-dropdown-item-check"
                    />
                  </button>

                  <div v-if="hasActiveThread" class="config-dropdown-hint">
                    当前对话已绑定智能体，新对话可切换。
                  </div>

                  <div class="config-dropdown-divider"></div>

                  <button
                    type="button"
                    class="config-dropdown-item action-item"
                    @click="openAgentManagement"
                  >
                    <Settings2 :size="15" class="config-dropdown-item-icon" />
                    <span class="config-dropdown-item-label">管理智能体</span>
                  </button>
                </div>
              </template>
            </a-dropdown>
          </template>
        </AgentChatComponent>
      </div>
    </div>
    <AgentEditModal
      ref="agentEditModalRef"
      :backend-options="agentBackendOptions"
      @saved="handleAgentSaved"
    />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Settings2, ChevronDown, Check } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { agentApi } from '@/apis/agent_api'
import AgentChatComponent from '@/components/AgentChatComponent.vue'
import AgentEditModal from '@/components/model-management/AgentEditModal.vue'
import { isBuiltinAgent, useAgentStore } from '@/stores/agent'
import { handleChatError } from '@/utils/errorHandler'
import { generatePixelAvatar } from '@/utils/pixelAvatar'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'

import { storeToRefs } from 'pinia'

// 组件引用
const chatComponentRef = ref(null)
const agentEditModalRef = ref(null)

// Stores
const agentStore = useAgentStore()
const route = useRoute()
const router = useRouter()

// 从 agentStore 中获取响应式状态
const { agents, selectedAgentId, isLoadingConfig } = storeToRefs(agentStore)

const syncingRouteThread = ref(false)

const getRouteThreadId = () => {
  const value = route.params.thread_id
  return typeof value === 'string' ? value : ''
}

const syncSelectedThreadFromRoute = async () => {
  const chatComponent = chatComponentRef.value
  if (!chatComponent?.selectThreadFromRoute) return

  const threadId = getRouteThreadId()
  syncingRouteThread.value = true
  try {
    if (!threadId && !agentStore.isInitialized) {
      await agentStore.initialize()
    }

    const ok = await chatComponent.selectThreadFromRoute(threadId)
    if (threadId && !ok) {
      await router.replace({ name: 'AgentComp' })
    }
  } catch (error) {
    handleChatError(error, 'load')
  } finally {
    syncingRouteThread.value = false
  }
}

watch(
  () => route.params.thread_id,
  () => {
    syncSelectedThreadFromRoute()
  },
  { immediate: true }
)

watch(chatComponentRef, (instance) => {
  if (!instance) return
  syncSelectedThreadFromRoute()
})

const handleThreadChange = (threadId) => {
  if (syncingRouteThread.value) return
  const currentRouteThreadId = getRouteThreadId()
  const nextThreadId = threadId || ''
  if (currentRouteThreadId === nextThreadId) return

  if (nextThreadId) {
    router.replace({ name: 'AgentCompWithThreadId', params: { thread_id: nextThreadId } })
  } else {
    router.replace({ name: 'AgentComp' })
  }
}

const agentQuickSwitchOptions = computed(() =>
  (agents.value || [])
    .filter((agent) => !agent.is_subagent)
    .map((agent) => ({
      label: agent.name || agent.id,
      value: agent.id,
      icon: agent.icon || '',
      defaultIcon: agent.id ? generatePixelAvatar(agent.id) : '',
      isBuiltin: isBuiltinAgent(agent)
    }))
)

const currentAgentOption = computed(() =>
  agentQuickSwitchOptions.value.find((agent) => agent.value === selectedAgentId.value)
)

const currentAgentLabel = computed(() => {
  if (isLoadingConfig.value) return '加载中...'
  return currentAgentOption.value?.label || '智能体'
})

const agentDropdownOpen = ref(false)
const agentBackendOptions = ref([])
const agentBackendsLoaded = ref(false)

const loadAgentBackends = async () => {
  if (agentBackendsLoaded.value) return
  const response = await agentApi.getAgentBackends()
  agentBackendOptions.value = (response.backends || []).map((backend) => ({
    label: backend.name || backend.backend_id,
    value: backend.backend_id
  }))
  agentBackendsLoaded.value = true
}

const handleAgentSwitch = async (agentId, hasActiveThread) => {
  if (!agentId || agentId === selectedAgentId.value) return
  if (hasActiveThread) {
    message.info('当前对话已绑定智能体，请新建对话后切换')
    return
  }
  try {
    await agentStore.selectAgent(agentId)
    agentDropdownOpen.value = false
  } catch (error) {
    console.error('切换智能体出错:', error)
    message.error('切换智能体失败')
  }
}

const handleAgentSaved = async () => {
  await agentStore.fetchAgents()
  if (selectedAgentId.value) {
    await agentStore.fetchAgentDetail(selectedAgentId.value, true)
  }
}

const openAgentManagement = async () => {
  agentDropdownOpen.value = false
  if (!selectedAgentId.value) {
    message.warning('请先选择智能体')
    return
  }
  try {
    await loadAgentBackends()
    await agentEditModalRef.value?.openEdit(selectedAgentId.value)
  } catch (error) {
    message.error(error.message || '打开智能体配置失败')
  }
}
</script>

<style lang="less" scoped>
.agent-view {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100vh;
  overflow: hidden;
}

.agent-view-body {
  --gap-radius: 6px;
  display: flex;
  flex-direction: row;
  width: 100%;
  flex: 1;
  height: 100%;
  overflow: hidden;
  position: relative;

  .content {
    flex: 1;
    display: flex;
    flex-direction: column;
  }
}

.content {
  flex: 1;
  overflow: hidden;
}

.config-dropdown-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  max-width: min(240px, calc(100vw - 160px));
  gap: 4px;
}

.config-dropdown-trigger :deep(svg) {
  color: currentColor;
}

.config-dropdown-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: currentColor;
}

.config-dropdown-chevron {
  flex-shrink: 0;
  color: currentColor;
}

// 响应式优化
@media (max-width: 520px) {
  .config-dropdown-trigger {
    max-width: calc(100vw - 112px);
  }
}
</style>

<style lang="less">
.config-dropdown-overlay .config-dropdown-panel {
  min-width: 188px;
  max-width: min(260px, calc(100vw - 24px));
  padding: 4px;
  background: var(--gray-0);
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  box-shadow:
    0 8px 24px rgba(0, 0, 0, 0.08),
    0 2px 8px rgba(0, 0, 0, 0.04);
}

.config-dropdown-overlay .config-dropdown-item {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  width: 100%;
  padding: 6px 8px;
  border: none;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.config-dropdown-overlay .config-dropdown-item:hover {
  background: var(--gray-50);
}

.config-dropdown-overlay .config-dropdown-item.disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.config-dropdown-overlay .config-dropdown-item.selected {
  background: var(--gray-50);
}

.config-dropdown-overlay .config-dropdown-item.action-item {
  color: var(--gray-800);
}

.config-dropdown-overlay .config-dropdown-item-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  line-height: 1.35;
  color: var(--gray-800);
}

.config-dropdown-overlay .config-dropdown-item-icon,
.config-dropdown-overlay .config-dropdown-item-icon-image,
.config-dropdown-overlay .config-dropdown-item-icon-empty {
  flex-shrink: 0;
}

.config-dropdown-overlay .config-dropdown-item-icon {
  color: var(--gray-500);
}

.config-dropdown-overlay .config-dropdown-item-icon-image,
.config-dropdown-overlay .config-dropdown-item-icon-empty {
  width: 24px;
  height: 24px;
  border-radius: 4px;
}

.config-dropdown-overlay .config-dropdown-item-icon-image {
  object-fit: cover;
}

.config-dropdown-overlay .config-dropdown-item-badge {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  line-height: 1.4;
}

.config-dropdown-overlay .config-dropdown-item-check {
  flex-shrink: 0;
  color: var(--main-600);
}

.config-dropdown-overlay .config-dropdown-hint {
  padding: 6px 8px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.4;
}

.config-dropdown-overlay .config-dropdown-divider {
  height: 1px;
  margin: 4px 4px;
  background: var(--gray-100);
}
</style>
