<template>
  <div
    ref="chatMainRef"
    class="chat-main"
    :class="{ 'is-loading-history': earlierMessagesLoading }"
    @scroll.passive="handleWorkspaceScroll"
  >
    <div v-if="isLoadingMessages" class="session-loading" role="status" aria-label="正在加载对话">
      <a-spin aria-hidden="true" />
    </div>
    <div class="chat-box">
      <button
        v-if="historyPages[threadId]?.hasMore"
        type="button"
        class="history-load-btn"
        :disabled="historyPages[threadId]?.loading || earlierMessagesLoading"
        :aria-busy="historyPages[threadId]?.loading || earlierMessagesLoading"
        @click="loadEarlierMessages"
      >
        {{ earlierMessagesLabel }}
      </button>
      <template v-for="row in messageGroupRows" :key="row.key">
        <div v-if="row.type === 'message-group'" class="group-box">
          <div v-if="row.timeLabel" class="message-group-time">
            {{ row.timeLabel }}
          </div>
          <div
            v-for="(displayItem, itemIndex) in row.displayItems"
            :key="displayItem.key"
            class="history-display-item"
            :data-history-key="displayItem.key"
          >
            <AgentMessageComponent
              v-if="displayItem.type === 'message'"
              :message="displayItem.message"
              :thread-id="threadId"
              :is-processing="isDisplayMessageProcessing(row.group, displayItem)"
              :show-refs="showMsgRefs(displayItem.message, row.group)"
              :hide-tool-calls="true"
              :mention="mentionConfig"
              :widget-action="widgetAction"
              :widgets-disabled="row.group.status === 'streaming' || widgetActionsDisabled"
              @open-refs="emit('open-refs', $event)"
              @open-session="emit('open-session', $event)"
            >
            </AgentMessageComponent>
            <ToolCallsGroupComponent
              v-else-if="displayItem.type === 'tool-group'"
              :tool-calls="displayItem.toolCalls"
              :entries="displayItem.entries"
              :cooperation-view="cooperationView"
              :is-active="isToolGroupActive(row.group, itemIndex, row.displayItems)"
            />
            <RunProcessGroupComponent
              v-else
              :items="displayItem.items"
              :cooperation-view="cooperationView"
              :message-count="displayItem.messageCount"
              :tool-call-count="displayItem.toolCallCount"
              :duration-ms="displayItem.durationMs"
              :widgets-disabled="row.group.status === 'streaming' || widgetActionsDisabled"
              :mention="mentionConfig"
              :widget-action="widgetAction"
            />
          </div>
          <RunFailureNotice
            v-if="row.group.run?.status === 'failed'"
            :error-message="row.group.run.error_message"
          />
          <div
            v-else-if="shouldShowRunStatusNotice(row)"
            class="chat-inline-notice"
            :class="{ 'is-terminal': isUserStoppedRun(row.group.run) }"
          >
            {{ formatEmptyRunStatus(row.group.run.status) }}
          </div>
          <AgentArtifactsCard
            v-if="row.artifacts.length && shouldShowRefs(row.group)"
            :artifacts="row.artifacts"
            :thread-id="threadId"
            @saved="emit('artifact-saved', $event)"
            @open-preview="emit('open-preview', $event)"
          />
          <!-- 显示对话最后一个消息使用的模型 -->
          <RefsComponent
            v-if="shouldShowRefs(row.group)"
            :thread-id="threadId"
            :message="getLastMessage(row.group)"
            :run="getMessageRun(getLastMessage(row.group))"
            :show-refs="['model', 'copy', 'sources']"
            :is-latest-message="false"
            :sources="getMessageGroupSources(row.group)"
            @open-refs="emit('open-refs', $event)"
          />
        </div>
        <div v-else class="chat-inline-notice">
          <span>{{ row.notice.message }}</span>
        </div>
      </template>

      <div v-if="state?.readingError" class="thread-reading-error" role="alert">
        <span>对话读取失败，请重试。</span>
        <button type="button" :disabled="state.readingLoading" @click="retry">
          {{ state.readingLoading ? '正在重试…' : '重新读取对话' }}
        </button>
      </div>
      <slot name="status" />
    </div>
    <slot />
  </div>
</template>

<script setup>
import {
  computed,
  provide,
  ref,
  toRef,
  watch,
  nextTick,
  onMounted,
  onUnmounted,
  onActivated,
  onDeactivated
} from 'vue'
import { storeToRefs } from 'pinia'
import { useUserStore } from '@/modules/identity'
import AgentMessageComponent from './AgentMessageComponent.vue'
import RunFailureNotice from './RunFailureNotice.vue'
import RefsComponent from './RefsComponent.vue'
import ToolCallsGroupComponent from './ToolCallsGroupComponent.vue'
import RunProcessGroupComponent from './RunProcessGroupComponent.vue'
import AgentArtifactsCard from './AgentArtifactsCard.vue'
import { useSessionRuntimeStore } from '../model/sessionRuntime'
import { useThreadMessageProjection } from '../model/threadMessageProjection'
import { ScrollController } from '../model/scrollController'
import { getMessageRunId } from '../model/messageDebug'
import { MessageProcessor } from '../model/messageProcessor'
import {
  formatEmptyRunStatus,
  isRunGroupSettled as isRunGroupExecutionSettled
} from '../model/runProcessGrouping'
import { getMessageGroupDisplayItems } from '../model/messageGrouping'
import { normalizeToolCalls } from './tools/toolRegistry'
import dayjs, { parseToShanghai } from '@/shared/lib/time'

// 阅读消费共享事实；命令和输入业务由父层的事件与插槽承接。
const props = defineProps({
  threadId: { type: String, default: null },
  active: { type: Boolean, default: true },
  pageVisible: { type: Boolean, default: true },
  loading: { type: Boolean, default: false },
  processing: { type: Boolean, default: false },
  replyLoading: { type: Boolean, default: false },
  suppressRefs: { type: Boolean, default: false },
  configNotice: { type: Object, default: null },
  configNoticeScrollVersion: { type: Number, default: 0 },
  mentionConfig: { type: Object, default: null },
  cooperationView: { type: Object, default: null },
  knowledgeBases: { type: Array, default: () => [] },
  widgetAction: { type: Function, default: null },
  widgetActionsDisabled: { type: Boolean, default: true }
})
const emit = defineEmits(['loaded', 'terminal', 'open-refs', 'open-session', 'artifact-saved', 'open-preview'])
const user = useUserStore()
const runtime = useSessionRuntimeStore()
const currentChatId = computed(() => user.isLoggedIn ? props.threadId : null)
const state = computed(() => runtime.getThreadState(currentChatId.value))
const activated = ref(true)
const documentVisible = ref(typeof document === 'undefined' || document.visibilityState === 'visible')
const workspaceActive = computed(() => props.active && activated.value && user.isLoggedIn)
const pageVisible = computed(() => props.pageVisible && documentVisible.value)
const isLoadingMessages = computed(() => props.loading || Boolean(state.value?.readingLoading && !state.value?.historyLoaded))
const isProcessing = computed(() => props.processing || Boolean(state.value?.isStreaming))
const currentThreadConfigNotice = toRef(props, 'configNotice')
const availableKnowledgeBases = toRef(props, 'knowledgeBases')
provide('getAgentThreadState', runtime.getThreadState)
const { historyPages } = storeToRefs(runtime)
const { runGroups, currentThreadRuns } = useThreadMessageProjection(currentChatId)
const currentRunById = computed(() => new Map(currentThreadRuns.value.map((run) => [run.id, run])))
const getMessageRun = (message) => currentRunById.value.get(getMessageRunId(message)) || null
const shouldSuppressRefsForApproval = () => props.suppressRefs || Boolean(state.value?.pendingInterrupt)
const isRunGroupSettled = (group) =>
  isRunGroupExecutionSettled(runGroups.value, group, isProcessing.value || props.replyLoading)

// Run 终态投影落定后才展示 Refs 操作栏与交付物卡片
const shouldShowRefs = computed(() => {
  return (group) => {
    if (!getLastMessage(group) || group.status === 'streaming' || shouldSuppressRefsForApproval()) {
      return false
    }
    return isRunGroupSettled(group)
  }
})

/** 间隔超过一小时时，在新用户消息上方显示发送时间。 */
const getMessageGroupTimeLabel = (group, previousGroup) => {
  const sentAt = group.messages.find((message) => message.type === 'human')?.created_at
  const finishedAt = getMessageRun(
    previousGroup?.messages.findLast((message) => message.type === 'ai')
  )?.timing?.finished_at
  if (!sentAt || !finishedAt) return ''

  // 历史消息的无时区时间来自 PostgreSQL UTC，不能按浏览器本地时间解析。
  const sentTime = dayjs.utc(sentAt)
  const finishedTime = dayjs.utc(finishedAt)
  if (!sentTime.isValid() || !finishedTime.isValid()) return ''
  if (sentTime.valueOf() - finishedTime.valueOf() <= 60 * 60 * 1000) return ''

  const displayTime = parseToShanghai(sentTime.toISOString())
  const today = parseToShanghai(Date.now())
  if (displayTime.isSame(today, 'day')) return displayTime.format('今天 HH:mm')
  if (displayTime.isSame(today.subtract(1, 'day'), 'day')) {
    return displayTime.format('昨天 HH:mm')
  }
  return displayTime.format(displayTime.isSame(today, 'year') ? 'MM-DD HH:mm' : 'YYYY-MM-DD HH:mm')
}

const messageGroupRows = computed(() => {
  const rows = runGroups.value.map((group, index) => ({
    type: 'message-group',
    key:
      group.displayKey ||
      group.run?.id ||
      (group.status === 'streaming' ? 'ongoing-message-group' : `history-${index}`),
    group,
    timeLabel: getMessageGroupTimeLabel(group, runGroups.value[index - 1]),
    displayItems: getDisplayItems(group),
    artifacts: MessageProcessor.extractArtifactsFromMessageGroup(group)
  }))

  if (currentThreadConfigNotice.value) {
    const insertAfterCount = Math.max(
      0,
      Math.min(Number(currentThreadConfigNotice.value.insertAfterGroupCount) || 0, rows.length)
    )
    rows.splice(insertAfterCount, 0, {
      type: 'notice',
      key: currentThreadConfigNotice.value.id,
      notice: currentThreadConfigNotice.value
    })
  }

  return rows
})

/** 用户主动停止的 Run 终态：即使已有工具摘要也要显式告知结果。 */
const USER_STOPPED_RUN_STATUSES = new Set(['cancelled', 'interrupted', 'cancel_requested'])

const isUserStoppedRun = (run) => Boolean(run) && USER_STOPPED_RUN_STATUSES.has(run.status)

const shouldShowRunStatusNotice = (row) => {
  const run = row?.group?.run
  if (!run) return false
  if (isUserStoppedRun(run)) return true
  return !row.displayItems.length
}

const getMessageToolCalls = (message) => normalizeToolCalls(message?.tool_calls)

const getDisplayItems = (group) =>
  getMessageGroupDisplayItems(group, {
    enrichToolCalls: getMessageToolCalls,
    runTiming: group.processTiming || getMessageRun(getLastMessage(group))?.timing,
    collapseIntermediate: group?.status !== 'streaming' && isRunGroupSettled(group)
  })

const isDisplayMessageProcessing = (group, displayItem) => {
  return (
    displayItem?.type === 'message' &&
    props.replyLoading &&
    group?.status === 'streaming' &&
    displayItem.sourceIndex === group.messages.length - 1
  )
}

const isToolGroupActive = (group, itemIndex, displayItems) => {
  return (
    props.replyLoading && group?.status === 'streaming' && itemIndex === displayItems.length - 1
  )
}

const getLastMessage = (group) => {
  if (!group?.messages?.length) return null
  for (let i = group.messages.length - 1; i >= 0; i--) {
    if (group.messages[i].type === 'ai') return group.messages[i]
  }
  return null
}

const showMsgRefs = (msg, group) => {
  if (shouldSuppressRefsForApproval()) {
    return false
  }

  // 该消息所在对话未收尾（后面跟的是没有 human message 的 AI 续写，或仍在生成）时不展示
  if (!isRunGroupSettled(group)) {
    return false
  }

  // 只有真正完成的消息才显示 refs
  if (msg.isLast && msg.status === 'finished') {
    return ['copy', 'sources']
  }
  return false
}

const getMessageGroupSources = (group) => {
  return MessageProcessor.extractSourcesFromMessageGroup(group, availableKnowledgeBases.value)
}

const chatMainRef = ref(null)
const scrollController = new ScrollController(() =>
  workspaceActive.value ? chatMainRef.value : null
)
let retainedScrollTop = 0
let readingGeneration = 0
const earlierMessagesLoading = ref(false)
const earlierMessagesError = ref(false)
const earlierMessagesLabel = computed(() => {
  if (historyPages.value[currentChatId.value]?.loading || earlierMessagesLoading.value) {
    return '正在加载更早消息…'
  }
  return earlierMessagesError.value ? '加载失败，点击重试' : '加载更早消息'
})

/** 追加历史后按可见消息恢复位置，保留请求期间用户的滚动。 */
const loadEarlierMessages = async () => {
  const threadId = currentChatId.value
  const container = chatMainRef.value
  const paging = historyPages.value[threadId]
  if (
    !container ||
    !workspaceActive.value ||
    !pageVisible.value ||
    isLoadingMessages.value ||
    !paging?.hasMore ||
    paging.loading ||
    earlierMessagesLoading.value
  ) {
    return
  }
  const generation = readingGeneration
  earlierMessagesLoading.value = true
  earlierMessagesError.value = false
  scrollController.disableAutoScroll()
  scrollController.cancelPendingScrolls()
  const anchor = [...container.querySelectorAll('.history-display-item')].find(
    (element) => element.getBoundingClientRect().bottom > container.getBoundingClientRect().top
  )
  const anchorKey = anchor?.dataset.historyKey
  const anchorTop = anchor?.getBoundingClientRect().top
  const previousHeight = container.scrollHeight
  const previousScrollTop = container.scrollTop
  try {
    await runtime.loadOlderHistory(threadId)
    await nextTick()
    if (
      generation !== readingGeneration ||
      currentChatId.value !== threadId ||
      chatMainRef.value !== container ||
      !workspaceActive.value
    ) {
      return
    }
    const currentAnchor = anchor?.isConnected
      ? anchor
      : [...container.querySelectorAll('.history-display-item')].find(
          (element) => element.dataset.historyKey === anchorKey
        )
    const offset = currentAnchor
      ? currentAnchor.getBoundingClientRect().top -
        anchorTop +
        container.scrollTop -
        previousScrollTop
      : container.scrollHeight - previousHeight
    container.scrollTop += offset
    retainedScrollTop = container.scrollTop
  } catch {
    // Runtime 已提供错误提示；停止自动重试，交由用户点击恢复。
    if (generation === readingGeneration) earlierMessagesError.value = true
  } finally {
    if (generation === readingGeneration) earlierMessagesLoading.value = false
  }
}

const handleWorkspaceScroll = () => {
  if (!workspaceActive.value) return
  if (scrollController.hasLayoutChanged()) return
  const scrollTop = chatMainRef.value?.scrollTop || 0
  const scrollingUp = scrollTop < retainedScrollTop
  retainedScrollTop = scrollTop
  scrollController.handleScroll()
  if (scrollingUp && scrollTop <= 600 && !earlierMessagesError.value) {
    void loadEarlierMessages()
  }
}

let resizeObserver = null
let readyTimer = null
let releaseView = () => {}
let threadGeneration = 0
let loadingPromise = null
let positioned = false

/** 暂停布局工作；隐藏视口仍持有共享历史与订阅。 */
function stopLayout() {
  readingGeneration += 1
  earlierMessagesLoading.value = false
  clearTimeout(readyTimer)
  resizeObserver?.disconnect()
  resizeObserver = null
  scrollController.cleanup()
}

/** 布局稳定后再记录字符锚点，输入插槽高度变化也属于阅读布局。 */
function observeLayout() {
  clearTimeout(readyTimer)
  resizeObserver?.disconnect()
  if (!workspaceActive.value || !chatMainRef.value) return
  if (typeof ResizeObserver === 'undefined') return
  let ready = false
  resizeObserver = new ResizeObserver(() => {
    if (!ready || earlierMessagesLoading.value) return
    scrollController.handleResize()
    retainedScrollTop = chatMainRef.value?.scrollTop || 0
  })
  resizeObserver.observe(chatMainRef.value)
  for (const child of chatMainRef.value.children) resizeObserver.observe(child)
  readyTimer = setTimeout(() => {
    ready = true
    scrollController.rememberReadingPosition()
  }, 50)
}

/** 首次定位完成后通知输入区；输入准备独立于阅读。 */
const load = (fresh = false) => {
  const threadId = currentChatId.value
  if (!workspaceActive.value || !threadId) return Promise.resolve(false)
  if (loadingPromise) return loadingPromise
  const generation = threadGeneration
  const promise = (async () => {
    const loaded = await runtime.loadThread(threadId, { fresh })
    if (!loaded || generation !== threadGeneration) return false
    if (!positioned && workspaceActive.value) {
      positioned = true
      await nextTick()
      if (generation === threadGeneration && workspaceActive.value) {
        await scrollController.scrollToBottomStaticForce()
      }
    }
    if (generation !== threadGeneration) return false
    emit('loaded', { threadId })
    return true
  })()
  loadingPromise = promise
  void promise.finally(() => {
    if (loadingPromise === promise) loadingPromise = null
  }).catch(() => {})
  return promise
}
const retry = () => { void load(true).catch(() => {}) }

watch(
  [currentChatId, () => user.token, workspaceActive, pageVisible],
  ([threadId, token, active, visible], [previousId, previousToken, wasActive] = []) => {
    // 快速隐藏可先于 scroll 事件，停用前直接保存真实位置。
    if (wasActive && chatMainRef.value) retainedScrollTop = chatMainRef.value.scrollTop
    stopLayout()
    const changed = threadId !== previousId || token !== previousToken
    if (changed) {
      threadGeneration++
      loadingPromise = null
      positioned = false
      earlierMessagesError.value = false
      retainedScrollTop = 0
      scrollController.enableAutoScroll()
      releaseView()
      releaseView = threadId ? runtime.observeThread(threadId, {
        onTerminalDetected: (event) => emit('terminal', event),
        onScrollToBottom() {
          if (workspaceActive.value && pageVisible.value) scrollController.scrollToBottom()
        }
      }) : () => {}
    }
    if (!active || !visible) return
    const generation = readingGeneration
    nextTick(() => {
      if (generation !== readingGeneration) return
      if (chatMainRef.value) chatMainRef.value.scrollTop = retainedScrollTop
      observeLayout()
    })
    void load(!changed).catch(() => {})
  },
  { immediate: true, flush: 'sync' }
)
watch(
  runGroups,
  () => {
    if (workspaceActive.value && pageVisible.value && isProcessing.value) scrollController.scrollToBottom()
  },
  { flush: 'post' }
)
watch(
  () => props.configNoticeScrollVersion,
  () => {
    if (workspaceActive.value && props.threadId) scrollController.scrollToBottom(true)
  },
  { flush: 'post' }
)
const updateVisibility = () => { documentVisible.value = document.visibilityState === 'visible' }
onMounted(() => {
  observeLayout()
  document.addEventListener('visibilitychange', updateVisibility)
})
onActivated(() => {
  activated.value = true
})
onDeactivated(() => {
  activated.value = false
})
onUnmounted(() => {
  threadGeneration++
  releaseView()
  stopLayout()
  document.removeEventListener('visibilitychange', updateVisibility)
})

defineExpose({
  refresh: () => load(true),
  element: chatMainRef,
  scrollToBottom: (force = false) => scrollController.scrollToBottom(force)
})
</script>

<style lang="less" scoped>
.thread-reading-error {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 12px;
  padding: 16px;
  color: var(--gray-700);
}
.thread-reading-error button {
  color: var(--main-color);
  background: var(--main-10);
  border: 1px solid var(--main-20);
  border-radius: 6px;
  padding: 6px 10px;
  cursor: pointer;
}

.session-loading {
  position: absolute;
  top: 8px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 6px 10px;
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  box-shadow: 0 1px 4px var(--shadow-2);
  pointer-events: none;

  :deep(.ant-spin) {
    display: flex;
    line-height: 1;
  }
}

.chat-box {
  width: 100%;
  max-width: 800px;
  margin: 0 auto;
  flex-grow: 1;
  padding: 1rem 2rem;
  display: flex;
  flex-direction: column;
}

.group-box,
.history-display-item {
  display: flex;
  flex-direction: column;
}

// 悬浮整个 Run 分组时显示 refs 行中的时间
.group-box:hover {
  :deep(.refs .time-entry) {
    opacity: 1;
    visibility: visible;
  }
}

.message-group-time {
  margin: 24px 0 16px;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.6;
  text-align: center;
}

.chat-inline-notice {
  display: flex;
  justify-content: center;
  padding: 6px 16px 12px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.6;
  text-align: center;

  &.is-terminal {
    color: var(--color-text-secondary);
    font-weight: 500;
  }
}

.history-load-btn {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 4px 7px;
  min-height: 40px;
  border-radius: 6px;
  font-size: 14px;
  line-height: 20px;
  transition: background-color 0.3s;
  flex-shrink: 0;

  &:hover:not(:disabled) {
    background-color: var(--gray-100);
  }
  color: var(--color-text-secondary);

  &:disabled {
    cursor: wait;
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 2px;
  }
}

.chat-main {
  flex: 1 1 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto; /* Scroll is here now */
  overflow-anchor: none; /* 阅读位置由会话滚动控制器补偿。 */
  position: relative;
  transition:
    flex-basis 0.3s cubic-bezier(0.4, 0, 0.2, 1),
    margin-right 0.3s cubic-bezier(0.4, 0, 0.2, 1),
    padding-right 0.24s cubic-bezier(0.16, 1, 0.3, 1),
    width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  min-width: 0; /* Prevent flex item from overflowing */

  scrollbar-width: none;
}

.history-load-btn {
  border: 0;
  background: transparent;
  cursor: pointer;
}
</style>
