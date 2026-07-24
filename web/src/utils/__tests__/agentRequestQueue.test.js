import assert from 'node:assert/strict'
import test from 'node:test'

import {
  applySteerRequestEvent,
  getQueuedRequestStatusText,
  getRunTerminalNotice,
  getSteerHandoffNoticeKey,
  getSteerFailureMessage,
  isPendingSteerRequest,
  shouldFinalizeRunStream,
  shouldNotifySteeredRunEnd
} from '../agentRequestQueue.js'

test('queued request 原地升级并保留 request_id', () => {
  const requests = [
    { request_id: 'request-c', status: 'queued', queue_policy: 'enqueue', queue_position: 2 }
  ]

  const upgraded = applySteerRequestEvent(requests, 'request-c', 'steering', {
    target_run_id: 'run-a'
  })

  assert.equal(upgraded.request_id, 'request-c')
  assert.equal(upgraded.queue_policy, 'steer')
  assert.equal(upgraded.target_run_id, 'run-a')
  assert.equal(upgraded.queue_position, null)
  assert.equal(isPendingSteerRequest(upgraded), true)
  assert.equal(getQueuedRequestStatusText(upgraded), '等待当前工具完成后接替')
})

test('steered 终态与普通取消使用不同提示', () => {
  assert.equal(
    getRunTerminalNotice({ status: 'cancelled', reason: 'steered' }),
    '当前任务已由新的引导请求接替'
  )
  assert.equal(getRunTerminalNotice({ status: 'cancelled', reason: 'cancelled' }), '')
})

test('Steer 目标中断返回可操作提示', () => {
  assert.equal(
    getSteerFailureMessage('steer_target_interrupted'),
    '原任务正在等待回答或审批，请处理后重新引导'
  )
})

test('迟到的旧 Run end 不清理 replacement stream', () => {
  assert.equal(shouldFinalizeRunStream('replacement-run', 'old-run'), false)
  assert.equal(shouldFinalizeRunStream('old-run', 'old-run'), true)
  const steeredEnd = { status: 'cancelled', reason: 'steered' }
  assert.equal(shouldNotifySteeredRunEnd(steeredEnd, false), true)
  assert.equal(shouldNotifySteeredRunEnd(steeredEnd, true), false)
  const shownNotices = new Set()
  const requestSseKey = getSteerHandoffNoticeKey('thread-1', 'steer-request')
  const oldRunEndKey = getSteerHandoffNoticeKey('thread-1', 'steer-request')
  shownNotices.add(requestSseKey)
  shownNotices.add(oldRunEndKey)
  assert.equal(shownNotices.size, 1)
})

test('steer_ready 使用安全交接文案且普通 FIFO 文案不变', () => {
  const requests = [{ request_id: 'request-b', status: 'queued', queue_policy: 'steer' }]
  const ready = applySteerRequestEvent(requests, 'request-b', 'steer_ready', {})

  assert.equal(ready.status, 'steer_ready')
  assert.equal(getQueuedRequestStatusText(ready), '正在安全接替')
  assert.equal(
    getQueuedRequestStatusText({ status: 'queued', queue_policy: 'enqueue', queue_position: 3 }),
    '排队 3'
  )
})
