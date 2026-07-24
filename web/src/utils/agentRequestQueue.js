export function isPendingSteerRequest(request) {
  return (
    request?.queue_policy === 'steer' &&
    (request.status === 'queued' || request.status === 'steer_ready')
  )
}

export function getQueuedRequestStatusText(request) {
  if (request?.queue_policy === 'steer') {
    return request.status === 'steer_ready' ? '正在安全接替' : '等待当前工具完成后接替'
  }
  return `排队 ${request?.queue_position || request?.position || 1}`
}

export function applySteerRequestEvent(requests, requestId, event, data = {}) {
  const request = requests?.find((item) => item.request_id === requestId)
  if (!request || (event !== 'steering' && event !== 'steer_ready')) return request

  request.status = event === 'steer_ready' ? 'steer_ready' : 'queued'
  request.queue_policy = 'steer'
  request.target_run_id = data.target_run_id || request.target_run_id
  request.queue_position = null
  return request
}

export function getRunTerminalNotice(payload) {
  return payload?.status === 'cancelled' && payload?.reason === 'steered'
    ? '当前任务已由新的引导请求接替'
    : ''
}

export function getSteerFailureMessage(errorCode) {
  const messages = {
    steer_target_failed: '原任务执行失败，引导请求未执行',
    steer_target_cancelled: '原任务已取消，引导请求未执行',
    steer_target_interrupted: '原任务正在等待回答或审批，请处理后重新引导'
  }
  return messages[errorCode] || '引导请求未执行，请稍后重试'
}

export function shouldFinalizeRunStream(activeRunId, terminalRunId) {
  return Boolean(activeRunId && terminalRunId && activeRunId === terminalRunId)
}

export function shouldNotifySteeredRunEnd(payload, alreadyNotified = false) {
  return !alreadyNotified && payload?.status === 'cancelled' && payload?.reason === 'steered'
}

export function getSteerHandoffNoticeKey(threadId, requestId) {
  return threadId && requestId ? `${threadId}:${requestId}` : ''
}
