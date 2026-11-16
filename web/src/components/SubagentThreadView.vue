<template>
  <div class="subagent-thread-view">
    <div ref="scrollContainerRef" class="subagent-thread-scroll" @scroll="handleScroll">
      <div ref="contentRef" class="subagent-thread-content">
        <div v-if="loading && !hasRenderableMessages" class="subagent-thread-state">
          正在加载子智能体消息...
        </div>
        <div v-else-if="error" class="subagent-thread-state is-error">{{ error }}</div>
        <ThreadMessageList
          v-else
          :messages="displayMessages"
          :runs="runs"
          :ongoing-messages="streamedMessages"
          :is-processing="streamActive"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { agentApi } from '@/apis'
import {
  dispatchRunEventChunks,
  processRunSseResponse
} from '@/composables/useAgentRunStream'
import { getMessageRunId } from '@/utils/messageDebug'
import { useAgentStreamHandler } from '@/composables/useAgentStreamHandler'
import { useStreamSmoother } from '@/composables/useStreamSmoother'
import ThreadMessageList from '@/components/ThreadMessageList.vue'
import { MessageProcessor } from '@/utils/messageProcessor'
import ScrollController from '@/utils/scrollController'

const props = defineProps({
  threadId: { type: String, required: true },
  active: { type: Boolean, default: false }
})

const RUN_TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled', 'interrupted'])
const loading = ref(false)
const error = ref('')
const messages = ref([])
const runs = ref([])
const currentRunId = ref('')
const currentRunStatus = ref('')
const streamActive = ref(false)
const lastEventId = ref('0-0')
const scrollContainerRef = ref(null)
const contentRef = ref(null)
const streamState = reactive({ threadStates: {} })
let streamAbortController = null
let resizeObserver = null
let reconnectTimer = null
let loadVersion = 0
let disposed = false

const normalizeRunStatus = (status) => String(status || '').trim()
const isTerminalRunStatus = (status) => RUN_TERMINAL_STATUSES.has(normalizeRunStatus(status))
const getStreamThreadState = (threadId) => {
  if (!streamState.threadStates[threadId]) {
    streamState.threadStates[threadId] = {
      isStreaming: false,
      replyLoadingVisible: false,
      pendingRequestId: null,
      pendingInterrupt: null,
      onGoingConv: {
        msgChunks: {},
        currentRequestKey: null,
        currentAssistantKey: null,
        toolCallBuffers: {}
      },
      agentState: null
    }
  }
  return streamState.threadStates[threadId]
}
const streamSmoother = useStreamSmoother({ getThreadState: getStreamThreadState })
const { handleStreamChunk } = useAgentStreamHandler({
  getThreadState: getStreamThreadState,
  processApprovalInStream: () => false,
  currentAgentId: ref(''),
  supportsFiles: ref(false),
  streamSmoother
})
const streamedMessages = computed(() => {
  const threadState = getStreamThreadState(props.threadId)
  const chunks = Object.values(threadState.onGoingConv.msgChunks)
    .map(MessageProcessor.mergeMessageChunk)
    .filter(Boolean)
  return chunks.length
    ? MessageProcessor.convertToolResultToMessages(chunks).filter(
        (message) => message.type !== 'tool'
      )
    : []
})
const displayMessages = computed(() => messages.value)
const hasRenderableMessages = computed(
  () => displayMessages.value.length > 0 || streamedMessages.value.length > 0
)
const scrollController = new ScrollController(() => scrollContainerRef.value, {
  threshold: 80,
  scrollDelay: 80
})
const handleScroll = (event) => {
  scrollController.handleScroll(event)
}

const flattenContent = (content) => {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return content ?? ''
  return content
    .filter((block) => block?.type === 'text')
    .map((block) => block.text || '')
    .join('')
}
const normalizeMessages = (items) =>
  (Array.isArray(items) ? items : []).map((message) => ({
    ...message,
    content: flattenContent(message.content)
  }))
const resetStreamState = () => {
  streamSmoother.resetThread(props.threadId)
  delete streamState.threadStates[props.threadId]
}
const stopRunStream = () => {
  streamAbortController?.abort()
  streamAbortController = null
  streamActive.value = false
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
}
const scrollToBottom = async (force = false) => {
  if (!props.active) return
  await nextTick()
  if (force) await scrollController.scrollToBottomStaticForce()
  else await scrollController.scrollToBottom()
}
const loadPersistedMessages = async () => {
  const response = await agentApi.getAgentHistory(props.threadId)
  messages.value = normalizeMessages(response.history || [])
  runs.value = response.runs
}
const loadThread = async () => {
  if (!props.threadId) return
  const version = ++loadVersion
  loading.value = true
  error.value = ''
  try {
    const response = await agentApi.getAgentState(props.threadId, { includeMessages: true })
    if (disposed || version !== loadVersion) return
    currentRunId.value = response?.subagent_run?.run_id ? String(response.subagent_run.run_id) : ''
    currentRunStatus.value = normalizeRunStatus(response?.subagent_run?.status)

    if (!currentRunId.value || isTerminalRunStatus(currentRunStatus.value)) {
      stopRunStream()
      resetStreamState()
      await loadPersistedMessages()
      if (disposed || version !== loadVersion) return
    } else {
      await loadPersistedMessages()
      if (disposed || version !== loadVersion) return
      messages.value = messages.value.filter(
        (message) => getMessageRunId(message) !== currentRunId.value
      )
      lastEventId.value = '0-0'
      void startRunStream(currentRunId.value, lastEventId.value, true)
    }
    await scrollToBottom(true)
  } catch (loadError) {
    error.value = '加载子智能体消息失败'
    console.error('Failed to load subagent thread messages:', loadError)
  } finally {
    loading.value = false
  }
}
const scheduleReconnect = (runId) => {
  if (disposed || reconnectTimer) return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    void startRunStream(runId, lastEventId.value, false)
  }, 1000)
}
const startRunStream = async (runId, afterSeq = '0-0', resetMessages = false) => {
  stopRunStream()
  if (disposed || !runId) return
  if (resetMessages) resetStreamState()
  const controller = new AbortController()
  streamAbortController = controller
  streamActive.value = true
  getStreamThreadState(props.threadId).isStreaming = true

  try {
    const response = await agentApi.streamAgentRunEvents(runId, afterSeq, {
      signal: controller.signal
    })
    if (!response.ok) throw new Error(`SSE response not ok: ${response.status}`)
    await processRunSseResponse(response, (event, data, eventId) => {
      if (!data) return
      if (eventId) lastEventId.value = String(eventId)
      const payload = data.payload || {}
      const isRetryableError =
        event === 'error' && (payload.retryable === true || payload.chunk?.retryable === true)
      if (isRetryableError) return
      dispatchRunEventChunks({
        data,
        runId,
        fallbackThreadId: props.threadId,
        onChunk: (chunk, threadId) => {
          if (threadId !== props.threadId) return
          handleStreamChunk(chunk, threadId)
        }
      })
      if (event === 'end') streamActive.value = false
    })
  } catch (streamError) {
    if (streamError?.name !== 'AbortError') {
      console.error('Failed to stream subagent run messages:', streamError)
    }
  } finally {
    if (streamAbortController === controller) streamAbortController = null
    streamActive.value = false
    if (!controller.signal.aborted && !disposed) {
      streamSmoother.flushThread(props.threadId)
      try {
        const runResponse = await agentApi.getAgentRun(runId)
        if (!disposed) {
          const status = normalizeRunStatus(runResponse?.run?.status)
          if (isTerminalRunStatus(status)) await loadThread()
          else scheduleReconnect(runId)
        }
      } catch {
        scheduleReconnect(runId)
      }
    }
  }
}

watch(() => props.threadId, loadThread)
watch(
  () => props.active,
  (active) => {
    if (active) scrollToBottom(true)
  }
)
watch(streamedMessages, () => scrollToBottom(), { deep: true, flush: 'post' })

onMounted(() => {
  loadThread()
  if (typeof ResizeObserver !== 'undefined' && contentRef.value) {
    resizeObserver = new ResizeObserver(() => scrollToBottom())
    resizeObserver.observe(contentRef.value)
  }
})
onUnmounted(() => {
  disposed = true
  loadVersion += 1
  stopRunStream()
  resetStreamState()
  resizeObserver?.disconnect()
  scrollController.reset()
})
</script>

<style scoped lang="less">
.subagent-thread-view,
.subagent-thread-scroll {
  width: 100%;
  height: 100%;
  min-height: 0;
}

.subagent-thread-scroll {
  overflow-y: auto;
  padding: 16px 28px 28px;
}

.subagent-thread-content {
  width: min(100%, 800px);
  min-height: 100%;
  margin: 0 auto;
}

.subagent-thread-state {
  padding: 32px 0;
  color: var(--gray-500);
  font-size: 13px;
  text-align: center;

  &.is-error {
    color: var(--color-error-600);
  }
}
</style>
