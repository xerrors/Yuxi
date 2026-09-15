const SUBAGENT_LAUNCH_TOOL_NAMES = new Set(['task', 'subagent_start'])

/** 子智能体 run 状态新鲜度：终态 > 取消请求 > 进行中 > 初始。 */
const SUBAGENT_RUN_STATUS_RANK = {
  pending: 0,
  running: 1,
  cancel_requested: 2,
  cancelled: 3,
  failed: 3,
  interrupted: 3,
  completed: 3
}

/** 判断工具调用是否会启动或继续子智能体运行。 */
export const isSubagentLaunchToolName = (name) => SUBAGENT_LAUNCH_TOOL_NAMES.has(name)

/**
 * 按 run_id/子线程定位既有条目。
 *
 * 状态防回退只能限定在同一个 run_id 内：incoming 携带 run_id 却未命中时，它就是一条
 * 全新 run（即便 child_thread_id 与旧 run 相同——「继续同一子线程」会复用线程但换新 run），
 * 不应回退到 child_thread_id 匹配，否则新 run 会被旧 run 的终态 rank 直接丢弃。
 * 只有 incoming 没有 run_id（旧的增量形状）才回退到 child_thread_id 匹配。
 */
const findSubagentRunIndex = (list, incoming) => {
  if (incoming.run_id) {
    const byRunId = list.findIndex((item) => item?.run_id === incoming.run_id)
    return byRunId >= 0 ? byRunId : -1
  }
  if (incoming.child_thread_id) {
    return list.findIndex((item) => item?.child_thread_id === incoming.child_thread_id)
  }
  return -1
}

/** 把单条子智能体 run 增量合并进列表；旧状态不得回退覆盖新状态。 */
export const mergeSubagentRunIntoList = (runs, incoming) => {
  if (!incoming || typeof incoming !== 'object') return Array.isArray(runs) ? runs : []
  const list = Array.isArray(runs) ? [...runs] : []
  const index = findSubagentRunIndex(list, incoming)
  if (index < 0) {
    list.push(incoming)
    return list
  }
  const current = list[index]
  const currentRank = SUBAGENT_RUN_STATUS_RANK[current?.status] ?? 0
  const incomingRank = SUBAGENT_RUN_STATUS_RANK[incoming.status] ?? 0
  if (incomingRank < currentRank) return list
  list[index] = { ...current, ...incoming }
  return list
}

/**
 * 应用一份新的 agentState（HTTP 轮询或流式 agent_state 事件）时，
 * 逐条回放本地已知条目，防止较旧的 checkpoint 状态把流式增量回退。
 */
export const reconcileAgentStateSubagentRuns = (incoming, current) => {
  if (!incoming || !Array.isArray(current?.subagent_runs) || current.subagent_runs.length === 0) {
    return incoming
  }
  let merged = Array.isArray(incoming.subagent_runs) ? incoming.subagent_runs : []
  current.subagent_runs.forEach((run) => {
    merged = mergeSubagentRunIntoList(merged, run)
  })
  return { ...incoming, subagent_runs: merged }
}

/** 补全任务描述并把同一子线程收敛为一个展示项。 */
export const mergeSubagentRunsForDisplay = (runs, descriptionByToolCallId = new Map()) => {
  if (!Array.isArray(runs)) return []

  const result = []
  const indexByThreadId = new Map()

  runs.forEach((run) => {
    const toolCallId = run?.id ? String(run.id) : ''
    const stateDescription = String(run?.description || '').trim()
    const taskDescription = toolCallId
      ? String(descriptionByToolCallId.get(toolCallId) || '').trim()
      : ''
    const normalizedRun = {
      ...run,
      description: stateDescription || taskDescription
    }
    const threadId = run?.child_thread_id ? String(run.child_thread_id) : ''

    if (!threadId || !indexByThreadId.has(threadId)) {
      if (threadId) indexByThreadId.set(threadId, result.length)
      result.push(normalizedRun)
      return
    }

    const index = indexByThreadId.get(threadId)
    result[index] = {
      ...result[index],
      ...normalizedRun,
      description: normalizedRun.description || result[index].description || ''
    }
  })

  return result.map((run) => ({
    ...run,
    description: run.description || String(run?.child_thread_id || run?.id || '')
  }))
}
