export const TOOL_APPROVAL_MODES = ['default', 'always_trust']
export const TOOL_APPROVAL_MODE_STORAGE_KEY = 'yuxi_tool_approval_mode'

export const isToolApprovalMode = (value) => TOOL_APPROVAL_MODES.includes(value)

const resolveStorage = (storage) =>
  storage || (typeof window !== 'undefined' ? window.localStorage : null)

export const readToolApprovalModePreference = (storage) => {
  const targetStorage = resolveStorage(storage)
  if (!targetStorage) return null

  try {
    const value = targetStorage.getItem(TOOL_APPROVAL_MODE_STORAGE_KEY)
    return isToolApprovalMode(value) ? value : null
  } catch {
    return null
  }
}

export const writeToolApprovalModePreference = (mode, storage) => {
  if (!isToolApprovalMode(mode)) return false
  const targetStorage = resolveStorage(storage)
  if (!targetStorage) return false

  try {
    targetStorage.setItem(TOOL_APPROVAL_MODE_STORAGE_KEY, mode)
    return true
  } catch {
    return false
  }
}

export const buildToolApprovalDecisions = (selectedDecisions, actionCount) =>
  Array.from({ length: actionCount }, (_, index) =>
    selectedDecisions[index] === 'approve'
      ? { type: 'approve' }
      : { type: 'reject', message: '用户拒绝执行该操作' }
  )

export const hasPendingInterruptPayload = (pendingInterrupt) => {
  if (!pendingInterrupt) return false
  if (pendingInterrupt.kind === 'tool_approval') {
    return Array.isArray(pendingInterrupt.actionRequests) && pendingInterrupt.actionRequests.length > 0
  }
  return Array.isArray(pendingInterrupt.questions) && pendingInterrupt.questions.length > 0
}

export const isThreadWaitingForUserAction = (threadState) =>
  hasPendingInterruptPayload(threadState?.pendingInterrupt) ||
  threadState?.queueSnapshot?.status === 'interrupted'
