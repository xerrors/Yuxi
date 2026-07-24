import { unref } from 'vue'
import { agentApi } from '@/apis'
import { handleChatError } from '@/utils/errorHandler'
import { shouldFinalizeRunStream, shouldNotifySteeredRunEnd } from '@/utils/agentRequestQueue'
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
  onTerminalDetected = null
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

  const notifyTerminalDetected = (threadId, runId, touchedThreadIds, terminal) => {
    if (typeof onTerminalDetected !== 'function') return
    onTerminalDetected({ threadId, runId, touchedThreadIds: [...touchedThreadIds], terminal })
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

  const finalizeRunStream = (
    threadId,
    runId,
    touchedThreadIds,
    { delay = 200, scroll = false, status = '', terminal = null, terminalNotified = false } = {}
  ) => {
    const ts = getThreadState(threadId)
    if (!ts || !shouldFinalizeRunStream(ts.activeRunId, runId)) return
    const isInterrupted =
      status === RUN_INTERRUPTED_STATUS && hasPendingInterruptInThreads(touchedThreadIds, runId)
    touchedThreadIds.forEach((id) => streamSmoother?.flushThread(id))
    ts.isStreaming = false
    if (isInterrupted) {
      ts.activeRunId = runId
      saveActiveRunSnapshot(threadId, runId, ts.runLastSeq)
    } else {
      ts.activeRunId = null
      clearActiveRunSnapshot(threadId)
      touchedThreadIds.forEach((id) => clearPendingInterruptForRun(id, runId))
    }
    ts.lastRetryableJobTry = null
    ts.replyLoadingVisible = false
    ts.pendingRequestId = null
    fetchThreadMessages({ agentId: unref(currentAgentId), threadId, delay }).finally(() => {
      const latest = getThreadState(threadId)
      if (!latest?.activeRunId || latest.activeRunId === runId) {
        resetOnGoingConv(threadId)
      }
      fetchAgentState(unref(currentAgentId), threadId)
      if (scroll) onScrollToBottom()
      if (isInterrupted) {
        notifyInterruptDetected(threadId, runId)
      } else {
        if (!terminalNotified) notifyTerminalDetected(threadId, runId, touchedThreadIds, terminal)
      }
    })
  }

  const preserveInterruptedRun = async (threadId, run, snapshot = null) => {
    const ts = getThreadState(threadId)
    if (!ts || !run?.id) return false

    streamSmoother?.flushThread(threadId)
    ts.activeRunId = run.id
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

  const startRunStream = async (threadId, runId, afterSeq = '0-0') => {
    if (!threadId || !runId) return
    const ts = getThreadState(threadId)
    if (!ts) return

    stopRunStreamSubscription(threadId)
    const runController = new AbortController()
    ts.runStreamAbortController = runController
    ts.activeRunId = runId
    ts.runLastSeq = normalizeRunSeq(afterSeq)
    ts.lastRetryableJobTry = null
    ts.isStreaming = true
    saveActiveRunSnapshot(threadId, runId, ts.runLastSeq)
    const touchedThreadIds = new Set([threadId])
    let sawTerminalEvent = false
    let steeredTerminalNotified = false

    try {
      const response = await agentApi.streamAgentRunEvents(runId, ts.runLastSeq, {
        signal: runController.signal
      })
      if (!response.ok) {
        throw new Error(`SSE response not ok: ${response.status}`)
      }

      await processRunSseResponse(response, (event, data, eventId) => {
        if (!data) return

        const payload = data.payload || {}
        if (event === 'end' && shouldNotifySteeredRunEnd(payload, steeredTerminalNotified)) {
          sawTerminalEvent = true
          steeredTerminalNotified = true
          notifyTerminalDetected(threadId, runId, touchedThreadIds, payload)
        }
        if (ts.activeRunId !== runId) return

        if (eventId) {
          const incomingSeq = normalizeRunSeq(eventId)
          if (compareRunSeq(incomingSeq, ts.runLastSeq) <= 0) return
          ts.runLastSeq = incomingSeq
          saveActiveRunSnapshot(threadId, runId, incomingSeq)
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

        if (Array.isArray(payload.items)) {
          payload.items.forEach((chunk) => {
            const routeThreadId = resolveChunkThreadId({
              envelope: data,
              payload,
              chunk,
              fallbackThreadId: threadId
            })
            touchedThreadIds.add(routeThreadId)
            handleStreamChunk(
              {
                ...chunk,
                request_id: chunk.request_id || data.request_id,
                run_id: chunk.run_id || data.run_id || runId,
                thread_id: routeThreadId
              },
              routeThreadId
            )
          })
        } else if (payload.chunk) {
          const routeThreadId = resolveChunkThreadId({
            envelope: data,
            payload,
            chunk: payload.chunk,
            fallbackThreadId: threadId
          })
          touchedThreadIds.add(routeThreadId)
          handleStreamChunk(
            {
              ...payload.chunk,
              request_id: payload.chunk.request_id || data.request_id,
              run_id: payload.chunk.run_id || data.run_id || runId,
              thread_id: routeThreadId
            },
            routeThreadId
          )
        }

        if (event === 'end') {
          sawTerminalEvent = true
          if (terminalStatus === RUN_INTERRUPTED_STATUS) {
            finalizeRunStream(threadId, runId, touchedThreadIds, {
              status: terminalStatus,
              terminal: payload,
              terminalNotified: steeredTerminalNotified
            })
          } else if (RUN_TERMINAL_STATUSES.has(terminalStatus)) {
            finalizeRunStream(threadId, runId, touchedThreadIds, {
              status: terminalStatus,
              terminal: payload,
              terminalNotified: steeredTerminalNotified
            })
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
      } else if (ts.activeRunId !== runId) {
        ts.replyLoadingVisible = false
        ts.pendingRequestId = null
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
          ts.activeRunId = null
          ts.isStreaming = false
          ts.replyLoadingVisible = false
          ts.pendingRequestId = null
          clearPendingInterruptForRun(threadId, run.id)
          clearActiveRunSnapshot(threadId)
          notifyTerminalDetected(threadId, run.id, new Set([threadId]))
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
            await startRunStream(threadId, run.id, afterSeq)
            return
          }
        } catch {
          // ignore
        }
        clearActiveRunSnapshot(threadId)
      }
    }

    try {
      const active = await agentApi.getThreadActiveRun(threadId)
      const run = active?.run
      if (run?.status === RUN_INTERRUPTED_STATUS) {
        if (hasPendingInterruptForRun(ts, run.id)) {
          await preserveInterruptedRun(threadId, run)
          return
        }
        resetOnGoingConv(threadId)
        await startRunStream(threadId, run.id, '0-0')
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

    ts.activeRunId = null
    ts.runLastSeq = '0-0'
    ts.isStreaming = false
    ts.replyLoadingVisible = false
    ts.pendingRequestId = null
    ts.pendingInterrupt = null
    clearActiveRunSnapshot(threadId)
    notifyTerminalDetected(threadId, null, new Set([threadId]))
  }

  return {
    startRunStream,
    resumeActiveRunForThread,
    stopRunStreamSubscription
  }
}
