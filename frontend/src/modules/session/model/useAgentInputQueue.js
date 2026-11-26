import { agentApi } from '@/apis'
import { mergeItemSnapshot } from '@/modules/session/model/agentItems'
import { IDLE_QUEUE_SNAPSHOT } from '@/modules/session/model/useAgentThreadState'
import { handleChatError } from '@/shared/lib/errorHandler'

const controlKey = () =>
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `input-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

/** 将持久 Input 队列投影到当前 Thread，并跟踪领取结果。 */
export function useAgentInputQueue({
  getThreadState,
  isThreadActive = () => true,
  onInputConsumed,
  onStreamError
}) {
  const snapshotVersions = new Map()
  const commandKeys = new Map()
  const advanceSnapshot = (threadId) => {
    const version = (snapshotVersions.get(threadId) || 0) + 1
    snapshotVersions.set(threadId, version)
    return version
  }
  const stopInputQueueMonitor = (ts) => {
    if (!ts?.inputQueueMonitor) return
    clearTimeout(ts.inputQueueMonitor.timer)
    ts.inputQueueMonitor.controller.abort()
    ts.inputQueueMonitor = null
  }

  const stopInputMonitor = (threadId, inputId) => {
    const ts = getThreadState(threadId)
    const monitor = ts?.inputQueueMonitor
    if (!monitor) return
    monitor.inputIds.delete(inputId)
    if (monitor.inputIds.size === 0) stopInputQueueMonitor(ts)
  }

  const stopAllInputMonitors = (threadId) => {
    const ts = getThreadState(threadId)
    if (!ts) return
    stopInputQueueMonitor(ts)
  }

  const removeInput = (ts, inputId) => {
    if (ts) ts.queuedInputs = (ts.queuedInputs || []).filter((item) => item.input_id !== inputId)
  }

  const startInputMonitor = (threadId, inputId) => {
    if (!isThreadActive(threadId)) return
    const ts = getThreadState(threadId)
    if (!ts || !inputId || ts.inputQueueMonitor?.inputIds.has(inputId)) return
    ts.inputQueueMonitor ||= { controller: new AbortController(), timer: null, inputIds: new Set() }
    const monitor = ts.inputQueueMonitor
    monitor.inputIds.add(inputId)
    if (monitor.inputIds.size > 1) return
    const { signal } = monitor.controller

    const poll = async () => {
      let retryDelay = 1000
      for (const currentInputId of [...monitor.inputIds]) {
        if (signal.aborted) return
        try {
          const input = await agentApi.getThreadInput(threadId, currentInputId, { signal })
          if (signal.aborted) return
          if (input.status === 'consumed' && input.run_id) {
            advanceSnapshot(threadId)
            const state = getThreadState(threadId)
            removeInput(state, currentInputId)
            mergeItemSnapshot(state.itemState, input.items || [])
            delete state.optimisticInputs[currentInputId]
            if (state.pendingInputId === currentInputId) state.pendingInputId = null
            // Input 只拥有历史归属；当前执行由持久 Turn 恢复入口选择。
            const resumed = await onInputConsumed?.(threadId)
            // 恢复期间可能已重置观察，旧轮询的结果不能清理新观察。
            if (signal.aborted) return
            if (resumed !== false) stopInputMonitor(threadId, currentInputId)
            continue
          }
          if (input.status === 'cancelled') {
            advanceSnapshot(threadId)
            const state = getThreadState(threadId)
            removeInput(state, currentInputId)
            if (state?.optimisticInputs) {
              delete state.optimisticInputs[currentInputId]
            }
            stopInputMonitor(threadId, currentInputId)
            onStreamError?.(threadId, currentInputId, 'cancelled')
          }
        } catch (error) {
          if (signal.aborted || error?.name === 'AbortError') return
          if (error?.status >= 400 && error.status < 500 && error.status !== 429) {
            stopInputMonitor(threadId, currentInputId)
            onStreamError?.(threadId, currentInputId, 'unavailable')
            handleChatError(error, 'stream')
          } else {
            retryDelay = 5000
          }
        }
      }
      if (!signal.aborted && monitor.inputIds.size > 0) {
        monitor.timer = setTimeout(poll, retryDelay)
      }
    }
    void poll()
  }

  const syncQueuedInputs = async (threadId) => {
    if (!isThreadActive(threadId)) return
    const ts = getThreadState(threadId)
    if (!ts) return
    const version = advanceSnapshot(threadId)
    try {
      const snapshot = await agentApi.getThreadQueue(threadId)
      if (!isThreadActive(threadId) || getThreadState(threadId) !== ts || snapshotVersions.get(threadId) !== version) return
      const inputs = snapshot?.inputs || []
      const knownIds = new Set(inputs.map((input) => input.input_id))
      ts.queuedInputs = [
        ...inputs.map((input) => {
          const existing = ts.queuedInputs?.find((item) => item.input_id === input.input_id)
          return {
            ...input,
            content: input.content || existing?.content,
            message: existing?.message
          }
        }),
        ...(ts.queuedInputs || []).filter(
          (input) => !knownIds.has(input.input_id) && input.status === 'sending'
        )
      ]
      ts.queueSnapshot = snapshot || { ...IDLE_QUEUE_SNAPSHOT }
      for (const input of inputs) startInputMonitor(threadId, input.input_id)
    } catch (error) {
      console.warn('Failed to sync queued inputs:', error)
    }
  }

  const resumeQueuedInputs = async (threadId) => {
    if (threadId) await syncQueuedInputs(threadId)
  }

  const changeInput = async (action, threadId, inputId) => {
    const ts = getThreadState(threadId)
    if (
      !ts ||
      !inputId ||
      ts.queuedInputs?.some((input) => input.input_id === inputId && input.status === 'sending')
    )
      return false
    const key = `${threadId}:${inputId}:${action}`
    if (!commandKeys.has(key)) commandKeys.set(key, controlKey())
    try {
      const command = action === 'promote' ? agentApi.promoteThreadInput : agentApi.cancelThreadInput
      await command(threadId, inputId, commandKeys.get(key))
      commandKeys.delete(key)
      advanceSnapshot(threadId)
      if (action === 'cancel') {
        stopInputMonitor(threadId, inputId)
        removeInput(ts, inputId)
        if (ts.optimisticInputs)
          delete ts.optimisticInputs[inputId]
      }
      await syncQueuedInputs(threadId)
      return true
    } catch (error) {
      if (error?.status === 409) {
        commandKeys.delete(key)
        await syncQueuedInputs(threadId)
      }
      handleChatError(error, action)
      return false
    }
  }

  const cancelInput = (threadId, inputId) => changeInput('cancel', threadId, inputId)
  const promoteInput = (threadId, inputId) => changeInput('promote', threadId, inputId)

  const continueQueue = async (threadId) => {
    const ts = getThreadState(threadId)
    if (!ts || ts.continueQueueInFlight) return false
    ts.continueQueueInFlight = true
    try {
      await agentApi.continueThreadQueue(threadId, controlKey())
      await syncQueuedInputs(threadId)
      return true
    } catch (error) {
      handleChatError(error, 'continue_queue')
      return false
    } finally {
      ts.continueQueueInFlight = false
    }
  }

  return {
    stopAllInputMonitors,
    startInputMonitor,
    cancelInput,
    promoteInput,
    syncQueuedInputs,
    resumeQueuedInputs,
    continueQueue
  }
}
