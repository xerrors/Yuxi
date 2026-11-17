import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildRunTimingPhases,
  buildRunTimingRows,
  buildSequentialRunTiming,
  buildTimelineRangeAtPoint,
  constrainTimelineRange,
  formatRunTimingDuration,
  getRunTimingWindow,
  getRunTotalLatencyMs,
  getTimelineMinimumRangeUnits
} from '../../src/modules/session/model/runTiming.js'

test('运行时延使用紧凑且稳定的单位', () => {
  assert.equal(formatRunTimingDuration(235), '235ms')
  assert.equal(formatRunTimingDuration(1530), '1.5s')
  assert.equal(formatRunTimingDuration(27776), '28s')
  assert.equal(formatRunTimingDuration(110128), '1m 50s')
  assert.equal(formatRunTimingDuration(119600), '2m 0s')
  assert.equal(formatRunTimingDuration(-1), '')
  assert.equal(formatRunTimingDuration(null), '')
})

test('运行时延只展示 PostgreSQL 已持久化的可用阶段', () => {
  const rows = buildRunTimingRows({
    dispatch_latency_ms: 200,
    preparation_latency_ms: 800,
    model_first_output_latency_ms: null,
    first_output_latency_ms: 7500,
    total_latency_ms: undefined
  })

  assert.deepEqual(
    rows.map(({ key, label, formattedValue }) => ({ key, label, formattedValue })),
    [
      { key: 'dispatch_latency_ms', label: '调度等待', formattedValue: '200ms' },
      { key: 'preparation_latency_ms', label: '运行准备', formattedValue: '800ms' },
      { key: 'first_output_latency_ms', label: '首次输出', formattedValue: '7.5s' }
    ]
  )
})

test('总耗时只接受后端 timing 投影中的非负有限数字', () => {
  assert.equal(getRunTotalLatencyMs({ total_latency_ms: 0 }), 0)
  assert.equal(getRunTotalLatencyMs({ total_latency_ms: 43670 }), 43670)
  assert.equal(getRunTotalLatencyMs({ total_latency_ms: '43670' }), null)
  assert.equal(getRunTotalLatencyMs({ total_latency_ms: -1 }), null)
  assert.equal(getRunTotalLatencyMs(null), null)
})

test('Trace 时间窗口优先采用后端总耗时且不使用浏览器当前时间', () => {
  assert.deepEqual(
    getRunTimingWindow({
      created_at: '2026-09-04T08:00:00Z',
      started_at: '2026-09-04T08:00:01Z',
      prepared_at: '2026-09-04T08:00:02Z',
      first_output_at: '2026-09-04T08:00:04Z',
      finished_at: '2026-09-04T08:00:08Z',
      total_latency_ms: 8000
    }),
    {
      startMs: Date.parse('2026-09-04T08:00:00Z'),
      endMs: Date.parse('2026-09-04T08:00:08Z'),
      durationMs: 8000,
      complete: true
    }
  )

  assert.equal(
    getRunTimingWindow({
      created_at: '2026-09-04T08:00:00Z',
      started_at: '2026-09-04T08:00:01Z'
    }).durationMs,
    1000
  )
  assert.equal(getRunTimingWindow({ created_at: 'invalid' }), null)
})

test('尚未开工的 Run 不生成未知耗时，也不污染已有时间轴', () => {
  const pending = {
    created_at: '2026-09-04T08:00:10Z',
    started_at: null,
    prepared_at: null,
    first_output_at: null,
    finished_at: null,
    total_latency_ms: null
  }
  const createdMs = Date.parse(pending.created_at)
  assert.deepEqual(getRunTimingWindow(pending), {
    startMs: createdMs,
    endMs: createdMs,
    durationMs: 0,
    complete: false
  })
  assert.equal(buildSequentialRunTiming([pending]), null)
  const completed = {
    created_at: '2026-09-04T08:00:00Z',
    finished_at: '2026-09-04T08:00:02Z',
    total_latency_ms: 2000
  }
  assert.deepEqual(
    buildSequentialRunTiming([completed, pending]),
    buildSequentialRunTiming([completed])
  )
})

test('Trace 阶段只连接真实存在的相邻时间点', () => {
  const phases = buildRunTimingPhases({
    created_at: '2026-09-04T08:00:00Z',
    started_at: '2026-09-04T08:00:01Z',
    prepared_at: '2026-09-04T08:00:02Z',
    first_output_at: null,
    finished_at: '2026-09-04T08:00:08Z',
    total_latency_ms: 8000
  })

  assert.deepEqual(
    phases.map(({ key, startOffsetMs, endOffsetMs }) => ({ key, startOffsetMs, endOffsetMs })),
    [
      { key: 'dispatch', startOffsetMs: 0, endOffsetMs: 1000 },
      { key: 'preparation', startOffsetMs: 1000, endOffsetMs: 2000 }
    ]
  )
})

test('会话时间概览首尾拼接 Run 并移除中间空档', () => {
  const timeline = buildSequentialRunTiming([
    {
      created_at: '2026-09-04T08:00:00Z',
      finished_at: '2026-09-04T08:00:02Z',
      total_latency_ms: 2000
    },
    {
      created_at: '2026-09-04T08:00:08Z',
      started_at: '2026-09-04T08:00:09Z',
      finished_at: '2026-09-04T08:00:12Z',
      total_latency_ms: 4000
    }
  ])

  assert.deepEqual(
    timeline.segments.map(({ sourceIndex, startOffsetMs, endOffsetMs }) => ({
      sourceIndex,
      startOffsetMs,
      endOffsetMs
    })),
    [
      { sourceIndex: 0, startOffsetMs: 0, endOffsetMs: 2000 },
      { sourceIndex: 1, startOffsetMs: 2000, endOffsetMs: 6000 }
    ]
  )
  assert.equal(timeline.durationMs, 6000)
})

test('时间轴点击始终选择附近两秒并在边缘贴边', () => {
  assert.equal(getTimelineMinimumRangeUnits(10_000), 200)
  assert.deepEqual(buildTimelineRangeAtPoint(10_000, 0.5), { start: 400, end: 600 })
  assert.deepEqual(buildTimelineRangeAtPoint(10_000, 0), { start: 0, end: 200 })
  assert.deepEqual(buildTimelineRangeAtPoint(10_000, 1), { start: 800, end: 1000 })
  assert.deepEqual(buildTimelineRangeAtPoint(1000, 0.5), { start: 0, end: 1000 })
})

test('时间轴手柄不能交叉且选区不能小于两秒', () => {
  assert.deepEqual(constrainTimelineRange(10_000, 900, 300, 'start'), {
    start: 100,
    end: 300
  })
  assert.deepEqual(constrainTimelineRange(10_000, 700, 300, 'end'), {
    start: 700,
    end: 900
  })
  assert.deepEqual(constrainTimelineRange(10_000, 490, 510), {
    start: 400,
    end: 600
  })
})
