const METRICS = [
  {
    key: 'dispatch_latency_ms',
    label: '调度等待',
    description: 'Run 创建到 Worker 取得执行权'
  },
  {
    key: 'preparation_latency_ms',
    label: '运行准备',
    description: 'Worker 取得执行权到 Agent 运行上下文与模型流准备完成'
  },
  {
    key: 'model_first_output_latency_ms',
    label: '模型首响',
    description: '准备完成到首个非空模型文本、推理或工具调用数据'
  },
  {
    key: 'first_output_latency_ms',
    label: '首次输出',
    description: 'Run 创建到首个非空模型语义输出'
  },
  {
    key: 'total_latency_ms',
    label: '总耗时',
    description: 'Run 创建到 PostgreSQL 终态'
  }
]

const finiteDuration = (value) =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null

const parseTimestamp = (value) => {
  if (typeof value !== 'string' || !value.trim()) return null
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? timestamp : null
}

const TIMELINE_PHASES = [
  { key: 'dispatch', label: '调度等待', start: 'created_at', end: 'started_at' },
  { key: 'preparation', label: '运行准备', start: 'started_at', end: 'prepared_at' },
  { key: 'first-output', label: '模型首响', start: 'prepared_at', end: 'first_output_at' },
  { key: 'completion', label: '输出到终态', start: 'first_output_at', end: 'finished_at' }
]
const TIMELINE_RANGE_SCALE = 1000
const TIMELINE_MIN_SELECTION_MS = 2000

const clampTimelineRangeUnit = (value) =>
  Math.max(0, Math.min(TIMELINE_RANGE_SCALE, Math.round(Number(value) || 0)))

/** 将两秒最小窗口换算为时间轴的整数范围单位。 */
export const getTimelineMinimumRangeUnits = (durationMs) => {
  const duration = finiteDuration(durationMs)
  if (duration === null || duration === 0) return TIMELINE_RANGE_SCALE
  return Math.min(
    TIMELINE_RANGE_SCALE,
    Math.ceil((TIMELINE_MIN_SELECTION_MS / duration) * TIMELINE_RANGE_SCALE)
  )
}

/** 约束时间选区的顺序与两秒最小宽度，并保留正在拖动的端点语义。 */
export const constrainTimelineRange = (durationMs, start, end, changedEdge = 'center') => {
  const minimum = getTimelineMinimumRangeUnits(durationMs)
  let nextStart = clampTimelineRangeUnit(start)
  let nextEnd = clampTimelineRangeUnit(end)

  if (changedEdge === 'start') {
    nextEnd = Math.max(nextEnd, minimum)
    nextStart = Math.min(nextStart, nextEnd - minimum)
  } else if (changedEdge === 'end') {
    nextStart = Math.min(nextStart, TIMELINE_RANGE_SCALE - minimum)
    nextEnd = Math.max(nextEnd, nextStart + minimum)
  } else if (nextEnd - nextStart < minimum) {
    const center = (nextStart + nextEnd) / 2
    nextStart = Math.max(
      0,
      Math.min(TIMELINE_RANGE_SCALE - minimum, Math.round(center - minimum / 2))
    )
    nextEnd = nextStart + minimum
  }

  return { start: nextStart, end: nextEnd }
}

/** 以点击位置为中心生成两秒选区，靠近边缘时保持宽度并贴边。 */
export const buildTimelineRangeAtPoint = (durationMs, pointRatio) => {
  const minimum = getTimelineMinimumRangeUnits(durationMs)
  const center = clampTimelineRangeUnit(Number(pointRatio) * TIMELINE_RANGE_SCALE)
  return constrainTimelineRange(durationMs, center - minimum / 2, center + minimum / 2, 'center')
}

/** 读取后端统一派生的 Run 总耗时，不从时间戳补算。 */
export const getRunTotalLatencyMs = (timing) => finiteDuration(timing?.total_latency_ms)

export const formatRunTimingDuration = (value) => {
  const milliseconds = finiteDuration(value)
  if (milliseconds === null) return ''
  if (milliseconds < 1000) return `${Math.round(milliseconds)}ms`

  const seconds = milliseconds / 1000
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`

  const roundedSeconds = Math.round(seconds)
  const minutes = Math.floor(roundedSeconds / 60)
  const remainingSeconds = roundedSeconds % 60
  return `${minutes}m ${remainingSeconds}s`
}

export const buildRunTimingRows = (timing) => {
  if (!timing || typeof timing !== 'object') return []
  return METRICS.flatMap((metric) => {
    const value = finiteDuration(timing[metric.key])
    return value === null
      ? []
      : [{ ...metric, value, formattedValue: formatRunTimingDuration(value) }]
  })
}

export const formatRunTimingSummary = (timing) => {
  const total = formatRunTimingDuration(getRunTotalLatencyMs(timing))
  if (total) return `总耗时 ${total}`

  const firstOutput = formatRunTimingDuration(timing?.first_output_latency_ms)
  return firstOutput ? `首输出 ${firstOutput}` : ''
}

/** 从持久 Run 时间点建立单次 Trace 的绝对时间窗口。 */
export const getRunTimingWindow = (timing) => {
  if (!timing || typeof timing !== 'object') return null
  const startMs = parseTimestamp(timing.created_at)
  if (startMs === null) return null

  const totalLatencyMs = getRunTotalLatencyMs(timing)
  const knownEndMs = [
    timing.finished_at,
    timing.first_output_at,
    timing.prepared_at,
    timing.started_at
  ]
    .map(parseTimestamp)
    .find((value) => value !== null)
  const durationMs = totalLatencyMs ?? Math.max(0, (knownEndMs ?? startMs) - startMs)

  return {
    startMs,
    endMs: startMs + durationMs,
    durationMs,
    complete: totalLatencyMs !== null
  }
}

/** 按原顺序首尾拼接 Run 时间窗口，不把 Run 之间的空档计入时间轴。 */
export const buildSequentialRunTiming = (timings) => {
  let durationMs = 0
  const segments = (Array.isArray(timings) ? timings : []).flatMap((timing, sourceIndex) => {
    const window = getRunTimingWindow(timing)
    if (!window || window.durationMs <= 0) return []
    const startOffsetMs = durationMs
    durationMs += window.durationMs
    return [{ sourceIndex, window, startOffsetMs, endOffsetMs: durationMs }]
  })
  return segments.length ? { durationMs, segments } : null
}

/** 将相邻的 Run 时间点映射为时间概览阶段，不补齐缺失阶段。 */
export const buildRunTimingPhases = (timing) => {
  if (!timing || typeof timing !== 'object') return []
  const window = getRunTimingWindow(timing)
  if (!window || window.durationMs <= 0) return []

  return TIMELINE_PHASES.flatMap((phase) => {
    const phaseStart = parseTimestamp(timing[phase.start])
    const phaseEnd = parseTimestamp(timing[phase.end])
    if (phaseStart === null || phaseEnd === null || phaseEnd < phaseStart) return []

    const startOffsetMs = Math.max(0, Math.min(window.durationMs, phaseStart - window.startMs))
    const endOffsetMs = Math.max(
      startOffsetMs,
      Math.min(window.durationMs, phaseEnd - window.startMs)
    )
    return [
      {
        key: phase.key,
        label: phase.label,
        startOffsetMs,
        endOffsetMs,
        leftPercent: (startOffsetMs / window.durationMs) * 100,
        widthPercent: Math.max(((endOffsetMs - startOffsetMs) / window.durationMs) * 100, 0.35)
      }
    ]
  })
}
