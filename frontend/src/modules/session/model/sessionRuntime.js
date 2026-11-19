import { reactive, ref, computed, watch, onScopeDispose } from 'vue'
import { defineStore } from 'pinia'
import { agentApi } from '@/apis'
import { useUserStore } from '@/modules/identity'
import { handleChatError } from '@/shared/lib/errorHandler'
import { useChatThreadsStore } from './chatThreads'
import { useAgentThreadState } from './useAgentThreadState'
import { useAgentRunStream } from './useAgentRunStream'
import { useAgentInputQueue } from './useAgentInputQueue'
import { useAgentStreamHandler } from './useAgentStreamHandler'
import { useStreamSmoother } from './useStreamSmoother'
import { getStreamingReasoning, itemsToMessages, mergeItemSnapshot } from './agentItems'
import { extractPendingInterrupt, pendingInterruptFromWaitpoint } from './useApproval'
import { useSessionFeedbackStore } from './sessionFeedback'

/** 协作摘要与首读使用相同的轮次、执行段和等待状态进行核对。 */
const executionSnapshot = (turnId, status, currentRunId, resultRunId, runStatus, waitingFor) =>
  JSON.stringify([turnId, status, currentRunId, resultRunId, runStatus, waitingFor])

/** 两次会话详情必须指向同一状态、执行段和正式结果。 */
const sameExecution = (a, b) =>
  a?.id === b?.id && a?.status === b?.status &&
  a?.current_run_id === b?.current_run_id && a?.result_run_id === b?.result_run_id

const isSettled = (status) => ['completed', 'failed', 'cancelled'].includes(status)

/** 每个 Thread 共享运行数据和订阅，视图仅注册展示回调。 */
export const useSessionRuntimeStore = defineStore('sessionRuntime', () => {
  const user = useUserStore()
  const chatThreads = useChatThreadsStore()
  const feedback = useSessionFeedbackStore()
  const chatState = reactive({ threadStates: {} })
  const threadMessages = computed(() =>
    Object.fromEntries(
      Object.entries(chatState.threadStates)
        .filter(([, state]) => state.historyLoaded || Object.keys(state.itemState.items).length)
        .map(([id, state]) => [id, itemsToMessages(
          Object.values(state.itemState.items), getStreamingReasoning(state)
        )])
    )
  )
  const threadRuns = ref({})
  const historyPages = reactive({})
  const pendingSends = reactive({})
  const observers = new Map()
  const requests = new Map()
  const cooperationSnapshots = new Map()
  const streamSmoother = useStreamSmoother({
    getThreadState: (id) => chatState.threadStates[id] || null
  })
  const { getThreadState, cleanupThreadState, resetThreadExecution } = useAgentThreadState({
    chatState,
    onBeforeResetThread: streamSmoother.resetThread,
    onBeforeCleanupThread: streamSmoother.resetThread
  })

  /** 同一类在途读取共用 Promise，释放视图后旧结果不能写回新状态。 */
  const once = (key, action) => {
    if (requests.has(key)) return requests.get(key)
    const request = Promise.resolve()
      .then(action)
      .finally(() => {
        if (requests.get(key) === request) requests.delete(key)
      })
    requests.set(key, request)
    return request
  }
  const notify = (name, payload) => {
    for (const observer of observers.get(payload.threadId) || []) observer[name]?.(payload)
  }
  const isCurrentThreadState = (threadId, state) => chatState.threadStates[threadId] === state

  const fetchThreadMessages = async ({ threadId, fresh = false, more = false, turnId = null, initialRead = false }) => {
    if (!threadId || !observers.has(threadId)) return
    const state = getThreadState(threadId)
    const key = `history:${threadId}`
    while ((fresh || initialRead) && requests.has(key)) {
      // 完成事件要读更新的快照；首次会话窗口也不能借用在途的单轮历史。
      try {
        await requests.get(key)
      } catch {
        /* 新请求负责重试。 */
      }
      if (!observers.has(threadId) || !isCurrentThreadState(threadId, state)) return
    }
    return once(key, async () => {
      if (!observers.has(threadId)) return
      const state = getThreadState(threadId)
      try {
        const projection = state.itemState
        const previousIds = new Set(Object.keys(projection.items))
        const paging = (historyPages[threadId] ||= { after: null, hasMore: false, loading: false })
        paging.loading = true
        let after = more ? paging.after : undefined
        let pagesRead = 0
        const readPage = (scope, cursor) => agentApi.getSessionItems(threadId, { after: cursor, turnId: scope, limit: 100 })
        // 首读先确认轮次已结束，再读取可复用的历史；真正的完成事件仍需单独回读。
        const [session, initialPage] = await Promise.all([
          agentApi.getPublicThread(threadId),
          initialRead ? null : readPage(turnId, after)
        ])
        if (!isCurrentThreadState(threadId, state)) return
        const current = session.yuxi.current_turn
        const settled = initialRead && isSettled(current?.status) ? current : null
        let page = initialPage || await readPage(turnId, after)
        let oldestTurnItem
        const runs = new Map((threadRuns.value[threadId] || []).map((run) => [run.id, run]))
        /** 公开历史页同时携带消息和所属 Run。 */
        const mergePage = (page) => {
          mergeItemSnapshot(projection, page.data)
          for (const run of page.yuxi.runs) runs.set(run.id, run)
        }
        while (true) {
          if (!isCurrentThreadState(threadId, state)) return
          mergePage(page)
          if (settled) oldestTurnItem = page.data.findLast((item) => item.turn_id === settled.id)?.id ?? oldestTurnItem
          after = page.last_id
          pagesRead++
          // 恢复目标轮次读取完整页；Session 刷新读到已加载区域即可。
          const recoverGap = !more && (!initialRead || paging.after) &&
            previousIds.size && !page.data.some((item) => previousIds.has(item.id))
          if (!page.has_more || (!turnId && !recoverGap && pagesRead >= 3)) break
          page = await readPage(turnId, after)
        }
        if (settled && page.has_more) {
          // 两种范围使用相同排序，但 Turn 游标必须属于本轮；Session 游标保留在普通窗口末尾。
          let tail
          do {
            tail = await readPage(settled.id, oldestTurnItem)
            if (!isCurrentThreadState(threadId, state)) return
            mergePage(tail)
            oldestTurnItem = tail.last_id
          } while (tail.has_more)
        }
        const target = turnId || session.yuxi.current_turn?.id
        let turn
        if (target && !more) {
          turn = await agentApi.getThreadTurn(threadId, target)
          if (!isCurrentThreadState(threadId, state)) return
          mergeItemSnapshot(projection, turn.yuxi.output)
          for (const run of turn.yuxi.runs || []) runs.set(run.id, run)
        }
        if (!turnId && (more || !paging.after || !page.has_more)) {
          paging.after = page.last_id
          paging.hasMore = page.has_more
        }
        if (!turnId) state.historyLoaded = true
        streamSmoother.flushThread(threadId)
        void feedback.load(
          threadId,
          (threadMessages.value[threadId] || [])
            .filter(
              (message) =>
                message.phase === 'final_answer' && message.execution_status === 'completed'
            )
            .map((message) => message.run_id),
          fresh || Boolean(settled)
        )
        threadRuns.value[threadId] = [...runs.values()]
        if (!state.isStreaming) state.turnStatus = session.yuxi.current_turn?.status || null
        chatThreads.upsertThread(session)
        if (initialRead) return { current, settled: settled ? turn : null }
      } catch (error) {
        handleChatError(error, 'load')
        throw error
      } finally {
        if (isCurrentThreadState(threadId, state) && historyPages[threadId])
          historyPages[threadId].loading = false
      }
    })
  }

  const fetchAgentState = async (threadId, { required = false } = {}) => {
    const success = await once(`state:${threadId}`, async () => {
      if (!threadId) return false
      const state = getThreadState(threadId)
      const runVersion = state.runStateVersion
      const version = (state.agentStateRequestVersion || 0) + 1
      state.agentStateRequestVersion = version
      try {
        const response = await agentApi.getAgentState(threadId, { includeRelations: false })
        if (
          !isCurrentThreadState(threadId, state) ||
          state.agentStateRequestVersion !== version ||
          state.runStateVersion !== runVersion
        )
          return false
        state.agentState = response.agent_state || null
        const interrupt = extractPendingInterrupt(response.interrupt, threadId)
        if (
          interrupt &&
          !state.isStreaming &&
          (!interrupt.interruptedRunId ||
            !state.activeRunId ||
            interrupt.interruptedRunId === state.activeRunId)
        ) {
          state.pendingInterrupt = interrupt
          notify('onInterruptDetected', { threadId })
        }
        return true
      } catch (error) {
        if (required) throw error
        return false
      }
    })
    if (required && (!success || !getThreadState(threadId)?.pendingInterrupt)) {
      throw new Error('checkpoint 中没有可恢复的审批状态')
    }
    return success
  }

  const { handlePublicEvent } = useAgentStreamHandler({
    getThreadState,
    streamSmoother,
    processApprovalInStream: (event, threadId) => {
      const state = getThreadState(threadId)
      state.pendingInterrupt = pendingInterruptFromWaitpoint(event.waitpoint, threadId)
      notify('onInterruptDetected', { threadId })
      return Boolean(state.pendingInterrupt)
    }
  })
  const stream = useAgentRunStream({
    getThreadState,
    isThreadActive: (threadId) => observers.has(threadId),
    handlePublicEvent: (event, threadId) => {
      const result = handlePublicEvent(event, threadId)
      if (event.turn && event.session_id === threadId) {
        chatThreads.upsertThread({
          id: threadId,
          status: event.turn.status === 'queued' ? 'in_progress' : event.turn.status
        })
      }
      return result
    },
    fetchThreadMessages,
    fetchAgentState,
    resetThreadExecution,
    streamSmoother,
    onScrollToBottom: (threadId) => notify('onScrollToBottom', { threadId }),
    onInterruptDetected: ({ threadId, turn }) => {
      void (async () => {
        const state = getThreadState(threadId)
        const turnId = state.currentTurnId
        const version = state.runStateVersion
        const waiting = turn || (turnId && (await agentApi.getThreadTurn(threadId, turnId)))
        if (
          !isCurrentThreadState(threadId, state) ||
          state.currentTurnId !== turnId ||
          state.runStateVersion !== version ||
          waiting?.status !== 'requires_action'
        )
          return
        state.pendingInterrupt = pendingInterruptFromWaitpoint(waiting.yuxi.waitpoint, threadId)
        notify('onInterruptDetected', { threadId })
      })().catch((error) => console.warn('Failed to restore Turn waitpoint:', error))
      void resumeQueuedInputs(threadId)
    },
    onTerminalDetected: (payload) => {
      void resumeQueuedInputs(payload.threadId)
      notify('onTerminalDetected', payload)
    },
    onRunStarted: ({ threadId }) => {
      chatThreads.upsertThread({ id: threadId, status: 'in_progress' })
    }
  })
  const queue = useAgentInputQueue({
    getThreadState,
    isThreadActive: (threadId) => observers.has(threadId),
    onInputConsumed: (threadId) => resumeActiveRunForThread(threadId, { fresh: true })
  })
  const resumeQueuedInputs = (threadId) =>
    once(`queue:${threadId}`, () => queue.resumeQueuedInputs(threadId))
  /** 摘要更新后等待旧恢复结束，再通过同一入口读取新的持久快照。 */
  const resumeActiveRunForThread = async (threadId, { fresh = false, initialHistory = null, cooperation = false } = {}) => {
    if (!threadId || !observers.has(threadId)) return false
    const state = getThreadState(threadId)
    const key = `resume:${threadId}`
    while ((initialHistory || fresh) && requests.has(key)) {
      try {
        await requests.get(key)
      } catch {
        /* 新请求负责重试。 */
      }
      if (!observers.has(threadId) || !isCurrentThreadState(threadId, state)) return false
    }
    return once(key, async () => {
      const lastSettled = cooperation ? cooperationSnapshots.get(threadId)?.settled : null
      if (!initialHistory && !lastSettled) return stream.resumeActiveRunForThread(threadId, { refreshHistory: fresh })
      // 摘要只复用实际恢复的状态；读取历史后轮次可能已推进。
      const onRestored = (turn) => {
        if (!isCurrentThreadState(threadId, state)) return
        const execution = turn?.yuxi
        if (execution?.waitpoint || execution?.cancellation_pending) return
        const running = ['in_progress', 'queued'].includes(turn?.status)
        const { current_run_id: runId, result_run_id: resultId } = execution || {}
        const run = execution?.runs?.find((run) => run.id === runId)
        const snapshot = executionSnapshot(turn?.id, running ? 'running' : turn?.status, runId, resultId, run?.status, null)
        const settled = isSettled(turn?.status) ? { id: turn.id, status: turn.status, current_run_id: runId, result_run_id: resultId } : null
        cooperationSnapshots.set(threadId, { snapshot, settled })
      }
      const version = state.runStateVersion
      const session = await agentApi.getPublicThread(threadId)
      if (!isCurrentThreadState(threadId, state) || state.runStateVersion !== version) return false
      if (!initialHistory) {
        // 摘要可能来自旧执行，已经读取的终态以当前持久详情为准。
        if (!state.currentTurnId && !state.isStreaming && sameExecution(lastSettled, session.yuxi.current_turn)) return false
        return stream.resumeActiveRunForThread(threadId, { refreshHistory: fresh, session, onRestored })
      }
      if (!sameExecution(initialHistory.current, session.yuxi.current_turn)) {
        // 新轮次的用户输入只在历史页提供正文，再按最新详情恢复订阅。
        await fetchThreadMessages({ threadId, fresh: true })
        if (!isCurrentThreadState(threadId, state) || state.runStateVersion !== version) return false
        return stream.resumeActiveRunForThread(threadId, { onRestored })
      }
      // 打开已完整读取的旧终态不会产生新的流式完成事件。
      if (initialHistory.settled && !state.currentTurnId && !state.isStreaming) {
        onRestored(initialHistory.settled)
        return true
      }
      return stream.resumeActiveRunForThread(threadId, { session, onRestored })
    })
  }

  /** 阅读者只指定 Thread；首读、持久运行恢复和队列观察共享一次装载。 */
  const loadThread = (threadId, { fresh = false } = {}) => {
    if (!threadId || !observers.has(threadId)) return Promise.resolve(false)
    const state = getThreadState(threadId)
    if (state.readingReady && !fresh) return Promise.resolve(true)
    return once(`reading:${threadId}`, async () => {
      const readHistory = !state.historyLoaded || (!state.readingReady && state.readingError)
      state.readingLoading = true
      state.readingError = null
      try {
        const version = state.runStateVersion
        // 初始化失败后重读会话窗口，补上期间提交的新输入。
        const initialHistory = readHistory ? await fetchThreadMessages({ threadId, initialRead: true }) : null
        if (!isCurrentThreadState(threadId, state)) return false
        await resumeActiveRunForThread(threadId, {
          fresh: fresh && state.readingReady,
          initialHistory: state.runStateVersion === version ? initialHistory : null
        })
        if (!isCurrentThreadState(threadId, state)) return false
        await resumeQueuedInputs(threadId)
        if (!isCurrentThreadState(threadId, state)) return false
        state.readingReady = true
        return true
      } catch (error) {
        if (isCurrentThreadState(threadId, state)) state.readingError = error
        throw error
      } finally {
        if (isCurrentThreadState(threadId, state)) state.readingLoading = false
      }
    })
  }

  /** 摘要只触发已打开成员回读详情，不覆盖共享运行事实。 */
  const reconcileCooperationSessions = async (sessions) => {
    await Promise.all(sessions.map(async (session) => {
      const threadId = session.session_id
      if (!observers.has(threadId)) return
      const state = getThreadState(threadId)
      const snapshot = executionSnapshot(
        session.turn_id, session.turn_status, session.current_run_id, session.result_run_id,
        session.run_status, session.waiting_for
      )
      await once(`cooperation:${threadId}`, async () => {
        try {
          const opening = !state.readingReady && requests.get(`reading:${threadId}`)
          // 首次历史后还会读队列。其余摘要即使执行没变，也要发现新排队的 Input。
          if (!opening || !(await opening.catch(() => false))) await resumeQueuedInputs(threadId)
          if (!isCurrentThreadState(threadId, state)) return
          const applied = cooperationSnapshots.get(threadId)
          if (applied?.snapshot === snapshot) return
          if (!(await resumeActiveRunForThread(threadId, { fresh: true, cooperation: true }))) return
          if (isCurrentThreadState(threadId, state) && cooperationSnapshots.get(threadId) === applied) {
            cooperationSnapshots.set(threadId, { snapshot })
          }
        } catch (error) {
          console.warn('Failed to reconcile cooperation member:', error)
        }
      })
    }))
  }

  /** 最后一个消费者释放时终止观察；读取视图和在途提交共享同一状态。 */
  const observeThread = (threadId, observer) => {
    if (!threadId) return () => {}
    if (!observers.has(threadId)) observers.set(threadId, new Set())
    const listeners = observers.get(threadId)
    listeners.add(observer)
    getThreadState(threadId)
    return () => {
      listeners.delete(observer)
      if (listeners.size || observers.get(threadId) !== listeners) return
      observers.delete(threadId)
      cooperationSnapshots.delete(threadId)
      cleanupThreadState(threadId)
      delete threadRuns.value[threadId]
      delete historyPages[threadId]
      for (const key of requests.keys()) {
        if (key.endsWith(`:${threadId}`)) requests.delete(key)
      }
    }
  }
  /** 身份切换使旧状态对象失效，迟到的历史、恢复和事件不能写入新身份。 */
  const clearRuntime = () => {
    for (const id of Object.keys(chatState.threadStates)) cleanupThreadState(id)
    threadRuns.value = {}
    for (const id of Object.keys(historyPages)) delete historyPages[id]
    for (const id of Object.keys(pendingSends)) delete pendingSends[id]
    observers.clear()
    cooperationSnapshots.clear()
    requests.clear()
  }
  watch(() => user.token, clearRuntime, { flush: 'sync' })
  onScopeDispose(clearRuntime)
  return {
    threadMessages,
    threadRuns,
    historyPages,
    pendingSends,
    getThreadState,
    isCurrentThreadState,
    resetThreadExecution,
    fetchThreadMessages,
    fetchAgentState,
    loadThread,
    observeThread,
    startRunStream: stream.startRunStream,
    requestTurnCancellation: stream.requestTurnCancellation,
    resumeActiveRunForThread,
    resumeQueuedInputs,
    reconcileCooperationSessions,
    startInputMonitor: queue.startInputMonitor,
    cancelInput: queue.cancelInput,
    promoteInput: queue.promoteInput,
    continueQueue: queue.continueQueue
  }
})
