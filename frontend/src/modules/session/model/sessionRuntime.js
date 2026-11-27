import { reactive, ref, computed, watch, onScopeDispose } from 'vue'
import { defineStore } from 'pinia'
import { agentApi } from '@/apis'
import { useUserStore } from '@/modules/identity'
import { handleChatError } from '@/shared/lib/errorHandler'
import { useChatThreadsStore } from './chatThreads'
import { useAgentThreadState } from './useAgentThreadState'
import { isCooperationCancellationEvent, useAgentRunStream } from './useAgentRunStream'
import { useAgentInputQueue } from './useAgentInputQueue'
import { useAgentStreamHandler } from './useAgentStreamHandler'
import { useStreamSmoother } from './useStreamSmoother'
import { getStreamingReasoning, itemsToMessages, mergeItemSnapshot } from './agentItems'
import { pendingInterruptFromWaitpoint } from './toolApproval'
import { useSessionFeedbackStore } from './sessionFeedback'
import { isCooperationWaitRun } from './runProcessGrouping'

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
  const historyReads = new Map()
  const cooperationSnapshots = new Map()
  let detailVersion = 0
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
  const peekThreadState = (threadId) => chatState.threadStates[threadId] || null

  /** 较早发起的详情读取不能覆盖更新的读取或修改回执。 */
  const publishThreadDetail = (threadId, detail, state, version) => {
    if (!state || !isCurrentThreadState(threadId, state) || detail?.id !== threadId || version < state.detailVersion)
      return false
    state.detail = detail
    state.detailVersion = version
    return true
  }

  /** 创建回执建立详情，配置和归档回执只更新会话元数据。 */
  const applyThreadDetail = (threadId, detail, state = peekThreadState(threadId)) => {
    if (detail?.id !== threadId) return false
    if (!state?.detail) return publishThreadDetail(threadId, detail, state, ++detailVersion)
    const { title, is_pinned, archived, tool_approval_mode } = detail.yuxi
    return publishThreadDetail(threadId, {
      ...state.detail,
      agent: detail.agent,
      yuxi: { ...state.detail.yuxi, title, is_pinned, archived, tool_approval_mode }
    }, state, ++detailVersion)
  }

  /** 已查看回执只更新未读标记，旧详情读取仍能发布其他字段。 */
  const applyThreadViewed = (threadId, detail, state) => {
    if (!state?.detail || !isCurrentThreadState(threadId, state) || detail?.id !== threadId) return
    const { unread } = detail.yuxi
    state.viewedVersion = ++detailVersion
    state.detail = { ...state.detail, yuxi: { ...state.detail.yuxi, unread } }
    chatThreads.updateThreadSummary(threadId, { yuxi: { unread } })
  }

  /** 详情与侧栏共享授权结果；侧栏只刷新已加载条目的摘要。 */
  const readThreadDetail = async (threadId, state) => {
    const version = ++detailVersion
    const runVersion = state.runStateVersion
    const detail = await agentApi.getPublicThread(threadId)
    const unread = state.viewedVersion > version ? state.detail.yuxi.unread : detail.yuxi.unread
    if (publishThreadDetail(threadId, { ...detail, yuxi: { ...detail.yuxi, unread } }, state, version)) {
      const { title, is_pinned, archived } = detail.yuxi
      chatThreads.updateThreadSummary(threadId, {
        ...(state.runStateVersion === runVersion ? { status: detail.status } : {}),
        yuxi: { title, unread, is_pinned, archived }
      })
    }
    return detail
  }

  /** 不同读取范围各自执行，按请求顺序向同一 item 投影发布结果。 */
  const scheduleHistoryRead = (threadId, read, sharedKind) => {
    if (!observers.has(threadId)) return Promise.resolve()
    const state = peekThreadState(threadId)
    const key = sharedKind && `${sharedKind}:${threadId}`
    if (requests.has(key)) return requests.get(key)
    const previous = historyReads.get(threadId)
    const execute = async () => {
      await previous?.catch(() => {})
      if (!isCurrentThreadState(threadId, state)) return
      try {
        return await read(state)
      } catch (error) {
        if (isCurrentThreadState(threadId, state)) handleChatError(error, 'load')
        throw error
      }
    }
    const pending = (key ? once(key, execute) : execute()).finally(() => {
      if (historyReads.get(threadId) === pending) historyReads.delete(threadId)
    })
    historyReads.set(threadId, pending)
    return pending
  }

  const readPage = (threadId, after, turnId) => agentApi.getSessionItems(threadId, { after, turnId, limit: 100 })

  /** 持久内容与所属 Run 一起合并；正式结果来自明确的 Turn 快照。 */
  const createHistoryMerge = (threadId, state) => {
    const runs = new Map((threadRuns.value[threadId] || []).map((run) => [run.id, run]))
    return {
      items(items, pageRuns) {
        mergeItemSnapshot(state.itemState, items)
        for (const run of pageRuns || []) runs.set(run.id, run)
      },
      finish(fresh = false) {
        const mergedRuns = [...runs.values()]
        threadRuns.value[threadId] = mergedRuns
        streamSmoother.flushThread(threadId)
        readHistoricalCooperationTurns(threadId, state, mergedRuns)
        void feedback.load(threadId, (threadMessages.value[threadId] || [])
          .filter((message) => message.phase === 'final_answer' && message.execution_status === 'completed')
          .map((message) => message.run_id), fresh)
      }
    }
  }

  /** 保留单轮接口已确认的协作终态，供当前和历史消息读取。 */
  const rememberSettledCooperationTurn = (state, turn) => {
    if (state.settledCooperationTurns.has(turn.id)) return
    if (!isSettled(turn.status) || !turn.yuxi.runs?.some(isCooperationWaitRun)) return
    state.settledCooperationTurns.set(turn.id, {
      id: turn.id, status: turn.status, current_run_id: turn.yuxi.current_run_id
    })
  }

  /** 旧协作等待段保留原 Run 状态，按明确 Turn 回读历史终态。 */
  const readHistoricalCooperationTurns = (threadId, state, runs) => {
    const currentTurnId = state.detail?.yuxi.current_turn?.id
    if (!currentTurnId) return

    let resumedRunIds
    for (const run of runs) {
      const turnId = run.turn_id
      if (!isCooperationWaitRun(run) || !turnId || turnId === currentTurnId || state.settledCooperationTurns.has(turnId)) continue

      // 已有续跑快照时，原等待段已不再是该轮当前 Run。
      resumedRunIds ??= new Set(runs.map((run) => run.resume_from_run_id))
      if (resumedRunIds.has(run.id)) continue

      void once(`cooperation-history:${turnId}:${threadId}`, async () => {
        const turn = await agentApi.getThreadTurn(threadId, turnId)
        if (!isCurrentThreadState(threadId, state) || turn.id !== turnId) return
        rememberSettledCooperationTurn(state, turn)
      }).catch(() => {
        // API 层记录读取失败，后续历史读取可重试，当前消息仍可阅读。
      })
    }
  }

  /** Turn 游标只属于指定轮，依序读取这一轮的剩余项目。 */
  const readTurnHistory = async (threadId, state, merge, turnId, after) => {
    let page
    do {
      page = await readPage(threadId, after, turnId)
      if (!isCurrentThreadState(threadId, state)) return
      merge.items(page.data, page.yuxi.runs)
      after = page.last_id
    } while (page.has_more)
  }

  /** 读取会话窗口，补齐断开期间的缺口，或沿会话游标向前翻页。 */
  const readThreadWindow = async (threadId, state, merge, { settled, append = false } = {}) => {
    const paging = (historyPages[threadId] ||= { after: null, hasMore: false, loading: false, newest: null })
    let after = append ? paging.after : undefined
    const previousHead = append ? null : paging.newest
    let reachedWindow = !previousHead
    let windowHead
    let oldestTurnItem
    let page
    let pagesRead = 0
    do {
      page = await readPage(threadId, after)
      if (!isCurrentThreadState(threadId, state)) return
      merge.items(page.data, page.yuxi.runs)
      windowHead ??= page.data[0]?.id
      reachedWindow ||= page.data.some((item) => item.id === previousHead)
      if (settled) oldestTurnItem = page.data.findLast((item) => item.turn_id === settled.id)?.id ?? oldestTurnItem
      after = page.last_id
      pagesRead++
    } while (page.has_more && (!reachedWindow || pagesRead < 3))
    return { page, windowHead, oldestTurnItem }
  }

  /** 仅在会话范围内提交分页位置，指定 Turn 内容可在同一投影独立补齐。 */
  const finishThreadWindow = (threadId, state, { page, windowHead }, append = false) => {
    const paging = historyPages[threadId]
    if (append || !paging.after || !page.has_more) {
      paging.after = page.last_id
      paging.hasMore = page.has_more
    }
    if (!append) paging.newest = windowHead ?? null
    state.historyLoaded = true
  }

  /** 首读确认当前 Thread 和窗口，再决定是否补齐已结束的当前轮。 */
  const readLatestWindow = (threadId, opening = false) => scheduleHistoryRead(threadId, async (state) => {
    const merge = createHistoryMerge(threadId, state)
    const version = state.runStateVersion
    const detail = readThreadDetail(threadId, state)
    const current = opening ? (await detail).yuxi.current_turn : null
    if (!isCurrentThreadState(threadId, state)) return
    const settled = isSettled(current?.status) ? current : null
    const [session, window] = await Promise.all([detail, readThreadWindow(threadId, state, merge, { settled })])
    if (!window || !isCurrentThreadState(threadId, state)) return
    if (settled && window.page.has_more) {
      await readTurnHistory(threadId, state, merge, settled.id, window.oldestTurnItem)
      if (!isCurrentThreadState(threadId, state)) return
    }
    const turnId = session.yuxi.current_turn?.id
    const turn = turnId && await agentApi.getThreadTurn(threadId, turnId)
    if (!isCurrentThreadState(threadId, state)) return
    if (turn) {
      merge.items(turn.yuxi.output, turn.yuxi.runs)
      rememberSettledCooperationTurn(state, turn)
    }
    finishThreadWindow(threadId, state, window)
    if (!state.isStreaming && state.runStateVersion === version) state.turnStatus = session.yuxi.current_turn?.status || null
    merge.finish(!opening || Boolean(settled))
    if (opening) return { current, settled: settled ? turn : null }
  }, opening ? undefined : 'latest')

  const refreshThreadHistory = (threadId) => readLatestWindow(threadId)

  /** 分页仅使用所读 Thread 的游标，不重新读取会话详情和当前轮。 */
  const loadOlderHistory = (threadId) => scheduleHistoryRead(threadId, async (state) => {
    const paging = historyPages[threadId]
    if (!paging?.hasMore) return
    paging.loading = true
    try {
      const merge = createHistoryMerge(threadId, state)
      const window = await readThreadWindow(threadId, state, merge, { append: true })
      if (!window || !isCurrentThreadState(threadId, state)) return
      finishThreadWindow(threadId, state, window, true)
      merge.finish()
    } finally {
      if (isCurrentThreadState(threadId, state)) paging.loading = false
    }
  }, 'older')

  /** 回读明确的 Turn；保留阅读窗口、当前运行和其他轮次状态。 */
  const reconcileTurnHistory = (threadId, turnId) => scheduleHistoryRead(threadId, async (state) => {
    const merge = createHistoryMerge(threadId, state)
    await readTurnHistory(threadId, state, merge, turnId)
    if (!isCurrentThreadState(threadId, state)) return
    const turn = await agentApi.getThreadTurn(threadId, turnId)
    if (!isCurrentThreadState(threadId, state)) return
    merge.items(turn.yuxi.output, turn.yuxi.runs)
    rememberSettledCooperationTurn(state, turn)
    merge.finish(true)
    return turn
  })

  const fetchAgentState = (threadId) => {
    const state = peekThreadState(threadId)
    if (!state) return Promise.resolve(false)
    const runVersion = state.runStateVersion
    return once(`state:${runVersion}:${threadId}`, async () => {
      if (!isCurrentThreadState(threadId, state) || state.runStateVersion !== runVersion) return false
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
        return true
      } catch {
        return false
      }
    })
  }

  const { handlePublicEvent } = useAgentStreamHandler({
    getThreadState,
    streamSmoother,
    processApprovalInStream: (event, threadId) => {
      const state = getThreadState(threadId)
      state.pendingInterrupt = pendingInterruptFromWaitpoint(event.waitpoint, threadId)
      return Boolean(state.pendingInterrupt)
    }
  })
  const stream = useAgentRunStream({
    getThreadState,
    isThreadActive: (threadId) => observers.has(threadId),
    handlePublicEvent: (event, threadId) => {
      const result = handlePublicEvent(event, threadId)
      if (event.turn && event.session_id === threadId) {
        if (isCooperationCancellationEvent(event)) {
          // 单轮终态事件已确认持久取消，详情和历史暂不可用时也能展示结果。
          const turns = peekThreadState(threadId)?.settledCooperationTurns
          const turnId = event.turn_id
          if (turns && !turns.has(turnId)) {
            turns.set(turnId, { id: turnId, status: 'cancelled', current_run_id: event.yuxi.run_id })
          }
        }
        chatThreads.updateThreadSummary(threadId, {
          status: event.turn.status === 'queued' ? 'in_progress' : event.turn.status
        })
      }
      return result
    },
    readThreadDetail,
    refreshThreadHistory,
    reconcileTurnHistory,
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
      })().catch((error) => console.warn('Failed to restore Turn waitpoint:', error))
      void resumeQueuedInputs(threadId)
    },
    onTerminalDetected: (payload) => {
      const state = peekThreadState(payload.threadId)
      if (state) void readThreadDetail(payload.threadId, state).catch((error) => {
        console.warn('Failed to refresh Thread summary after settlement:', error)
      })
      void resumeQueuedInputs(payload.threadId)
      notify('onTerminalDetected', payload)
    },
    onRunStarted: ({ threadId }) => {
      chatThreads.updateThreadSummary(threadId, { status: 'in_progress' })
    }
  })
  const queue = useAgentInputQueue({
    getThreadState,
    isCurrentThreadState,
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
      if (!isCurrentThreadState(threadId, state)) return false
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
      const session = await readThreadDetail(threadId, state)
      if (!isCurrentThreadState(threadId, state) || state.runStateVersion !== version) return false
      if (!initialHistory) {
        // 摘要可能来自旧执行，已经读取的终态以当前持久详情为准。
        if (!state.currentTurnId && !state.isStreaming && sameExecution(lastSettled, session.yuxi.current_turn)) return false
        return stream.resumeActiveRunForThread(threadId, { refreshHistory: fresh, session, onRestored })
      }
      if (!sameExecution(initialHistory.current, session.yuxi.current_turn)) {
        // 新轮次的用户输入只在历史页提供正文，再按最新详情恢复订阅。
        await refreshThreadHistory(threadId)
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

  /** 提交冲突后从当前持久等待点恢复交互，不依赖状态面板的 checkpoint。 */
  const restoreThreadWaitpoint = async (threadId) => {
    const state = peekThreadState(threadId)
    await resumeActiveRunForThread(threadId, { fresh: true })
    if (!state || !isCurrentThreadState(threadId, state) || !state.pendingInterrupt?.waitpointId) {
      throw new Error('当前轮次没有可恢复的等待点')
    }
  }

  /** 阅读者只指定 Thread；首读、持久运行恢复和队列观察共享一次装载。 */
  const loadThread = (threadId, { fresh = false } = {}) => {
    if (!threadId || !observers.has(threadId)) return Promise.resolve(false)
    const state = getThreadState(threadId)
    if (state.readingReady && !fresh) return Promise.resolve(true)
    return once(`reading:${threadId}`, async () => {
      if (!isCurrentThreadState(threadId, state)) return false
      const readHistory = !state.historyLoaded || (!state.readingReady && state.readingError)
      state.readingLoading = true
      state.readingError = null
      try {
        const version = state.runStateVersion
        // 初始化失败后重读会话窗口，补上期间提交的新输入。
        const initialHistory = readHistory ? await readLatestWindow(threadId, true) : null
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
      historyReads.delete(threadId)
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
    historyReads.clear()
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
    peekThreadState,
    applyThreadDetail,
    applyThreadViewed,
    isCurrentThreadState,
    resetThreadExecution,
    fetchAgentState,
    restoreThreadWaitpoint,
    loadThread,
    loadOlderHistory,
    refreshThreadHistory,
    reconcileTurnHistory,
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
