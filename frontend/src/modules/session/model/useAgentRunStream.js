import { agentApi } from '@/apis'
import { handleChatError } from '@/shared/lib/errorHandler'
import { getReconnectDelay } from './reconnectBackoff.js'

export const processRunSseResponse = async (response, onEvent) => {
  if (!response || !response.body) return
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventType = 'message'
  let eventId = null
  let dataLines = []
  let idleTimer = null

  const dispatch = async () => {
    if (dataLines.length === 0) return
    const dataText = dataLines.join('\n')
    let parsed
    try {
      parsed = JSON.parse(dataText)
    } catch (e) {
      console.warn('Failed to parse run SSE data:', e, dataText)
      return
    }
    await onEvent(eventType, parsed, eventId)
  }

  try {
    while (true) {
      // 服务每 15 秒发送空闲心跳；半开连接失去字节后进入既有对账和重连。
      const { done, value } = await Promise.race([
        reader.read(),
        new Promise((_, reject) => {
          idleTimer = setTimeout(() => reject(new Error('会话事件连接超时')), 60000)
        })
      ])
      clearTimeout(idleTimer)
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const rawLine of lines) {
        const line = rawLine.replace(/\r$/, '')
        if (!line) {
          await dispatch()
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
  } finally {
    clearTimeout(idleTimer)
    try {
      await reader.cancel()
      reader.releaseLock()
    } catch {
      // ignore
    }
  }
}

export function useAgentRunStream({
  getThreadState,
  isThreadActive = () => true,
  handlePublicEvent,
  fetchThreadMessages,
  fetchAgentState,
  resetThreadExecution,
  onScrollToBottom,
  streamSmoother,
  onInterruptDetected = null,
  onTerminalDetected = null,
  onRunStarted = null
}) {
  const cancellationRequests = new Set()
  const stopRunStreamSubscription = (threadId) => {
    const ts = getThreadState(threadId)
    if (!ts) return
    streamSmoother?.flushThread(threadId)
    ts.runStreamAbortController?.abort()
    ts.runStreamAbortController = null
    clearTimeout(ts.runReconnectTimer)
    ts.runReconnectTimer = null
  }

  const settleTurn = async (threadId, turnId, status, runId, touchedThreadIds, turn = null) => {
    if (!isThreadActive(threadId)) return false
    const ts = getThreadState(threadId)
    if (!ts || ts.currentTurnId !== turnId) return false
    ts.runStateVersion = (ts.runStateVersion || 0) + 1
    const version = ts.runStateVersion
    ts.cooperationWaiting = false
    ts.cancellationPending = false
    streamSmoother?.flushThread(threadId)
    ts.runStreamAbortController?.abort()
    ts.runStreamAbortController = null
    ts.isStreaming = false
    ts.turnStatus = status
    ts.activeRunSteerable = false
    ts.replyLoadingVisible = false
    ts.pendingInputId = null
    ts.runReconnectAttempts = 0
    if (status === 'requires_action') {
      ts.activeRunId = runId
    } else {
      ts.activeRunId = null
      ts.currentTurnId = null
      ts.pendingInterrupt = null
    }
    let refreshed = false
    try {
      await fetchThreadMessages({ threadId, turnId, fresh: true })
      refreshed = true
    } catch (error) {
      console.warn('Failed to refresh terminal history; retaining streamed output:', error)
    }
    if (
      !isThreadActive(threadId) ||
      getThreadState(threadId) !== ts ||
      ts.runStateVersion !== version
    )
      return false
    if (status !== 'requires_action' && refreshed)
      resetThreadExecution(threadId, { preserveInputMonitors: true })
    void fetchAgentState(threadId)
    if (status === 'requires_action') onInterruptDetected?.({ threadId, runId, turn })
    else onTerminalDetected?.({ threadId, runId, touchedThreadIds: [...touchedThreadIds] })
    if (status === 'completed') onScrollToBottom?.(threadId)
    return refreshed
  }

  const reconcileTurn = async (threadId, turnId, fallbackRunId) => {
    const ts = getThreadState(threadId)
    const version = ts?.runStateVersion
    const turn = await agentApi.getThreadTurn(threadId, turnId)
    if (!isThreadActive(threadId)) return true
    if (!ts || getThreadState(threadId) !== ts || ts.currentTurnId !== turnId) return true
    if (ts.runStateVersion !== version) return false
    ts.runStateVersion = (ts.runStateVersion || 0) + 1
    const currentRunId = turn.yuxi.current_run_id || fallbackRunId
    ts.cancellationPending =
      cancellationRequests.has(turnId) || turn.yuxi.cancellation_pending === true
    if (
      ['requires_action', 'completed', 'failed', 'cancelled'].includes(turn.status) &&
      !(turn.status === 'requires_action' && ts.cancellationPending)
    ) {
      void settleTurn(threadId, turnId, turn.status, currentRunId, new Set([threadId]), turn)
      return true
    }
    ts.activeRunId = currentRunId
    ts.cooperationWaiting = turn.yuxi.waitpoint?.kind === 'cooperation'
    ts.turnStatus = turn.status
    ts.isStreaming = true
    ts.activeRunSteerable = !ts.cooperationWaiting && !ts.cancellationPending
    return false
  }

  const startRunStream = async (threadId, runId, afterCursor = null, options = {}) => {
    if (!threadId || !isThreadActive(threadId)) return
    const ts = getThreadState(threadId)
    if (!ts) return
    const version = ts.runStateVersion
    const turnId =
      options.turnId ||
      ts.currentTurnId ||
      (await agentApi.getPublicThread(threadId)).yuxi.current_turn?.id
    if (
      !turnId ||
      !isThreadActive(threadId) ||
      getThreadState(threadId) !== ts ||
      ts.runStateVersion !== version
    )
      return
    if (ts.currentTurnId === turnId && ts.activeRunId === runId && ts.runStreamAbortController)
      return

    stopRunStreamSubscription(threadId)
    ts.runStateVersion = (ts.runStateVersion || 0) + 1
    const controller = new AbortController()
    ts.runStreamAbortController = controller
    ts.cooperationWaiting = ts.currentTurnId === turnId && !!ts.cooperationWaiting
    ts.cancellationPending = ts.currentTurnId === turnId && !!ts.cancellationPending
    ts.currentTurnId = turnId
    ts.activeRunId = runId
    ts.turnStatus = 'in_progress'
    ts.pendingInterrupt = null
    ts.activeRunSteerable = !ts.cooperationWaiting && !ts.cancellationPending
    ts.isStreaming = true
    onRunStarted?.({ threadId, runId })

    let sawTurnEnd = false
    try {
      if (afterCursor || ts.threadCursor) {
        sawTurnEnd = await reconcileTurn(threadId, turnId, runId)
        if (sawTurnEnd) return
      }
      if (controller.signal.aborted || ts.runStreamAbortController !== controller) return
      const response = await agentApi.streamThreadEvents(threadId, afterCursor || ts.threadCursor, {
        signal: controller.signal
      })
      if (!response.ok) throw new Error(`SSE response not ok: ${response.status}`)
      await processRunSseResponse(response, async (_event, data, eventId) => {
        if (!data || controller.signal.aborted || ts.currentTurnId !== turnId) return
        ts.runReconnectAttempts = 0
        if (data.session_id !== threadId) return
        if (data.type === 'yuxi.session.resync') {
          // 暂停消费，让 ReadableStream 缓冲新事件，快照应用后再继续合并。
          await fetchThreadMessages({ threadId, turnId, fresh: true })
          sawTurnEnd = await reconcileTurn(threadId, turnId, ts.activeRunId)
          if (eventId) ts.threadCursor = String(eventId)
          return
        }
        if (
          data.type === 'yuxi.session.input.accepted' &&
          data.yuxi?.input_type === 'agent.session.input.cancel' &&
          data.yuxi?.turn_id === turnId
        ) {
          ts.runStateVersion = (ts.runStateVersion || 0) + 1
          ts.cancellationPending = true
          ts.activeRunSteerable = false
        }
        if (data.turn_id !== turnId) {
          if (eventId) ts.threadCursor = String(eventId)
          return
        }
        const status = data.turn?.status
        // 取消请求与旧等待事件可以交错；只由整轮终态结束停止反馈。
        if (status === 'requires_action' && ts.cancellationPending) {
          if (eventId) ts.threadCursor = String(eventId)
          return
        }
        const owner = data.yuxi?.current_run_id || data.current_run_id
        if (
          ['requires_action', 'completed', 'failed', 'cancelled', 'in_progress', 'queued'].includes(
            status
          )
        ) {
          if (owner && data.yuxi?.run_id !== owner) return
          // 同一 SSE 会跨协作恢复的多个 Run，事件进展也必须使旧快照失效。
          ts.runStateVersion = (ts.runStateVersion || 0) + 1
          const eventVersion = ts.runStateVersion
          if (data.yuxi?.run_id !== ts.activeRunId) {
            const turn = await agentApi.getThreadTurn(threadId, turnId)
            if (controller.signal.aborted || ts.runStateVersion !== eventVersion) return
            ts.activeRunId = turn.yuxi.current_run_id
            if (data.yuxi?.run_id !== turn.yuxi.current_run_id) return
          }
        }
        if (['in_progress', 'queued'].includes(status)) {
          ts.pendingInterrupt = null
          ts.cooperationWaiting = data.waitpoint?.kind === 'cooperation'
          ts.turnStatus = status
          ts.isStreaming = true
          ts.activeRunSteerable = !ts.cooperationWaiting && !ts.cancellationPending
        }
        handlePublicEvent(data, threadId)
        if (eventId) ts.threadCursor = String(eventId)
        if (['requires_action', 'completed', 'failed', 'cancelled'].includes(status)) {
          sawTurnEnd = true
          void settleTurn(
            threadId,
            turnId,
            status,
            data.yuxi?.run_id || ts.activeRunId,
            new Set([threadId])
          )
        }
      })
      if (!sawTurnEnd && !controller.signal.aborted) {
        sawTurnEnd = await reconcileTurn(threadId, turnId, runId)
      }
    } catch (error) {
      if (error?.name !== 'AbortError' && !controller.signal.aborted) {
        console.error('Thread SSE stream error:', error)
        try {
          sawTurnEnd = await reconcileTurn(threadId, turnId, runId)
        } catch (lookupError) {
          console.warn('Failed to reconcile Thread after SSE error:', lookupError)
        }
        if (!sawTurnEnd) handleChatError(error, 'stream')
      }
    } finally {
      if (ts.runStreamAbortController === controller) ts.runStreamAbortController = null
      if (!sawTurnEnd && !controller.signal.aborted && ts.currentTurnId === turnId) {
        const reconnectAttempt = ts.runReconnectAttempts || 0
        ts.runReconnectAttempts = reconnectAttempt + 1
        ts.runReconnectTimer = setTimeout(() => {
          ts.runReconnectTimer = null
          if (
            !controller.signal.aborted &&
            ts.currentTurnId === turnId &&
            !ts.runStreamAbortController
          ) {
            void startRunStream(threadId, ts.activeRunId, ts.threadCursor, { turnId })
          }
        }, getReconnectDelay(reconnectAttempt))
      }
    }
  }

  /** 恢复持久运行事实；终态等历史收尾，运行态只启动订阅。 */
  const resumeActiveRunForThread = async (threadId, { refreshHistory = false, session = null, onRestored } = {}) => {
    if (!threadId || !isThreadActive(threadId)) return false
    const ts = getThreadState(threadId)
    if (!ts) return false
    const version = ts.runStateVersion
    const thread = session || await agentApi.getPublicThread(threadId)
    if (
      !isThreadActive(threadId) ||
      getThreadState(threadId) !== ts ||
      ts.runStateVersion !== version
    )
      return false
    const turnId = thread.yuxi.current_turn?.id
    if (!turnId) {
      stopRunStreamSubscription(threadId)
      ts.cooperationWaiting = false
      ts.cancellationPending = false
      ts.activeRunId = null
      ts.currentTurnId = null
      ts.turnStatus = null
      ts.isStreaming = false
      ts.pendingInterrupt = null
      ts.activeRunSteerable = false
      if (refreshHistory) await fetchThreadMessages({ threadId, fresh: true })
      if (!isThreadActive(threadId) || getThreadState(threadId) !== ts || ts.runStateVersion !== version)
        return false
      onRestored?.(null)
      return true
    }
    const turn = await agentApi.getThreadTurn(threadId, turnId)
    if (
      !isThreadActive(threadId) ||
      getThreadState(threadId) !== ts ||
      ts.runStateVersion !== version
    )
      return false
    if (ts.currentTurnId !== turnId) stopRunStreamSubscription(threadId)
    ts.runStateVersion = (ts.runStateVersion || 0) + 1
    ts.cancellationPending =
      cancellationRequests.has(turnId) || turn.yuxi.cancellation_pending === true
    ts.currentTurnId = turnId
    ts.activeRunId = turn.yuxi.current_run_id
    ts.turnStatus = turn.status
    if (
      ['queued', 'in_progress'].includes(turn.status) ||
      (turn.status === 'requires_action' && ts.cancellationPending)
    ) {
      ts.cooperationWaiting = turn.yuxi.waitpoint?.kind === 'cooperation'
      ts.isStreaming = true
      ts.activeRunSteerable = !ts.cooperationWaiting && !ts.cancellationPending
      if (!ts.runStreamAbortController) {
        void startRunStream(threadId, turn.yuxi.current_run_id, ts.threadCursor, { turnId })
      }
      const restoredVersion = ts.runStateVersion
      if (refreshHistory) await fetchThreadMessages({ threadId, fresh: true })
      if (!isThreadActive(threadId) || getThreadState(threadId) !== ts || ts.runStateVersion !== restoredVersion)
        return false
      onRestored?.(turn)
      return true
    }
    const restored = await settleTurn(threadId, turnId, turn.status, turn.yuxi.current_run_id, new Set([threadId]), turn)
    if (restored) onRestored?.(turn)
    return restored
  }

  const requestTurnCancellation = async (threadId) => {
    const ts = getThreadState(threadId)
    if (!ts?.activeRunId || !ts.currentTurnId || ts.cancellationPending) return false
    const turnId = ts.currentTurnId
    const runId = ts.activeRunId
    ts.runStateVersion = (ts.runStateVersion || 0) + 1
    ts.cancellationPending = true
    ts.activeRunSteerable = false
    cancellationRequests.add(turnId)
    let accepted = false
    try {
      await agentApi.cancelThreadTurn(threadId, turnId, `cancel-${turnId}`, runId)
      accepted = true
    } catch (error) {
      handleChatError(error, 'stop')
    } finally {
      cancellationRequests.delete(turnId)
    }
    // 响应丢失也可能已经提交取消；只用持久快照解除本地请求等待。
    try {
      await reconcileTurn(threadId, turnId, runId)
    } catch (error) {
      console.warn('Failed to reconcile cancellation; retaining stop feedback:', error)
    }
    if (
      getThreadState(threadId) === ts &&
      ts.currentTurnId === turnId &&
      (ts.isStreaming || ts.cancellationPending) &&
      !ts.runStreamAbortController
    ) {
      void startRunStream(threadId, ts.activeRunId, ts.threadCursor, { turnId })
    }
    return accepted
  }

  return {
    startRunStream,
    resumeActiveRunForThread,
    stopRunStreamSubscription,
    requestTurnCancellation
  }
}
