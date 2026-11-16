import { unref } from 'vue'
import { agentApi } from '@/apis'
import { handleChatError } from '@/utils/errorHandler'
import { isSteerableMainChatRun } from '@/utils/agentRun'
import { compareRunSeq, normalizeRunSeq, resolveRunResumeAfterSeq } from '@/utils/runStreamResume'
import { hasPendingInterruptPayload } from '@/utils/toolApproval'

const RUN_INTERRUPTED_STATUS = 'interrupted'
const RUN_TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])
const ACTIVE_RUN_STORAGE_TTL_MS = 60 * 60 * 1000
const ACTIVE_RUN_CLIENT_ID = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

const getActiveRunStorageKey = (threadId) => `active_run:${threadId}`

const getThreadIdFromObject = (value) => {
  if (!value || typeof value !== 'object') return ''
  if (typeof value.thread_id === 'string' && value.thread_id.trim()) return value.thread_id.trim()
  const nestedSources = [value.meta, value.metadata, value.configurable, value.stream_event]
  for (const source of nestedSources) {
    const nestedThreadId = getThreadIdFromObject(source)
    if (nestedThreadId) return nestedThreadId
  }
  return ''
}

const resolveChunkThreadId = ({ envelope, payload, chunk, fallbackThreadId }) => {
  return (
    getThreadIdFromObject(envelope) ||
    getThreadIdFromObject(payload) ||
    getThreadIdFromObject(chunk) ||
    fallbackThreadId
  )
}

export function dispatchRunEventChunks({
  data,
  runId,
  fallbackThreadId,
  streamRunId = null,
  streamThreadId = null,
  onChunk
}) {
  if (typeof onChunk !== 'function') return
  const payload = data?.payload || {}
  const chunks = Array.isArray(payload.items) ? payload.items : payload.chunk ? [payload.chunk] : []
  chunks.forEach((chunk) => {
    const routeThreadId = resolveChunkThreadId({
      envelope: data,
      payload,
      chunk,
      fallbackThreadId
    })
    onChunk(
      {
        ...chunk,
        request_id: chunk.request_id || data?.request_id,
        run_id: chunk.run_id || data?.run_id || runId,
        thread_id: routeThreadId,
        ...(streamRunId ? { stream_run_id: streamRunId } : {}),
        ...(streamThreadId ? { stream_thread_id: streamThreadId } : {})
      },
      routeThreadId
    )
  })
}

export const processRunSseResponse = async (response, onEvent) => {
  if (!response || !response.body) return
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventType = 'message'
  let eventId = null
  let dataLines = []

  const dispatch = () => {
    if (dataLines.length === 0) return
    const dataText = dataLines.join('\n')
    try {
      const parsed = JSON.parse(dataText)
      onEvent(eventType, parsed, eventId)
    } catch (e) {
      console.warn('Failed to parse run SSE data:', e, dataText)
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const rawLine of lines) {
        const line = rawLine.replace(/\r$/, '')
        if (!line) {
          dispatch()
          eventType = 'message'
          eventId = null
          dataLines = []
          continue
        }

        if (line.startsWith(':')) {
          continue
        }
        if (line.startsWith('event:')) {
          eventType = line.slice(6).trim() || 'message'
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trimStart())
        } else if (line.startsWith('id:')) {
          eventId = line.slice(3).trim()
        }
      }
    }

    dispatch()
  } finally {
    try {
      reader.releaseLock()
    } catch {
      // ignore
    }
  }
}

export function useAgentRunStream({
  getThreadState,
  currentAgentId,
  handleStreamChunk,
  fetchThreadMessages,
  fetchAgentState,
  resetOnGoingConv,
  onScrollToBottom,
  streamSmoother,
  onInterruptDetected = null,
  onTerminalDetected = null,
  onRunStarted = null
}) {
  const saveActiveRunSnapshot = (threadId, runId, lastSeq = '0-0') => {
    if (!threadId || !runId) return
    localStorage.setItem(
      getActiveRunStorageKey(threadId),
      JSON.stringify({
        run_id: runId,
        last_seq: normalizeRunSeq(lastSeq),
        created_at: Date.now(),
        client_id: ACTIVE_RUN_CLIENT_ID
      })
    )
  }

  const loadActiveRunSnapshot = (threadId) => {
    if (!threadId) return null
    try {
      const raw = localStorage.getItem(getActiveRunStorageKey(threadId))
      return raw ? JSON.parse(raw) : null
    } catch {
      return null
    }
  }

  const clearActiveRunSnapshot = (threadId) => {
    if (!threadId) return
    localStorage.removeItem(getActiveRunStorageKey(threadId))
  }

  const stopRunStreamSubscription = (threadId) => {
    const ts = getThreadState(threadId)
    if (!ts) return
    streamSmoother?.flushThread(threadId)
    if (ts.runStreamAbortController) {
      ts.runStreamAbortController.abort()
      ts.runStreamAbortController = null
    }
  }

  const notifyInterruptDetected = (threadId, runId, run = null) => {
    if (typeof onInterruptDetected !== 'function') return
    onInterruptDetected({ threadId, runId, run })
  }

  const notifyTerminalDetected = (threadId, runId, touchedThreadIds) => {
    if (typeof onTerminalDetected !== 'function') return
    onTerminalDetected({ threadId, runId, touchedThreadIds: [...touchedThreadIds] })
  }

  const hasPendingInterruptForRun = (threadState, runId) => {
    const pendingInterrupt = threadState?.pendingInterrupt
    if (!hasPendingInterruptPayload(pendingInterrupt)) return false
    return !pendingInterrupt.interruptedRunId || pendingInterrupt.interruptedRunId === runId
  }

  const hasPendingInterruptInThreads = (threadIds, runId) => {
    return [...threadIds].some((id) => hasPendingInterruptForRun(getThreadState(id), runId))
  }

  const clearPendingInterruptForRun = (threadId, runId) => {
    const threadState = getThreadState(threadId)
    if (hasPendingInterruptForRun(threadState, runId)) {
      threadState.pendingInterrupt = null
    }
  }

  const resolveRunSteerable = async (run) => {
    if (!run?.request_id || run.status !== 'running' || run.run_type !== 'chat') return false
    try {
      const response = await agentApi.getRequest(run.request_id)
      return response?.request?.source === 'chat'
    } catch {
      return false
    }
  }

  const finalizeRunStream = (
    threadId,
    runId,
    touchedThreadIds,
    { delay = 200, scroll = false, status = '', expectedActiveRunId = runId } = {}
  ) => {
    const ts = getThreadState(threadId)
    if (!ts) return false
    const settlesIdleThread = runId === null
    if (ts.activeRunId !== expectedActiveRunId) {
      return false
    }
    const isInterrupted =
      status === RUN_INTERRUPTED_STATUS && hasPendingInterruptInThreads(touchedThreadIds, runId)
    touchedThreadIds.forEach((id) => streamSmoother?.flushThread(id))
    ts.isStreaming = false
    ts.activeRunSteerable = false
    if (settlesIdleThread) ts.runLastSeq = '0-0'
    if (isInterrupted) {
      ts.activeRunId = runId
      saveActiveRunSnapshot(threadId, runId, ts.runLastSeq)
    } else {
      ts.activeRunId = null
      clearActiveRunSnapshot(threadId)
      if (settlesIdleThread) {
        touchedThreadIds.forEach((id) => {
          const threadState = getThreadState(id)
          if (threadState) threadState.pendingInterrupt = null
        })
      } else {
        touchedThreadIds.forEach((id) => clearPendingInterruptForRun(id, runId))
      }
    }
    ts.lastRetryableJobTry = null
    ts.replyLoadingVisible = false
    ts.pendingRequestId = null
    fetchThreadMessages({ agentId: unref(currentAgentId), threadId, delay }).finally(() => {
      const latest = getThreadState(threadId)
      if (!latest?.activeRunId || latest.activeRunId === runId) {
        resetOnGoingConv(threadId, { preserveRequestStreams: true })
      }
      fetchAgentState(unref(currentAgentId), threadId)
      if (scroll) onScrollToBottom()
      if (isInterrupted) {
        notifyInterruptDetected(threadId, runId)
      } else {
        notifyTerminalDetected(threadId, runId, touchedThreadIds)
      }
    })
    return true
  }

  const preserveInterruptedRun = async (threadId, run, snapshot = null) => {
    const ts = getThreadState(threadId)
    if (!ts || !run?.id) return false

    streamSmoother?.flushThread(threadId)
    ts.activeRunId = run.id
    ts.activeRunSteerable = false
    ts.runLastSeq = normalizeRunSeq(snapshot?.last_seq || ts.runLastSeq || '0-0')
    ts.lastRetryableJobTry = null
    ts.isStreaming = false
    ts.replyLoadingVisible = false
    ts.pendingRequestId = null
    saveActiveRunSnapshot(threadId, run.id, ts.runLastSeq)

    try {
      await fetchThreadMessages({ agentId: unref(currentAgentId), threadId })
    } catch (e) {
      console.warn('Failed to refresh messages for interrupted run:', threadId, e)
    }
    fetchAgentState(unref(currentAgentId), threadId)
    notifyInterruptDetected(threadId, run.id, run)
    return true
  }

  const scheduleRunReconnect = (threadId, runId, delay = 500) => {
    const ts = getThreadState(threadId)
    if (!ts || ts.activeRunId !== runId) return
    setTimeout(() => {
      const latest = getThreadState(threadId)
      if (latest?.activeRunId === runId && !latest.runStreamAbortController) {
        void startRunStream(threadId, runId, latest.runLastSeq)
      }
    }, delay)
  }

  const startRunStream = async (threadId, runId, afterSeq = '0-0', options = {}) => {
    if (!threadId || !runId) return
    const ts = getThreadState(threadId)
    if (!ts) return

    const isSameRun = ts.activeRunId === runId
    const initialSteerable = options.steerable ?? (isSameRun && ts.activeRunSteerable === true)
    stopRunStreamSubscription(threadId)
    const runController = new AbortController()
    ts.runStreamAbortController = runController
    ts.activeRunId = runId
    ts.activeRunSteerable = initialSteerable
    ts.runLastSeq = normalizeRunSeq(afterSeq)
    ts.lastRetryableJobTry = null
    ts.isStreaming = true
    saveActiveRunSnapshot(threadId, runId, ts.runLastSeq)
    if (typeof onRunStarted === 'function') {
      onRunStarted({ threadId, runId, requestId: options.requestId })
    }
    const touchedThreadIds = new Set([threadId])
    let sawTerminalEvent = false

    try {
      const response = await agentApi.streamAgentRunEvents(runId, ts.runLastSeq, {
        signal: runController.signal
      })
      if (!response.ok) {
        throw new Error(`SSE response not ok: ${response.status}`)
      }

      await processRunSseResponse(response, (event, data, eventId) => {
        if (!data || ts.activeRunId !== runId) return

        if (eventId) {
          const incomingSeq = normalizeRunSeq(eventId)
          if (compareRunSeq(incomingSeq, ts.runLastSeq) <= 0) return
          ts.runLastSeq = incomingSeq
          saveActiveRunSnapshot(threadId, runId, incomingSeq)
        }

        const payload = data.payload || {}
        if (event === 'metadata') {
          ts.activeRunSteerable = isSteerableMainChatRun({
            status: 'running',
            run_type: payload.run_type,
            source: payload.source
          })
        }
        const terminalStatus = event === 'end' ? payload.status : data.status
        const isRetryableError =
          event === 'error' && (payload?.retryable === true || payload?.chunk?.retryable === true)
        if (isRetryableError) {
          const parsedJobTry = Number.parseInt(payload?.chunk?.job_try, 10)
          const retryJobTry = Number.isNaN(parsedJobTry) ? null : parsedJobTry
          if (retryJobTry !== null && ts.lastRetryableJobTry === retryJobTry) {
            return
          }
          ts.lastRetryableJobTry = retryJobTry
          console.warn('Run encountered retryable error, waiting for worker retry', {
            threadId,
            runId,
            retryJobTry,
            errorType: payload?.chunk?.error_type
          })
          return
        }

        dispatchRunEventChunks({
          data,
          runId,
          fallbackThreadId: threadId,
          streamRunId: runId,
          streamThreadId: threadId,
          onChunk: (chunk, routeThreadId) => {
            touchedThreadIds.add(routeThreadId)
            handleStreamChunk(chunk, routeThreadId)
          }
        })

        if (event === 'end') {
          sawTerminalEvent = true
          if (
            terminalStatus === RUN_INTERRUPTED_STATUS ||
            RUN_TERMINAL_STATUSES.has(terminalStatus)
          ) {
            finalizeRunStream(threadId, runId, touchedThreadIds, { status: terminalStatus })
          } else {
            touchedThreadIds.forEach((id) => streamSmoother?.flushThread(id))
            ts.isStreaming = false
          }
        }

        if (event === 'error') {
          sawTerminalEvent = true
          finalizeRunStream(threadId, runId, touchedThreadIds, { delay: 300, scroll: true })
        }
      })

      if (!sawTerminalEvent && !runController.signal.aborted && ts.activeRunId === runId) {
        try {
          const runRes = await agentApi.getAgentRun(runId)
          const run = runRes?.run
          if (run?.status === RUN_INTERRUPTED_STATUS) {
            if (hasPendingInterruptInThreads(touchedThreadIds, run.id)) {
              await preserveInterruptedRun(threadId, run)
            } else {
              finalizeRunStream(threadId, runId, touchedThreadIds, { status: run.status })
            }
          } else if (run && RUN_TERMINAL_STATUSES.has(run.status)) {
            finalizeRunStream(threadId, runId, touchedThreadIds, { status: run.status })
          } else {
            scheduleRunReconnect(threadId, runId)
          }
        } catch (e) {
          console.warn(
            'Run SSE closed before terminal event; reconnecting after status check failed:',
            e
          )
          scheduleRunReconnect(threadId, runId)
        }
      }
    } catch (error) {
      if (error?.name !== 'AbortError') {
        streamSmoother?.flushThread(threadId)
        console.error('Run SSE stream error:', error)
        handleChatError(error, 'stream')
        scheduleRunReconnect(threadId, runId)
      }
    } finally {
      if (ts.runStreamAbortController === runController) {
        ts.runStreamAbortController = null
      }
      if (!ts.activeRunId) {
        ts.isStreaming = false
        ts.replyLoadingVisible = false
        ts.pendingRequestId = null
      }
    }
  }

  const resumeActiveRunForThread = async (threadId) => {
    if (!threadId) return
    const ts = getThreadState(threadId)
    if (!ts) return

    if (ts.runStreamAbortController) {
      if (!ts.activeRunId) return
      try {
        const runRes = await agentApi.getAgentRun(ts.activeRunId)
        const run = runRes?.run
        if (run?.status === RUN_INTERRUPTED_STATUS) {
          stopRunStreamSubscription(threadId)
          const snapshot = loadActiveRunSnapshot(threadId)
          if (hasPendingInterruptForRun(ts, run.id)) {
            await preserveInterruptedRun(threadId, run, snapshot)
          } else {
            resetOnGoingConv(threadId)
            await startRunStream(threadId, run.id, '0-0')
          }
        } else if (run && RUN_TERMINAL_STATUSES.has(run.status)) {
          stopRunStreamSubscription(threadId)
          finalizeRunStream(threadId, run.id, new Set([threadId]), {
            status: run.status,
            delay: 0
          })
        }
      } catch (e) {
        console.warn('Failed to refresh active run while stream is open:', threadId, e)
      }
      return
    }

    const snapshot = loadActiveRunSnapshot(threadId)
    if (snapshot?.run_id) {
      if (Date.now() - Number(snapshot.created_at || 0) > ACTIVE_RUN_STORAGE_TTL_MS) {
        clearActiveRunSnapshot(threadId)
      } else {
        try {
          const runRes = await agentApi.getAgentRun(snapshot.run_id)
          const run = runRes?.run
          if (run?.status === RUN_INTERRUPTED_STATUS) {
            // 仅当本地仍持有该中断时才据快照恢复；否则不能仅凭快照重放旧中断
            // （可能已被回复），交由下方 active_run 做权威判定。
            if (hasPendingInterruptForRun(ts, run.id)) {
              await preserveInterruptedRun(threadId, run, snapshot)
              return
            }
          } else if (run && !RUN_TERMINAL_STATUSES.has(run.status)) {
            const afterSeq = resolveRunResumeAfterSeq({
              snapshot,
              threadState: ts
            })
            if (afterSeq === '0-0') {
              resetOnGoingConv(threadId)
            }
            await startRunStream(threadId, run.id, afterSeq, {
              steerable: await resolveRunSteerable(run)
            })
            return
          }
        } catch {
          // ignore
        }
        clearActiveRunSnapshot(threadId)
      }
    }

    const expectedActiveRunId = ts.activeRunId
    try {
      const active = await agentApi.getThreadActiveRun(threadId)
      const run = active?.run
      if (run?.status === RUN_INTERRUPTED_STATUS) {
        if (hasPendingInterruptForRun(ts, run.id)) {
          await preserveInterruptedRun(threadId, run)
          return
        }
        resetOnGoingConv(threadId)
        await startRunStream(threadId, run.id, '0-0', {
          steerable: await resolveRunSteerable(run)
        })
        return
      }
      if (run && !RUN_TERMINAL_STATUSES.has(run.status)) {
        resetOnGoingConv(threadId)
        await startRunStream(threadId, run.id, '0-0')
        return
      }
    } catch (e) {
      console.warn('Failed to load active run for thread:', threadId, e)
    }

    finalizeRunStream(threadId, null, new Set([threadId]), {
      delay: 0,
      expectedActiveRunId
    })
  }

  return {
    startRunStream,
    resumeActiveRunForThread,
    stopRunStreamSubscription
  }
}
