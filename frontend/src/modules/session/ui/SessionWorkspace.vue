<template>
  <div class="chat-container">
    <div
      class="chat"
      ref="workspaceChatRef"
      :class="{
        'has-file-panel': isFilePanelOpen,
        'has-maximized-panel': isFilePanelOpen && isAgentPanelMaximized,
        'is-resizing-file-panel': isResizing
      }"
      :style="{ '--file-panel-width': filePanelWidthStyle }"
    >
      <div
        v-if="!isAgentPanelMaximized"
        class="chat-header"
        :class="{ 'has-active-thread': !!currentChatId }"
      >
        <div class="header__left">
          <slot name="header-left"></slot>
          <div
            v-if="currentThread?.yuxi.title && currentThread.yuxi.title !== '新的对话'"
            class="session-title"
          >
            {{ currentThread.yuxi.title }}
          </div>
        </div>
        <div class="header__right">
          <button
            v-if="messageDebugEnabled"
            type="button"
            class="agent-nav-btn agent-debug-mode-btn"
            title="调试模式已开启：点击打开消息时序调试面板"
            @click.stop="toggleMessageDebugPanel"
          >
            <Bug size="15" class="nav-btn-icon debug-icon" />
            <span class="hide-text">Debug</span>
          </button>
          <button
            v-if="showStateEntry"
            type="button"
            class="agent-nav-btn agent-state-btn state-entry-btn"
            ref="mainStateTriggerRef"
            :class="{ active: statePanelOpen }"
            title="查看状态"
            :aria-expanded="statePanelOpen"
            aria-controls="agent-state-panel"
            @click.stop="toggleStatePanel"
          >
            <Gauge size="16" class="nav-btn-icon" />
            <span class="hide-text">状态</span>
          </button>
          <button
            v-if="showFileEntry && !isFilePanelOpen"
            type="button"
            class="agent-nav-btn agent-state-btn file-entry-btn"
            ref="filePanelTriggerRef"
            title="打开侧边栏"
            aria-label="打开侧边栏"
            :aria-expanded="isFilePanelOpen"
            aria-controls="agent-file-panel"
            @click.stop="toggleAgentPanel"
          >
            <PanelRight size="16" class="nav-btn-icon" />
          </button>
          <slot
            name="header-right"
            :side-active="sideActive"
            :is-file-panel-open="isFilePanelOpen"
            :is-state-panel-open="statePanelOpen"
            :has-active-thread="!!currentChatId"
            :toggle-agent-panel="toggleAgentPanel"
          ></slot>
        </div>
      </div>

      <div
        ref="chatContentContainerRef"
        class="chat-content-container"
        :class="{
          'has-file-panel': isFilePanelOpen
        }"
      >
        <!-- Main Chat Area -->
        <Teleport defer :to="`#${mainChatPanelId}`" :disabled="!isAgentPanelMaximized">
          <SessionThreadView
            ref="mainThread"
            :active="mainThreadVisible"
            :page-visible="pageVisible"
            :is-new-session="isNewSession"
            :initial-project-id="initialProjectId"
            :send-disabled="sendDisabled"
            :agent-id="agentId"
            :cooperation-members="cooperationSessions"
            :class="{
              'has-embedded-state': statePanelOpen && statePanelPlacement.mode === 'embedded'
            }"
            @thread-change="emit('thread-change', $event)"
            @open-session="navigateToSession"
            @open-refs="openTurnSourcesPanel"
            @open-preview="openPanelPreview"
            @open-tree="openCooperationTree"
            @artifact-saved="handleArtifactSaved"
            @terminal="handleTerminal"
          >
            <template #before-input>
              <CooperationAttention
                :key="currentChatId"
                :actions="cooperationPendingActions"
                :error="cooperationError"
                @open="openCooperationAttention"
                @refresh="refreshCooperation"
              />
            </template>
            <template #context-usage>
              <ContextUsageRing
                v-if="showStateEntry"
                :used-tokens="contextUsageSummary.usedTokens"
                :limit-tokens="contextUsageSummary.limitTokens"
                :ratio="contextUsageSummary.ratio"
                @click="toggleStatePanel"
              />
            </template>
            <template #input-actions-left="scope">
              <slot name="input-actions-left" v-bind="scope" />
            </template>
            <template #input-actions-right="scope">
              <slot name="input-actions-right" v-bind="scope" />
            </template>
          </SessionThreadView>
        </Teleport>
      </div>

      <div
        id="agent-state-panel"
        ref="statePanelRef"
        :inert="!statePanelOpen"
        :aria-hidden="!statePanelOpen"
        class="side-panel side-panel--state"
        :class="{
          'is-visible': statePanelOpen,
          'is-embedded': statePanelPlacement.mode === 'embedded'
        }"
        :style="statePanelPlacement.style"
      >
        <div
          class="state-panel"
          :style="{
            maxHeight: statePanelPlacement.style.maxHeight
          }"
        >
          <div class="side-panel__header state-panel-header">
            <span class="state-panel-title">状态</span>
            <button
              type="button"
              class="state-refresh-btn"
              title="刷新状态"
              aria-label="刷新状态"
              :disabled="isRefreshingState"
              @click.stop="handleAgentStateRefresh()"
            >
              <RefreshCw :size="14" :class="{ 'is-spinning': isRefreshingState }" />
            </button>
          </div>

          <div class="state-panel-body" :aria-busy="isRefreshingState">
            <section v-if="currentAgentState?.warnings?.length" class="state-warning" role="status">
              <span class="state-section-title">警告</span>
              <p
                v-for="warning in currentAgentState.warnings"
                :key="`${warning.code}:${warning.source}`"
              >
                {{ warning.message }}
              </p>
            </section>
            <ContextUsagePanel
              :usage="currentTokenUsage"
              :compressing="isContextCompressionPending"
              :compression-disabled="mainThread?.compressionDisabled ?? true"
              @compress="mainThread?.compressContext()"
            />

            <section
              v-if="currentStateFiles.length"
              class="state-section"
              :class="{ 'is-collapsed': !isStateSectionExpanded('files') }"
            >
              <button
                type="button"
                class="state-section-header"
                :aria-expanded="isStateSectionExpanded('files')"
                aria-controls="state-section-files"
                @click="toggleStateSection('files')"
              >
                <span class="state-section-label">
                  <span class="state-section-title">附件/文件</span>
                  <ChevronDown
                    :size="15"
                    class="state-section-chevron"
                    :class="{ 'is-collapsed': !isStateSectionExpanded('files') }"
                  />
                </span>
                <span class="state-section-meta">{{ currentStateFiles.length }}</span>
              </button>
              <div
                class="state-collapse-panel"
                :class="{ 'is-expanded': isStateSectionExpanded('files') }"
              >
                <div class="state-collapse-inner">
                  <div id="state-section-files" class="state-section-content">
                    <div class="state-list">
                      <button
                        v-for="file in currentStateFiles"
                        :key="file.key"
                        type="button"
                        class="state-list-item state-list-item--button state-list-item--file"
                        :title="`打开 ${file.name}`"
                        @click="openPanelPreview(file)"
                      >
                        <FileTypeIcon
                          :name="file.name || file.path"
                          :size="15"
                          class="state-list-item-icon"
                        />
                        <div class="state-list-item-body">
                          <div class="state-list-item-title">{{ file.name }}</div>
                        </div>
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </section>

            <section
              v-if="currentArtifactFiles.length"
              class="state-section"
              :class="{ 'is-collapsed': !isStateSectionExpanded('artifacts') }"
            >
              <button
                type="button"
                class="state-section-header"
                :aria-expanded="isStateSectionExpanded('artifacts')"
                aria-controls="state-section-artifacts"
                @click="toggleStateSection('artifacts')"
              >
                <span class="state-section-label">
                  <span class="state-section-title">产物</span>
                  <ChevronDown
                    :size="15"
                    class="state-section-chevron"
                    :class="{ 'is-collapsed': !isStateSectionExpanded('artifacts') }"
                  />
                </span>
                <span class="state-section-meta">{{ currentArtifactFiles.length }}</span>
              </button>
              <div
                class="state-collapse-panel"
                :class="{ 'is-expanded': isStateSectionExpanded('artifacts') }"
              >
                <div class="state-collapse-inner">
                  <div id="state-section-artifacts" class="state-section-content">
                    <div class="state-list">
                      <button
                        v-for="file in currentArtifactFiles"
                        :key="file.path"
                        type="button"
                        class="state-list-item state-list-item--button state-list-item--artifact"
                        :title="`打开 ${file.name}`"
                        @click="openPanelPreview(file)"
                      >
                        <FileTypeIcon
                          :name="file.name || file.path"
                          :size="15"
                          class="state-list-item-icon"
                        />
                        <div class="state-list-item-body">
                          <div class="state-list-item-title">{{ file.name }}</div>
                        </div>
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </section>

            <CooperationTree
              v-if="cooperationSessions.length > 1 || cooperationError"
              compact
              :sessions="cooperationSessions"
              :current-id="currentChatId"
              :error="cooperationError"
              @open-tree="openCooperationTree"
              @refresh="refreshCooperation"
            />

            <div v-if="!hasVisibleStateSections" class="state-panel-empty" role="status">
              {{ isRefreshingState ? '正在读取状态…' : '暂无状态内容' }}
            </div>
          </div>
        </div>
      </div>

      <div
        id="agent-file-panel"
        class="side-panel side-panel--file"
        :inert="!isFilePanelOpen"
        :aria-hidden="!isFilePanelOpen"
        ref="panelWrapperRef"
        :class="{
          'is-visible': isFilePanelOpen,
          'no-transition': isResizing
        }"
        :style="{
          width: filePanelWidthStyle
        }"
      >
        <AgentPanel
          :thread-id="currentChatId"
          :active-run-id="currentThreadState?.activeRunId || null"
          :run-active="Boolean(currentThreadState?.activeRunId && currentThreadState?.isStreaming)"
          :visible="isFilePanelOpen && cooperationObservationEnabled"
          :active-preview="activeAgentPanelPreview"
          :panel-width-percentage="panelWidthPercentage"
          :preview-cache="agentPanelPreviewCache"
          :maximized="isAgentPanelMaximized"
          :sections="visibleAgentPanelSections"
          :active-section-key="agentPanelActiveSectionKey"
          :filesystem-visible="agentPanelFilesystemVisible"
          :filesystem-polling-active="agentPanelFilesystemPollingActive"
          :filesystem-refresh-versions="filesystemRefreshVersions"
          @close="closeFilePanel(true)"
          @refresh="handleAgentStateRefresh"
          @resize="handlePanelResize"
          @resize-by="resizePanelBy"
          @resizing="handleResizingChange"
          @open-preview="openPanelPreview"
          @close-preview-path="closePanelPreviewPath"
          @toggle-maximize="toggleAgentPanelMaximized"
          @activate-section="activateAgentPanelSection"
          @close-section="closeAgentPanelSection"
        >
          <template #window-actions>
            <button
              v-if="showStateEntry && isAgentPanelMaximized"
              class="agent-nav-btn agent-state-btn"
              ref="panelStateTriggerRef"
              type="button"
              title="主对话状态"
              aria-label="主对话状态"
              :aria-expanded="statePanelOpen"
              @click="toggleStatePanel"
            >
              <Gauge :size="16" />
            </button>
          </template>
          <template #session-content>
            <CooperationTree
              v-if="agentPanelSections.some((section) => section.key === 'cooperation')"
              v-show="agentPanelActiveSectionKey === 'cooperation'"
              :sessions="cooperationSessions"
              :current-id="currentChatId"
              :error="cooperationError"
              @open="openCooperationSession"
              @refresh="refreshCooperation"
            />
            <div
              :id="mainChatPanelId"
              v-show="isAgentPanelMaximized && agentPanelActiveSectionKey === 'main-session'"
              class="panel-main-chat"
            ></div>
            <div
              v-for="section in agentPanelSections.filter((item) => item.type === 'session')"
              :key="section.key"
              v-show="agentPanelActiveSectionKey === section.key"
              class="panel-session-chat"
            >
              <SessionThreadView
                :ref="(view) => setCooperationWorkspaceRef(section.sessionId, view)"
                :active="
                  workspaceActive && isFilePanelOpen && agentPanelActiveSectionKey === section.key
                "
                :page-visible="pageVisible"
                :cooperation-members="cooperationSessions"
                :initial-thread-id="section.sessionId"
                @open-session="navigateToSession"
                @open-refs="openTurnSourcesPanel($event, section.sessionId)"
                @open-preview="openPanelPreview"
                @open-tree="openCooperationTree"
                @artifact-saved="handleArtifactSaved"
                @terminal="handleTerminal"
              />
            </div>
          </template>
        </AgentPanel>
      </div>
    </div>
  </div>
</template>

<script setup>
import {
  computed,
  getCurrentInstance,
  nextTick,
  onActivated,
  onDeactivated,
  onMounted,
  onUnmounted,
  reactive,
  ref,
  watch
} from 'vue'
import { useRouter } from 'vue-router'
import { Bug, ChevronDown, Gauge, PanelRight, RefreshCw } from '@lucide/vue'
import { agentApi } from '@/apis'
import { useUserStore } from '@/modules/identity'
import { useInfoStore } from '@/modules/settings'
import { handleChatError } from '@/shared/lib/errorHandler'
import { useOutsidePointerdown } from '@/shared/lib/useOutsidePointerdown'
import FileTypeIcon from '@/shared/ui/FileTypeIcon.vue'
import { createSessionLinkNavigator } from '../model/sessionLinks'
import {
  getCooperationPendingActions,
  getSessionAttentionLabel
} from '../model/cooperationAttention'
import { resolveContextUsageSummary } from '../model/contextUsage'
import { useSessionRuntimeStore } from '../model/sessionRuntime'
import { useSessionCooperation } from '../model/useSessionCooperation'
import { normalizeArtifacts } from '../model/artifacts'
import { getStatePanelPlacement } from '../model/statePanelLayout'
import {
  MESSAGE_DEBUG_SECTION,
  TURN_SOURCES_SECTION,
  shouldPollAgentPanelFilesystem
} from '../model/agentPanelSections'
import { useSessionPanel } from '../model/useSessionPanel'
import AgentPanel from './workspace/AgentPanel.vue'
import ContextUsagePanel from './ContextUsagePanel.vue'
import ContextUsageRing from './ContextUsageRing.vue'
import CooperationAttention from './CooperationAttention.vue'
import CooperationTree from './CooperationTree.vue'
import SessionThreadView from './SessionThreadView.vue'

const props = defineProps({
  visible: { type: Boolean, default: true },
  agentId: { type: String, default: '' },
  initialProjectId: { type: String, default: '' },
  isNewSession: { type: Boolean, required: true },
  sendDisabled: { type: Boolean, default: false }
})
const emit = defineEmits(['thread-change'])
const router = useRouter()
const userStore = useUserStore()
const infoStore = useInfoStore()
const sessionRuntime = useSessionRuntimeStore()
const mainThread = ref(null)
const currentChatId = computed(() => mainThread.value?.threadId || null)
const currentThreadState = computed(() => sessionRuntime.peekThreadState(currentChatId.value))
const currentThread = computed(() => currentThreadState.value?.detail || null)
const chatMainRef = computed(() => mainThread.value?.timelineElement || null)
const messageInputDockRef = computed(() => mainThread.value?.inputDock || null)
const messageDebugEnabled = computed(() => infoStore.debugMode && userStore.isSuperAdmin)
const mainChatPanelId = `main-chat-panel-${getCurrentInstance().uid}`
const workspaceActivated = ref(true)
const workspaceActive = computed(
  () => workspaceActivated.value && props.visible && userStore.isLoggedIn
)
const pageVisible = ref(typeof document === 'undefined' || document.visibilityState === 'visible')
const cooperationWorkspaceRefs = new Map()
const isRefreshingState = ref(false)
const filePanelTriggerRef = ref(null)

const collapsedStateSections = reactive({
  files: false,
  artifacts: false
})
const localUIState = reactive({
  chatMainWidth: typeof window !== 'undefined' ? window.innerWidth : 0,
  chatContentWidth: typeof window !== 'undefined' ? window.innerWidth : 0
})

const statePanelOpen = ref(false)
const workspaceChatRef = ref(null)
const mainStateTriggerRef = ref(null)
const panelStateTriggerRef = ref(null)
const statePanelRef = ref(null)
const statePanelPlacement = ref({ mode: 'floating', style: {} })

const {
  isFilePanelOpen,
  isAgentPanelMaximized,
  isResizing,
  chatContentContainerRef,
  panelWrapperRef,
  filePanelWidthStyle,
  agentPanelPreviewCache,
  panelWidthPercentage,
  activeAgentPanelPreview,
  agentPanelSections,
  agentPanelActiveSectionKey,
  showFilePanel,
  showFileTreePanel,
  filesystemRefreshVersions,
  markThreadFilesChanged,
  openPanelPreview,
  closePanelPreviewPath,
  toggleAgentPanelMaximized,
  handlePanelResize,
  handleResizingChange,
  showSection,
  activateAgentPanelSection,
  closeAgentPanelSection,
  hideFilePanel,
  resizePanelBy
} = useSessionPanel({
  threadId: currentChatId,
  statePanelOpen,
  containerWidth: computed(() => localUIState.chatContentWidth)
})
const mainThreadVisible = computed(() =>
  workspaceActive.value && (!isAgentPanelMaximized.value || agentPanelActiveSectionKey.value === 'main-session')
)

useOutsidePointerdown(
  computed({
    get: () =>
      workspaceActive.value &&
      statePanelOpen.value &&
      statePanelPlacement.value.mode === 'floating',
    set: (open) => {
      statePanelOpen.value = open
    }
  }),
  [statePanelRef, mainStateTriggerRef, panelStateTriggerRef]
)

/** 状态面板靠右显示，保留顶部工具栏和输入区的操作空间。 */
const updateStatePanelPlacement = () => {
  if (!statePanelOpen.value || !stateTriggerRef.value || !workspaceChatRef.value) return
  const trigger = stateTriggerRef.value.getBoundingClientRect()
  if (!trigger.width || !trigger.height) return
  const workspace = workspaceChatRef.value.getBoundingClientRect()
  const input = messageInputDockRef.value?.getBoundingClientRect()
  const visibleInput = input?.height && input.top > trigger.bottom ? input : null
  const main = chatMainRef.value?.getBoundingClientRect()
  const embeddedArea =
    !isAgentPanelMaximized.value && main && main.width >= 1000
      ? {
          left: Math.min(main.right, workspace.right) - 348,
          right: Math.min(main.right, workspace.right) - 8
        }
      : null
  statePanelPlacement.value = getStatePanelPlacement(
    workspace,
    trigger,
    visibleInput,
    embeddedArea,
    !isAgentPanelMaximized.value && main ? main : workspace
  )
}

const sideActive = computed(() => {
  if (isFilePanelOpen.value) return 'file'
  if (statePanelOpen.value) return 'state'
  return ''
})
const stateTriggerRef = computed(() =>
  isAgentPanelMaximized.value ? panelStateTriggerRef.value : mainStateTriggerRef.value
)
const currentAgentState = computed(() => currentThreadState.value?.agentState || null)
const currentThreadAttachments = computed(() => mainThread.value?.attachments || [])
const isStreaming = computed(() => Boolean(currentThreadState.value?.isStreaming))
const currentTokenUsage = computed(() => {
  const usage = currentAgentState.value?.token_usage
  return usage && typeof usage === 'object' && !Array.isArray(usage) ? usage : null
})
const contextUsageSummary = computed(() => resolveContextUsageSummary(currentTokenUsage.value))
const isContextCompressionPending = computed(() =>
  Boolean(currentThreadState.value?.contextCompressing)
)
const currentArtifacts = computed(() => {
  const artifacts = currentAgentState.value?.artifacts
  return Array.isArray(artifacts) ? artifacts : []
})
const currentArtifactFiles = computed(() =>
  normalizeArtifacts(currentArtifacts.value).map((path) => ({
    path,
    name: path.split('/').pop() || path
  }))
)
const cooperationObservationEnabled = computed(
  () => workspaceActive.value && pageVisible.value && Boolean(currentThread.value)
)
const {
  sessions: cooperationSessions,
  error: cooperationError,
  refresh: refreshCooperation
} = useSessionCooperation({
  threadId: computed(() => (userStore.isLoggedIn ? currentChatId.value : '')),
  enabled: cooperationObservationEnabled,
  onSnapshot: sessionRuntime.reconcileCooperationSessions
})

const toggleMessageDebugPanel = () => {
  if (isFilePanelOpen.value && agentPanelActiveSectionKey.value === MESSAGE_DEBUG_SECTION.key) {
    closeFilePanel()
    return
  }
  showSection(MESSAGE_DEBUG_SECTION)
}

/** 消息内"来源"改为在右侧栏展开；再次点击同一 Turn 的入口时收起面板。 */
const openTurnSourcesPanel = (payload, threadId = currentChatId.value) => {
  const turnId = payload?.turnId || ''
  const key = `sources:${threadId}`
  if (
    isFilePanelOpen.value &&
    agentPanelActiveSectionKey.value === key &&
    agentPanelSections.value.find((section) => section.key === key)?.turnId === turnId
  ) {
    closeFilePanel()
    return
  }
  const member = cooperationSessions.value.find((session) => session.session_id === threadId)
  showSection({
    ...TURN_SOURCES_SECTION,
    key,
    threadId,
    turnId,
    title: threadId === currentChatId.value
      ? TURN_SOURCES_SECTION.title
      : `${member?.name || '协作成员'}的来源`,
    sources: {
      knowledgeChunks: Array.isArray(payload?.knowledgeChunks) ? payload.knowledgeChunks : [],
      webSources: Array.isArray(payload?.webSources) ? payload.webSources : []
    }
  })
}
const cooperationPendingActions = computed(() =>
  getCooperationPendingActions(cooperationSessions.value, currentChatId.value)
)
const visibleAgentPanelSections = computed(() => {
  const sessions = new Map(
    cooperationSessions.value.map((session) => [session.session_id, session])
  )
  const sections = agentPanelSections.value.map((section) => ({
    ...section,
    attentionLabel:
      section.type === 'session' ? getSessionAttentionLabel(sessions.get(section.sessionId)) : ''
  }))
  return isAgentPanelMaximized.value
    ? [
        {
          key: 'main-session',
          type: 'main-session',
          title: '当前对话',
          avatarSeed: currentChatId.value
        },
        ...sections
      ]
    : sections
})

/** 保存成员视图引用，关闭标签时释放引用。 */
function setCooperationWorkspaceRef(sessionId, workspace) {
  if (workspace) cooperationWorkspaceRefs.set(sessionId, workspace)
  else cooperationWorkspaceRefs.delete(sessionId)
}

/** 待办入口复用标签，并在成员读取当前等待点后定位人工卡片。 */
async function openCooperationAttention(sessionId) {
  openCooperationSession(sessionId)
  await nextTick()
  try {
    await cooperationWorkspaceRefs.get(sessionId)?.focusPendingAction()
  } catch (error) {
    handleChatError(error, 'load')
  } finally {
    void refreshCooperation()
  }
}

/** 在现有侧栏标签中打开协作列表，重复点击复用同一标签。 */
const openCooperationTree = () => {
  showSection({
    key: 'cooperation',
    type: 'cooperation',
    title: '协作会话'
  })
}

const sessionLinkNavigator = createSessionLinkNavigator({
  router,
  resolveTarget: (target) => agentApi.getSessionNavigation(target),
  isActive: () => workspaceActive.value,
  openTarget(destination, { restore }) {
    if (
      restore &&
      (destination.tree_root_session_id !== currentChatId.value || !destination.parent_session_id)
    ) {
      throw new Error('会话不属于当前主页面')
    }
    if (destination.tree_root_session_id === currentChatId.value) {
      if (destination.parent_session_id)
        openCooperationSession(destination.session_id, destination.name)
      else if (isAgentPanelMaximized.value) agentPanelActiveSectionKey.value = 'main-session'
      else hideFilePanel()
    }
  },
  onError: (error) => handleChatError(error, 'load')
})
watch(
  [workspaceActive, currentChatId, () => router.currentRoute.value],
  () => sessionLinkNavigator.invalidate(),
  { flush: 'sync' }
)
onUnmounted(() => sessionLinkNavigator.invalidate())

/** 嵌入会话把导航交给根工作区，避免侧栏递归嵌套。 */
async function navigateToSession(target) {
  await sessionLinkNavigator.navigate(target)
}

/** 路由恢复先校验成员属于当前根页面，再打开侧栏。 */
async function openSessionFromRoute(sessionId) {
  if (!sessionId) {
    sessionLinkNavigator.invalidate()
    if (isAgentPanelMaximized.value) agentPanelActiveSectionKey.value = 'main-session'
    else if (
      agentPanelSections.value.find((section) => section.key === agentPanelActiveSectionKey.value)
        ?.type === 'session'
    ) {
      hideFilePanel()
    }
    return
  }
  await sessionLinkNavigator.navigate(sessionId, { restore: true })
}

const openCooperationSession = (sessionId, title) => {
  if (sessionId === currentChatId.value) {
    if (isAgentPanelMaximized.value) agentPanelActiveSectionKey.value = 'main-session'
    return
  }
  const session = cooperationSessions.value.find((item) => item.session_id === sessionId)
  const key = `session:${sessionId}`
  showSection({
    key,
    type: 'session',
    title: title || session?.name || session?.path?.split('/').pop() || '协作会话',
    sessionId,
    avatarSeed: sessionId
  })
}

watch(messageDebugEnabled, (enabled) => {
  if (enabled) return
  const hadDebugSection = agentPanelSections.value.some(
    (section) => section.key === MESSAGE_DEBUG_SECTION.key
  )
  if (!hadDebugSection) return
  closeAgentPanelSection(MESSAGE_DEBUG_SECTION.key)
})

const isStateSectionExpanded = (key) => !collapsedStateSections[key]
const toggleStateSection = (key) => {
  collapsedStateSections[key] = !collapsedStateSections[key]
}
const currentStateFiles = computed(() => {
  const files = []
  const seenPaths = new Set()
  const pushFile = (entry, fallbackName = '文件') => {
    const path = String(entry?.path || entry?.file_path || entry?.file_name || entry?.name || '')
    if (!path || seenPaths.has(path)) return
    seenPaths.add(path)
    const name = entry?.file_name || entry?.name || path.split('/').pop() || fallbackName
    files.push({
      key: path,
      path,
      name
    })
  }

  currentThreadAttachments.value.forEach((attachment) => pushFile(attachment, '附件'))

  return files
})
const showStateEntry = computed(() => Boolean(currentThread.value))
const showFileEntry = showStateEntry
const hasVisibleStateSections = computed(
  () =>
    Boolean(currentAgentState.value?.warnings?.length) ||
    Boolean(currentTokenUsage.value) ||
    currentStateFiles.value.length > 0 ||
    currentArtifactFiles.value.length > 0 ||
    cooperationSessions.value.length > 1
)

const shouldRefreshStateWhileStreaming = computed(
  () =>
    workspaceActive.value &&
    Boolean(currentChatId.value) &&
    isStreaming.value &&
    statePanelOpen.value
)
const activeAgentPanelSection = computed(() =>
  agentPanelSections.value.find((section) => section.key === agentPanelActiveSectionKey.value)
)
const agentPanelFilesystemVisible = computed(
  () =>
    workspaceActive.value &&
    isFilePanelOpen.value &&
    (activeAgentPanelSection.value?.type === 'file-tree' ||
      (activeAgentPanelPreview.value?.workdir === true &&
        activeAgentPanelPreview.value.threadId === currentChatId.value))
)
const agentPanelFilesystemPollingActive = computed(() =>
  shouldPollAgentPanelFilesystem({
    panelOpen: isFilePanelOpen.value,
    pageVisible: workspaceActive.value && pageVisible.value,
    streaming: isStreaming.value,
    activeSection: activeAgentPanelSection.value,
    activePreview:
      activeAgentPanelPreview.value?.threadId === currentChatId.value
        ? activeAgentPanelPreview.value
        : null
  })
)
let streamingStateRefreshTimer = null
let chatMainResizeObserver = null
// 初始化延迟标志，避免首次挂载时 ResizeObserver 立即触发导致侧边栏意外关闭
let isResizeObserverReady = false
let resizeObserverReadyTimer = null

const armResizeObserver = (onReady) => {
  if (resizeObserverReadyTimer) {
    clearTimeout(resizeObserverReadyTimer)
  }

  isResizeObserverReady = false
  // keep-alive 切页回来时等布局稳定后再恢复宽度判断，避免隐藏态宽度污染侧边栏状态。
  resizeObserverReadyTimer = setTimeout(() => {
    isResizeObserverReady = true
    onReady?.()
  }, 50)
}

const stopChatMainResizeObserver = () => {
  if (resizeObserverReadyTimer) {
    clearTimeout(resizeObserverReadyTimer)
    resizeObserverReadyTimer = null
  }

  isResizeObserverReady = false

  if (chatMainResizeObserver) {
    chatMainResizeObserver.disconnect()
    chatMainResizeObserver = null
  }
}

const stopStreamingStateRefresh = () => {
  if (streamingStateRefreshTimer) {
    clearInterval(streamingStateRefreshTimer)
    streamingStateRefreshTimer = null
  }
}

const startStreamingStateRefresh = () => {
  stopStreamingStateRefresh()
  streamingStateRefreshTimer = setInterval(() => {
    if (!shouldRefreshStateWhileStreaming.value) return
    void handleAgentStateRefresh()
  }, 5000)
}

const startChatMainResizeObserver = () => {
  if (!chatMainRef.value || chatMainResizeObserver) {
    return
  }

  const syncLayoutMetrics = () => {
    localUIState.chatMainWidth = chatMainRef.value?.clientWidth || window.innerWidth
    localUIState.chatContentWidth =
      chatContentContainerRef.value?.clientWidth || localUIState.chatMainWidth

    updateStatePanelPlacement()
  }

  syncLayoutMetrics()
  if (!window.ResizeObserver) return

  chatMainResizeObserver = new ResizeObserver((entries) => {
    // 初始化期间跳过检查，等待 layout 稳定
    if (!isResizeObserverReady) return

    if (!entries.length) return
    syncLayoutMetrics()
  })
  if (panelWrapperRef.value) chatMainResizeObserver.observe(panelWrapperRef.value)
  chatMainResizeObserver.observe(chatMainRef.value)
  if (chatContentContainerRef.value) {
    chatMainResizeObserver.observe(chatContentContainerRef.value)
  }
  if (messageInputDockRef.value) {
    chatMainResizeObserver.observe(messageInputDockRef.value)
  }
  armResizeObserver(() => {
    syncLayoutMetrics()
  })
}

onMounted(() => {
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', handlePageVisibilityChange)
  }

  nextTick(() => {
    startChatMainResizeObserver()
  })
})

onActivated(() => {
  workspaceActivated.value = true
  nextTick(() => {
    startChatMainResizeObserver()
  })
})

onDeactivated(() => {
  workspaceActivated.value = false
  stopChatMainResizeObserver()
  stopStreamingStateRefresh()
})

onUnmounted(() => {
  if (typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', handlePageVisibilityChange)
  }
  stopChatMainResizeObserver()
  stopStreamingStateRefresh()
})

const handlePageVisibilityChange = () => {
  pageVisible.value = typeof document === 'undefined' || document.visibilityState === 'visible'
}

/** 根状态面板仅刷新自己显示的 Thread。 */
async function handleAgentStateRefresh() {
  const threadId = currentChatId.value
  if (!threadId || isRefreshingState.value) return
  isRefreshingState.value = true
  try {
    await Promise.all([
      sessionRuntime.fetchAgentState(threadId),
      mainThread.value?.refreshAttachments(threadId)
    ])
  } finally {
    isRefreshingState.value = false
  }
}

function handleTerminal(threadId) {
  markThreadFilesChanged(threadId)
  if (workspaceActive.value && pageVisible.value && threadId === currentChatId.value) {
    void sessionRuntime.fetchAgentState(threadId)
  }
}

function handleArtifactSaved(threadId) {
  markThreadFilesChanged(threadId)
  if (threadId === currentChatId.value) showFileTreePanel()
}

const toggleStatePanel = async () => {
  const nextOpen = !statePanelOpen.value
  statePanelOpen.value = nextOpen
  if (nextOpen && currentChatId.value && !currentAgentState.value) {
    await handleAgentStateRefresh()
  }
}

function closeFilePanel(restoreFocus = false) {
  sessionLinkNavigator.invalidate()
  hideFilePanel()
  if (restoreFocus) void nextTick(() => filePanelTriggerRef.value?.focus())
}

const toggleAgentPanel = () => {
  const nextOpen = !isFilePanelOpen.value

  if (!nextOpen) {
    closeFilePanel()
    return
  }

  showFilePanel()
}

watch([statePanelOpen, isAgentPanelMaximized], () => {
  void nextTick(updateStatePanelPlacement)
})
watch(
  [
    () => workspaceActive.value && currentThreadState.value?.readingReady ? currentChatId.value : null,
    () => currentThreadState.value?.pendingInterrupt?.waitpointId,
    () => currentThreadState.value?.pendingInterrupt?.waitpointId ? currentThreadState.value.runStateVersion : null
  ],
  ([threadId, waitpointId, runVersion], [previousThreadId, previousWaitpointId, previousRunVersion]) => {
    const waitingStateChanged = waitpointId && (waitpointId !== previousWaitpointId || runVersion !== previousRunVersion)
    if (threadId && (threadId !== previousThreadId || waitingStateChanged)) {
      void sessionRuntime.fetchAgentState(threadId)
    }
  }
)
watch(workspaceActive, (active) => {
  if (active) void nextTick(startChatMainResizeObserver)
  else stopChatMainResizeObserver()
})

defineExpose({
  getExportPayload: () => mainThread.value?.getExportPayload(),
  selectThreadFromRoute: (threadId) => mainThread.value?.selectThreadFromRoute(threadId),
  openSessionFromRoute
})

watch(
  shouldRefreshStateWhileStreaming,
  (shouldRefresh) => {
    if (shouldRefresh) {
      void handleAgentStateRefresh()
      startStreamingStateRefresh()
    } else {
      stopStreamingStateRefresh()
    }
  },
  { immediate: true }
)
</script>

<style lang="less" scoped>
.chat-container {
  display: flex;
  width: 100%;
  height: 100%;
  position: relative;
}

.chat {
  --header-height: 40px;

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
    z-index: 10;
    height: var(--header-height);
    min-height: var(--header-height);
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 8px;
    flex-shrink: 0; /* Prevent header from shrinking */
    transition: padding-right 0.3s cubic-bezier(0.4, 0, 0.2, 1);

    &.has-active-thread {
      border-bottom: 1px solid var(--gray-150);
    }

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

    .session-title {
      font-size: 14px;
      line-height: 20px;
      font-weight: 400;
      color: var(--gray-900);
      max-width: 200px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      margin-left: 8px;
    }
  }

  &.has-file-panel .chat-header {
    padding-right: calc(var(--file-panel-width) + 8px);
  }

  &.has-maximized-panel .chat-header {
    padding-right: 8px;
  }

  &.is-resizing-file-panel {
    .chat-header,
    .chat-main {
      transition: none;
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

.chat-content-container.has-file-panel .chat-main {
  margin-right: var(--file-panel-width);
}

.chat-main.has-embedded-state {
  box-sizing: border-box;
  padding-right: 364px;
}

.chat.has-maximized-panel .chat-main {
  margin-right: 0;
  min-width: 0;
}

.side-panel {
  flex: 0 0 auto;
  overflow: hidden;
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  box-shadow:
    0 16px 40px var(--shadow-1),
    0 2px 10px var(--shadow-0);
  z-index: 20;
  min-width: 0;
  opacity: 0;
  pointer-events: none;
  transform: translateX(10px);
  will-change: width, flex-basis, opacity, transform;
  transition:
    width 0.24s cubic-bezier(0.16, 1, 0.3, 1),
    flex-basis 0.24s cubic-bezier(0.16, 1, 0.3, 1),
    opacity 0.22s ease,
    transform 0.24s cubic-bezier(0.16, 1, 0.3, 1);
}

.side-panel.is-visible {
  opacity: 1;
  pointer-events: auto;
  transform: translateX(0);
}

.side-panel.no-transition {
  transition: none !important;
}

.side-panel--file {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 30;
  display: flex;
  height: auto;
  max-width: 100%;
  border: none;
  border-left: 0 solid var(--gray-150);
  border-radius: 0;
  box-shadow: none;
}

.side-panel--file.is-visible {
  min-width: 0;
  border-left-width: 1px;
}

.side-panel--state {
  position: absolute;
  top: calc(var(--header-height) + 8px);
  right: 8px;
  width: min(340px, calc(100% - 24px));
  min-width: 0;
  height: auto;
  margin: 0;
  z-index: 40;
  overflow: hidden;
  box-shadow:
    0 12px 28px var(--shadow-1),
    0 2px 8px var(--shadow-0);
}

.side-panel--state .state-panel {
  height: auto;
  max-height: calc(100vh - 16px);
}

.side-panel--state.is-embedded {
  box-shadow: none;
}

.state-panel {
  width: 100%;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--gray-0);
  flex-shrink: 0;
  box-sizing: border-box;
}

.agent-segment-wrapper {
  width: fit-content;
  max-width: 100%;
  margin: 0 auto 18px;
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

.agent-switcher-wrapper {
  display: flex;
  justify-content: center;
  margin: 0 auto 18px;
}

.agent-switcher-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
  padding: 4px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--gray-900);
  cursor: pointer;
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease,
    color 0.2s ease;

  &:hover {
    background: var(--gray-0);
    border-color: var(--gray-200);
  }
}

.agent-switcher-icon,
.agent-switcher-chevron {
  flex-shrink: 0;
  color: var(--gray-600);
}

.agent-switcher-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.agent-switcher-menu) {
  min-width: 220px;
}

:deep(.agent-switcher-menu-item) {
  display: flex;
  align-items: center;
  gap: 8px;
}

:deep(.agent-switcher-menu-icon) {
  flex-shrink: 0;
  color: var(--gray-600);
}

:deep(.agent-switcher-menu-text) {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.agent-switcher-menu-badge) {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--main-30);
  color: var(--main-700);
  font-size: 12px;
}

@media (max-width: 1024px) {
  .chat-content-container.has-file-panel .chat-main {
    min-width: 350px;
  }

  .side-panel--file.is-visible {
    max-width: 100%;
  }
}

@media (max-width: 768px) {
  .chat.has-file-panel .chat-header {
    padding-right: 8px;
  }

  .chat-content-container.has-file-panel .chat-main {
    margin-right: 0;
    min-width: 0;
  }

  .side-panel--file {
    top: calc(var(--header-height) + 4px);
  }

  .side-panel--file.is-visible {
    min-width: 0;
    max-width: calc(100% - 16px);
  }

  .side-panel--state.is-visible {
    min-width: 0;
    max-width: calc(100% - 24px);
  }

  .chat.has-maximized-panel .side-panel--file {
    top: 0;
    max-width: 100%;
  }

  .state-panel {
    width: 100%;
    min-width: 0;
  }

  .agent-segment-wrapper {
    margin-bottom: 8px;

    :deep(.ant-segmented-item-label) {
      font-size: 12px;
    }
  }

  .agent-switcher-wrapper {
    margin-bottom: 8px;
  }

  .agent-switcher-btn {
    width: 100%;
    justify-content: center;
  }

  .chat-header {
    .header__left {
      .text {
        display: none;
      }
    }
  }
}

// 智能体选择器的图标对齐
.agent-segment-wrapper {
  :deep(.ant-segmented-item-label) {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  :deep(.agent-option-label) {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  :deep(.agent-option-icon) {
    flex-shrink: 0;
    color: var(--gray-600);
  }
}
</style>

<style lang="less">
.agent-nav-btn {
  display: flex;
  gap: 5px;
  padding: 4px 7px;
  height: 28px;
  justify-content: center;
  align-items: center;
  border-radius: 6px;
  color: var(--gray-900);
  cursor: pointer;
  width: auto;
  font-size: 14px;
  line-height: 20px;
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
    width: 16px;
    height: 16px;
  }

  .loading-icon {
    animation: spin 1s linear infinite;
  }
}

.side-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: var(--header-height);
  padding: 4px 12px;
  background: var(--gray-25);
  border-bottom: 1px solid var(--gray-100);
  flex-shrink: 0;
}

.state-entry-btn.active {
  color: var(--main-700);
  background-color: var(--main-20);
}

.state-panel-header {
  padding: 10px 14px;
  padding-bottom: 0px;
  background: transparent;
  border-bottom: none;
}

.state-refresh-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: 6px;
  color: var(--gray-500);
  background: transparent;
  cursor: pointer;
  opacity: 0;
  pointer-events: none;
  transition:
    opacity 0.16s ease,
    color 0.16s ease,
    background-color 0.16s ease;

  &:hover:not(:disabled) {
    color: var(--main-700);
    background: var(--gray-100);
  }

  &:disabled {
    cursor: not-allowed;
  }

  .is-spinning {
    animation: spin 1s linear infinite;
  }
}

.state-panel:hover .state-refresh-btn,
.state-panel:focus-within .state-refresh-btn,
.state-refresh-btn:focus-visible {
  opacity: 1;
  pointer-events: auto;
}

@media (hover: none) {
  .state-refresh-btn {
    opacity: 1;
    pointer-events: auto;
  }
}

.state-panel-title {
  min-width: 0;
  font-size: 14px;
  font-weight: 400;
  color: var(--gray-500);
}

.state-section-meta {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--gray-500);
}

.state-panel-body {
  flex: 1;
  min-height: 0;
  padding: 8px 14px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: auto;

  // 子项被 flex 压缩时会溢出贴底、吃掉 padding，空间不足应交给滚动
  > * {
    flex-shrink: 0;
  }
}

.state-warning {
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--color-warning-50);
  color: var(--color-warning-900);

  p {
    margin: 4px 0 0;
    font-size: 13px;
    line-height: 1.6;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
}

.state-section {
  display: flex;
  flex-direction: column;

  .state-collapse-panel.is-expanded {
    margin-top: 6px;
  }
}

.state-collapse-panel {
  display: grid;
  grid-template-rows: 0fr;
  transition:
    grid-template-rows 0.24s cubic-bezier(0.16, 1, 0.3, 1),
    visibility 0.24s ease;
  visibility: hidden;
  min-width: 0;

  &.is-expanded {
    grid-template-rows: 1fr;
    visibility: visible;
  }
}

.state-collapse-inner {
  overflow: hidden;
  min-height: 0;
  opacity: 0;
  transform: translateY(-4px);
  transition:
    opacity 0.2s ease,
    transform 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.state-collapse-panel.is-expanded .state-collapse-inner {
  opacity: 1;
  transform: translateY(0);
}

.state-section-header {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;

  &:hover {
    .state-section-title,
    .state-section-chevron {
      color: var(--gray-900);
    }
  }

  &:focus-visible {
    outline: 2px solid var(--main-200);
    outline-offset: 2px;
  }
}

.state-section-label {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.state-section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--gray-800);
}

.state-section-chevron {
  flex-shrink: 0;
  color: var(--gray-400);
  transition:
    transform 0.22s cubic-bezier(0.16, 1, 0.3, 1),
    color 0.18s ease;

  &.is-collapsed {
    transform: rotate(-90deg);
  }
}

.state-section-content {
  min-width: 0;
}

.state-panel-empty {
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--gray-25);
  color: var(--gray-500);
  font-size: 13px;
  text-align: center;
}

@media (prefers-reduced-motion: reduce) {
  .state-section-chevron,
  .state-collapse-panel,
  .state-collapse-inner {
    transition: none;
  }
}

.state-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.state-list-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 9px;
  border: 1px solid var(--gray-100);
  border-radius: 10px;
  background: var(--gray-25);
  color: inherit;
  text-align: left;
}

.state-list-item--button {
  cursor: pointer;
}

.state-list-item--button:hover,
.state-list-item.is-clickable:hover {
  border-color: var(--main-200);
  background: var(--gray-0);
}

.state-list-item.is-clickable {
  cursor: pointer;
}

.state-list-item--file,
.state-list-item--artifact {
  min-height: 32px;
  padding: 5px 8px;
}

.state-list-item-icon {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  font-size: 15px;
}

.state-list-item-body {
  min-width: 0;
  flex: 1;
}

.state-list-item-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--gray-900);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.state-list-item-meta {
  margin-top: 1px;
  font-size: 12px;
  line-height: 1.25;
  color: var(--gray-500);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  color: var(--gray-900);
  background-color: var(--gray-100);
}

.agent-nav-btn.agent-state-btn.active {
  color: var(--gray-900);
  background-color: var(--gray-100);
}

.agent-nav-btn.agent-debug-mode-btn {
  color: var(--gray-800);
  background-color: var(--gray-100);
  border: 1px solid var(--gray-300);

  &:hover {
    background-color: var(--gray-200);
    color: var(--gray-1000);
    border-color: var(--gray-400);
  }

  .debug-icon {
    color: var(--gray-700);
  }
}

.panel-main-chat,
.panel-session-chat {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.panel-session-chat .chat-main {
  margin-right: 0;
  min-height: 0;
}

.panel-session-chat .bottom {
  background: linear-gradient(to bottom, transparent, var(--gray-0) 10%);
}

.chat > .side-panel--state.is-floating {
  position: absolute;
  z-index: 40;
}
</style>
