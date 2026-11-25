import assert from 'node:assert/strict'
import test from 'node:test'
import { nextTick, toRaw } from 'vue'
import { message } from 'ant-design-vue'
import { mountSessionWorkspace } from '../helpers/sessionWorkspace.js'

/** 在真实审批入口控制 Turn 响应和续跑连接。 */
async function setupSubmission(t, { accepted = false } = {}) {
  const original = {
    threadId: 'thread', interruptedRunId: 'run-A', waitpointId: 'wait-A',
    kind: 'question', questions: [{ question_id: 'q-A', question: '旧问题' }]
  }
  const errors = []
  let rejectRequest, markStreamStarted
  const streamStarted = new Promise((resolve) => { markStreamStarted = resolve })
  const pending = new Promise((_, reject) => { rejectRequest = reject })
  t.mock.method(message, 'error', (error) => errors.push(error))
  const { state: panel, runtime, agentApi } = await mountSessionWorkspace(t, async () => {}, {
    threadId: 'thread', runtime: { startRunStream: () => { markStreamStarted(); return pending } }
  })
  t.mock.method(agentApi, 'getThreadTurn', async () => ({
    status: 'requires_action', yuxi: { current_run_id: 'run-A', waitpoint: {
      id: 'wait-A', run_id: 'run-A', questions: original.questions
    } }
  }))
  t.mock.method(agentApi, 'resumeThreadTurn', () => accepted ? { run_id: 'run-B' } : pending)
  const state = runtime.getThreadState('thread')
  Object.assign(state, {
    currentTurnId: 'turn', activeRunId: 'run-A', runStateVersion: 1,
    turnStatus: 'requires_action', pendingInterrupt: original, approvalSubmitting: false,
    isStreaming: false
  })
  panel.restorePendingInterruptForThread('thread')
  return {
    state, approval: { approvalState: panel.approvalState,
      hideApprovalState: panel.hideApprovalState,
      restoreInterruptFromThreadState: panel.restorePendingInterruptForThread },
    submit: panel.handleApprovalWithStream, visible: panel.currentApprovalModalVisible,
    errors, streamStarted, reject: () => rejectRequest(new Error('延迟失败'))
  }
}

test('原审批未变化时提交失败，保留审批并允许重试', async (t) => {
  const h = await setupSubmission(t)
  const pendingInterrupt = h.state.pendingInterrupt
  const submitting = h.submit({ 'q-A': '回答' })
  assert.equal(h.state.approvalSubmitting, true)
  h.reject()
  await submitting
  assert.equal(h.state.pendingInterrupt, pendingInterrupt)
  assert.equal(h.visible.value, true)
  assert.equal(h.state.isStreaming, false)
  assert.equal(h.state.approvalSubmitting, false)
  assert.match(String(h.errors[0]), /延迟失败/)
})

for (const status of ['in_progress', 'completed', 'requires_action']) {
  test(`旧审批提交失败不能覆盖后续状态：${status}`, async (t) => {
    const h = await setupSubmission(t)
    const submitting = h.submit({ 'q-A': '回答' })
    Object.assign(h.state, {
      activeRunId: status === 'completed' ? null : 'run-B',
      runStateVersion: 2,
      turnStatus: status,
      pendingInterrupt: status === 'requires_action' ? {
        threadId: 'thread', interruptedRunId: 'run-B', waitpointId: 'wait-B',
        kind: 'question', questions: [{ question_id: 'q-B', question: '新问题' }]
      } : null,
      isStreaming: status === 'in_progress'
    })
    h.approval.hideApprovalState()
    if (status === 'requires_action') h.approval.restoreInterruptFromThreadState('thread')
    const expected = structuredClone(toRaw(h.state))
    h.reject()
    await submitting
    assert.deepEqual(structuredClone(toRaw(h.state)), { ...expected, approvalSubmitting: false })
    assert.equal(h.visible.value, status === 'requires_action')
    assert.equal(h.approval.approvalState.interruptedRunId, status === 'requires_action' ? 'run-B' : null)
  })
}

test('审批已接受后续跑连接失败，不能恢复已消费的旧审批', async (t) => {
  const h = await setupSubmission(t, { accepted: true })
  const submitting = h.submit({ 'q-A': '回答' })
  await h.streamStarted
  assert.equal(h.state.pendingInterrupt, null)
  assert.equal(h.visible.value, false)
  h.reject()
  await submitting
  assert.equal(h.state.pendingInterrupt, null)
  assert.equal(h.visible.value, false)
  assert.equal(h.state.isStreaming, true)
  assert.equal(h.state.approvalSubmitting, false)
  await nextTick()
})
