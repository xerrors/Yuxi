import { unref } from 'vue'
import { agentApi } from '@/apis'
import { handleChatError } from '@/utils/errorHandler'

const RUN_TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled', 'interrupted'])
const ACTIVE_RUN_STORAGE_TTL_MS = 60 * 60 * 1000
const ACTIVE_RUN_CLIENT_ID = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

const getActiveRunStorageKey = (threadId) => `active_run:${threadId}`

const normalizeRunSeq = (value) => {
  if (value === undefined || value === null) return '0'
  const text = String(value).trim()
  return text || '0'
}

const parseRunSeq = (value) => {
  const text = normalizeRunSeq(value)
  if (text.includes('-')) {
    const [majorRaw, minorRaw] = text.split('-', 2)
    let major = 0n
    let minor = 0n
    try {
      major = BigInt(majorRaw || '0')
      minor = BigInt(minorRaw || '0')
    } catch {
      return { kind: 'legacy', value: 0 }
    }
    return { kind: 'stream', major, minor }
  }

  const numberValue = Number.parseInt(text, 10)
  if (!Number.isNaN(numberValue)) {
    return { kind: 'legacy', value: numberValue }
  }
  return { kind: 'legacy', value: 0 }
}

const compareRunSeq = (incoming, current) => {
  const left = parseRunSeq(incoming)
  const right = parseRunSeq(current)

  if (left.kind === 'stream' && right.kind === 'stream') {
    if (left.major > right.major) return 1
    if (left.major < right.major) return -1
    if (left.minor > right.minor) return 1
    if (left.minor < right.minor) return -1
    return 0
  }

  if (left.kind === 'legacy' && right.kind === 'legacy') {
    return left.value - right.value
  }

  if (left.kind === 'stream' && right.kind === 'legacy') return 1
  return -1
}

const processRunSseResponse = async (response, onEvent) => {
  if (!response || !response.body) return
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventType = 'message'
  let dataLines = []

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
          if (dataLines.length > 0) {
            const dataText = dataLines.join('\n')
            try {
              const parsed = JSON.parse(dataText)
              onEvent(eventType, parsed)
            } catch (e) {
              console.warn('Failed to parse run SSE data:', e, dataText)
            }
          }
          eventType = 'message'
          dataLines = []
          continue
        }

        if (line.startsWith('event:')) {
          eventType = line.slice(6).trim() || 'message'
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trim())
        }
      }
    }

    if (dataLines.length > 0) {
      const dataText = dataLines.join('\n')
      try {
        const parsed = JSON.parse(dataText)
        onEvent(eventType, parsed)
      } catch (e) {
        console.warn('Failed to parse trailing run SSE data:', e, dataText)
      }
    }
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
  useRunsApi,
  currentAgentId,
  handleStreamChunk,
  processApprovalInStream,
  fetchThreadMessages,
  fetchAgentState,
  resetOnGoingConv,
  onScrollToBottom
}) {
  const saveActiveRunSnapshot = (threadId, runId, lastSeq = '0') => {
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
    if (ts.runStreamAbortController) {
      ts.runStreamAbortController.abort()
      ts.runStreamAbortController = null
    }
  }

  const startRunStream = async (threadId, runId, afterSeq = '0') => {
    if (!threadId || !runId || !useRunsApi) return
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

    try {
      const response = await agentApi.streamAgentRunEvents(runId, ts.runLastSeq, {
        signal: runController.signal
      })
      if (!response.ok) {
        throw new Error(`SSE response not ok: ${response.status}`)
      }

      await processRunSseResponse(response, (event, data) => {
        if (!data || ts.activeRunId !== runId) return

        if (data.seq !== undefined && data.seq !== null) {
          const incomingSeq = normalizeRunSeq(data.seq)
          if (compareRunSeq(incomingSeq, ts.runLastSeq) <= 0) return
          ts.runLastSeq = incomingSeq
          saveActiveRunSnapshot(threadId, runId, incomingSeq)
        }

        if (event === 'heartbeat') return

        const payload = data.payload || {}
        const isRetryableError = event === 'error' && payload?.chunk?.retryable === true
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
            handleStreamChunk(chunk, threadId)
          })
        } else if (payload.chunk) {
          handleStreamChunk(payload.chunk, threadId)
        }

        const approvalStatuses = ['ask_user_question_required', 'human_approval_required']
        const isApprovalEvent =
          approvalStatuses.includes(event) || approvalStatuses.includes(payload?.chunk?.status)

        if (isApprovalEvent) {
          const approvalChunk = payload?.chunk || { status: event, thread_id: threadId }
          processApprovalInStream(approvalChunk, threadId, unref(currentAgentId))
        }

        if (event === 'close') {
          ts.isStreaming = false
          if (RUN_TERMINAL_STATUSES.has(data.status)) {
            ts.activeRunId = null
            ts.lastRetryableJobTry = null
            clearActiveRunSnapshot(threadId)
            fetchThreadMessages({ agentId: unref(currentAgentId), threadId, delay: 200 }).finally(
              () => {
                fetchAgentState(unref(currentAgentId), threadId)
              }
            )
          } else if (ts.activeRunId === runId) {
            setTimeout(() => {
              if (ts.activeRunId === runId && !ts.runStreamAbortController) {
                void startRunStream(threadId, runId, ts.runLastSeq)
              }
            }, 300)
          }
        }

        const chunkStatus = payload?.chunk?.status
        if (
          event === 'finished' ||
          event === 'error' ||
          event === 'interrupted' ||
          approvalStatuses.includes(event) ||
          approvalStatuses.includes(chunkStatus)
        ) {
          ts.isStreaming = false
          ts.activeRunId = null
          ts.lastRetryableJobTry = null
          clearActiveRunSnapshot(threadId)
          fetchThreadMessages({ agentId: unref(currentAgentId), threadId, delay: 300 }).finally(() => {
            resetOnGoingConv(threadId)
            fetchAgentState(unref(currentAgentId), threadId)
            onScrollToBottom()
          })
        }
      })
    } catch (error) {
      if (error?.name !== 'AbortError') {
        console.error('Run SSE stream error:', error)
        handleChatError(error, 'stream')
        if (ts.activeRunId === runId) {
          setTimeout(() => {
            if (ts.activeRunId === runId && !ts.runStreamAbortController) {
              void startRunStream(threadId, runId, ts.runLastSeq)
            }
          }, 500)
        }
      }
    } finally {
      if (ts.runStreamAbortController === runController) {
        ts.runStreamAbortController = null
      }
      if (!ts.activeRunId) {
        ts.isStreaming = false
      }
    }
  }

  const resumeActiveRunForThread = async (threadId) => {
    if (!useRunsApi || !threadId) return
    const ts = getThreadState(threadId)
    if (!ts || ts.runStreamAbortController) return

    const snapshot = loadActiveRunSnapshot(threadId)
    if (snapshot?.run_id) {
      if (Date.now() - Number(snapshot.created_at || 0) > ACTIVE_RUN_STORAGE_TTL_MS) {
        clearActiveRunSnapshot(threadId)
      } else {
        try {
          const runRes = await agentApi.getAgentRun(snapshot.run_id)
          const run = runRes?.run
          if (run && !RUN_TERMINAL_STATUSES.has(run.status)) {
            await startRunStream(threadId, run.id, snapshot.last_seq || '0')
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
      if (run && !RUN_TERMINAL_STATUSES.has(run.status)) {
        await startRunStream(threadId, run.id, 0)
        return
      }
    } catch (e) {
      console.warn('Failed to load active run for thread:', threadId, e)
    }

    ts.activeRunId = null
    ts.runLastSeq = '0'
    ts.isStreaming = false
    clearActiveRunSnapshot(threadId)
  }

  return {
    startRunStream,
    resumeActiveRunForThread,
    stopRunStreamSubscription
  }
}
