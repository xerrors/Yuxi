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
          <template #input-actions-left>
            <a-dropdown
              v-if="selectedAgentId"
              v-model:open="configDropdownOpen"
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
                <Settings2 size="18" class="nav-btn-icon" />
                <span class="hide-text config-dropdown-text">{{ currentConfigLabel }}</span>
                <ChevronDown size="15" class="config-dropdown-chevron" />
              </button>

              <template #overlay>
                <div class="config-dropdown-panel" @click.stop>
                  <button
                    v-for="config in configQuickSwitchOptions"
                    :key="config.value"
                    type="button"
                    class="config-dropdown-item"
                    :class="{ selected: config.value === selectedAgentConfigId }"
                    @click="handleConfigSwitch(config.value)"
                  >
                    <span class="config-dropdown-item-label">{{ config.label }}</span>
                    <span v-if="config.isDefault" class="config-dropdown-item-badge">默认</span>
                    <Check
                      v-if="config.value === selectedAgentConfigId"
                      :size="14"
                      class="config-dropdown-item-check"
                    />
                  </button>

                  <div class="config-dropdown-divider"></div>

                  <button
                    type="button"
                    class="config-dropdown-item action-item"
                    @click="toggleConfigSidebar"
                  >
                    <Settings2 :size="15" class="config-dropdown-item-icon" />
                    <span class="config-dropdown-item-label">{{ configSidebarActionLabel }}</span>
                  </button>

                  <button
                    v-if="userStore.isAdmin"
                    type="button"
                    class="config-dropdown-item action-item"
                    @click="openCreateConfigModal"
                  >
                    <Plus :size="15" class="config-dropdown-item-icon" />
                    <span class="config-dropdown-item-label">新建配置</span>
                  </button>
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

      <!-- 配置侧边栏 -->
      <AgentConfigSidebar
        :isOpen="chatUIStore.isConfigSidebarOpen"
        @close="() => (chatUIStore.isConfigSidebarOpen = false)"
      />

      <!-- 反馈模态框 -->
      <FeedbackModalComponent
        v-if="userStore.isAdmin"
        ref="feedbackModal"
        :agent-id="selectedAgentId"
      />

      <a-modal
        v-model:open="createConfigModalOpen"
        title="新建配置"
        :width="360"
        :confirm-loading="createConfigLoading"
        @ok="handleCreateConfig"
        @cancel="closeCreateConfigModal"
      >
        <a-input v-model:value="createConfigName" placeholder="请输入配置名称" allow-clear />
      </a-modal>

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
import { Settings2, Ellipsis, ChevronDown, Check, Plus, FolderKanban } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import AgentChatComponent from '@/components/AgentChatComponent.vue'
import AgentConfigSidebar from '@/components/AgentConfigSidebar.vue'
import FeedbackModalComponent from '@/components/dashboard/FeedbackModalComponent.vue'
import { useUserStore } from '@/stores/user'
import { useAgentStore } from '@/stores/agent'
import { useChatUIStore } from '@/stores/chatUI'
import { ChatExporter } from '@/utils/chatExporter'
import { handleChatError } from '@/utils/errorHandler'
import { onClickOutside } from '@vueuse/core'

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
const { selectedAgentId, defaultAgentId, selectedAgentConfigId, agentConfigs, isLoadingConfig } =
  storeToRefs(agentStore)

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
    if (!threadId) {
      if (!agentStore.isInitialized) {
        await agentStore.initialize()
      }
      const targetAgentId = defaultAgentId.value
      if (targetAgentId && selectedAgentId.value !== targetAgentId) {
        await agentStore.selectAgent(targetAgentId)
      }
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

// 输入区只负责快速切换已有配置，详细查看/编辑仍通过侧边栏完成。
const configQuickSwitchOptions = computed(() => {
  if (!selectedAgentId.value) return []
  const list = agentConfigs.value[selectedAgentId.value] || []
  return list.map((config) => ({
    label: config.name,
    value: config.id,
    isDefault: !!config.is_default
  }))
})

const currentConfigLabel = computed(() => {
  if (isLoadingConfig.value) return '加载中...'
  const current = configQuickSwitchOptions.value.find(
    (config) => config.value === selectedAgentConfigId.value
  )
  return current?.label || '配置'
})

const configSidebarActionLabel = computed(() => {
  return chatUIStore.isConfigSidebarOpen ? '收起配置侧边栏' : '查看/编辑配置'
})

const configDropdownOpen = ref(false)
const createConfigModalOpen = ref(false)
const createConfigLoading = ref(false)
const createConfigName = ref('')

const handleConfigSwitch = async (configId) => {
  if (!configId || configId === selectedAgentConfigId.value) return
  try {
    await agentStore.selectAgentConfig(configId)
    configDropdownOpen.value = false
  } catch (error) {
    console.error('切换配置出错:', error)
    message.error('切换配置失败')
  }
}

const toggleConfigSidebar = () => {
  chatUIStore.isConfigSidebarOpen = !chatUIStore.isConfigSidebarOpen
  configDropdownOpen.value = false
}

const openCreateConfigModal = () => {
  if (!userStore.isAdmin) return
  configDropdownOpen.value = false
  createConfigName.value = ''
  createConfigModalOpen.value = true
}

const closeCreateConfigModal = () => {
  createConfigModalOpen.value = false
  createConfigName.value = ''
}

const handleCreateConfig = async () => {
  if (!userStore.isAdmin || !selectedAgentId.value) return

  const name = createConfigName.value.trim()
  if (!name) {
    message.error('请输入配置名称')
    return
  }

  createConfigLoading.value = true
  try {
    await agentStore.createAgentConfigProfile({
      name,
      setDefault: false,
      fromCurrent: false
    })
    closeCreateConfigModal()
    chatUIStore.isConfigSidebarOpen = true
    message.success('配置已创建')
  } catch (error) {
    console.error('创建配置出错:', error)
    message.error(error.message || '创建配置失败')
  } finally {
    createConfigLoading.value = false
  }
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

.config-dropdown-overlay .config-dropdown-item-icon {
  flex-shrink: 0;
  color: var(--gray-500);
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

.config-dropdown-overlay .config-dropdown-divider {
  height: 1px;
  margin: 4px 4px;
  background: var(--gray-100);
}
</style>
