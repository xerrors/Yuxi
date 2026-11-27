import { computed, shallowRef } from 'vue'
import { message } from 'ant-design-vue'
import { handleChatError } from '@/shared/lib/errorHandler'
import { resumeSessionWaitpoint } from './sessionCommands'
import { hasPendingInterruptPayload } from './toolApproval'

/** 从当前 Thread 的持久等待点派生界面，并按该等待点提交人工操作。 */
export function useThreadTurnAction({ threadId, enabled, runtime, onExecutionError }) {
  const state = computed(() => runtime.peekThreadState(threadId.value))
  const feedback = shallowRef(null)
  const action = computed(() => {
    const current = state.value
    const pending = current?.pendingInterrupt
    if (
      !enabled.value ||
      current?.turnStatus !== 'requires_action' ||
      !current.currentTurnId ||
      !pending?.waitpointId ||
      (current.cancellationPending && current.approvalSubmitting !== pending.waitpointId) ||
      pending.threadId !== threadId.value ||
      pending.interruptedRunId !== current.activeRunId ||
      !hasPendingInterruptPayload(pending)
    ) return null
    return { ...pending, turnId: current.currentTurnId }
  })
  const visible = computed(() => Boolean(action.value))
  const isToolApproval = computed(() => action.value?.kind === 'tool_approval')
  const processing = computed(() =>
    Boolean(action.value && state.value.approvalSubmitting === action.value.waitpointId)
  )
  const error = computed(() =>
    feedback.value && feedback.value.state === state.value &&
    feedback.value.waitpointId === action.value?.waitpointId
      ? feedback.value.text
      : ''
  )

  const execute = async (operation, displayedWaitpointId, answer) => {
    const target = action.value
    if (!target || displayedWaitpointId !== target.waitpointId || processing.value) return false
    const current = state.value
    const { threadId: id, turnId, interruptedRunId: runId, waitpointId } = target
    const sameRun = () =>
      runtime.isCurrentThreadState(id, current) &&
      current.currentTurnId === turnId && current.activeRunId === runId
    const sameWaitpoint = () =>
      sameRun() && current.turnStatus === 'requires_action' &&
      current.pendingInterrupt?.waitpointId === waitpointId &&
      current.pendingInterrupt.interruptedRunId === runId
    const stillVisible = () => state.value === current && action.value?.waitpointId === waitpointId
    const release = runtime.observeThread(id, {})
    current.approvalSubmitting = waitpointId
    feedback.value = null

    try {
      if (operation === 'cancel') {
        const accepted = await runtime.requestTurnCancellation(id)
        if (accepted && runtime.isCurrentThreadState(id, current)) {
          if (sameWaitpoint()) current.pendingInterrupt = null
          await runtime.resumeQueuedInputs(id)
          if (state.value === current && enabled.value) message.info('已请求停止本轮任务')
        }
        if (!accepted && stillVisible()) {
          feedback.value = { state: current, waitpointId, text: '停止请求未确认，请重试。' }
        }
        return accepted
      }

      const accepted = await resumeSessionWaitpoint({
        ...target,
        runId,
        answer,
        isCurrent: () => sameWaitpoint() && !current.cancellationPending
      })
      if (!sameWaitpoint() || current.cancellationPending) return true

      // 接收回执消费此等待点；历史或流失败由读取路径恢复。
      current.agentStateRequestVersion += 1
      current.pendingInterrupt = null
      current.isStreaming = true
      runtime.resetThreadExecution(id, { preserveInputMonitors: true })
      const version = current.runStateVersion
      try {
        await runtime.reconcileTurnHistory(id, turnId)
      } catch (error) {
        console.warn('Failed to refresh history before resume stream:', error)
      }
      if (sameRun() && current.runStateVersion === version && !current.pendingInterrupt) {
        void runtime.startRunStream(id, accepted.run_id, null, { turnId }).catch((error) => {
          if (runtime.isCurrentThreadState(id, current)) handleChatError(error, 'stream')
        })
      }
      return true
    } catch (error) {
      if (operation === 'submit' && sameWaitpoint()) onExecutionError?.(id, error)
      if (stillVisible()) {
        feedback.value = {
          state: current,
          waitpointId,
          text: error?.message || '提交失败，请重试。'
        }
      }
      return false
    } finally {
      if (current.approvalSubmitting === waitpointId) current.approvalSubmitting = false
      release()
    }
  }
  return {
    action,
    visible,
    isToolApproval,
    processing,
    error,
    submit: (answer, waitpointId) => execute('submit', waitpointId, answer),
    cancel: (waitpointId) => execute('cancel', waitpointId)
  }
}
