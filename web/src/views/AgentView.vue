<template>
  <div class="agent-view">
    <div class="agent-view-body">
      <!-- 中间内容区域 -->
      <div class="content">
        <AgentChatComponent
          ref="chatComponentRef"
          :single-mode="false"
          @close-config-sidebar="() => (chatUIStore.isConfigSidebarOpen = false)"
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
const { agents, selectedAgentId, selectedConfigSummary, isLoadingConfig } = storeToRefs(agentStore)

const syncingRouteAgent = ref(false)

const getRouteAgentId = () => {
  const value = route.params.agent_id
  return typeof value === 'string' ? value : ''
}

const syncSelectedAgentFromRoute = async () => {
  const routeAgentId = getRouteAgentId()
  if (!routeAgentId) return

  syncingRouteAgent.value = true
  try {
    if (!agentStore.isInitialized) {
      await agentStore.initialize()
    }

    const routeAgentExists = (agents.value || []).some((agent) => agent.id === routeAgentId)
    if (!routeAgentExists) {
      if (selectedAgentId.value) {
        await router.replace({
          name: 'AgentCompWithId',
          params: { agent_id: selectedAgentId.value }
        })
      }
      return
    }

    if (selectedAgentId.value !== routeAgentId) {
      await agentStore.selectAgent(routeAgentId)
    }
  } catch (error) {
    handleChatError(error, 'load')
  } finally {
    syncingRouteAgent.value = false
  }
}

watch(
  () => route.params.agent_id,
  () => {
    syncSelectedAgentFromRoute()
  },
  { immediate: true }
)

watch(selectedAgentId, (newAgentId) => {
  if (!newAgentId || syncingRouteAgent.value) return
  const routeAgentId = getRouteAgentId()
  if (routeAgentId === newAgentId) return
  router.replace({ name: 'AgentCompWithId', params: { agent_id: newAgentId } })
})

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
    chatUIStore.openMoreMenu(rect.right - 130, rect.bottom + 8)
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

  .no-agent-selected {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    background-color: var(--bg-content);
  }

  .no-agent-content {
    text-align: center;
    color: var(--text-secondary);

    svg {
      margin-bottom: 16px;
      opacity: 0.6;
    }

    h3 {
      margin-bottom: 16px;
      color: var(--text-primary);
    }
  }

  // .content {
  //   border-radius: var(--gap-radius);
  //   border: 1px solid var(--gray-300);
  // }
}

.content {
  flex: 1;
  overflow: hidden;
}

// 配置弹窗内容样式
.conf-content {
  max-height: 70vh;
  overflow-y: auto;

  .agent-info {
    padding: 0;
    width: 100%;
    overflow-y: visible;
    max-height: none;
  }
}

.agent-model {
  width: 100%;
}

.config-modal-content {
  user-select: text;

  div[role='alert'] {
    margin-bottom: 10px;
  }

  .description {
    font-size: 12px;
    color: var(--gray-700);
  }

  .form-actions {
    display: flex;
    justify-content: space-between;
    margin-top: 20px;
    gap: 10px;

    .form-actions-left,
    .form-actions-right {
      display: flex;
      gap: 10px;
    }
  }
}

// 添加新按钮的样式
.agent-action-buttons {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.action-button {
  background-color: var(--gray-0);
  border: 1px solid var(--main-20);
  text-align: left;
  height: auto;
  padding: 8px 12px;

  &:hover {
    background-color: var(--main-20);
  }

  &.primary-action {
    color: var(--main-color);
    border-color: var(--main-color);

    &:disabled {
      color: var(--main-color);
      background-color: var(--main-20);
      cursor: not-allowed;
      opacity: 0.7;
    }
  }

  .anticon {
    margin-right: 8px;
  }
}

.agent-option {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  .agent-option-content {
    display: flex;
    flex-direction: column;
    gap: 2px;

    p {
      margin: 0;
    }

    .agent-option-description {
      font-size: 12px;
      color: var(--gray-700);
      word-break: break-word;
      white-space: pre-wrap;
    }
  }
}
// 工具选择器样式（与项目风格一致）
.tools-selector {
  .tools-summary {
    display: flex;
    justify-content: space-between;
    align-items: center;
    // margin-bottom: 8px;
    padding: 8px 12px;
    background: var(--gray-50);
    border-radius: 8px;
    border: 1px solid var(--gray-200);
    font-size: 14px;
    color: var(--gray-700);
    transition: border-color 0.2s ease;

    .tools-summary-left {
      display: flex;
      align-items: center;
      gap: 8px;

      .tools-count {
        color: var(--gray-900);
      }
    }

    .select-tools-btn {
      background: var(--main-color);
      border: none;
      color: var(--gray-0);
      border-radius: 6px;
      padding: 4px 12px;
      font-size: 13px;
      font-weight: 500;
      height: 28px;
      transition: all 0.2s ease;

      &:hover {
        background: var(--main-color);
        transform: translateY(-1px);
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
      }

      &:active {
        transform: translateY(0);
      }
    }
  }

  .selected-tools-preview {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 8px 0;
    background: none;
    border: none;
    min-height: 32px;
    :deep(.ant-tag) {
      margin: 0;
      padding: 4px 10px;
      border-radius: 6px;
      background: var(--gray-100);
      border: 1px solid var(--gray-300);
      color: var(--gray-900);
      font-size: 13px;
      font-weight: 400;
      .anticon-close {
        color: var(--gray-600);
        margin-left: 4px;
        &:hover {
          color: var(--gray-900);
        }
      }
    }
  }
}

// 工具选择弹窗样式（与项目风格一致）
.tools-modal {
  :deep(.ant-modal-content) {
    border-radius: 8px;
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.08);
    overflow: hidden;
  }
  :deep(.ant-modal-header) {
    background: var(--gray-0);
    border-bottom: 1px solid var(--gray-200);
    padding: 16px 20px;
    .ant-modal-title {
      font-size: 16px;
      font-weight: 600;
      color: var(--gray-900);
    }
  }
  :deep(.ant-modal-body) {
    padding: 20px;
    background: var(--gray-0);
  }
  .tools-modal-content {
    .tools-search {
      margin-bottom: 16px;
      :deep(.ant-input) {
        border-radius: 8px;
        border: 1px solid var(--gray-300);
        padding: 8px 12px;
        font-size: 14px;
        &:focus {
          border-color: var(--main-color);
          box-shadow: none;
        }
      }
    }
    .tools-list {
      max-height: 350px;
      overflow-y: auto;
      border: 1px solid var(--gray-200);
      border-radius: 8px;
      margin-bottom: 16px;
      background: var(--gray-0);
      .tool-item {
        padding: 14px 16px;
        border-bottom: 1px solid var(--gray-100);
        cursor: pointer;
        transition:
          background 0.2s,
          border 0.2s;
        border-left: 3px solid transparent;
        &:last-child {
          border-bottom: none;
        }
        &:hover {
          background: var(--gray-50);
        }
        &.selected {
          background: var(--main-10);
          border-left: 3px solid var(--main-color);
        }
        .tool-content {
          .tool-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
            .tool-name {
              font-weight: 500;
              color: var(--gray-900);
              font-size: 14px;
            }
            .tool-indicator {
              display: none;
            }
          }
          .tool-description {
            font-size: 13px;
            color: var(--gray-700);
            margin-bottom: 6px;
            line-height: 1.5;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            text-overflow: ellipsis;
          }
        }
      }
    }
    .tools-modal-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 0 0 0;
      border-top: 1px solid var(--gray-200);
      .selected-count {
        font-size: 13px;
        color: var(--gray-700);
        background: none;
        padding: 0;
        border: none;
      }
      .modal-actions {
        display: flex;
        gap: 10px;
        :deep(.ant-btn) {
          border-radius: 8px;
          font-weight: 500;
          padding: 6px 18px;
          height: 36px;
          font-size: 14px;
          &.ant-btn-default {
            border: 1px solid var(--gray-300);
            color: var(--gray-900);
            background: var(--gray-0);
            &:hover {
              border-color: var(--main-color);
              color: var(--main-color);
              background: var(--main-10);
            }
          }
          &.ant-btn-primary {
            background: var(--main-color);
            border: none;
            color: var(--gray-0);
            &:hover {
              background: var(--main-color);
            }
          }
        }
      }
    }
  }
}

// 多选卡片样式
.multi-select-cards {
  .multi-select-label {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
    font-size: 12px;
    color: var(--gray-600);
    height: 24px;
  }

  .options-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 8px;
  }

  .option-card {
    border: 1px solid var(--gray-300);
    border-radius: 8px;
    padding: 8px 12px;
    cursor: pointer;
    transition: all 0.2s ease;
    background: var(--gray-0);
    user-select: none;

    &:hover {
      border-color: var(--main-color);
    }

    &.selected {
      border-color: var(--main-color);
      background: var(--main-10);

      .option-indicator {
        color: var(--main-color);
      }

      .option-text {
        color: var(--main-color);
        font-weight: 500;
      }
    }

    &.unselected {
      .option-indicator {
        color: var(--gray-400);
      }

      .option-text {
        color: var(--gray-700);
      }
    }

    .option-content {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
    }

    .option-text {
      flex: 1;
      font-size: 14px;
      line-height: 1.4;
      word-break: break-word;
    }

    .option-indicator {
      flex-shrink: 0;
      font-size: 16px;
      transition: color 0.2s ease;
    }
  }
}

// 响应式适配
@media (max-width: 768px) {
  .multi-select-cards {
    .options-grid {
      grid-template-columns: 1fr;
    }
  }

  .conf-content {
    max-height: 60vh;
  }
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

<style lang="less">
.toggle-conf {
  cursor: pointer;

  &.nav-btn {
    height: 2.5rem;
    display: flex;
    justify-content: center;
    align-items: center;
    border-radius: 8px;
    color: var(--gray-900);
    cursor: pointer;
    font-size: 15px;
    width: auto;
    padding: 0.5rem 1rem;
    transition: background-color 0.3s;
    overflow: hidden;

    .text {
      margin-left: 10px;
    }

    &:hover {
      background-color: var(--main-20);
    }

    .nav-btn-icon {
      width: 1.5rem;
      height: 1.5rem;
    }
  }
}

// 针对 Ant Design Select 组件的深度样式修复
:deep(.ant-select-item-option-content) {
  .agent-option-name {
    color: var(--main-color);
    font-size: 14px;
    font-weight: 500;
  }
}

// 菜单项布局样式
.menu-item-layout {
  display: flex;
  align-items: center;
  gap: 8px;
}

.menu-item-full {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}
</style>
