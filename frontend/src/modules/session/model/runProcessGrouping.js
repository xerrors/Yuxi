import { formatRunTimingDuration, getRunTotalLatencyMs } from './runTiming.js'

/** 在展示层连接同一 Turn 的相邻续跑。 */
export const groupRunContinuations = (runGroups) => {
  const groups = []
  for (const group of runGroups) {
    const previous = groups.at(-1)
    // 实时续跑尚未刷新 Run 快照时，使用消息明确携带的归属。
    const liveMessage = group.status === 'streaming' && group.messages.find((message) => message.type === 'ai')
    const run = group.run || (liveMessage?.run_id && liveMessage.turn_id
      ? { id: liveMessage.run_id, turn_id: liveMessage.turn_id }
      : null)
    const isContinuation = run?.run_type === 'resume' || (
      group.status === 'streaming' && run?.id && run.id !== previous?.run?.id
    )
    if (
      !isContinuation ||
      !run?.turn_id ||
      run.turn_id !== previous?.run?.turn_id ||
      !previous.messages.length ||
      !group.messages.length ||
      group.messages.some((message) => message.type === 'human')
    ) {
      groups.push(group)
      continue
    }

    const previousDuration = getRunTotalLatencyMs(previous.processTiming || previous.run.timing)
    const duration = getRunTotalLatencyMs(run.timing)
    groups[groups.length - 1] = {
      ...group,
      run,
      displayKey: previous.displayKey || previous.run.id,
      messages: [
        ...previous.messages.map((message) =>
          message.isLast ? { ...message, isLast: false } : message
        ),
        ...group.messages
      ],
      processTiming: {
        total_latency_ms:
          previousDuration === null || duration === null ? null : previousDuration + duration
      }
    }
  }
  return groups
}

export const formatProcessDuration = (durationMs) => {
  const formattedDuration = formatRunTimingDuration(durationMs)
  return formattedDuration ? `耗时 ${formattedDuration}` : '处理过程'
}

/** 将最后一个可见处理分组之前的内容折叠，后续正文连续展示。 */
export const collapseRunProcess = (items, enabled = false, runTiming = null) => {
  if (!enabled) return items

  const finalIndex = items.findLastIndex(
    (item) => item.type === 'message' && item.message?.type === 'ai'
  )
  if (finalIndex <= 1 || finalIndex !== items.length - 1) return items

  // 隐藏的交付物调用已在展示投影中过滤，不再以最后一条正文作为截止。
  const processEnd = items.findLastIndex((item) => item.type === 'tool-group') + 1
  if (processEnd === 0) return items

  const processStart = items.findIndex(
    (item, index) =>
      index < processEnd &&
      (item.type === 'tool-group' || (item.type === 'message' && item.message?.type === 'ai'))
  )
  if (processStart < 0) return items

  const processItems = items.slice(processStart, processEnd)
  if (processItems.some((item) => item.type !== 'tool-group' && item.message?.type !== 'ai')) {
    return items
  }

  const hasMessagesOrTools = processItems.some(
    (item) => item.type === 'message' || (item.type === 'tool-group' && item.toolCalls.length > 0)
  )
  if (!hasMessagesOrTools) return items

  const durationMs = getRunTotalLatencyMs(runTiming)

  return [
    ...items.slice(0, processStart),
    {
      type: 'process-group',
      key: `process-group-${processItems[0].key}`,
      items: processItems,
      durationMs
    },
    ...items.slice(processEnd)
  ]
}

/** 只有同一 Turn 的后续 resume 才延后当前回答的操作栏。 */
export const isRunGroupSettled = (runGroups, group, isProcessing = false) => {
  const index = runGroups.indexOf(group)
  if (index === -1) return false
  const next = runGroups[index + 1]
  if (next?.run) {
    return !(next.run.run_type === 'resume' && next.run.turn_id && next.run.turn_id === group.run?.turn_id)
  }
  if (next) return next.messages?.[0]?.type === 'human'
  return !isProcessing
}

/** 为没有普通消息的 Run 提供可观察状态。 */
export const formatEmptyRunStatus = (status) => ({
  pending: '等待运行',
  running: '正在处理',
  cancel_requested: '取消中',
  cancelled: '本次运行已取消',
  failed: '本次运行失败',
  interrupted: '本次运行已中断',
  completed: '本次运行已结束'
})[status] || '暂无消息'

const STOPPED_RUN_STATUSES = new Set(['cancelled', 'interrupted', 'cancel_requested'])

/** 识别为协作等待而结束的执行段。 */
export const isCooperationWaitRun = (run) =>
  run?.status === 'interrupted' && run.error_type === 'cooperation_waiting'

/** 根据执行段的结束原因及所属轮次生成消息区状态提示。 */
export const getRunStatusNotice = (run, hasDisplayItems, turn) => {
  if (!run) return null

  let { status } = run
  if (isCooperationWaitRun(run)) {
    // 等待中的 Run 在取消后仍保留原结束原因，取消结果由同一 Turn 确认。
    if (turn?.status !== 'cancelled' || turn.id !== run.turn_id || turn.current_run_id !== run.id) {
      return null
    }
    status = 'cancelled'
  }

  const stopped = STOPPED_RUN_STATUSES.has(status)
  return stopped || !hasDisplayItems ? { message: formatEmptyRunStatus(status), stopped } : null
}
