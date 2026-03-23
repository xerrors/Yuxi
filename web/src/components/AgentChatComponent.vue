<template>
  <div class="chat-container">
    <ChatSidebarComponent
      :current-chat-id="currentChatId"
      :chats-list="chatsList"
      :is-sidebar-open="chatUIStore.isSidebarOpen"
      :is-floating="isSidebarFloating"
      :is-initial-render="localUIState.isInitialRender"
      :single-mode="props.singleMode"
      :agents="agents"
      :selected-agent-id="currentAgentId"
      :is-creating-new-chat="chatUIStore.creatingNewChat"
      :has-more-chats="hasMoreChats"
      :is-loading-more="isLoadingMoreChats"
      @create-chat="createNewChat"
      @select-chat="selectChat"
      @delete-chat="deleteChat"
      @rename-chat="renameChat"
      @toggle-pin="togglePinChat"
      @toggle-sidebar="toggleSidebar"
      @load-more-chats="loadMoreChats"
      :class="{
        'sidebar-open': chatUIStore.isSidebarOpen,
        'no-transition': localUIState.isInitialRender
      }"
    />
    <div class="chat">
      <div class="chat-header">
        <div class="header__left">
          <slot name="header-left"></slot>
          <div
            v-if="!chatUIStore.isSidebarOpen && !userStore.isAdmin"
            type="button"
            class="sidebar-logo-toggle"
            @click="toggleSidebar"
          >
            <img v-if="sidebarLogo" :src="sidebarLogo" alt="logo" class="sidebar-logo-image" />
            <PanelLeftOpen v-else class="sidebar-logo-fallback" size="18" />
            <div class="sidebar-expand-overlay">
              <PanelLeftOpen class="nav-btn-icon" size="18" />
            </div>
          </div>
          <div
            type="button"
            class="agent-nav-btn"
            v-if="!chatUIStore.isSidebarOpen && userStore.isAdmin"
            @click="toggleSidebar"
          >
            <PanelLeftOpen class="nav-btn-icon" size="18" />
          </div>
          <div
            type="button"
            class="agent-nav-btn"
            v-if="!chatUIStore.isSidebarOpen"
            :class="{ 'is-disabled': chatUIStore.creatingNewChat }"
            @click="createNewChat"
          >
            <LoaderCircle
              v-if="chatUIStore.creatingNewChat"
              class="nav-btn-icon loading-icon"
              size="18"
            />
            <MessageCirclePlus v-else class="nav-btn-icon" size="16" />
            <span class="text">新对话</span>
          </div>
          <div
            v-if="currentThread?.title && currentThread.title !== '新的对话'"
            class="conversation-title"
          >
            {{ currentThread.title }}
          </div>
        </div>
        <div class="header__right">
          <UserInfoComponent v-if="!userStore.isAdmin" />
          <!-- AgentState 显示按钮已移动到输入框底部 -->
          <slot name="header-right"></slot>
        </div>
      </div>

      <div class="chat-content-container">
        <!-- Main Chat Area -->
        <div class="chat-main" ref="chatMainRef">
          <div class="chat-box">
            <div class="conv-box" v-for="(conv, index) in conversations" :key="index">
              <AgentMessageComponent
                v-for="(message, msgIndex) in conv.messages"
                :message="message"
                :key="msgIndex"
                :is-processing="
                  isProcessing &&
                  conv.status === 'streaming' &&
                  msgIndex === conv.messages.length - 1
                "
                :show-refs="showMsgRefs(message)"
                @retry="retryMessage(message)"
              >
              </AgentMessageComponent>
              <!-- 显示对话最后一个消息使用的模型 -->
              <RefsComponent
                v-if="shouldShowRefs(conv)"
                :message="getLastMessage(conv)"
                :show-refs="['model', 'copy', 'sources']"
                :is-latest-message="false"
                :sources="getConversationSources(conv)"
              />
            </div>

            <!-- 生成中的加载状态 - 增强条件支持主聊天和resume流程 -->
            <div class="generating-status" v-if="isProcessing && conversations.length > 0">
              <div class="generating-indicator">
                <div class="loading-dots">
                  <div></div>
                  <div></div>
                  <div></div>
                </div>
                <span class="generating-text">正在生成回复...</span>
              </div>
            </div>
          </div>
          <div class="bottom" :class="{ 'start-screen': !conversations.length }">
            <!-- 人工审批弹窗 - 放在输入框上方 -->
            <HumanApprovalModal
              :visible="approvalState.showModal"
              :questions="approvalState.questions"
              @submit="handleQuestionSubmit"
              @cancel="handleQuestionCancel"
            />

            <div class="message-input-wrapper">
              <!-- 加载状态：加载消息 -->
              <div v-if="isLoadingMessages" class="chat-loading">
                <div class="loading-spinner"></div>
                <span>正在加载消息...</span>
              </div>

              <!-- 打招呼区域 - 在输入框上方 -->
              <div v-if="!conversations.length" class="chat-examples-input">
                <h1>👋 您好，我是{{ currentAgentName }}！</h1>
              </div>

              <div v-if="showStartAgentSegment" class="agent-segment-wrapper">
                <a-segmented
                  :value="currentAgentId"
                  :options="agentSegmentOptions"
                  @change="handleStartAgentChange"
                />
              </div>

              <AgentInputArea
                v-model="userInput"
                :is-loading="isProcessing"
                :disabled="!currentAgent"
                :send-button-disabled="(!userInput || !currentAgent) && !isProcessing"
                placeholder="输入问题..."
                :mention="mentionConfig"
                :supports-file-upload="supportsFileUpload"
                :has-state-content="hasAgentStateContent"
                :is-panel-open="isAgentPanelOpen"
                @send="handleSendOrStop"
                @upload-attachment="handleAttachmentUpload"
                @toggle-panel="toggleAgentPanel"
              >
                <template #actions-left-extra>
                  <slot name="input-actions-left"></slot>
                </template>
              </AgentInputArea>

              <!-- 示例问题 -->
              <div
                class="example-questions"
                v-if="!conversations.length && exampleQuestions.length > 0"
              >
                <div class="example-chips">
                  <div
                    v-for="question in exampleQuestions"
                    :key="question.id"
                    class="example-chip"
                    @click="handleExampleClick(question.text)"
                  >
                    {{ question.text }}
                  </div>
                </div>
              </div>

              <div class="bottom-actions" v-if="conversations.length > 0">
                <p class="note">当前智能体：{{ currentThreadAgentName }}；请注意辨别内容的可靠性</p>
              </div>
            </div>
          </div>
        </div>

        <!-- Agent Panel Area -->

        <div
          class="agent-panel-wrapper"
          ref="panelWrapperRef"
          :class="{
            'is-visible': isAgentPanelOpen && hasAgentStateContent,
            'no-transition': isResizing
          }"
          :style="{
            flexBasis: isAgentPanelOpen && hasAgentStateContent ? `${panelRatio * 100}%` : '0px'
          }"
        >
          <AgentPanel
            v-if="isAgentPanelOpen && hasAgentStateContent"
            :agent-state="currentAgentState"
            :thread-id="currentChatId"
            :panel-ratio="panelRatio"
            @refresh="handleAgentStateRefresh"
            @close="toggleAgentPanel"
            @resize="handlePanelResize"
            @resizing="handleResizingChange"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch, nextTick, computed, onUnmounted } from 'vue'
import { message } from 'ant-design-vue'
import AgentInputArea from '@/components/AgentInputArea.vue'
import AgentMessageComponent from '@/components/AgentMessageComponent.vue'
import ChatSidebarComponent from '@/components/ChatSidebarComponent.vue'
import RefsComponent from '@/components/RefsComponent.vue'
import { PanelLeftOpen, MessageCirclePlus, LoaderCircle } from 'lucide-vue-next'
import { handleChatError, handleValidationError } from '@/utils/errorHandler'
import { ScrollController } from '@/utils/scrollController'
import { AgentValidator } from '@/utils/agentValidator'
import { useAgentStore } from '@/stores/agent'
import { useChatUIStore } from '@/stores/chatUI'
import { useInfoStore } from '@/stores/info'
import { useUserStore } from '@/stores/user'
import { useConfigStore } from '@/stores/config'
import { storeToRefs } from 'pinia'
import { MessageProcessor } from '@/utils/messageProcessor'
import { agentApi, threadApi } from '@/apis'
import HumanApprovalModal from '@/components/HumanApprovalModal.vue'
import { useApproval } from '@/composables/useApproval'
import { useAgentThreadState } from '@/composables/useAgentThreadState'
import { useAgentRunStream } from '@/composables/useAgentRunStream'
import { useAgentStreamHandler } from '@/composables/useAgentStreamHandler'
import { useAgentMentionConfig } from '@/composables/useAgentMentionConfig'
import AgentPanel from '@/components/AgentPanel.vue'
import UserInfoComponent from '@/components/UserInfoComponent.vue'

// ==================== PROPS & EMITS ====================
const props = defineProps({
  agentId: { type: String, default: '' },
  singleMode: { type: Boolean, default: true }
})
const emit = defineEmits(['thread-change'])

// ==================== STORE MANAGEMENT ====================
const agentStore = useAgentStore()
const chatUIStore = useChatUIStore()
const infoStore = useInfoStore()
const userStore = useUserStore()
const configStore = useConfigStore()
const {
  agents,
  selectedAgentId,
  defaultAgentId,
  selectedAgentConfigId,
  agentConfig,
  configurableItems,
  availableKnowledgeBases,
  availableMcps,
  availableSkills
} = storeToRefs(agentStore)
const { organization } = storeToRefs(infoStore)

// ==================== LOCAL CHAT & UI STATE ====================
const userInput = ref('')
const sidebarLogo = computed(() => organization.value?.logo || organization.value?.avatar || '')
const useRunsApi =
  import.meta.env.VITE_USE_RUNS_API === 'true' &&
  localStorage.getItem('force_legacy_stream') !== 'true'

// 从智能体元数据获取示例问题
const exampleQuestions = computed(() => {
  const agentId = currentAgentId.value
  let examples = []
  if (agentId && agents.value && agents.value.length > 0) {
    const agent = agents.value.find((a) => a.id === agentId)
    examples = agent ? agent.metadata?.examples || [] : []
  }
  return examples.map((text, index) => ({
    id: index + 1,
    text: text
  }))
})

// 业务状态（保留在组件本地）
const chatState = reactive({
  currentThreadId: null,
  // 以threadId为键的线程状态
  threadStates: {}
})
const { getThreadState, resetOnGoingConv, stopThreadStream } = useAgentThreadState({
  chatState,
  getCurrentThreadId: () => chatState.currentThreadId
})

// 组件级别的线程和消息状态
const threads = ref([])
const threadMessages = ref({})
const hasMoreChats = ref(true) // 是否还有更多对话可加载
const isLoadingMoreChats = ref(false) // 加载更多对话中

// 本地 UI 状态（仅在本组件使用）
const localUIState = reactive({
  isInitialRender: true
})

// Agent Panel State
const isAgentPanelOpen = ref(false)
const isResizing = ref(false)
const panelRatio = ref(0.4) // 面板宽度比例 (0-1)
const panelWrapperRef = ref(null) // 直接操作 DOM
const minPanelRatio = 0.3 // 最小比例 30%
const maxPanelRatio = 0.6 // 最大比例 60%
let panelContainerWidth = 0

// ==================== COMPUTED PROPERTIES ====================
const currentAgentId = computed(() => {
  if (props.singleMode) {
    return props.agentId || defaultAgentId.value
  } else {
    return selectedAgentId.value
  }
})

const currentAgentName = computed(() => {
  const agent = currentAgent.value
  return agent ? agent.name : '智能体'
})

const currentAgent = computed(() => {
  if (!currentAgentId.value || !agents.value || !agents.value.length) return null
  return agents.value.find((a) => a.id === currentAgentId.value) || null
})
const chatsList = computed(() => threads.value || [])
const currentChatId = computed(() => chatState.currentThreadId)
const currentThread = computed(() => {
  if (!currentChatId.value) return null
  return threads.value.find((thread) => thread.id === currentChatId.value) || null
})

const currentThreadAgentName = computed(() => {
  const threadAgentId = currentThread.value?.agent_id
  if (threadAgentId && agents.value?.length) {
    const threadAgent = agents.value.find((agent) => agent.id === threadAgentId)
    if (threadAgent?.name) {
      return threadAgent.name
    }
  }
  return currentAgentName.value
})

// 检查当前智能体是否支持文件上传
const supportsFileUpload = computed(() => {
  if (!currentAgent.value) return false
  const capabilities = currentAgent.value.capabilities || []
  return capabilities.includes('file_upload')
})
const supportsTodo = computed(() => {
  if (!currentAgent.value) return false
  const capabilities = currentAgent.value.capabilities || []
  return capabilities.includes('todo')
})

const supportsFiles = computed(() => {
  if (!currentAgent.value) return false
  const capabilities = currentAgent.value.capabilities || []
  return capabilities.includes('files')
})

// AgentState 相关计算属性
const currentAgentState = computed(() => {
  return currentChatId.value ? getThreadState(currentChatId.value)?.agentState || null : null
})
const { mentionConfig } = useAgentMentionConfig({
  currentAgentState,
  configurableItems,
  agentConfig,
  availableKnowledgeBases,
  availableMcps,
  availableSkills
})

const countFiles = (files) => {
  if (!files) return 0
  if (Array.isArray(files)) {
    return files.reduce(
      (c, item) => c + (item && typeof item === 'object' ? Object.keys(item).length : 0),
      0
    )
  }
  return typeof files === 'object' ? Object.keys(files).length : 0
}

const hasAgentStateContent = computed(() => {
  const s = currentAgentState.value
  if (!s) return false
  const todoCount = Array.isArray(s.todos) ? s.todos.length : 0
  const fileCount = countFiles(s.files)
  return todoCount > 0 || fileCount > 0
})

// 监听 hasAgentStateContent 从 false → true 时，自动展开面板
watch(hasAgentStateContent, (newVal, oldVal) => {
  if (newVal && !oldVal) {
    // 从无状态变为有状态时，自动展开面板
    isAgentPanelOpen.value = true
  }
})

const currentThreadMessages = computed(() => threadMessages.value[currentChatId.value] || [])

// 计算是否显示Refs组件的条件
const shouldShowRefs = computed(() => {
  return (conv) => {
    return (
      getLastMessage(conv) &&
      conv.status !== 'streaming' &&
      !approvalState.showModal &&
      !(
        approvalState.threadId &&
        chatState.currentThreadId === approvalState.threadId &&
        isProcessing.value
      )
    )
  }
})

// 当前线程状态的computed属性
const currentThreadState = computed(() => {
  return getThreadState(currentChatId.value)
})

const onGoingConvMessages = computed(() => {
  const threadState = currentThreadState.value
  if (!threadState || !threadState.onGoingConv) return []

  const msgs = Object.values(threadState.onGoingConv.msgChunks).map(
    MessageProcessor.mergeMessageChunk
  )
  return msgs.length > 0
    ? MessageProcessor.convertToolResultToMessages(msgs).filter((msg) => msg.type !== 'tool')
    : []
})

const historyConversations = computed(() => {
  return MessageProcessor.convertServerHistoryToMessages(currentThreadMessages.value)
})

const conversations = computed(() => {
  const historyConvs = historyConversations.value

  // 如果有进行中的消息且线程状态显示正在流式处理，添加进行中的对话
  if (onGoingConvMessages.value.length > 0) {
    const onGoingConv = {
      messages: onGoingConvMessages.value,
      status: 'streaming'
    }
    return [...historyConvs, onGoingConv]
  }
  return historyConvs
})

const agentSegmentOptions = computed(() => {
  return (agents.value || []).map((agent) => ({
    label: agent.name || 'Unknown',
    value: agent.id
  }))
})

const showStartAgentSegment = computed(() => {
  return !props.singleMode && !conversations.value.length && agentSegmentOptions.value.length > 1
})

const handleStartAgentChange = async (agentId) => {
  if (!agentId || agentId === currentAgentId.value) return
  if (conversations.value.length > 0) return
  try {
    await agentStore.selectAgent(agentId)
  } catch (error) {
    handleChatError(error, 'load')
  }
}

const isLoadingMessages = computed(() => chatUIStore.isLoadingMessages)
const isStreaming = computed(() => {
  const threadState = currentThreadState.value
  return threadState ? threadState.isStreaming : false
})
const isProcessing = computed(() => isStreaming.value)

// ==================== SCROLL & RESIZE HANDLING ====================
const scrollController = new ScrollController('.chat-main')
const chatMainRef = ref(null)
const isSidebarFloating = ref(false)
let chatMainResizeObserver = null

onMounted(() => {
  nextTick(() => {
    const chatMainContainer = document.querySelector('.chat-main')
    if (chatMainContainer) {
      chatMainContainer.addEventListener('scroll', scrollController.handleScroll, { passive: true })
    }

    if (window.ResizeObserver && chatMainRef.value) {
      chatMainResizeObserver = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const width = entry.contentRect.width
          const isTakingSpace = chatUIStore.isSidebarOpen && !isSidebarFloating.value

          if (isTakingSpace) {
            if (width < 600) {
              isSidebarFloating.value = true
              chatUIStore.isSidebarOpen = false
              localStorage.setItem('chat_sidebar_open', 'false')
            }
          } else {
            if (width >= 880) {
              isSidebarFloating.value = false
            } else {
              isSidebarFloating.value = true
            }
          }
        }
      })
      chatMainResizeObserver.observe(chatMainRef.value)
    }
  })
  setTimeout(() => {
    localUIState.isInitialRender = false
  }, 300)
})

onUnmounted(() => {
  scrollController.cleanup()
  if (chatMainResizeObserver) {
    chatMainResizeObserver.disconnect()
  }
  // 清理所有线程状态
  resetOnGoingConv()
})

// ==================== 线程管理方法 ====================
// 获取当前智能体的线程列表
const fetchThreads = async (agentId = null) => {
  const targetAgentId = props.singleMode ? agentId || currentAgentId.value : agentId
  if (props.singleMode && !targetAgentId) return

  chatUIStore.isLoadingThreads = true
  try {
    const fetchedThreads = await threadApi.getThreads(targetAgentId, 100, 0)
    threads.value = fetchedThreads || []
    // 如果返回的数量小于limit，说明没有更多了
    hasMoreChats.value = fetchedThreads && fetchedThreads.length >= 100
  } catch (error) {
    console.error('Failed to fetch threads:', error)
    handleChatError(error, 'fetch')
    throw error
  } finally {
    chatUIStore.isLoadingThreads = false
  }
}

// 加载更多对话
const loadMoreChats = async () => {
  if (isLoadingMoreChats.value || !hasMoreChats.value) return

  const targetAgentId = props.singleMode ? currentAgentId.value : null
  if (props.singleMode && !targetAgentId) return

  isLoadingMoreChats.value = true
  try {
    const offset = threads.value.length
    const fetchedThreads = await threadApi.getThreads(targetAgentId, 100, offset)
    if (fetchedThreads && fetchedThreads.length > 0) {
      // 去除重复的置顶对话（后端每次都返回所有置顶对话）
      const existingIds = new Set(threads.value.map((t) => t.id))
      const newThreads = fetchedThreads.filter((t) => !existingIds.has(t.id))

      threads.value = [...threads.value, ...newThreads]
      hasMoreChats.value = newThreads.length >= 100
    } else {
      hasMoreChats.value = false
    }
  } catch (error) {
    console.error('Failed to load more chats:', error)
    handleChatError(error, 'fetch')
  } finally {
    isLoadingMoreChats.value = false
  }
}

// 创建新线程
const createThread = async (agentId, title = '新的对话') => {
  if (!agentId) return null

  chatState.isCreatingThread = true
  try {
    const thread = await threadApi.createThread(agentId, title)
    if (thread) {
      threads.value.unshift(thread)
      threadMessages.value[thread.id] = []
    }
    return thread
  } catch (error) {
    console.error('Failed to create thread:', error)
    handleChatError(error, 'create')
    throw error
  } finally {
    chatState.isCreatingThread = false
  }
}

// 删除线程
const deleteThread = async (threadId) => {
  if (!threadId) return

  chatState.isDeletingThread = true
  try {
    await threadApi.deleteThread(threadId)
    threads.value = threads.value.filter((thread) => thread.id !== threadId)
    delete threadMessages.value[threadId]

    if (chatState.currentThreadId === threadId) {
      chatState.currentThreadId = null
    }
  } catch (error) {
    console.error('Failed to delete thread:', error)
    handleChatError(error, 'delete')
    throw error
  } finally {
    chatState.isDeletingThread = false
  }
}

// 更新线程标题
const updateThread = async (threadId, title, is_pinned) => {
  if (!threadId) return

  if (title) {
    const normalizedTitle = String(title).replace(/\s+/g, ' ').trim().slice(0, 255)
    if (!normalizedTitle) return

    chatState.isRenamingThread = true
    try {
      await threadApi.updateThread(threadId, normalizedTitle, is_pinned)
      const thread = threads.value.find((t) => t.id === threadId)
      if (thread) {
        thread.title = normalizedTitle
        if (is_pinned !== undefined) {
          thread.is_pinned = is_pinned
        }
      }
    } catch (error) {
      console.error('Failed to update thread:', error)
      handleChatError(error, 'update')
      throw error
    } finally {
      chatState.isRenamingThread = false
    }
  } else if (is_pinned !== undefined) {
    // 只更新置顶状态
    try {
      await threadApi.updateThread(threadId, null, is_pinned)
      const thread = threads.value.find((t) => t.id === threadId)
      if (thread) {
        thread.is_pinned = is_pinned
      }
    } catch (error) {
      console.error('Failed to update thread pin status:', error)
      handleChatError(error, 'update')
      throw error
    }
  }
}

// 获取线程消息
const fetchThreadMessages = async ({ agentId, threadId, delay = 0 }) => {
  if (!threadId || !agentId) return

  // 如果指定了延迟，等待指定时间（用于确保后端数据库事务提交）
  if (delay > 0) {
    await new Promise((resolve) => setTimeout(resolve, delay))
  }

  try {
    const response = await agentApi.getAgentHistory(agentId, threadId)
    threadMessages.value[threadId] = response.history || []
  } catch (error) {
    handleChatError(error, 'load')
    throw error
  }
}

const fetchAgentState = async (agentId, threadId) => {
  if (!agentId || !threadId) return
  try {
    const res = await agentApi.getAgentState(agentId, threadId)
    const targetChatId = currentChatId.value || threadId
    const ts = getThreadState(targetChatId)
    if (ts) {
      ts.agentState = res.agent_state || null
    } else {
      const newTs = getThreadState(threadId)
      if (newTs) newTs.agentState = res.agent_state || null
    }
  } catch {
    // 忽略状态拉取失败，不阻塞主流程
  }
}

const ensureActiveThread = async (title = '新的对话') => {
  if (currentChatId.value) return currentChatId.value
  try {
    const newThread = await createThread(currentAgentId.value, title || '新的对话')
    if (newThread) {
      chatState.currentThreadId = newThread.id
      return newThread.id
    }
  } catch {
    // createThread 已处理错误提示
  }
  return null
}

const handleAttachmentUpload = async (files) => {
  if (!files?.length) return
  if (!AgentValidator.validateAgentIdWithError(currentAgentId.value, '上传附件', handleValidationError))
    return

  const preferredTitle = files[0]?.name || '新的对话'
  let threadId = currentChatId.value

  if (!threadId) {
    threadId = await ensureActiveThread(preferredTitle)
  }

  if (!threadId) {
    message.error('创建对话失败，无法上传附件')
    return
  }

  try {
    message.loading({
      content: '正在上传附件...',
      key: 'upload-attachment',
      duration: 0
    })
    for (const file of files) {
      await threadApi.uploadThreadAttachment(threadId, file)
    }
    message.success({ content: '附件上传成功', key: 'upload-attachment', duration: 2 })
    await fetchAgentState(currentAgentId.value, threadId)
  } catch (error) {
    message.destroy('upload-attachment')
    handleChatError(error, 'upload')
  }
}

// ==================== 审批功能管理 ====================
const { approvalState, handleApproval, processApprovalInStream } = useApproval({
  getThreadState,
  resetOnGoingConv,
  fetchThreadMessages
})

const { handleAgentResponse, handleStreamChunk } = useAgentStreamHandler({
  getThreadState,
  processApprovalInStream,
  currentAgentId,
  supportsTodo,
  supportsFiles
})
const { startRunStream, resumeActiveRunForThread, stopRunStreamSubscription } = useAgentRunStream({
  getThreadState,
  useRunsApi,
  currentAgentId,
  handleStreamChunk,
  processApprovalInStream,
  fetchThreadMessages,
  fetchAgentState,
  resetOnGoingConv,
  onScrollToBottom: () => scrollController.scrollToBottom()
})

// 发送消息并处理流式响应
const sendMessage = async ({
  agentId,
  threadId,
  text,
  signal = undefined,
  imageData = undefined
}) => {
  if (!agentId || !threadId || !text) {
    const error = new Error('Missing agent, thread, or message text')
    handleChatError(error, 'send')
    return Promise.reject(error)
  }

  // 如果是新对话，用 fast-model 异步生成标题（不阻塞消息发送）
  if ((threadMessages.value[threadId] || []).length === 0) {
    const autoTitle = text.replace(/\s+/g, ' ').trim().slice(0, 2000)
    if (autoTitle) {
      void (async () => {
        try {
          const generatedTitle = await agentApi.generateTitle(autoTitle, configStore.config?.fast_model)
          if (generatedTitle) {
            const finalTitle = generatedTitle.slice(0, 30).replace(/\s+/g, ' ').trim()
            if (finalTitle) {
              void updateThread(threadId, finalTitle).catch(() => {})
            }
          }
        } catch (e) {
          // 失败时使用原始文本作为标题
          void updateThread(threadId, autoTitle.slice(0, 30)).catch(() => {})
        }
      })()
    }
  }

  const requestData = {
    query: text,
    config: {
      thread_id: threadId,
      ...(selectedAgentConfigId.value ? { agent_config_id: selectedAgentConfigId.value } : {})
    }
  }

  // 如果有图片，添加到请求中
  if (imageData && imageData.imageContent) {
    requestData.image_content = imageData.imageContent
  }

  try {
    return await agentApi.sendAgentMessage(agentId, requestData, signal ? { signal } : undefined)
  } catch (error) {
    handleChatError(error, 'send')
    throw error
  }
}

// ==================== CHAT ACTIONS ====================
// 获取第一个非置顶的对话
const getFirstNonPinnedChat = (chatList) => {
  if (!chatList || chatList.length === 0) return null
  return chatList.find((chat) => !chat.is_pinned) || chatList[0]
}

const createNewChat = async () => {
  const previousThreadId = chatState.currentThreadId
  if (previousThreadId) {
    stopThreadStream(previousThreadId)
    // run 模式下仅断开 SSE 订阅，不取消后台运行任务
    stopRunStreamSubscription(previousThreadId)
  }
  // 进入未选中对话空态，路由由 thread-change 统一同步到 /agent
  chatState.currentThreadId = null
}

const selectChat = async (chatId) => {
  const targetChat = threads.value.find((chat) => chat.id === chatId) || null
  const targetAgentId = targetChat?.agent_id || currentAgentId.value
  const previousThreadId = chatState.currentThreadId

  if (!targetAgentId) {
    handleValidationError('选择对话失败：缺少智能体信息')
    return
  }

  if (!AgentValidator.validateAgentIdWithError(targetAgentId, '选择对话', handleValidationError))
    return

  // 中断之前线程的流式输出（如果存在）
  if (previousThreadId && previousThreadId !== chatId) {
    stopThreadStream(previousThreadId)
    // run 模式下仅断开 SSE 订阅，不取消后台运行任务
    stopRunStreamSubscription(previousThreadId)
  }

  // 先更新当前线程，确保底部智能体名称与选中项即时同步
  chatState.currentThreadId = chatId

  if (!props.singleMode && targetChat?.agent_id && targetChat.agent_id !== currentAgentId.value) {
    try {
      await agentStore.selectAgent(targetChat.agent_id)
    } catch (error) {
      chatState.currentThreadId = previousThreadId
      handleChatError(error, 'load')
      return
    }
  }

  chatUIStore.isLoadingMessages = true
  try {
    await fetchThreadMessages({ agentId: targetAgentId, threadId: chatId })
  } catch (error) {
    handleChatError(error, 'load')
  } finally {
    chatUIStore.isLoadingMessages = false
  }

  await nextTick()
  scrollController.scrollToBottomStaticForce()
  await fetchAgentState(targetAgentId, chatId)
  await resumeActiveRunForThread(chatId)
}

const selectThreadFromRoute = async (threadId) => {
  if (!agentStore.isInitialized) {
    await initAll()
  }

  if (!threadId) {
    const previousThreadId = chatState.currentThreadId
    if (previousThreadId) {
      stopThreadStream(previousThreadId)
      stopRunStreamSubscription(previousThreadId)
    }
    chatState.currentThreadId = null
    return true
  }

  if (chatState.currentThreadId === threadId) {
    return true
  }

  if (!threads.value.length || !threads.value.find((thread) => thread.id === threadId)) {
    await loadChatsList()
  }

  const targetThread = threads.value.find((thread) => thread.id === threadId)
  if (!targetThread) {
    return false
  }

  await selectChat(threadId)
  return true
}

const deleteChat = async (chatId) => {
  if (
    !AgentValidator.validateAgentIdWithError(
      currentAgentId.value,
      '删除对话',
      handleValidationError
    )
  )
    return
  try {
    await deleteThread(chatId)
    if (chatState.currentThreadId === chatId) {
      chatState.currentThreadId = null
      // 删除当前对话后回到空态，发送消息时再创建新线程
      await createNewChat()
    }
  } catch (error) {
    handleChatError(error, 'delete')
  }
}

const renameChat = async (data) => {
  let { chatId, title } = data
  if (
    !AgentValidator.validateRenameOperation(
      chatId,
      title,
      currentAgentId.value,
      handleValidationError
    )
  )
    return
  if (title.length > 30) title = title.slice(0, 30)
  try {
    await updateThread(chatId, title)
  } catch (error) {
    handleChatError(error, 'rename')
  }
}

const togglePinChat = async (chatId) => {
  const chat = chatsList.value.find((c) => c.id === chatId)
  if (!chat) return
  try {
    // 保存当前选中的对话ID
    const prevChatId = currentChatId.value

    await updateThread(chatId, null, !chat.is_pinned)

    // 刷新对话列表
    await loadChatsList()

    // 恢复当前选中的对话
    if (prevChatId) {
      chatState.currentThreadId = prevChatId
    }
  } catch (error) {
    handleChatError(error, 'pin')
  }
}

const handleSendMessage = async ({ image } = {}) => {
  const text = userInput.value.trim()
  if ((!text && !image) || !currentAgent.value || isProcessing.value) return

  let threadId = currentChatId.value
  if (!threadId) {
    threadId = await ensureActiveThread(text)
    if (!threadId) {
      message.error('创建对话失败，请重试')
      return
    }
  }

  userInput.value = ''

  await nextTick()
  scrollController.scrollToBottom(true)

  const threadState = getThreadState(threadId)
  if (!threadState) return

  if (useRunsApi) {
    if ((threadMessages.value[threadId] || []).length === 0) {
      const autoTitle = text.replace(/\s+/g, ' ').trim().slice(0, 2000)
      if (autoTitle) {
        void (async () => {
          try {
            const generatedTitle = await agentApi.generateTitle(autoTitle, configStore.config?.fast_model)
            if (generatedTitle) {
              const finalTitle = generatedTitle.slice(0, 30).replace(/\s+/g, ' ').trim()
              if (finalTitle) {
                void updateThread(threadId, finalTitle).catch(() => {})
              }
            }
          } catch (e) {
            // 失败时使用原始文本作为标题
            void updateThread(threadId, autoTitle.slice(0, 30)).catch(() => {})
          }
        })()
      }
    }

    resetOnGoingConv(threadId)
    threadState.isStreaming = true
    try {
      const runResp = await agentApi.createAgentRun(currentAgentId.value, {
        query: text,
        config: {
          thread_id: threadId,
          ...(selectedAgentConfigId.value ? { agent_config_id: selectedAgentConfigId.value } : {})
        },
        image_content: image?.imageContent
      })
      const runId = runResp?.run_id
      if (!runId) {
        throw new Error('创建 run 失败：缺少 run_id')
      }
      await startRunStream(threadId, runId, 0)
    } catch (error) {
      threadState.isStreaming = false
      handleChatError(error, 'send')
    }
    return
  }

  threadState.isStreaming = true
  resetOnGoingConv(threadId)
  threadState.streamAbortController = new AbortController()

  try {
    const response = await sendMessage({
      agentId: currentAgentId.value,
      threadId: threadId,
      text: text,
      signal: threadState.streamAbortController?.signal,
      imageData: image
    })

    await handleAgentResponse(response, threadId)
  } catch (error) {
    if (error.name !== 'AbortError') {
      console.error('Stream error:', error)
      handleChatError(error, 'send')
    } else {
      console.warn('[Interrupted] Catch')
    }
    threadState.isStreaming = false
  } finally {
    threadState.streamAbortController = null
    // 异步加载历史记录，保持当前消息显示直到历史记录加载完成
    fetchThreadMessages({ agentId: currentAgentId.value, threadId: threadId }).finally(() => {
      // 历史记录加载完成后，安全地清空当前进行中的对话
      resetOnGoingConv(threadId)
      fetchAgentState(currentAgentId.value, threadId)
      scrollController.scrollToBottom()
    })
  }
}

// 发送或中断
const handleSendOrStop = async (payload) => {
  const threadId = currentChatId.value
  const threadState = getThreadState(threadId)
  if (isProcessing.value && threadState) {
    if (useRunsApi && threadState.activeRunId) {
      try {
        await agentApi.cancelAgentRun(threadState.activeRunId)
        message.info('已发送取消请求')
      } catch (error) {
        handleChatError(error, 'stop')
      }
      return
    }

    if (threadState.streamAbortController) {
      // 中断生成
      threadState.streamAbortController.abort()

      // 中断后刷新消息历史，确保显示最新的状态
      try {
        await fetchThreadMessages({ agentId: currentAgentId.value, threadId: threadId, delay: 500 })
        fetchAgentState(currentAgentId.value, threadId)
        message.info('已中断对话生成')
      } catch (error) {
        console.error('刷新消息历史失败:', error)
        message.info('已中断对话生成')
      }
      return
    }
  }
  await handleSendMessage(payload)
}

// ==================== 人工审批处理 ====================
const handleApprovalWithStream = async (answer) => {
  const threadId = approvalState.threadId
  if (!threadId) {
    message.error('无效的提问请求')
    approvalState.showModal = false
    return
  }

  const threadState = getThreadState(threadId)
  if (!threadState) {
    message.error('无法找到对应的对话线程')
    approvalState.showModal = false
    return
  }

  try {
    // 使用审批 composable 处理审批
    const response = await handleApproval(answer, currentAgentId.value, selectedAgentConfigId.value)

    if (!response) return // 如果 handleApproval 抛出错误，这里不会执行

    // 处理流式响应
    await handleAgentResponse(response, threadId)
  } catch (error) {
    if (error.name !== 'AbortError') {
      console.error('Resume approval error:', error)
    }
  } finally {
    if (threadState) {
      threadState.isStreaming = false
      threadState.streamAbortController = null
    }

    // 异步加载历史记录，保持当前消息显示直到历史记录加载完成
    fetchThreadMessages({ agentId: currentAgentId.value, threadId: threadId }).finally(() => {
      resetOnGoingConv(threadId)
      fetchAgentState(currentAgentId.value, threadId)
      scrollController.scrollToBottom()
    })
  }
}

const handleQuestionSubmit = (answer) => {
  handleApprovalWithStream(answer)
}

const handleQuestionCancel = () => {
  handleApprovalWithStream('reject')
}

// 处理示例问题点击
const handleExampleClick = (questionText) => {
  userInput.value = questionText
  nextTick(() => {
    handleSendMessage()
  })
}

const buildExportPayload = () => {
  const agentId = currentAgentId.value
  let agentDescription = ''
  if (agentId && agents.value && agents.value.length > 0) {
    const agent = agents.value.find((a) => a.id === agentId)
    agentDescription = agent ? agent.description || '' : ''
  }

  const payload = {
    chatTitle: currentThread.value?.title || '新对话',
    agentName: currentAgentName.value || currentAgent.value?.name || '智能助手',
    agentDescription: agentDescription || currentAgent.value?.description || '',
    messages: conversations.value ? JSON.parse(JSON.stringify(conversations.value)) : [],
    onGoingMessages: onGoingConvMessages.value
      ? JSON.parse(JSON.stringify(onGoingConvMessages.value))
      : []
  }

  return payload
}

defineExpose({
  getExportPayload: buildExportPayload,
  selectThreadFromRoute
})

const toggleSidebar = () => {
  chatUIStore.toggleSidebar()
}

const handleAgentStateRefresh = async (threadId = null) => {
  if (!currentAgentId.value) return
  const chatId = threadId || currentChatId.value
  if (!chatId) return
  await fetchAgentState(currentAgentId.value, chatId)
}

const toggleAgentPanel = () => {
  isAgentPanelOpen.value = !isAgentPanelOpen.value
}

// 处理面板宽度调整（使用比例）
// 向右拖动(deltaX > 0)让面板变窄，向左拖动(deltaX < 0)让面板变宽
const handlePanelResize = (deltaX) => {
  if (!panelWrapperRef.value) return

  // 初始化容器宽度
  if (!panelContainerWidth) {
    const container = document.querySelector('.chat-content-container')
    panelContainerWidth = container ? container.clientWidth : window.innerWidth
  }

  const currentWidth = panelWrapperRef.value.offsetWidth
  // 反转 deltaX：向右拖(deltaX > 0)让面板变窄
  const newWidth = currentWidth - deltaX
  const newRatio = newWidth / panelContainerWidth

  // 限制在合理范围内
  if (newRatio >= minPanelRatio && newRatio <= maxPanelRatio) {
    // 直接操作 DOM，不触发 Vue 响应式，使用 !important 确保不被覆盖
    panelWrapperRef.value.style.setProperty('flex', `0 0 ${newWidth}px`, 'important')
  }
}

// 拖拽状态变化时，同步最终状态到 Vue 响应式数据
const handleResizingChange = (isResizingState) => {
  isResizing.value = isResizingState

  // 拖拽结束时，同步 DOM 宽度到响应式数据
  if (!isResizingState && panelWrapperRef.value && panelContainerWidth) {
    const finalWidth = panelWrapperRef.value.offsetWidth
    panelRatio.value = finalWidth / panelContainerWidth
    panelContainerWidth = 0 // 重置，供下次使用
  }
}

// ==================== HELPER FUNCTIONS ====================
const getLastMessage = (conv) => {
  if (!conv?.messages?.length) return null
  for (let i = conv.messages.length - 1; i >= 0; i--) {
    if (conv.messages[i].type === 'ai') return conv.messages[i]
  }
  return null
}

const showMsgRefs = (msg) => {
  // 如果正在审批中，不显示 refs
  if (approvalState.showModal) {
    return false
  }

  // 如果当前线程ID与审批线程ID匹配，但审批框已关闭（说明刚刚处理完审批）
  // 且当前有新的流式处理正在进行，则不显示之前被中断的消息的 refs
  if (
    approvalState.threadId &&
    chatState.currentThreadId === approvalState.threadId &&
    !approvalState.showModal &&
    isProcessing
  ) {
    return false
  }

  // 只有真正完成的消息才显示 refs
  if (msg.isLast && msg.status === 'finished') {
    return ['copy', 'sources']
  }
  return false
}

const getConversationSources = (conv) => {
  return MessageProcessor.extractSourcesFromConversation(conv, availableKnowledgeBases.value)
}

// ==================== LIFECYCLE & WATCHERS ====================
const loadChatsList = async () => {
  const agentId = props.singleMode ? currentAgentId.value : null
  if (props.singleMode && !agentId) {
    console.warn('No agent selected, cannot load chats list')
    threads.value = []
    chatState.currentThreadId = null
    return
  }

  try {
    await fetchThreads(agentId)
    if (props.singleMode && currentAgentId.value !== agentId) return

    // 如果当前线程不在线程列表中，清空当前线程
    if (
      chatState.currentThreadId &&
      !threads.value.find((t) => t.id === chatState.currentThreadId)
    ) {
      chatState.currentThreadId = null
    }

    // singleMode 保持旧行为：自动选择首个可用对话
    if (props.singleMode && threads.value.length > 0 && !chatState.currentThreadId) {
      await selectChat(getFirstNonPinnedChat(threads.value).id)
    }
  } catch (error) {
    handleChatError(error, 'load')
  }
}

const initAll = async () => {
  try {
    if (!agentStore.isInitialized) {
      await agentStore.initialize()
    }
  } catch (error) {
    handleChatError(error, 'load')
  }
}

onMounted(async () => {
  await initAll()
  scrollController.enableAutoScroll()
})

watch(
  currentAgentId,
  async (newAgentId, oldAgentId) => {
    if (!props.singleMode) {
      if (oldAgentId === undefined) {
        await loadChatsList()
      }
      return
    }

    if (newAgentId !== oldAgentId) {
      // 清理当前线程状态
      chatState.currentThreadId = null
      threadMessages.value = {}
      // 清理所有线程状态
      resetOnGoingConv()

      if (newAgentId) {
        await loadChatsList()
      } else {
        threads.value = []
      }
    }
  },
  { immediate: true }
)

watch(
  conversations,
  () => {
    if (isProcessing.value) {
      scrollController.scrollToBottom()
    }
  },
  { deep: true, flush: 'post' }
)

watch(currentChatId, (threadId, oldThreadId) => {
  if (threadId === oldThreadId) return
  emit('thread-change', threadId || '')
})
</script>

<style lang="less" scoped>
@import '@/assets/css/main.css';
@import '@/assets/css/animations.less';

.chat-container {
  display: flex;
  width: 100%;
  height: 100%;
  position: relative;
}

.chat {
  position: relative;
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden; /* Changed from overflow-x: hidden to overflow: hidden */
  position: relative;
  box-sizing: border-box;
  transition: all 0.3s ease;

  .chat-header {
    user-select: none;
    // position: sticky; // Not needed if .chat is flex col and header is fixed height item
    // top: 0;
    z-index: 10;
    height: var(--header-height);
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 1rem 8px;
    flex-shrink: 0; /* Prevent header from shrinking */

    .header__left,
    .header__right {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .switch-icon {
      color: var(--gray-500);
      transition: all 0.2s ease;
    }

    .agent-nav-btn:hover .switch-icon {
      color: var(--main-500);
    }

    .sidebar-logo-toggle {
      position: relative;
      width: 32px;
      height: 32px;
      border-radius: 8px;
      border: 1px solid var(--gray-150);
      background: var(--gray-0);
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      cursor: pointer;
      flex-shrink: 0;
    }

    .sidebar-logo-image {
      width: 24px;
      height: 24px;
      border-radius: 6px;
      object-fit: cover;
    }

    .sidebar-logo-fallback {
      color: var(--gray-700);
    }

    .sidebar-expand-overlay {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      background: var(--gray-100);
      color: var(--gray-900);
      opacity: 0;
      transition: opacity 0.2s ease;
    }

    .sidebar-logo-toggle:hover .sidebar-expand-overlay {
      opacity: 1;
    }

    .conversation-title {
      font-size: 14px;
      font-weight: 500;
      color: var(--text-primary);
      max-width: 200px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      margin-left: 8px;
    }
  }
}

.chat-content-container {
  flex: 1;
  display: flex;
  flex-direction: row;
  overflow: hidden;
  position: relative;
  width: 100%;
  contain: layout;
}

.chat-main {
  flex: 1 1 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto; /* Scroll is here now */
  position: relative;
  transition:
    flex-basis 0.3s cubic-bezier(0.4, 0, 0.2, 1),
    width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  min-width: 0; /* Prevent flex item from overflowing */

  scrollbar-width: none;
}

.agent-panel-wrapper {
  flex: 0 0 auto;
  align-self: flex-end;
  height: 70vh;
  overflow: hidden;
  z-index: 20;
  margin: 28px 8px;
  margin-left: 0;
  background: var(--gray-0);
  border-radius: 16px;
  box-shadow: 0 4px 20px var(--shadow-1);
  border: 1px solid var(--gray-150);
  min-width: 0;
  will-change: flex-basis;
}

@media (max-height: 700px) {
  .agent-panel-wrapper {
    height: calc(100% - 56px);
  }
}

/* Workbench transition animations */
.agent-panel-wrapper {
  transition: flex-basis 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  opacity: 0;
  transform: translateX(10px);
  margin-left: -16px;
}

.agent-panel-wrapper.is-visible {
  opacity: 1;
  transform: translateX(0);
  margin-left: 0;
}

.agent-panel-wrapper.no-transition {
  transition: none !important;
}

.chat-examples-input {
  padding: 32px 0;
  text-align: center;

  h1 {
    font-size: 1.2rem;
    color: var(--gray-1000);
    margin: 0;
  }
}

.agent-segment-wrapper {
  width: fit-content;
  max-width: 100%;
  margin: 0 auto 10px;
  overflow-x: auto;
  scrollbar-width: none;

  &::-webkit-scrollbar {
    display: none;
  }

  :deep(.ant-segmented) {
    width: auto;
    max-width: 100%;
    white-space: nowrap;
    background: var(--gray-50);
    border: 1px solid var(--gray-150);
    border-radius: 10px;
  }

  :deep(.ant-segmented-group) {
    width: auto;
    display: inline-flex;
  }

  :deep(.ant-segmented-item) {
    flex: 0 0 auto;
    min-width: 0;
  }

  :deep(.ant-segmented-item-label) {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.example-questions {
  margin-top: 16px;
  text-align: center;

  .example-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: center;
  }

  .example-chip {
    padding: 6px 12px;
    background: var(--gray-25);
    // border: 1px solid var(--gray-100);
    border-radius: 16px;
    cursor: pointer;
    font-size: 0.8rem;
    color: var(--gray-700);
    transition: all 0.15s ease;
    white-space: nowrap;
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;

    &:hover {
      // background: var(--main-25);
      border-color: var(--main-200);
      color: var(--main-700);
      box-shadow: 0 0px 4px rgba(0, 0, 0, 0.03);
    }

    &:active {
      transform: translateY(0);
      box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
    }
  }
}

.chat-loading {
  padding: 0 50px;
  text-align: center;
  position: absolute;
  top: 20%;
  width: 100%;
  z-index: 9;
  animation: slideInUp 0.5s ease-out;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;

  span {
    color: var(--gray-700);
    font-size: 14px;
  }

  .loading-spinner {
    width: 20px;
    height: 20px;
    border: 2px solid var(--gray-200);
    border-top-color: var(--main-color);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
}

.chat-box {
  width: 100%;
  max-width: 800px;
  margin: 0 auto;
  flex-grow: 1;
  padding: 1rem 1.25rem;
  display: flex;
  flex-direction: column;
}

.conv-box {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bottom {
  position: sticky;
  bottom: 0;
  width: 100%;
  margin: 0 auto;
  padding: 4px 1rem 0 1rem;
  background: var(--gray-0);
  z-index: 1000;

  .message-input-wrapper {
    width: 100%;
    max-width: 800px;
    margin: 0 auto;

    .bottom-actions {
      display: flex;
      justify-content: center;
      align-items: center;
    }

    .note {
      font-size: small;
      color: var(--gray-300);
      margin: 4px 0;
      user-select: none;
    }
  }

  &.start-screen {
    position: absolute;
    top: 45%;
    left: 50%;
    transform: translate(-50%, -50%);
    bottom: auto;
    max-width: 800px;
    width: 90%;
    background: transparent;
    padding: 0;
    border-top: none;
    z-index: 100; /* Ensure it's above other elements */
  }
}

.loading-dots {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
}

.loading-dots div {
  width: 6px;
  height: 6px;
  background: linear-gradient(135deg, var(--main-color), var(--main-700));
  border-radius: 50%;
  animation: dotPulse 1.4s infinite ease-in-out both;
}

.loading-dots div:nth-child(1) {
  animation-delay: -0.32s;
}

.loading-dots div:nth-child(2) {
  animation-delay: -0.16s;
}

.loading-dots div:nth-child(3) {
  animation-delay: 0s;
}

.generating-status {
  display: flex;
  justify-content: flex-start;
  padding: 1rem 0;
  animation: fadeInUp 0.4s ease-out;
  transition: all 0.2s;
}

.generating-indicator {
  display: flex;
  align-items: center;
  padding: 0.75rem 0rem;

  .generating-text {
    margin-left: 12px;
    font-size: 14px;
    font-weight: 500;
    letter-spacing: 0.025em;
    /* 恢复灰色调：深灰 -> 亮灰(高光) -> 深灰 */
    background: linear-gradient(
      90deg,
      var(--gray-700) 0%,
      var(--gray-700) 40%,
      var(--gray-300) 45%,
      var(--gray-200) 50%,
      var(--gray-300) 55%,
      var(--gray-700) 60%,
      var(--gray-700) 100%
    );
    background-size: 200% auto;
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
    animation: waveFlash 2s linear infinite;
  }
}

@keyframes waveFlash {
  0% {
    background-position: 200% center;
  }
  100% {
    background-position: -200% center;
  }
}

@media (max-width: 1800px) {
  .chat-header {
    background-color: var(--gray-0);
    border-bottom: 1px solid var(--gray-100);
  }
}

@media (max-width: 768px) {
  .agent-segment-wrapper {
    margin-bottom: 8px;

    :deep(.ant-segmented-item-label) {
      font-size: 12px;
    }
  }

  .chat-header {
    .header__left {
      .text {
        display: none;
      }
    }
  }
}
</style>

<style lang="less">
.agent-nav-btn {
  display: flex;
  gap: 6px;
  padding: 6px 8px;
  height: 32px;
  justify-content: center;
  align-items: center;
  border-radius: 6px;
  color: var(--gray-900);
  cursor: pointer;
  width: auto;
  font-size: 15px;
  transition: background-color 0.3s;
  border: none;
  background: transparent;

  &:hover:not(.is-disabled) {
    background-color: var(--gray-100);
  }

  &.is-disabled {
    cursor: not-allowed;
    opacity: 0.7;
    pointer-events: none;
  }

  .nav-btn-icon {
    height: 18px;
  }

  .loading-icon {
    animation: spin 1s linear infinite;
  }
}

.hide-text {
  display: none;
}

@media (min-width: 769px) {
  .hide-text {
    display: inline;
  }
}

/* AgentState 按钮有内容时的样式 */
.agent-nav-btn.agent-state-btn.has-content:hover:not(.is-disabled) {
  color: var(--main-700);
  background-color: var(--main-20);
}

.agent-nav-btn.agent-state-btn.active {
  color: var(--main-700);
  background-color: var(--main-20);
}
</style>
