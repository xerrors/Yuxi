const SUBAGENT_LAUNCH_TOOL_NAMES = new Set(['task', 'subagent_start'])

/** 判断工具调用是否会启动或继续子智能体运行。 */
export const isSubagentLaunchToolName = (name) => SUBAGENT_LAUNCH_TOOL_NAMES.has(name)

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
