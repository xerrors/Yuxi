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
            <button
              v-if="selectedAgentId"
              class="input-action-btn"
              :class="{ active: chatUIStore.isConfigSidebarOpen }"
              :disabled="isLoadingConfig"
              @click="openConfigSidebar"
            >
              <Settings2 size="18" />
              <span class="hide-text">
                {{ isLoadingConfig ? '加载中...' : selectedConfigSummary?.name || '配置' }}
              </span>
            </button>
          </template>

          <template #header-right v-if="userStore.isAdmin">
            <div
              v-if="selectedAgentId"
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
import { ref, watch } from 'vue'
import { MessageOutlined, ShareAltOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { Settings2, Ellipsis } from 'lucide-vue-next'
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
const { selectedAgentId, defaultAgentId, selectedConfigSummary, isLoadingConfig } =
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

const openConfigSidebar = () => {
  chatUIStore.isConfigSidebarOpen = !chatUIStore.isConfigSidebarOpen
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
  .more-popup-menu {
    box-shadow:
      0 12px 32px rgba(0, 0, 0, 0.12),
      0 4px 12px rgba(0, 0, 0, 0.06);
  }
}
</style>
