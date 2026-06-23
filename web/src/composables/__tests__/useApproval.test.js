import assert from 'node:assert/strict'

import { extractPendingInterrupt } from '../approvalInterrupt.js'

const run = () => {
  // ask_user_question: 仍走 questions 通道
  const askChunk = {
    status: 'ask_user_question_required',
    questions: [{ question: '选择一个', options: ['A', 'B'] }],
    source: 'ask_user',
    thread_id: 't1',
    run_id: 'run-1'
  }
  const askPending = extractPendingInterrupt(askChunk, 't1')
  assert.equal(askPending.kind, 'ask_user_question')
  assert.equal(askPending.questions.length, 1)
  assert.equal(askPending.parentRunId, 'run-1')

  // human_approval: 走 action_requests 通道
  const hilChunk = {
    status: 'human_approval_required',
    action_requests: [{ name: 'delete_file', args: { path: '/tmp/x' }, description: '需要确认' }],
    review_configs: [{ action_name: 'delete_file', allowed_decisions: ['approve', 'reject'] }],
    thread_id: 't2',
    run_id: 'run-2'
  }
  const hilPending = extractPendingInterrupt(hilChunk, 't2')
  assert.equal(hilPending.kind, 'human_approval')
  assert.equal(hilPending.actionRequests.length, 1)
  assert.equal(hilPending.actionRequests[0].name, 'delete_file')
  assert.equal(hilPending.reviewConfigs.length, 1)
  assert.equal(hilPending.parentRunId, 'run-2')
  assert.equal(hilPending.questions, undefined)

  // human_approval 但 payload 被误塞空 questions(修复前的退化情形)→ 应返回 null
  const degradedChunk = {
    status: 'human_approval_required',
    questions: []
  }
  assert.equal(extractPendingInterrupt(degradedChunk, 't3'), null)

  // 非 interrupt 状态 → null
  assert.equal(extractPendingInterrupt({ status: 'finished' }, 't4'), null)
}

run()
