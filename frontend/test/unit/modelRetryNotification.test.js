import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { message } from 'ant-design-vue'
import { createTestServer } from '../helpers/createTestServer.js'

let server
let useAgentStreamHandler
let createItemState
before(async () => {
  server = await createTestServer()
  ;({ useAgentStreamHandler } = await server.ssrLoadModule('/src/modules/session/model/useAgentStreamHandler.js'))
  ;({ createItemState } = await server.ssrLoadModule('/src/modules/session/model/agentItems.js'))
})
after(async () => server?.close())

test('模型重试通知按事件去重，只显示当前 Thread 的活动 Run', () => {
  const warnings = []
  const original = message.warning
  message.warning = (value) => warnings.push(value)
  try {
    const state = { activeRunId: 'run', itemState: createItemState() }
    const { handlePublicEvent } = useAgentStreamHandler({ getThreadState: () => state })
    const event = { type: 'yuxi.session.turn.model_retry', event_id: 'retry-1',
      session_id: 'thread', yuxi: { run_id: 'run' }, retry: { attempt: 1, delay_seconds: 2 } }
    handlePublicEvent(event, 'thread')
    handlePublicEvent(event, 'thread')
    handlePublicEvent({ ...event, event_id: 'other-run', yuxi: { run_id: 'old-run' } }, 'thread')
    handlePublicEvent({ ...event, event_id: 'other-thread', session_id: 'other' }, 'thread')
    state.activeRunId = null
    handlePublicEvent({ ...event, event_id: 'finished' }, 'thread')
    assert.equal(warnings.length, 1)
    assert.equal(warnings[0].content, '模型连接中断或响应超时，2 秒后进行第 1 次重试')
    assert.equal(warnings[0].key, 'model-retry-thread')
  } finally {
    message.warning = original
  }
})
