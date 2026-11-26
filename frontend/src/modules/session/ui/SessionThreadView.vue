<template>
  <ThreadTimeline
    ref="timelineRef"
    :thread-id="currentChatId"
    :active="workspaceActive && userStore.isLoggedIn"
    :page-visible="pageVisible"
    :loading="isSessionLoading"
    :processing="isProcessing"
    :reply-loading="isReplyLoading"
    :suppress-refs="shouldSuppressRefsForApproval()"
    :config-notice="currentThreadConfigNotice"
    :config-notice-scroll-version="configNoticeScrollVersion"
    :mention-config="mentionConfig"
    :cooperation-view="cooperationToolView"
    :knowledge-bases="availableKnowledgeBases"
    :widget-action="handleWidgetAction"
    :widget-actions-disabled="widgetActionsDisabled"
    @terminal="handleTerminal"
    @loaded="initializeThreadInput"
    @open-refs="emit('open-refs', $event)"
    @open-session="emit('open-session', $event)"
    @artifact-saved="handleArtifactSaved"
    @open-preview="emit('open-preview', $event, currentChatId)"
  >
    <template #status>
      <div class="generating-status" v-if="isReplyLoading && !isSessionLoading" role="status">
        <div class="generating-indicator">
          <span class="loading-spark" aria-hidden="true"></span>
          <span class="generating-text">{{ replyLoadingText }}</span>
          <button
            v-if="primaryQueuedInput && !currentThreadState?.activeRunId"
            type="button"
            class="generating-cancel lucide-icon-btn"
            aria-label="取消这条待处理输入"
            title="取消这条待处理输入"
            :disabled="cancellingInputIds.has(primaryQueuedInput.input_id)"
            @click="handleCancelQueuedInput(primaryQueuedInput.input_id)"
          >
            <X :size="14" />
          </button>
        </div>
      </div>
    </template>
    <div ref="messageInputDockRef" class="bottom" :class="{ 'start-screen': showStartScreen }">
      <div class="message-input-wrapper">
        <div
          v-if="pendingSends[currentChatId] && !pendingSends[currentChatId].submitting"
          class="queued-request-notice"
        >
          <span>发送结果待确认，重试会确认同一条消息。</span>
          <button
            class="queued-request-continue"
            :disabled="
              !agentExecutionAvailable ||
              sendCooldownActive ||
              currentThreadState?.approvalModeSaving ||
              pendingSends[currentChatId]?.submitting
            "
            @click="handleSendMessage({ retry: true })"
          >
            重试发送
          </button>
        </div>
        <!-- 打招呼区域 - 在输入框上方 -->
        <div v-if="showStartScreen" class="chat-greeting-input">
          <h1>{{ randomGreeting }}</h1>
        </div>

        <section
          v-if="visibleQueuedInputs.length"
          class="queued-request-panel"
          aria-label="排队请求"
        >
          <div
            v-if="currentQueueSnapshot.status === 'paused'"
            class="queued-request-notice is-paused"
          >
            <span>{{ queuePausedMessage }}</span>
            <button
              type="button"
              class="queued-request-continue"
              :disabled="!agentExecutionAvailable || currentThreadState?.continueQueueInFlight"
              @click="handleContinueQueue"
            >
              <Play :size="14" fill="currentColor" />
              继续队列
            </button>
          </div>
          <div v-else-if="isWaitingForUserAction" class="queued-request-notice">
            当前任务正在等待回答或审批，完成后将继续处理后续输入。
          </div>
          <div class="queued-request-list">
            <div
              v-for="input in visibleQueuedInputs"
              :key="input.input_id"
              class="queued-request-row"
            >
              <CornerDownRight :size="16" class="queued-request-icon" aria-hidden="true" />
              <span class="queued-request-content" :title="input.content || '排队输入'">
                {{ input.content || '排队输入' }}
              </span>
              <span class="queued-request-status" :title="input.attachment_error || ''">
                {{
                  input.attachment_status === 'preparing'
                    ? '附件准备中'
                    : input.kind === 'steer'
                      ? '等待引导'
                      : '排队中'
                }}
              </span>
              <div class="queued-request-actions">
                <button
                  v-if="canPromoteQueuedInput(input)"
                  type="button"
                  class="queued-request-promote lucide-icon-btn"
                  :disabled="
                    promotingInputIds.has(input.input_id) || cancellingInputIds.has(input.input_id)
                  "
                  :title="isStreaming ? '转为引导，将沿用当前任务的模型和审批配置' : '转为引导'"
                  aria-label="转为引导"
                  @click="handlePromoteQueuedInput(input.input_id)"
                >
                  <ArrowUpFromLine :size="16" />
                </button>
                <button
                  v-if="canCancelQueuedInput(input)"
                  type="button"
                  class="queued-request-delete lucide-icon-btn"
                  :disabled="cancellingInputIds.has(input.input_id)"
                  :aria-label="`取消排队输入：${input.content || '排队输入'}`"
                  @click="handleCancelQueuedInput(input.input_id)"
                >
                  <Trash2 :size="16" />
                </button>
              </div>
            </div>
          </div>
        </section>

        <slot name="before-input" />

        <div
          ref="messageInputStageRef"
          class="message-input-stage"
          :class="{ 'has-tool-approval': currentToolApprovalVisible }"
        >
          <div
            v-if="currentChatId && ['unavailable', 'error'].includes(sessionAgentStatus)"
            class="agent-availability-notice"
            role="status"
          >
            <span v-if="sessionAgentStatus === 'unavailable'">智能体当前不可用</span>
            <template v-else-if="sessionAgentStatus === 'error'">
              <span>智能体加载失败，请重试</span>
              <a-button
                type="link"
                size="small"
                @click="initializeThreadInput({ threadId: currentChatId })"
                >重试</a-button
              >
            </template>
          </div>
          <HumanApprovalModal
            :key="currentTurnAction?.waitpointId"
            :waitpoint-id="currentTurnAction?.waitpointId"
            :visible="currentApprovalModalVisible"
            :processing="approvalSubmitting"
            :questions="currentTurnAction?.questions"
            :kind="currentTurnAction?.kind"
            :action-requests="currentTurnAction?.actionRequests"
            :error="approvalError"
            @submit="submitTurnAction"
            @cancel="cancelTurnAction"
          />

          <div
            class="message-input-surface"
            :inert="currentToolApprovalVisible"
            :aria-hidden="currentToolApprovalVisible ? 'true' : undefined"
          >
            <p v-if="threadArchived" class="archived-thread-notice" role="status">
              该对话已归档，只能查看历史记录，不能继续发送消息。
            </p>
            <AgentInputArea
              ref="agentInputAreaRef"
              v-model="userInput"
              :is-loading="shouldShowStopButton"
              :disabled="creatingDraft || !agentExecutionAvailable || currentToolApprovalVisible"
              :send-button-disabled="isSendButtonDisabled"
              :mention="mentionConfig"
              :thread-id="currentChatId"
              :show-extra="showStartScreen"
              :attachments="currentPendingThreadAttachments"
              @send="handleSendOrStop"
              @upload-attachment="handleAttachmentUpload"
              @remove-attachment="handleAttachmentRemove"
            >
              <template #extra>
                <ProjectSelectionSection
                  upward
                  v-if="showStartScreen"
                  v-model="selectedProjectId"
                  :disabled="threadCreationInFlight"
                />
              </template>
              <template #actions-left-extra>
                <slot
                  name="input-actions-left"
                  :agent-id="currentAgentId"
                  :has-active-thread="!!currentChatId"
                  :is-creating-thread="threadCreationInFlight"
                ></slot>
                <ToolApprovalModeSelector
                  upward
                  :model-value="currentToolApprovalMode"
                  :saving="currentThreadState?.approvalModeSaving"
                  @update:model-value="handleToolApprovalModeSelect"
                />
              </template>
              <template #actions-right-extra>
                <button
                  v-if="canSubmitSteer"
                  type="button"
                  class="direct-steer-button"
                  title="当前步骤结束后优先执行这条消息"
                  @click="handleDirectSteer"
                >
                  <CornerDownRight :size="14" aria-hidden="true" />
                  引导
                </button>
                <slot name="context-usage" />
                <div class="input-model-selector">
                  <ModelSelectorComponent
                    upward
                    :model_spec="currentModelSpec"
                    size="nano"
                    display-name="mini"
                    placeholder="选择模型"
                    @select-model="handleModelSelect"
                  />
                </div>
                <slot name="input-actions-right" :has-active-thread="!!currentChatId"></slot>
              </template>
            </AgentInputArea>
          </div>
        </div>

        <AttachmentTmpUploadModal
          v-model:open="attachmentUploadModalOpen"
          :initial-files="attachmentInitialFiles"
          :initial-files-key="attachmentInitialFilesKey"
          @added="handleTmpAttachmentsAdded"
        />

        <div class="bottom-actions" v-if="!showStartScreen">
          <p class="note">当前智能体：{{ currentThreadAgentName }}；请注意辨别内容的可靠性</p>
        </div>
      </div>
    </div>
  </ThreadTimeline>
</template>

<script setup>
import { computed, nextTick, onDeactivated, onUnmounted, provide, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { ArrowUpFromLine, CornerDownRight, Play, Trash2, X } from '@lucide/vue'
import { storeToRefs } from 'pinia'
import { agentApi, threadApi } from '@/apis'
import { mcpApi } from '@/apis/mcp_api'
import { AgentValidator, normalizeAgentConfigurableItems, useAgentStore } from '@/modules/agents'
import ModelSelectorComponent from '@/modules/agents/ui/ModelSelectorComponent.vue'
import { useUserStore } from '@/modules/identity'
import { AUTO_PROJECT_ID } from '@/modules/projects'
import ProjectSelectionSection from '@/modules/projects/ui/ProjectSelectionSection.vue'
import { useConfigStore } from '@/modules/settings'
import { handleChatError, handleValidationError } from '@/shared/lib/errorHandler'
import { createSingleFlight } from '@/shared/lib/singleFlight'
import { useThreadMessageProjection } from '../model/threadMessageProjection'
import { useSessionAgentAvailability } from '../model/sessionAgentAvailability'
import {
  DRAFT_THREAD_ID,
  threadDraftStore,
  createThreadDraftSession,
  entryDraftHandoff
} from '../model/thread_draft'
import { useChatThreadsStore } from '../model/chatThreads'
import { IDLE_QUEUE_SNAPSHOT } from '../model/useAgentThreadState'
import { createSessionInput, createInputRequestId } from '../model/sessionInput'
import { useSessionRuntimeStore } from '../model/sessionRuntime'
import { useThreadTurnAction } from '../model/useThreadTurnAction'
import { useAgentMentionConfig } from '../model/useAgentMentionConfig'
import { useSessionPluginMentions } from '../model/useSessionPluginMentions'
import { getToolCallId, parseToolCallResult } from '../model/toolCallProjection'
import { collectCooperationTasks } from '../model/cooperationToolView'
import { createThreadForContext } from '../model/threadCreation'
import {
  isRunInterruptedConflict,
  isThreadWaitingForUserAction,
  isToolApprovalMode,
  readToolApprovalModePreference,
  resolveToolApprovalMode,
  writeToolApprovalModePreference
} from '../model/toolApproval'
import AgentInputArea from './AgentInputArea.vue'
import AttachmentTmpUploadModal from './AttachmentTmpUploadModal.vue'
import HumanApprovalModal from './HumanApprovalModal.vue'
import ToolApprovalModeSelector from './ToolApprovalModeSelector.vue'
import ThreadTimeline from './ThreadTimeline.vue'

const props = defineProps({
  initialThreadId: { type: String, default: '' },
  active: { type: Boolean, default: true },
  pageVisible: { type: Boolean, default: true },
  cooperationMembers: { type: Array, default: () => [] },
  agentId: { type: String, default: '' },
  initialProjectId: { type: String, default: '' },
  isNewSession: { type: Boolean, default: false },
  sendDisabled: { type: Boolean, default: false }
})
const emit = defineEmits([
  'thread-change',
  'open-session',
  'open-refs',
  'open-preview',
  'open-tree',
  'terminal',
  'artifact-saved'
])
const agentStore = useAgentStore()
const chatThreadsStore = useChatThreadsStore()
const configStore = useConfigStore()
const userStore = useUserStore()
const { agents, selectedAgentId, agentDetails, availableKnowledgeBases } = storeToRefs(agentStore)
const { threadCreationInFlight } = storeToRefs(chatThreadsStore)
const currentChatId = ref(props.initialThreadId || null)
const creatingDraft = computed(() => props.isNewSession && threadCreationInFlight.value)
const showStartScreen = computed(
  () => props.isNewSession && !currentChatId.value && !creatingDraft.value
)
const workspaceActive = computed(() => props.active && userStore.isLoggedIn)
const isLoadingMessages = computed(() => Boolean(currentThreadState.value?.readingLoading))
const sessionRuntime = useSessionRuntimeStore()
const {
  getThreadState,
  peekThreadState,
  applyThreadDetail,
  applyThreadViewed,
  fetchAgentState,
  restoreThreadWaitpoint,
  requestTurnCancellation,
  resumeQueuedInputs,
  promoteInput,
  cancelInput,
  continueQueue
} = sessionRuntime
const { threadMessages, pendingSends } = storeToRefs(sessionRuntime)
const currentThreadState = computed(() => peekThreadState(currentChatId.value))
const currentThread = computed(() => currentThreadState.value?.detail || null)

const threadDraftSession = createThreadDraftSession(threadDraftStore, currentChatId.value)
const userInput = ref(threadDraftStore.read(currentChatId.value || DRAFT_THREAD_ID))
watch(userInput, (text) => threadDraftSession.saveInput(text))
const agentInputAreaRef = ref(null)

/** 组件动作追加到当前草稿，保留用户已输入内容并等待显式发送。 */
function composeWidgetDraft(text) {
  userInput.value = [userInput.value, text].filter(Boolean).join('\n\n')
  nextTick(() => agentInputAreaRef.value?.focus())
}

/** 表单和按钮独立发送用户回答，旧列表动作继续填写草稿。 */
function handleWidgetAction(action) {
  if (typeof action === 'string') return composeWidgetDraft(action)
  if (action?.type !== 'send_message' || widgetActionsDisabled.value) return false
  return handleSendMessage({
    text: action.text,
    fromWidget: true,
    onWidgetAccepted: action.onAccepted
  })
}

watch(
  [workspaceActive, () => props.isNewSession, currentChatId],
  ([active, isNewSession, threadId]) => {
    if (!active || !isNewSession || threadId) return
    const text = entryDraftHandoff.take()
    if (text) composeWidgetDraft(text)
  },
  { immediate: true, flush: 'post' }
)

const messageInputStageRef = ref(null)
const pendingActionFocusTurnId = ref(null)
const sendCooldownActive = ref(false)
const cancellingInputIds = reactive(new Set())
const promotingInputIds = reactive(new Set())
let sendCooldownTimer = null
// 预设的打招呼文本
const greetingMessages = [
  '语析，Natural Language Processing',
  '语析，与知识对话',
  '答案藏在知识里，我来找',
  '与知识对话，与答案相遇',
  '你负责提问，我负责寻找'
]

// 随机选择一个打招呼文本
const randomGreeting = greetingMessages[Math.floor(Math.random() * greetingMessages.length)]

const threadAttachmentsMap = ref({})
const draftFilesByThread = ref({})
const attachmentUploadModalOpen = ref(false)
const attachmentUploadThreadKey = ref(null)
const attachmentInitialFiles = ref([])
const attachmentInitialFilesKey = ref(0)
const selectedProjectId = ref(AUTO_PROJECT_ID)
const threadCreationRequest = ref(null)
const threadConfigNoticeMap = ref({})
const threadPendingConfigNoticeMap = ref({})
const threadConfigSnapshotMap = ref({})
const configNoticeSyncDepth = ref(0)
const configNoticeScrollVersion = ref(0)

const currentAgentId = computed(() => {
  if (currentChatId.value) return currentThread.value?.agent.id
  return props.agentId || selectedAgentId.value
})

const { status: sessionAgentStatus, refresh: refreshSessionAgent } = useSessionAgentAvailability(
  currentChatId,
  (agentId) => agentStore.fetchAgentDetail(agentId, true, { notify: false })
)
/** 归档会话只读：后端会拒绝新输入，前端同步禁用而不是让发送失败。 */
const threadArchived = computed(() => currentThread.value?.yuxi?.archived === true)

const agentExecutionAvailable = computed(
  () =>
    !threadArchived.value &&
    (currentChatId.value
      ? sessionAgentStatus.value === 'ready'
      : Boolean(props.isNewSession && currentAgent.value?.can_run !== false && currentAgent.value))
)
const isSessionLoading = computed(
  () => creatingDraft.value || (!currentThreadState.value?.historyLoaded && isLoadingMessages.value)
)

const currentAgentName = computed(() => {
  const agent = currentAgent.value
  return agent ? agent.name : '智能体'
})

const currentAgent = computed(() => {
  if (!currentAgentId.value) return null
  return (
    agentDetails.value[currentAgentId.value] ||
    agents.value?.find((agent) => agent.agent_id === currentAgentId.value) ||
    null
  )
})
const agentConfig = computed(() => {
  if (!currentThread.value) return agentStore.agentConfig
  const config = currentAgent.value?.config_json || {}
  return config.context || config
})
const configurableItems = computed(() =>
  currentThread.value
    ? normalizeAgentConfigurableItems(currentAgent.value?.configurable_items)
    : agentStore.configurableItems
)
watch(
  [currentChatId, () => props.initialProjectId],
  ([threadId, initialProjectId]) => {
    if (!threadId) selectedProjectId.value = initialProjectId || AUTO_PROJECT_ID
  },
  { immediate: true }
)

// ==================== 对话级模型覆盖 ====================
// 当前选择优先；否则依次使用 Session、智能体和系统默认模型。
const DRAFT_MODEL_KEY = '__draft__'
const selectedModelByThread = reactive({})
const savedToolApprovalMode = ref(readToolApprovalModePreference())
const agentDefaultModel = computed(
  () =>
    agentConfig.value?.model ||
    currentAgent.value?.config_json?.context?.model ||
    configStore.config?.default_model ||
    ''
)
const currentModelSpec = computed(
  () =>
    selectedModelByThread[currentChatId.value || DRAFT_MODEL_KEY] ||
    currentThread.value?.agent.model ||
    agentDefaultModel.value
)
const handleModelSelect = (spec) => {
  if (typeof spec === 'string') {
    if (spec) {
      selectedModelByThread[currentChatId.value || DRAFT_MODEL_KEY] = spec
    } else {
      delete selectedModelByThread[currentChatId.value || DRAFT_MODEL_KEY]
    }
  }
}

const currentToolApprovalMode = computed(() =>
  resolveToolApprovalMode({
    hasThread: Boolean(currentChatId.value),
    threadMode: currentThread.value?.yuxi.tool_approval_mode,
    savedMode: savedToolApprovalMode.value
  })
)
const handleToolApprovalModeSelect = async (mode) => {
  if (!isToolApprovalMode(mode)) return

  const thread = currentThread.value
  if (!thread) {
    savedToolApprovalMode.value = mode
    writeToolApprovalModePreference(mode)
    return
  }

  const state = peekThreadState(thread.id)
  if (!state || state.approvalModeSaving || currentToolApprovalMode.value === mode) return
  state.approvalModeSaving = true
  try {
    const detail = await chatThreadsStore.updateThread(thread.id, null, undefined, mode)
    if (applyThreadDetail(thread.id, detail, state)) {
      savedToolApprovalMode.value = mode
      writeToolApprovalModePreference(mode)
    }
  } catch {
    message.error('审批模式保存失败')
  } finally {
    state.approvalModeSaving = false
  }
}

const currentThreadAgentName = computed(() => {
  const threadAgentId = currentThread.value?.agent.id
  if (threadAgentId && agents.value?.length) {
    const threadAgent = agents.value.find((agent) => agent.agent_id === threadAgentId)
    if (threadAgent?.name) {
      return threadAgent.name
    }
  }
  return currentAgentName.value
})
const isContextCompressionPending = computed(() =>
  Boolean(currentThreadState.value?.contextCompressing)
)
const currentThreadAttachments = computed(() => {
  if (!currentChatId.value) return []
  return threadAttachmentsMap.value[currentChatId.value] || []
})
const currentPendingThreadAttachments = computed(
  () => draftFilesByThread.value[currentChatId.value || DRAFT_MODEL_KEY] || []
)
const mentionMcpServers = ref([])
watch(
  workspaceActive,
  async (active) => {
    if (!active) return
    try {
      const result = await mcpApi.getMcpServers({ directOnly: true })
      mentionMcpServers.value = result.data || []
    } catch {
      mentionMcpServers.value = []
      message.error('连接器列表加载失败，请重新打开对话重试')
    }
  },
  { immediate: true }
)

const sessionMentionPlugins = useSessionPluginMentions(currentChatId, workspaceActive)
const { mentionConfig } = useAgentMentionConfig({
  agents,
  currentThreadAttachments,
  configurableItems,
  agentConfig,
  sessionPlugins: sessionMentionPlugins,
  mcpServers: mentionMcpServers
})

const currentThreadMessages = computed(() => threadMessages.value[currentChatId.value] || [])
const { runGroups, ongoingRunMessages } = useThreadMessageProjection(currentChatId)
const currentThreadHasHistory = computed(() => currentThreadMessages.value.length > 0)
const currentThreadConfigNotice = computed(() => {
  if (!currentChatId.value) return null
  return threadConfigNoticeMap.value[currentChatId.value] || null
})

const {
  action: currentTurnAction,
  visible: currentApprovalModalVisible,
  isToolApproval: currentToolApprovalVisible,
  processing: approvalSubmitting,
  error: approvalError,
  submit: submitTurnAction,
  cancel: cancelTurnAction
} = useThreadTurnAction({
  threadId: currentChatId,
  enabled: computed(() => userStore.isLoggedIn && !threadArchived.value),
  runtime: sessionRuntime
})
const shouldSuppressRefsForApproval = () => Boolean(currentThreadState.value?.pendingInterrupt)

const cooperationToolView = computed(() => {
  const sessions = [
    ...currentThreadMessages.value.flatMap((message) =>
      (message.tool_calls || [])
        .filter((call) => getToolCallId(call) === 'list_sessions')
        .flatMap((call) => parseToolCallResult(call)?.sessions || [])
    ),
    ...props.cooperationMembers
  ]
  return {
    sessions,
    treeRootSessionId:
      sessions.find((member) => member.session_id === currentChatId.value)?.tree_root_session_id ||
      currentChatId.value,
    tasks: collectCooperationTasks(currentThreadMessages.value),
    openSession: (target) => emit('open-session', target),
    openTree: () => emit('open-tree')
  }
})

const isStreaming = computed(() => {
  const threadState = currentThreadState.value
  return threadState ? threadState.isStreaming : false
})
const currentQueuedInputs = computed(() => currentThreadState.value?.queuedInputs || [])
const currentQueueSnapshot = computed(
  () => currentThreadState.value?.queueSnapshot || IDLE_QUEUE_SNAPSHOT
)
// 当前首条输入已有消息气泡，等待消费时只在回复位置提供取消操作。
const primaryQueuedInput = computed(() => {
  const state = currentThreadState.value
  if (currentQueueSnapshot.value.status === 'paused') return null
  return (
    currentQueuedInputs.value.find(
      (input) =>
        input.input_id === state?.pendingInputId &&
        state?.optimisticInputs?.[input.input_id]?.length &&
        input.attachment_status !== 'preparing'
    ) || null
  )
})
const visibleQueuedInputs = computed(() =>
  currentQueuedInputs.value.filter((input) => input !== primaryQueuedInput.value)
)
const queuedInputCount = computed(() => currentQueuedInputs.value.length)
const hasQueuedInputs = computed(() => queuedInputCount.value > 0)
const isWaitingForUserAction = computed(() =>
  isThreadWaitingForUserAction(currentThreadState.value)
)
const queuePausedMessage = '后续队列已暂停。发送新消息将优先处理并继续队列。'
const hasDraftContent = computed(() =>
  Boolean(String(userInput.value || '').trim() || agentInputAreaRef.value?.hasImages)
)
const shouldShowStopButton = computed(
  () =>
    (isStreaming.value || currentThreadState.value?.cooperationWaiting) && !hasDraftContent.value
)
const inputActionBlocked = computed(
  () =>
    !agentExecutionAvailable.value ||
    props.sendDisabled ||
    sendCooldownActive.value ||
    isLoadingMessages.value ||
    isWaitingForUserAction.value ||
    !!pendingSends.value[currentChatId.value] ||
    Boolean(currentThreadState.value?.cancellationPending)
)
const inputSubmissionBlocked = computed(
  () => inputActionBlocked.value || Boolean(currentThreadState.value?.approvalModeSaving)
)
const canSubmitSteer = computed(
  () =>
    !inputSubmissionBlocked.value &&
    isStreaming.value &&
    Boolean(currentThreadState.value?.activeRunId) &&
    !currentThreadState.value?.cooperationWaiting &&
    currentThreadState.value?.activeRunSteerable === true &&
    hasDraftContent.value
)
const canCancelQueuedInput = (input) => input?.status !== 'sending'
const canPromoteQueuedInput = (input) =>
  agentExecutionAvailable.value &&
  input?.status === 'pending' &&
  input.kind === 'follow_up' &&
  input.attachment_status !== 'preparing' &&
  !isWaitingForUserAction.value &&
  !currentThreadState.value?.cooperationWaiting &&
  (!currentThreadState.value?.activeRunId || currentThreadState.value?.activeRunSteerable === true)
const isProcessing = computed(
  () =>
    isStreaming.value ||
    currentThreadState.value?.cooperationWaiting ||
    currentThreadState.value?.cancellationPending ||
    (hasQueuedInputs.value && currentQueueSnapshot.value.status !== 'paused')
)
const isReplyLoading = computed(() => {
  return (
    currentThreadState.value?.cancellationPending ||
    (isProcessing.value && !isWaitingForUserAction.value)
  )
})
const replyLoadingText = computed(() => {
  const threadState = currentThreadState.value
  if (threadState?.cancellationPending) return '正在停止，等待任务清理完成...'
  if (threadState?.cooperationWaiting) return '等待协作任务完成...'
  if (threadState?.contextCompressing) return '正在压缩上下文...'
  if (!isStreaming.value && hasQueuedInputs.value && !primaryQueuedInput.value)
    return `排队中（${queuedInputCount.value} 条）...`
  return '正在生成回复...'
})
const isSendButtonDisabled = computed(
  () => shouldShowStopButton.value
    ? inputActionBlocked.value
    : inputSubmissionBlocked.value || (!hasDraftContent.value && !isProcessing.value)
)

const startSendCooldown = () => {
  sendCooldownActive.value = true
  if (sendCooldownTimer) {
    clearTimeout(sendCooldownTimer)
  }
  sendCooldownTimer = setTimeout(() => {
    sendCooldownActive.value = false
    sendCooldownTimer = null
  }, 2000)
}

const CONFIG_CHANGE_NOTICE_MESSAGE =
  '本会话沿用创建时的智能体配置；之后的修改只对新会话生效，建议新建对话以使用最新配置。'

const withConfigNoticeSync = async (task) => {
  configNoticeSyncDepth.value += 1
  try {
    return await task()
  } finally {
    configNoticeSyncDepth.value = Math.max(0, configNoticeSyncDepth.value - 1)
  }
}

const buildThreadConfigSnapshot = () => {
  return {
    agentId: currentAgentId.value || '',
    configJson: JSON.stringify(agentConfig.value || {})
  }
}

const syncThreadConfigSnapshot = (threadId, options = {}) => {
  if (!threadId) return

  const { overwrite = true } = options
  if (!overwrite && threadConfigSnapshotMap.value[threadId]) return
  if (threadPendingConfigNoticeMap.value[threadId]) return

  // 线程切换时先记录当前 UI 的配置快照，避免同步 thread 绑定配置时误报。
  threadConfigSnapshotMap.value = {
    ...threadConfigSnapshotMap.value,
    [threadId]: buildThreadConfigSnapshot()
  }
}

const upsertThreadConfigNotice = (threadId, insertAfterGroupCount) => {
  if (!threadId) return

  const existingNotice = threadConfigNoticeMap.value[threadId]
  const nextNotice = {
    id: existingNotice?.id || `config-change-notice-${threadId}`,
    message: existingNotice?.message || CONFIG_CHANGE_NOTICE_MESSAGE,
    insertAfterGroupCount
  }
  const shouldScroll =
    !existingNotice || existingNotice.insertAfterGroupCount !== insertAfterGroupCount

  threadConfigNoticeMap.value = {
    ...threadConfigNoticeMap.value,
    [threadId]: nextNotice
  }

  if (threadPendingConfigNoticeMap.value[threadId]) {
    const nextPendingNotices = { ...threadPendingConfigNoticeMap.value }
    delete nextPendingNotices[threadId]
    threadPendingConfigNoticeMap.value = nextPendingNotices
  }

  if (shouldScroll) {
    configNoticeScrollVersion.value += 1
  }
}

const queuePendingThreadConfigNotice = (threadId) => {
  if (!threadId) return
  threadPendingConfigNoticeMap.value = {
    ...threadPendingConfigNoticeMap.value,
    [threadId]: {
      id: `config-change-notice-${threadId}`,
      message: CONFIG_CHANGE_NOTICE_MESSAGE
    }
  }
}

const flushPendingThreadConfigNotice = (threadId) => {
  if (
    !threadId ||
    !currentThreadHasHistory.value ||
    !threadPendingConfigNoticeMap.value[threadId]
  ) {
    return
  }

  upsertThreadConfigNotice(threadId, runGroups.value.length)
}

const maybeInsertThreadConfigNotice = () => {
  const threadId = currentChatId.value
  if (!threadId || configNoticeSyncDepth.value > 0) {
    return
  }

  const previousSnapshot = threadConfigSnapshotMap.value[threadId]
  const currentSnapshot = buildThreadConfigSnapshot()

  if (!previousSnapshot) {
    threadConfigSnapshotMap.value = {
      ...threadConfigSnapshotMap.value,
      [threadId]: currentSnapshot
    }
    return
  }

  if (
    previousSnapshot.agentId === currentSnapshot.agentId &&
    previousSnapshot.configJson === currentSnapshot.configJson
  ) {
    return
  }

  if (currentThreadHasHistory.value) {
    upsertThreadConfigNotice(threadId, runGroups.value.length)
  } else if (isLoadingMessages.value) {
    // 历史线程仍在加载时先挂起提示，避免消息返回后把变更误当成新的基线。
    queuePendingThreadConfigNotice(threadId)
  } else {
    return
  }

  threadConfigSnapshotMap.value = {
    ...threadConfigSnapshotMap.value,
    [threadId]: currentSnapshot
  }
}

// 布局只读取视口几何；阅读锚点、滚动与分页由 ThreadTimeline 拥有。
const timelineRef = ref(null)
const messageInputDockRef = ref(null)

onDeactivated(() => {
  if (props.isNewSession && currentChatId.value) {
    // 正式路由接管线程草稿，释放仍保活的新建页草稿实例。
    userInput.value = threadDraftSession.switchThread('', userInput.value)
  }
})
onUnmounted(() => clearTimeout(sendCooldownTimer))

const promoteDraftSelection = (selectionByThread, threadId) => {
  const draft = selectionByThread[DRAFT_MODEL_KEY]
  if (!draft) return
  if (!selectionByThread[threadId]) selectionByThread[threadId] = draft
  delete selectionByThread[DRAFT_MODEL_KEY]
}

const fetchThreadAttachments = async (threadId) => {
  const state = peekThreadState(threadId)
  if (!state) return
  try {
    const response = await threadApi.getThreadAttachments(threadId)
    if (!sessionRuntime.isCurrentThreadState(threadId, state)) return
    threadAttachmentsMap.value[threadId] = Array.isArray(response?.attachments)
      ? response.attachments
      : []
  } catch (error) {
    console.warn('Failed to fetch thread attachments:', error)
  }
}

const handleArtifactSaved = async () => {
  const threadId = currentChatId.value
  if (!threadId) return
  await fetchThreadAttachments(threadId)
  if (currentChatId.value === threadId && workspaceActive.value) emit('artifact-saved', threadId)
}

const createActiveThread = async () => {
  if (currentChatId.value) return currentChatId.value
  if (!props.isNewSession) return null
  const selectedAgent = currentAgentId.value
  const selectedProject = selectedProjectId.value
  const startingThreadId = currentChatId.value
  const creationContext = {
    agentId: selectedAgent,
    projectId: selectedProject,
    threadId: startingThreadId
  }
  let request = threadCreationRequest.value
  if (!request || request.agentId !== selectedAgent || request.projectId !== selectedProject) {
    request = {
      ...creationContext,
      id: createInputRequestId(),
      metadata: { tool_approval_mode: currentToolApprovalMode.value }
    }
    threadCreationRequest.value = request
  }
  const finishCreation = chatThreadsStore.beginThreadCreation()
  try {
    const projectId = selectedProject === AUTO_PROJECT_ID ? '' : selectedProject
    const { thread, accepted } = await createThreadForContext({
      context: creationContext,
      getCurrentContext: () => ({
        agentId: currentAgentId.value,
        projectId: selectedProjectId.value,
        threadId: currentChatId.value
      }),
      requestId: request.id,
      create: (stableRequestId) =>
        chatThreadsStore.createThread(selectedAgent, null, request.metadata, {
          requestId: stableRequestId,
          projectId: projectId || undefined
        })
    })
    if (!thread) throw new Error('创建对话失败')
    if (!accepted) throw new Error('新对话上下文已变化，请重新发送或添加附件')

    getThreadState(thread.id).historyLoaded = true
    applyThreadDetail(thread.id, thread)
    threadAttachmentsMap.value[thread.id] = []
    threadCreationRequest.value = null
    promoteDraftSelection(draftFilesByThread.value, thread.id)
    if (attachmentUploadThreadKey.value === DRAFT_MODEL_KEY) {
      attachmentUploadThreadKey.value = thread.id
    }
    currentChatId.value = thread.id
    await refreshSessionAgent(thread.id, selectedAgent)
    return thread.id
  } catch (error) {
    if (error.status >= 400 && error.status < 500 && error.status !== 429)
      threadCreationRequest.value = null
    throw error
  } finally {
    finishCreation()
  }
}

const ensureActiveThread = createSingleFlight(createActiveThread)

const handleAttachmentUpload = async (files = []) => {
  if (
    !AgentValidator.validateAgentIdWithError(
      currentAgentId.value,
      '上传附件',
      handleValidationError
    )
  )
    return

  if (!attachmentUploadModalOpen.value) {
    attachmentUploadThreadKey.value = currentChatId.value || DRAFT_MODEL_KEY
  }
  const droppedFiles = Array.from(files || []).filter((file) => file instanceof File)
  if (droppedFiles.length) {
    attachmentInitialFiles.value = droppedFiles
    attachmentInitialFilesKey.value += 1
  }

  attachmentUploadModalOpen.value = true
}

const handleTmpAttachmentsAdded = (files) => {
  const key = attachmentUploadThreadKey.value
  draftFilesByThread.value[key] = [...(draftFilesByThread.value[key] || []), ...files]
}

const handleAttachmentRemove = async (file) => {
  const key = currentChatId.value || DRAFT_MODEL_KEY
  try {
    await threadApi.deleteDraftFile(file.id)
    draftFilesByThread.value[key] = (draftFilesByThread.value[key] || []).filter(
      (item) => item.id !== file.id
    )
  } catch (error) {
    handleChatError(error, 'delete')
  }
}

provide('sessionAgentConfigurableItems', configurableItems)

/** 已读状态与当前 Thread 对齐，侧栏仅同步已有摘要。 */
async function markViewed(threadId) {
  const state = peekThreadState(threadId)
  if (!workspaceActive.value || !props.pageVisible || !state?.detail) return
  const detail = await chatThreadsStore.markThreadViewed(threadId)
  applyThreadViewed(threadId, detail, state)
}

/** 完成事件更新本 Thread 的已读状态，由根面板决定是否刷新文件。 */
function handleTerminal({ threadId, runId }) {
  if (!runId || threadId !== currentChatId.value) return
  void markViewed(threadId)
  emit('terminal', threadId)
}

const preparingInputs = new WeakSet()
/** 从已授权详情准备输入，配置或附件失败只影响当前编辑区。 */
async function initializeThreadInput({ threadId }) {
  if (threadId !== currentChatId.value) return
  const state = peekThreadState(threadId)
  if (!state?.detail || preparingInputs.has(state)) return
  preparingInputs.add(state)
  try {
    void markViewed(threadId)
    await Promise.all([
      fetchThreadAttachments(threadId),
      refreshSessionAgent(threadId, state.detail.agent.id)
    ])
    if (threadId !== currentChatId.value || !sessionRuntime.isCurrentThreadState(threadId, state))
      return
    if (sessionAgentStatus.value === 'ready')
      await withConfigNoticeSync(async () => syncThreadConfigSnapshot(threadId))
  } finally {
    preparingInputs.delete(state)
  }
}

const handlePromoteQueuedInput = async (inputId) => {
  const threadId = currentChatId.value
  if (!threadId || promotingInputIds.has(inputId)) return
  promotingInputIds.add(inputId)
  try {
    if (await promoteInput(threadId, inputId)) message.success('已转为引导')
  } finally {
    promotingInputIds.delete(inputId)
  }
}

const handleCancelQueuedInput = async (inputId) => {
  const threadId = currentChatId.value
  if (!threadId || !inputId || cancellingInputIds.has(inputId)) return

  cancellingInputIds.add(inputId)
  const cancelled = await cancelInput(threadId, inputId)
  cancellingInputIds.delete(inputId)
  if (cancelled) {
    await resumeQueuedInputs(threadId)
    message.success('已取消排队输入')
  }
}

const handleContinueQueue = async () => {
  if (!agentExecutionAvailable.value) return
  const threadId = currentChatId.value
  if (!threadId || currentThreadState.value?.continueQueueInFlight) return

  if (await continueQueue(threadId)) {
    message.success('队列已继续')
  }
}

watch(workspaceActive, (active) => {
  if (!active) agentInputAreaRef.value?.closeOptions()
  else if (currentThread.value) void markViewed(currentChatId.value)
})

/** 选择已有 Thread；读取失败由时间线显示和重试。 */
async function selectThreadFromRoute(threadId) {
  if (!threadId && threadCreationInFlight.value) return null
  const previous = currentChatId.value
  currentChatId.value = threadId || null
  if (!threadId) {
    if (previous) emit('thread-change', '')
    return true
  }
  await nextTick()
  if (currentChatId.value !== threadId) return null
  try {
    return (await timelineRef.value?.refresh()) ?? null
  } catch (error) {
    return [403, 404, 410].includes(error?.status) ? false : null
  }
}

const sessionInput = createSessionInput({
  runtime: sessionRuntime,
  onPrepared: (threadId, draft) => {
    if (currentChatId.value !== threadId) return
    if (!draft.fromWidget && userInput.value.trim() === draft.text) userInput.value = ''
    void nextTick(() => timelineRef.value?.scrollToBottom(true))
  },
  onAccepted: async (threadId, draft) => {
    draft.onWidgetAccepted?.()
    draftFilesByThread.value[threadId] = (draftFilesByThread.value[threadId] || []).filter(
      (item) => !draft.data.attachment_file_ids.includes(item.id)
    )
    const model = draft.data.model_spec
    if (model && peekThreadState(threadId)?.detail) {
      const state = peekThreadState(threadId)
      void chatThreadsStore
        .updateThread(threadId, null, undefined, undefined, model)
        .then((detail) => {
          if (
            applyThreadDetail(threadId, detail, state) &&
            selectedModelByThread[threadId] === model
          )
            delete selectedModelByThread[threadId]
        })
        .catch(() => {})
    }
    if (draft.createdFromDraft && currentChatId.value === threadId)
      threadDraftSession.clearDraftThread()
    if (props.isNewSession && currentChatId.value === threadId) emit('thread-change', threadId)
    await fetchThreadAttachments(threadId)
  },
  onRejected: async (threadId, draft, error, { accepted, restore }) => {
    if (restore && !draft.fromWidget && currentChatId.value === threadId) {
      userInput.value = [draft.text, userInput.value].filter(Boolean).join('\n')
      agentInputAreaRef.value?.restoreImages?.(draft.images)
    }
    if (isRunInterruptedConflict(error)) {
      try {
        await restoreThreadWaitpoint(threadId)
      } catch {
        message.error('审批状态恢复失败，请刷新页面后重试')
      }
    }
    if (accepted && props.isNewSession) emit('thread-change', threadId)
    handleChatError(error, 'send')
  }
})

const widgetActionsDisabled = computed(
  () =>
    !agentExecutionAvailable.value ||
    props.sendDisabled ||
    isLoadingMessages.value ||
    isProcessing.value ||
    isWaitingForUserAction.value ||
    sendCooldownActive.value ||
    Boolean(currentThreadState.value?.approvalModeSaving) ||
    Boolean(pendingSends.value[currentChatId.value])
)

const handleSendMessage = async ({
  images = [],
  mode = 'follow_up',
  retry = false,
  text: submittedText,
  fromWidget = false,
  onWidgetAccepted
} = {}) => {
  const original = pendingSends.value[currentChatId.value]
  if ((original && !retry) || (retry && !original)) return false
  const text = original?.text ?? submittedText ?? userInput.value.trim()
  if (
    (!text && !images.length && !original?.images.length) ||
    !agentExecutionAvailable.value ||
    sendCooldownActive.value ||
    props.sendDisabled ||
    currentThreadState.value?.approvalModeSaving ||
    isLoadingMessages.value ||
    isWaitingForUserAction.value
  )
    return false
  startSendCooldown()
  let threadId = currentChatId.value
  const createdFromDraft = !threadId
  const draft = {
    text,
    images: images.map((image) => ({ ...image })),
    createdFromDraft,
    fromWidget,
    onWidgetAccepted,
    attachments: fromWidget
      ? []
      : currentPendingThreadAttachments.value.map((file) => ({ ...file })),
    modelSpec: currentModelSpec.value || null,
    toolApprovalMode: currentToolApprovalMode.value,
    mode
  }
  if (!threadId) {
    const consumeDraft = !fromWidget && userInput.value.trim() === text
    if (consumeDraft) userInput.value = ''
    try {
      threadId = await ensureActiveThread()
      if (!threadId) throw new Error('创建对话未返回线程')
    } catch {
      if (consumeDraft) {
        userInput.value = [draft.text, userInput.value].filter(Boolean).join('\n')
        agentInputAreaRef.value?.restoreImages?.(draft.images)
      }
      message.error('创建对话失败，请重试')
      return
    }
    promoteDraftSelection(selectedModelByThread, threadId)
  }
  return sessionInput.send(threadId, draft, { retry })
}

const handleDirectSteer = async () => {
  if (!canSubmitSteer.value) return
  agentInputAreaRef.value?.submit('steer')
}

const handleContextCompression = async () => {
  if (!agentExecutionAvailable.value) return
  const threadId = currentChatId.value
  const threadState = getThreadState(threadId)
  if (
    !threadId ||
    !threadState ||
    isContextCompressionPending.value ||
    isProcessing.value ||
    hasQueuedInputs.value ||
    isWaitingForUserAction.value
  )
    return

  threadState.contextCompressing = true
  try {
    const response = await agentApi.compressThreadContext(threadId)
    await fetchAgentState(threadId)
    if (response?.status === 'completed') message.success('上下文压缩完成')
    else message.info('当前没有足够的历史消息可压缩')
  } catch (error) {
    handleChatError(error, 'compress_context')
  } finally {
    if (sessionRuntime.isCurrentThreadState(threadId, threadState))
      threadState.contextCompressing = false
  }
}

// 发送或中断
const handleSendOrStop = async (payload) => {
  if (sendCooldownActive.value) {
    return
  }

  const threadId = currentChatId.value
  const threadState = getThreadState(threadId)
  const hasNewInput = Boolean(String(userInput.value || '').trim() || payload?.images?.length)
  if (
    threadState?.activeRunId &&
    (threadState.isStreaming || threadState.cooperationWaiting) &&
    !hasNewInput
  ) {
    try {
      if (!(await requestTurnCancellation(threadId))) return
      message.info('已发送取消请求')
    } catch (error) {
      handleChatError(error, 'stop')
    }
    return
  }
  await handleSendMessage(payload)
}

const buildExportPayload = () => {
  const agentId = currentAgentId.value
  let agentDescription = ''
  if (agentId && agents.value && agents.value.length > 0) {
    const agent = agents.value.find((a) => a.agent_id === agentId)
    agentDescription = agent ? agent.description || '' : ''
  }

  const payload = {
    chatTitle: currentThread.value?.yuxi.title || '新对话',
    agentName: currentAgentName.value || currentAgent.value?.name || '智能助手',
    agentDescription: agentDescription || currentAgent.value?.description || '',
    messages: runGroups.value ? JSON.parse(JSON.stringify(runGroups.value)) : [],
    onGoingMessages: ongoingRunMessages.value
      ? JSON.parse(JSON.stringify(ongoingRunMessages.value))
      : []
  }

  return payload
}

defineExpose({
  threadId: currentChatId,
  timelineElement: computed(() => timelineRef.value?.element || null),
  inputDock: messageInputDockRef,
  attachments: currentThreadAttachments,
  compressionDisabled: computed(
    () =>
      !agentExecutionAvailable.value ||
      isProcessing.value ||
      hasQueuedInputs.value ||
      isWaitingForUserAction.value
  ),
  compressContext: handleContextCompression,
  refreshAttachments: fetchThreadAttachments,
  getExportPayload: buildExportPayload,
  selectThreadFromRoute,
  focusPendingAction
})

/** 重新读取目标会话，为当前人工等待登记一次聚焦意图。 */
async function focusPendingAction() {
  await timelineRef.value?.refresh()
  if (workspaceActive.value && currentThreadState.value?.turnStatus === 'requires_action') {
    pendingActionFocusTurnId.value = currentThreadState.value.currentTurnId
  }
}

// 等待点在历史恢复后异步到达；离开视图或轮次变化时撤销导航聚焦。
watch(
  [
    currentApprovalModalVisible,
    workspaceActive,
    pendingActionFocusTurnId,
    () => currentThreadState.value?.currentTurnId,
    () => currentThreadState.value?.turnStatus
  ],
  async ([visible, active, requestedTurnId, turnId, turnStatus]) => {
    if (!requestedTurnId) return
    if (!active || requestedTurnId !== turnId || turnStatus !== 'requires_action') {
      pendingActionFocusTurnId.value = null
      return
    }
    if (!visible) return
    await nextTick()
    if (
      workspaceActive.value &&
      currentApprovalModalVisible.value &&
      pendingActionFocusTurnId.value === currentThreadState.value?.currentTurnId
    ) {
      messageInputStageRef.value?.querySelector('[role="dialog"]')?.focus()
      pendingActionFocusTurnId.value = null
    }
  }
)

watch(
  currentThreadMessages,
  () => {
    if (currentThreadHasHistory.value) {
      flushPendingThreadConfigNotice(currentChatId.value)
      syncThreadConfigSnapshot(currentChatId.value, { overwrite: false })
    }
  },
  { deep: false }
)

watch(currentAgentId, (newAgentId, oldAgentId) => {
  if (oldAgentId === undefined || newAgentId === oldAgentId) return
  maybeInsertThreadConfigNotice()
})

watch(
  () => JSON.stringify(agentConfig.value || {}),
  (newConfigJson, oldConfigJson) => {
    if (oldConfigJson === undefined || newConfigJson === oldConfigJson) return
    maybeInsertThreadConfigNotice()
  }
)

watch(currentChatId, (threadId, oldThreadId) => {
  if (threadId === oldThreadId) return
  const archived = peekThreadState(oldThreadId)?.detail?.yuxi.archived === true
  userInput.value = threadDraftSession.switchThread(threadId, archived ? '' : userInput.value)
  if (!threadId) {
    ensureActiveThread.reset()
    selectedProjectId.value = props.initialProjectId || AUTO_PROJECT_ID
    threadCreationRequest.value = null
  }
})
</script>

<style lang="less" scoped>
.agent-availability-notice {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px;
  background: var(--gray-0);
  color: var(--gray-600);
  font-size: 14px;
}

@import '@/assets/css/animations.less';
@import '@/modules/session/ui/composerStyles.less';

.chat-greeting-input {
  padding: 10px 0;
  text-align: center;
  margin-bottom: 7vh;

  h1 {
    font-size: 1.4rem;
    color: var(--gray-1000);
    margin: 0;
  }
}

.archived-thread-notice {
  margin: 0 0 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--gray-25);
  color: var(--color-text-secondary);
  font-size: 12px;
  text-align: center;
}

.bottom {
  position: sticky;
  bottom: 0;
  width: 100%;
  margin: 0 auto;
  padding: 14px 14px 0 14px;
  z-index: 1000;
  background: linear-gradient(to bottom, transparent, var(--bg-page) 10%);

  .message-input-wrapper {
    width: 100%;
    max-width: 800px;
    margin: 0 auto;

    .message-input-stage {
      position: relative;
      min-width: 0;
      z-index: 1;
    }

    .message-input-stage.has-tool-approval {
      display: grid;

      > .approval-modal,
      > .message-input-surface {
        min-width: 0;
        grid-area: 1 / 1;
      }

      > .approval-modal {
        z-index: 2;
      }

      > .message-input-surface {
        opacity: 0;
        pointer-events: none;
      }
    }

    .message-input-surface {
      min-width: 0;
      transition: opacity 0.18s ease;
    }

    .queued-request-panel {
      .composer-top-attachment();
      max-height: 196px;
      overflow-y: auto;
      padding: 6px 14px 4px;
    }

    .queued-request-notice {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin: 0 6px 4px;
      padding: 0;
      color: var(--color-text-tertiary);
      background: transparent;
      font-size: 13px;
      line-height: 1.5;

      &.is-paused {
        color: var(--color-warning-700);
        background: transparent;
      }
    }

    .queued-request-continue {
      display: inline-flex;
      flex: 0 0 auto;
      align-items: center;
      gap: 4px;
      padding: 4px 0;
      color: var(--color-warning-700);
      background: transparent;
      border: 0;
      cursor: pointer;
      font-size: 12px;

      &:disabled {
        opacity: 0.55;
        cursor: wait;
      }
    }

    .queued-request-list {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    .queued-request-row {
      min-height: 28px;
      display: grid;
      grid-template-columns: 18px minmax(0, 1fr) auto auto;
      gap: 10px;
      align-items: center;
      padding: 0 4px 0 6px;
      color: var(--color-text);
      border-radius: 8px;
      transition: background-color 0.18s ease;

      &:hover {
        background: var(--gray-50);
      }
    }

    .queued-request-icon {
      color: var(--gray-500);
    }

    .queued-request-content {
      min-width: 0;
      overflow: hidden;
      font-size: 13px;
      font-weight: 400;
      line-height: 1.4;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .queued-request-position {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      color: var(--gray-500);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;

      &::before {
        content: '↪';
        color: var(--gray-400);
        font-size: 14px;
      }
    }

    .queued-request-actions {
      display: inline-flex;
      align-items: center;
      justify-content: flex-end;
      min-width: 64px;
      gap: 4px;
    }

    .queued-request-status {
      color: var(--color-text-tertiary);
      font-size: 12px;
      white-space: nowrap;
    }

    .queued-request-steer,
    .direct-steer-button {
      height: 28px;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 0 6px;
      color: var(--gray-500);
      background: transparent;
      border: 0;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12px;
      line-height: 1;
      transition:
        color 0.18s ease,
        background-color 0.18s ease;

      &:hover:not(:disabled) {
        color: var(--gray-700);
        background: var(--gray-100);
      }

      &:focus-visible {
        outline: 2px solid var(--main-color);
        outline-offset: 1px;
      }

      &:disabled {
        opacity: 0.45;
        cursor: wait;
      }
    }

    .queued-request-delete {
      width: 30px;
      height: 30px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0;
      color: var(--gray-500);
      background: transparent;
      border: 0;
      border-radius: 6px;
      cursor: pointer;
      transition:
        color 0.18s ease,
        background-color 0.18s ease;

      &:hover:not(:disabled) {
        color: var(--color-error-700);
        background: var(--color-error-50);
      }

      &:focus-visible {
        outline: 2px solid var(--main-color);
        outline-offset: 1px;
      }

      &:disabled {
        color: var(--gray-300);
        cursor: wait;
      }
    }

    .queued-request-promote {
      width: 30px;
      height: 30px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0;
      color: var(--gray-500);
      background: transparent;
      border: 0;
      border-radius: 6px;
      cursor: pointer;
      transition:
        color 0.18s ease,
        background-color 0.18s ease;

      &:hover:not(:disabled) {
        color: var(--color-success-700);
        background: var(--color-success-50);
      }

      &:focus-visible {
        outline: 2px solid var(--main-color);
        outline-offset: 1px;
      }

      &:disabled {
        color: var(--gray-300);
        cursor: wait;
      }
    }

    .bottom-actions {
      display: flex;
      justify-content: center;
      align-items: center;
      width: 100%;
    }

    .note {
      font-size: small;
      color: var(--gray-300);
      margin: 4px 0;
      user-select: none;
    }
  }

  .input-model-selector {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 0;
    max-width: min(224px, 50cqi);
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

.loading-spark {
  position: relative;
  display: inline-block;
  flex: 0 0 22px;
  width: 22px;
  height: 22px;
  color: var(--main-color);
}

.loading-spark::before,
.loading-spark::after {
  content: '';
  position: absolute;
  background: currentColor;
}

.loading-spark::before {
  inset: 5%;
  clip-path: polygon(50% 0, 63% 36%, 100% 50%, 63% 64%, 50% 100%, 37% 64%, 0 50%, 37% 36%);
  animation: loading-spark-bloom 2.6s ease-in-out infinite;
}

.loading-spark::after {
  top: 0;
  right: 0;
  width: 24%;
  height: 24%;
  clip-path: polygon(50% 0, 65% 35%, 100% 50%, 65% 65%, 50% 100%, 35% 65%, 0 50%, 35% 35%);
  animation: loading-spark-satellite 2.6s ease-in-out infinite;
}

@keyframes loading-spark-bloom {
  0%,
  100% {
    transform: scale(0.65) rotate(-15deg);
    opacity: 0.6;
  }
  50% {
    transform: scale(1) rotate(75deg);
    opacity: 1;
  }
}

@keyframes loading-spark-satellite {
  0%,
  100% {
    transform: scale(0.3);
    opacity: 0.2;
  }
  50% {
    transform: scale(1);
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .loading-spark::before,
  .loading-spark::after {
    animation: none;
  }
}

.generating-status {
  display: flex;
  justify-content: flex-start;
  padding: 1rem 0;
}

.generating-indicator {
  display: flex;
  align-items: center;
  gap: 8px;

  // 轻微呼吸的文本，替代原先的高亮闪动
  .generating-text {
    font-size: 14px;
    font-weight: 500;
    letter-spacing: 0.025em;
    color: var(--gray-600);
    animation: textBreath 1.8s ease-in-out infinite;
  }
}

@keyframes textBreath {
  0%,
  100% {
    opacity: 0.55;
  }
  50% {
    opacity: 1;
  }
}

.generating-cancel {
  border: none;
  padding: 4px;
  background: transparent;
  color: var(--gray-500);

  &:disabled {
    cursor: wait;
  }
}
</style>
