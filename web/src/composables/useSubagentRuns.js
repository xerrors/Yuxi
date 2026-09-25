import { computed, onScopeDispose, ref, watch } from 'vue'
import { agentApi } from '@/apis'
import { processRunSseResponse } from './useAgentRunStream'
import { normalizeRunSeq } from '@/utils/runStreamResume'

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'interrupted'])
// HTTP/1.1 下给父流、状态查询和取消请求保留同源连接；额外子 Run 每 2 秒回读。
const MAX_CHILD_STREAMS = 3

/** 独立观察子 Run；父 checkpoint 只提供身份，不覆盖已读取的运行状态。 */
export function useSubagentRuns({ scope, runs, enabled = ref(true) }) {
  const records = ref({})
  const subscriptions = new Map()

  const stop = () => {
    for (const entry of subscriptions.values()) {
      entry.controller.abort()
      clearTimeout(entry.retry)
    }
    subscriptions.clear()
    records.value = {}
  }

  const isCurrent = (entry) =>
    subscriptions.get(entry.id) === entry && !entry.controller.signal.aborted

  const refresh = (entry) => {
    if (entry.refreshing) return entry.refreshing
    entry.refreshing = (async () => {
      const { run } = await agentApi.getAgentRun(entry.id, { signal: entry.controller.signal })
      if (!isCurrent(entry)) return
      records.value[entry.id] = {
        ...records.value[entry.id],
        created_at: run.created_at || records.value[entry.id].created_at,
        status: run.status,
        completed_at: run.finished_at,
        error: run.error_message,
        observation_error: false
      }
      if (TERMINAL.has(run.status)) entry.controller.abort()
    })().finally(() => {
      entry.refreshing = null
    })
    return entry.refreshing
  }

  const markUnavailable = (entry) => {
    if (isCurrent(entry)) {
      records.value[entry.id] = { ...records.value[entry.id], observation_error: true }
    }
  }

  const observe = async (entry) => {
    try {
      await refresh(entry)
      if (!isCurrent(entry)) return
      if ([...subscriptions.values()].filter((item) => item.streaming).length >= MAX_CHILD_STREAMS)
        return
      entry.streaming = true
      const response = await agentApi.streamAgentRunEvents(entry.id, entry.cursor, {
        signal: entry.controller.signal
      })
      if (!response.ok) throw new Error(`子任务订阅失败: ${response.status}`)
      await processRunSseResponse(response, (event, _data, eventId) => {
        if (!isCurrent(entry)) return
        if (eventId) entry.cursor = normalizeRunSeq(eventId)
        if (event === 'metadata') void refresh(entry).catch(() => markUnavailable(entry))
      })
      // 流结束后重新回读终态；不复用结束事件之前尚在途的状态查询。
      await entry.refreshing
      if (isCurrent(entry)) await refresh(entry)
    } catch {
      markUnavailable(entry)
    } finally {
      entry.streaming = false
      if (isCurrent(entry)) entry.retry = setTimeout(() => observe(entry), 2000)
    }
  }

  watch([scope, enabled], stop, { flush: 'sync' })
  watch(
    [scope, enabled, runs],
    ([currentScope, active, discovered]) => {
      if (!currentScope || !active) return
      for (const run of discovered || []) {
        if (!run.run_id || subscriptions.has(run.run_id)) continue
        const entry = { id: run.run_id, controller: new AbortController(), cursor: '0-0' }
        subscriptions.set(entry.id, entry)
        records.value[entry.id] = { ...run }
        void observe(entry)
      }
    },
    { immediate: true }
  )

  // 无事件或连接悬挂时仍核对持久状态；终态关闭对应连接，不等待父 graph。
  const timer = setInterval(() => {
    for (const entry of subscriptions.values()) {
      if (isCurrent(entry)) void refresh(entry).catch(() => markUnavailable(entry))
    }
  }, 15000)
  onScopeDispose(() => {
    clearInterval(timer)
    stop()
  })

  return computed(() =>
    Object.values(records.value).sort(
      (left, right) =>
        String(left.created_at || '').localeCompare(String(right.created_at || '')) ||
        left.run_id.localeCompare(right.run_id)
    )
  )
}
