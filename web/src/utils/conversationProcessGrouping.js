import { formatRunTimingDuration, getRunTotalLatencyMs } from './runTiming.js'

/** 在展示层连接具有明确父子关系的相邻续跑。 */
export const groupConversationContinuations = (conversations) => {
  const groups = []
  for (const conv of conversations) {
    const previous = groups.at(-1)
    if (
      conv.run?.run_type !== 'resume' ||
      !previous?.run?.run_id ||
      conv.run.created_by_run_id !== previous.run.run_id ||
      !previous.messages.length ||
      !conv.messages.length ||
      conv.messages.some((message) => message.type === 'human')
    ) {
      groups.push(conv)
      continue
    }

    const previousDuration = getRunTotalLatencyMs(previous.processTiming || previous.run.timing)
    const duration = getRunTotalLatencyMs(conv.run.timing)
    groups[groups.length - 1] = {
      ...conv,
      displayKey: previous.displayKey || previous.run.run_id,
      messages: [
        ...previous.messages.map((message) =>
          message.isLast ? { ...message, isLast: false } : message
        ),
        ...conv.messages
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

export const collapseConversationProcess = (items, enabled = false, runTiming = null) => {
  if (!enabled) return items

  const finalIndex = items.findLastIndex(
    (item) => item.type === 'message' && item.message?.type === 'ai'
  )
  if (finalIndex <= 1 || finalIndex !== items.length - 1) return items

  const processStart = items.findIndex(
    (item, index) =>
      index < finalIndex &&
      (item.type === 'tool-group' || (item.type === 'message' && item.message?.type === 'ai'))
  )
  if (processStart < 0) return items

  const processItems = items.slice(processStart, finalIndex)
  if (processItems.some((item) => item.type !== 'tool-group' && item.message?.type !== 'ai')) {
    return items
  }

  const messageCount = processItems.filter((item) => item.type === 'message').length
  const toolCallCount = processItems.reduce(
    (count, item) => count + (item.type === 'tool-group' ? item.toolCalls.length : 0),
    0
  )
  if (!messageCount && !toolCallCount) return items

  const durationMs = getRunTotalLatencyMs(runTiming)

  return [
    ...items.slice(0, processStart),
    {
      type: 'process-group',
      key: `process-group-${processItems[0].key}`,
      items: processItems,
      messageCount,
      toolCallCount,
      durationMs
    },
    items[finalIndex]
  ]
}

/** 只有关联到本次运行的后续 resume 才延后当前回答的操作栏。 */
export const isConversationSettled = (conversations, conv, isProcessing = false) => {
  const index = conversations.indexOf(conv)
  if (index === -1) return false
  const next = conversations[index + 1]
  if (next?.run) {
    return !(next.run.run_type === 'resume' && next.run.created_by_run_id === conv.run?.run_id)
  }
  if (next) return next.messages?.[0]?.type === 'human'
  return !isProcessing
}

/** 为没有普通消息的 Run 提供可观察状态。 */
export const formatEmptyRunStatus = (status) => ({
  pending: '等待运行',
  running: '正在处理',
  cancel_requested: '正在取消',
  cancelled: '本次运行已取消',
  failed: '本次运行失败',
  interrupted: '本次运行已中断',
  completed: '本次运行已结束'
})[status] || '暂无消息'
