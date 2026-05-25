<template>
  <div class="agent-view">
    <div class="agent-view-body">
      <!-- 中间内容区域 -->
      <div class="content">
        <AgentChatComponent
          ref="chatComponentRef"
          :single-mode="false"
          :send-disabled="isSavingInputModel"
          @thread-change="handleThreadChange"
        >
          <template #input-actions-left>
            <div v-if="showInputModelSelector" class="input-model-selector">
              <ModelSelectorComponent
                :model_spec="currentInputModelSpec"
                :disabled="isInputModelSelectorDisabled"
                size="nano"
                display-name="mini"
                @select-model="handleInputModelChange"
              />
            </div>
          </template>

          <template #input-actions-right="{ hasActiveThread }">
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
                <img
                  class="config-dropdown-agent-icon nav-btn-icon"
                  :src="currentAgentIcon"
                  :alt="`${currentAgentLabel}图标`"
                />
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
                    <img
                      class="config-dropdown-item-icon-image"
                      :src="agent.icon"
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

                  <template v-if="userStore.isAdmin">
                    <div class="config-dropdown-divider"></div>

                    <button
                      type="button"
                      class="config-dropdown-item action-item"
                      @click="openAgentManagement"
                    >
                      <Settings2 :size="15" class="config-dropdown-item-icon" />
                      <span class="config-dropdown-item-label">管理智能体</span>
                    </button>
                  </template>
                </div>
              </template>
            </a-dropdown>
          </template>

          <template #header-right="{ isAgentPanelOpen, hasActiveThread, toggleAgentPanel }">
            <button
              v-if="hasActiveThread"
              type="button"
              class="agent-nav-btn agent-state-btn"
              :class="{ active: isAgentPanelOpen }"
              title="查看文件"
              @click.stop="toggleAgentPanel"
            >
              <FolderKanban size="18" class="nav-btn-icon" />
              <span class="hide-text">文件</span>
            </button>
            <div
              v-if="userStore.isAdmin && selectedAgentId"
              ref="moreButtonRef"
              type="button"
              class="agent-nav-btn"
              @click="toggleMoreMenu"
            >
              <Ellipsis size="18" class="nav-btn-icon" />
            </div>
          </template>
        </AgentChatComponent>
      </div>

      <!-- 反馈模态框 -->
      <FeedbackModalComponent
        v-if="userStore.isAdmin"
        ref="feedbackModal"
        :agent-id="selectedAgentId"
      />

      <!-- 自定义更多菜单 -->
      <Teleport to="body">
        <Transition name="menu-fade">
          <div
            v-if="userStore.isAdmin && chatUIStore.moreMenuOpen"
            ref="moreMenuRef"
            class="more-popup-menu"
            :style="{
              left: chatUIStore.moreMenuPosition.x + 'px',
              top: chatUIStore.moreMenuPosition.y + 'px'
            }"
          >
            <div class="menu-item" @click="handleShareChat">
              <ShareAltOutlined class="menu-icon" />
              <span class="menu-text">分享对话</span>
            </div>
            <div class="menu-item" @click="handleFeedback">
              <MessageOutlined class="menu-icon" />
              <span class="menu-text">查看反馈</span>
            </div>
          </div>
        </Transition>
      </Teleport>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { MessageOutlined, ShareAltOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { Settings2, Ellipsis, ChevronDown, Check, FolderKanban } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import AgentChatComponent from '@/components/AgentChatComponent.vue'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
import FeedbackModalComponent from '@/components/dashboard/FeedbackModalComponent.vue'
import { useUserStore } from '@/stores/user'
import { isBuiltinAgent, useAgentStore } from '@/stores/agent'
import { useChatUIStore } from '@/stores/chatUI'
import { ChatExporter } from '@/utils/chatExporter'
import { handleChatError } from '@/utils/errorHandler'
import { onClickOutside } from '@vueuse/core'
import defaultAgentIcon from '@/assets/defaults/agent.png'

import { storeToRefs } from 'pinia'

// 组件引用
const feedbackModal = ref(null)
const chatComponentRef = ref(null)

// Stores
const userStore = useUserStore()
const agentStore = useAgentStore()
const chatUIStore = useChatUIStore()
const route = useRoute()
const router = useRouter()

// 从 agentStore 中获取响应式状态
const { agents, selectedAgentId, selectedAgent, agentConfig, configurableItems, isLoadingConfig } =
  storeToRefs(agentStore)

const syncingRouteThread = ref(false)
const isSavingInputModel = ref(false)

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
  (agents.value || []).map((agent) => ({
    label: agent.name || agent.id,
    value: agent.id,
    icon: agent.icon || defaultAgentIcon,
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

const currentAgentIcon = computed(() => currentAgentOption.value?.icon || defaultAgentIcon)

const inputModelKey = computed(() => {
  if (configurableItems.value?.model?.kind === 'llm') return 'model'
  return (
    Object.entries(configurableItems.value || {}).find(
      ([key, item]) => key !== 'subagents_model' && item?.kind === 'llm'
    )?.[0] || ''
  )
})

const currentInputModelSpec = computed(() => {
  const key = inputModelKey.value
  return key ? agentConfig.value?.[key] || '' : ''
})

const showInputModelSelector = computed(() => Boolean(selectedAgentId.value && inputModelKey.value))
const isInputModelSelectorDisabled = computed(
  () => isLoadingConfig.value || isSavingInputModel.value || !selectedAgent.value?.can_manage
)

const handleInputModelChange = async (spec) => {
  const key = inputModelKey.value
  if (!key || typeof spec !== 'string' || !spec || spec === currentInputModelSpec.value) return
  if (isInputModelSelectorDisabled.value) return

  const previousSpec = currentInputModelSpec.value
  isSavingInputModel.value = true
  try {
    agentStore.updateAgentConfig({ [key]: spec })
    await agentStore.saveAgentConfig()
  } catch {
    agentStore.updateAgentConfig({ [key]: previousSpec })
  } finally {
    isSavingInputModel.value = false
  }
}

const agentDropdownOpen = ref(false)

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

const openAgentManagement = () => {
  agentDropdownOpen.value = false
  router.push({ name: 'ModelManageComp', query: { tab: 'agents' } })
}

// 更多菜单相关
const moreMenuRef = ref(null)
const moreButtonRef = ref(null)

const toggleMoreMenu = (event) => {
  event.stopPropagation()
  // 切换状态，而不是只打开
  chatUIStore.moreMenuOpen = !chatUIStore.moreMenuOpen

  if (chatUIStore.moreMenuOpen) {
    // 只在打开时计算位置
    const rect = event.currentTarget.getBoundingClientRect()
    chatUIStore.openMoreMenu(rect.right - 110, rect.bottom + 8)
  }
}

const closeMoreMenu = () => {
  chatUIStore.closeMoreMenu()
}

// 使用 VueUse 的 onClickOutside
onClickOutside(
  moreMenuRef,
  () => {
    if (chatUIStore.moreMenuOpen) {
      closeMoreMenu()
    }
  },
  { ignore: [moreButtonRef] }
)

const handleShareChat = async () => {
  closeMoreMenu()

  try {
    // 从聊天组件获取导出数据
    const exportData = chatComponentRef.value?.getExportPayload?.()

    console.log('[AgentView] Export data:', exportData)

    if (!exportData) {
      message.warning('当前没有可导出的对话内容')
      return
    }

    // 检查是否有实际的消息内容
    const hasMessages = exportData.messages && exportData.messages.length > 0
    const hasOngoingMessages = exportData.onGoingMessages && exportData.onGoingMessages.length > 0

    if (!hasMessages && !hasOngoingMessages) {
      console.warn('[AgentView] Export data has no messages:', {
        messages: exportData.messages,
        onGoingMessages: exportData.onGoingMessages
      })
      message.warning('当前对话暂无内容可导出，请先进行对话')
      return
    }

    const result = await ChatExporter.exportToHTML(exportData)
    message.success(`对话已导出为HTML文件: ${result.filename}`)
  } catch (error) {
    console.error('[AgentView] Export error:', error)
    if (error?.message?.includes('没有可导出的对话内容')) {
      message.warning('当前对话暂无内容可导出，请先进行对话')
      return
    }
    handleChatError(error, 'export')
  }
}

const handleFeedback = () => {
  closeMoreMenu()
  feedbackModal.value?.show()
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

.input-model-selector {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  max-width: min(168px, calc(100vw - 160px));
}

.config-dropdown-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  max-width: min(240px, calc(100vw - 160px));
  gap: 4px;
}

.config-dropdown-trigger .nav-btn-icon {
  color: currentColor;
}

.config-dropdown-agent-icon {
  width: 18px;
  height: 18px;
  border-radius: 3px;
  flex-shrink: 0;
  object-fit: contain;
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

// 自定义更多菜单样式
.more-popup-menu {
  position: fixed;
  min-width: 100px;
  background: var(--gray-0);
  border-radius: 10px;
  box-shadow:
    0 8px 24px rgba(0, 0, 0, 0.08),
    0 2px 8px rgba(0, 0, 0, 0.04);
  border: 1px solid var(--gray-100);
  padding: 4px;
  z-index: 9999;

  .menu-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 8px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
    font-size: 14px;
    color: var(--gray-900);
    position: relative;
    user-select: none;

    .menu-icon {
      font-size: 16px;
      color: var(--gray-600);
      transition: color 0.15s ease;
      flex-shrink: 0;
    }

    .menu-text {
      font-weight: 400;
      letter-spacing: 0.01em;
    }

    &:hover {
      background: var(--gray-50);
      // color: var(--main-700);

      // .menu-icon {
      //   color: var(--main-600);
      // }
    }

    &:active {
      background: var(--gray-100);
    }
  }

  .menu-divider {
    height: 1px;
    background: var(--gray-100);
    margin: 4px 8px;
  }
}

// 菜单淡入淡出动画
.menu-fade-enter-active {
  animation: menuSlideIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.menu-fade-leave-active {
  animation: menuSlideOut 0.15s cubic-bezier(0.4, 0, 1, 1);
}

@keyframes menuSlideIn {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes menuSlideOut {
  from {
    opacity: 1;
    transform: translateY(0);
  }
  to {
    opacity: 0;
    transform: translateY(-4px);
  }
}

// 响应式优化
@media (max-width: 520px) {
  .config-dropdown-trigger {
    max-width: calc(100vw - 112px);
  }

  .more-popup-menu {
    box-shadow:
      0 12px 32px rgba(0, 0, 0, 0.12),
      0 4px 12px rgba(0, 0, 0, 0.06);
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
.config-dropdown-overlay .config-dropdown-item-icon-image {
  flex-shrink: 0;
}

.config-dropdown-overlay .config-dropdown-item-icon {
  color: var(--gray-500);
}

.config-dropdown-overlay .config-dropdown-item-icon-image {
  width: 24px;
  height: 24px;
  object-fit: contain;
  border-radius: 4px;
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
