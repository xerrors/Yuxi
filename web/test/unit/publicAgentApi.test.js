import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

test('产品创建、follow-up、控制事件与订阅使用 Public Session', async () => {
  const saved = new Map()
  globalThis.localStorage = {
    getItem: (key) => saved.get(key) ?? null,
    setItem: (key, value) => saved.set(key, String(value)),
    removeItem: (key) => saved.delete(key)
  }
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options })
    const body = String(url).includes('/api/agent/runs/')
      ? { run: { conversation_thread_id: 'session-1', turn_id: 'turn-1' } }
      : String(url).endsWith('/sessions')
        ? { id: 'session-1', title: '新对话', project_id: 'project-1' }
        : { status: 'accepted', request_id: 'persisted-request' }
    return new Response(JSON.stringify(body), {
      status: String(url).includes('/events') && options?.method === 'POST' ? 202 : 200,
      headers: { 'content-type': 'application/json' }
    })
  }
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    setActivePinia(createPinia())
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const userStore = useUserStore()
    userStore.token = 'test-token'
    userStore.userId = 1
    const { agentApi, threadApi } = await server.ssrLoadModule('/src/apis/agent_api.js')

    const thread = await threadApi.createThread(
      'agent-1', '新对话', { tool_approval_mode: 'default' },
      { requestId: 'create-key', projectId: 'project-1' }
    )
    assert.equal(thread.id, 'session-1')
    assert.equal(calls[0].url, '/api/v1/agents/sessions')
    assert.equal(calls[0].options.headers['Idempotency-Key'], 'create-key')

    await agentApi.sendSessionMessage('session-1', {
      request_id: 'message-key',
      query: '继续',
      image_content: ['abc'],
      queue_policy: 'enqueue',
      attachment_file_ids: ['file-1']
    })
    assert.equal(calls[1].url, '/api/v1/agents/sessions/session-1/events')
    assert.equal(calls[1].options.headers['Idempotency-Key'], 'message-key')
    const message = JSON.parse(calls[1].options.body).events[0]
    assert.equal(message.mode, 'follow_up')
    assert.equal(message.input[0].content[1].image_url, 'data:image/jpeg;base64,abc')
    assert.deepEqual(message.attachment_file_ids, ['file-1'])

    await agentApi.cancelSessionTurn('session-1', 'cancel-key', 'run-1')
    await agentApi.resumeSessionTurn('session-1', {
      turn_id: 'turn-1', run_id: 'run-1', resume: 'approve', request_id: 'resume-key'
    })
    assert.equal(JSON.parse(calls[2].options.body).events[0].type, 'agent.session.input.cancel')
    assert.equal(JSON.parse(calls[2].options.body).events[0].run_id, 'run-1')
    assert.equal(JSON.parse(calls[3].options.body).events[0].turn_id, 'turn-1')

    await agentApi.streamAgentRunEvents('run-1', '12-0', { threadId: 'session-1', publicSession: true })
    assert.equal(calls[4].url, '/api/agent/runs/run-1')
    assert.equal(calls[5].url, '/api/v1/agents/sessions/session-1/events?turn_id=turn-1')
    assert.equal(calls[5].options.headers['Last-Event-ID'], 'run-1:12-0')

    await agentApi.streamAgentRunEvents('subagent-run', '3-0', { verbose: true })
    assert.equal(calls[6].url, '/api/agent/runs/subagent-run/events?verbose=true')
    assert.equal(calls[6].options.headers['Last-Event-ID'], '3-0')
  } finally {
    await server.close()
    delete globalThis.fetch
    delete globalThis.localStorage
  }
})
