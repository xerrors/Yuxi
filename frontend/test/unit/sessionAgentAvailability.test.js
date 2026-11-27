import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'
import { useSessionAgentAvailability } from '../../src/modules/session/model/sessionAgentAvailability.js'

const checkWorkdir = async () => {}

for (const status of [403, 404]) {
  test(`详情 ${status} 为不可用，已有历史不受影响`, async () => {
    const thread = ref('history')
    const availability = useSessionAgentAvailability(thread, ref('token'), async () => { throw Object.assign(new Error('denied'), { status }) }, checkWorkdir)
    await availability.refresh('history', 'removed')
    assert.equal(availability.status.value, 'unavailable')
  })
}

test('有配置读取权但 can_run=false 时不可执行', async () => {
  const availability = useSessionAgentAvailability(ref('history'), ref('token'), async () => ({ can_run: false }), checkWorkdir)
  await availability.refresh('history', 'preview')
  assert.equal(availability.status.value, 'unavailable')
})

for (const status of [undefined, 503, 401]) {
  test(`临时或认证错误 ${status} 可重试，不误标为不可用`, async () => {
    let fail = true
    const availability = useSessionAgentAvailability(ref('history'), ref('token'), async () => {
      if (fail) throw Object.assign(new Error('failed'), { status })
      return { can_run: true }
    }, checkWorkdir)
    await availability.refresh('history', 'agent')
    assert.equal(availability.status.value, 'error')
    fail = false
    await availability.refresh('history', 'agent')
    assert.equal(availability.status.value, 'ready')
  })
}

test('切换对话后迟到的 404 不覆盖新对话，重试中的旧失败也不覆盖成功结果', async () => {
  const thread = ref('old')
  const requests = []
  const availability = useSessionAgentAvailability(thread, ref('token'), () => new Promise((resolve, reject) => requests.push({ resolve, reject })), checkWorkdir)
  const old = availability.refresh('old', 'old-agent')
  thread.value = 'new'
  const current = availability.refresh('new', 'new-agent')
  requests[1].resolve({ can_run: true }); await current
  requests[0].reject({ status: 404 }); await old
  assert.equal(availability.status.value, 'ready')
  const pending = availability.refresh('new', 'new-agent')
  const retry = availability.refresh('new', 'new-agent')
  requests[3].resolve({ can_run: true }); await retry
  requests[2].reject({ status: 503 }); await pending
  assert.equal(availability.status.value, 'ready')
  thread.value = 'other'
  assert.equal(availability.status.value, 'loading')
})

test('目录检查和执行拒绝都能停用会话，恢复原目录后重新打开可用', async () => {
  const thread = ref('history')
  const unavailable = { response: { data: { detail: { code: 'session_workdir_unavailable' } } } }
  let missing = true
  const availability = useSessionAgentAvailability(thread, ref('token'), async () => ({ can_run: true }), async () => {
    if (missing) throw unavailable
  })
  await availability.refresh('history', 'agent')
  assert.equal(availability.status.value, 'workdir-unavailable')
  missing = false
  await availability.refresh('history', 'agent')
  assert.equal(availability.status.value, 'ready')
  availability.handleExecutionError('other-thread', unavailable)
  assert.equal(availability.status.value, 'ready')
  availability.handleExecutionError('history', unavailable)
  assert.equal(availability.status.value, 'workdir-unavailable')
})

test('同一对话切换账号会清除可执行状态并丢弃旧账号的目录检查结果', async () => {
  const thread = ref('history')
  const token = ref('first-account')
  const checks = []
  const availability = useSessionAgentAvailability(thread, token, async () => ({ can_run: true }),
    () => new Promise(resolve => checks.push(resolve)))
  const initial = availability.refresh('history', 'agent')
  checks[0]()
  await initial
  assert.equal(availability.status.value, 'ready')
  token.value = 'second-account'
  assert.equal(availability.status.value, 'loading')
  const pending = availability.refresh('history', 'agent')
  token.value = 'third-account'
  checks[1]()
  await pending
  assert.equal(availability.status.value, 'loading')
})
