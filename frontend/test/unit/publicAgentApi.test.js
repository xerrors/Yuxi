import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createTestServer } from '../helpers/createTestServer.js'

test('产品创建、消息、等待恢复和队列控制仅使用 Public Thread', async () => {
  const saved = new Map()
  globalThis.localStorage = {
    getItem: (key) => saved.get(key) ?? null,
    setItem: (key, value) => saved.set(key, String(value)),
    removeItem: (key) => saved.delete(key)
  }
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options })
    const body = String(url).endsWith('/sessions')
      ? {
          object: 'agent.session', id: 'thread-1', status: 'idle', agent: { id: 'agent-1', model: null },
          yuxi: { title: '新对话', project_id: 'project-1', archived: false,
          receipt: { event_id: 'create-event', input_id: null, turn_id: null, run_id: null, status: 'accepted' } }
        }
      : { status: 'accepted', event_id: 'event-1', input_id: 'input-1' }
    return new Response(JSON.stringify(body), {
      status: options?.method === 'POST' && String(url).endsWith('/events') ? 202 : 200,
      headers: { 'content-type': 'application/json' }
    })
  }
  const server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    setActivePinia(createPinia())
    const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
    const userStore = useUserStore()
    userStore.token = 'test-token'
    userStore.userId = 1
    const { agentApi, threadApi } = await server.ssrLoadModule('/src/apis/agent_api.js')

    const thread = await threadApi.createThread(
      'agent-1', '新对话', { tool_approval_mode: 'default' },
      { requestId: 'create-key', projectId: 'project-1' }
    )
    assert.equal(thread.id, 'thread-1')
    assert.equal(thread.yuxi.title, '新对话')
    assert.equal(thread.yuxi.project_id, 'project-1')
    assert.equal(thread.object, 'agent.session')
    assert.equal(thread.status, 'idle')
    assert.ok(!('thread_id' in thread.yuxi))
    assert.equal(calls[0].url, '/api/v1/agents/sessions')
    assert.equal(calls[0].options.headers['Idempotency-Key'], 'create-key')
    assert.equal(JSON.parse(calls[0].options.body).agent_id, 'agent-1')

    await agentApi.sendThreadMessage('thread-1', {
      idempotency_key: 'message-key',
      query: '继续',
      image_content: ['data:image/png;base64,iVBORw0KGgo='],
      mode: 'follow_up',
      mcps: ['connector'],
      attachment_file_ids: ['file-1']
    })
    assert.equal(calls[1].url, '/api/v1/agents/sessions/thread-1/events')
    assert.equal(calls[1].options.headers['Idempotency-Key'], 'message-key')
    const message = JSON.parse(calls[1].options.body).events[0]
    assert.equal(message.type, 'agent.session.input.message')
    assert.equal(message.yuxi.mode, 'follow_up')
    assert.deepEqual(message.yuxi.attachment_file_ids, ['file-1'])
    assert.deepEqual(message.yuxi.mcps, ['connector'])
    assert.equal('yuxi' in message.input[0], false)
    assert.equal(message.input[0].content[1].image_url, 'data:image/png;base64,iVBORw0KGgo=')

    await agentApi.sendThreadMessage('thread-1', {
      idempotency_key: 'steer-key', query: '改成英文',
      mode: 'steer', turn_id: 'turn-1',
      image_content: ['data:image/png;base64,iVBORw0KGgo='], attachment_file_ids: ['file-2'], mcps: ['steer-connector']
    })
    assert.equal(JSON.parse(calls[2].options.body).events[0].yuxi.mode, 'steer')
    assert.ok(!('turn_id' in JSON.parse(calls[2].options.body).events[0].yuxi))
    assert.equal(JSON.parse(calls[2].options.body).events[0].input[0].content[1].type, 'input_image')
    assert.deepEqual(JSON.parse(calls[2].options.body).events[0].yuxi.attachment_file_ids, ['file-2'])
    assert.deepEqual(JSON.parse(calls[2].options.body).events[0].yuxi.mcps, ['steer-connector'])

    await agentApi.resumeThreadTurn('thread-1', {
      turn_id: 'turn-1', waitpoint_id: 'wait-1',
      response: { type: 'answer', answers: [
        { question_id: 'q-1', answer: ['杭州', '上海'] },
        { question_id: 'q-2', answer: { type: 'other', text: '苏州', selected: ['杭州'] } }
      ] },
      idempotency_key: 'resume-key'
    })
    assert.deepEqual(JSON.parse(calls[3].options.body).events[0], {
      type: 'yuxi.session.input.resume',
      turn_id: 'turn-1',
      waitpoint_id: 'wait-1',
      response: { type: 'answer', answers: [
        { question_id: 'q-1', answer: ['杭州', '上海'] },
        { question_id: 'q-2', answer: { type: 'other', text: '苏州', selected: ['杭州'] } }
      ] }
    })

    await agentApi.cancelThreadTurn('thread-1', 'turn-1', 'cancel-key', 'run-1')
    assert.deepEqual(JSON.parse(calls[4].options.body).events[0], {
      type: 'agent.session.input.cancel', yuxi: { turn_id: 'turn-1', expected_run_id: 'run-1' }
    })
    await agentApi.continueThreadQueue('thread-1', 'continue-key')
    await agentApi.cancelThreadInput('thread-1', 'input-2', 'cancel-input-key')
    assert.equal(JSON.parse(calls[5].options.body).events[0].type, 'yuxi.session.input.continue')
    assert.equal(JSON.parse(calls[6].options.body).events[0].input_id, 'input-2')

    await agentApi.streamThreadEvents('thread-1', 'cursor-7')
    assert.equal(calls[7].url, '/api/v1/agents/sessions/thread-1/events')
    assert.equal(calls[7].options.headers['Last-Event-ID'], 'cursor-7')

    await threadApi.updateThread('thread-1', null, undefined, 'default', 'model-a')
    assert.equal(calls[8].url, '/api/v1/agents/sessions/thread-1')
    assert.equal(calls[8].options.method, 'POST')
    assert.deepEqual(JSON.parse(calls[8].options.body), {
      yuxi: { title: null, tool_approval_mode: 'default' }, agent: { model: 'model-a' }
    })
    await threadApi.archiveThread('thread-1')
    assert.equal(calls[9].url, '/api/v1/agents/sessions/thread-1/archive')
    assert.equal(calls[9].options.method, 'POST')
    assert.ok(calls.every((call) => !call.url.includes('/api/chat/')))

    const longTitle = '协作😀'.repeat(100)
    await threadApi.createThread('agent-1', longTitle, {}, { requestId: 'long-title' })
    const sentTitle = JSON.parse(calls[10].options.body).title
    assert.equal(Array.from(sentTitle).length, 255)
    assert.equal(sentTitle, '协作😀'.repeat(85))

    await agentApi.getCooperationSummary('thread-1')
    assert.equal(calls.at(-1).url, '/api/v1/agents/sessions/thread-1/cooperation')

    await threadApi.createThread('agent-1', null, {}, { requestId: 'auto-title' })
    assert.equal(JSON.parse(calls.at(-1).options.body).title, null)

    await agentApi.promoteThreadInput('thread-1', 'input-2', 'promote-key')
    assert.deepEqual(JSON.parse(calls.at(-1).options.body).events[0], {
      type: 'yuxi.session.input.promote', input_id: 'input-2'
    })
    assert.equal(calls.at(-1).options.headers['Idempotency-Key'], 'promote-key')

    const controller = new AbortController()
    globalThis.fetch = async (_url, options) => {
      if (!options.signal) return new Response('{}', { status: 200 })
      return new Promise((_resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
      })
    }
    const pendingInput = agentApi.getThreadInput('thread-1', 'pending', { signal: controller.signal })
    controller.abort()
    await assert.rejects(pendingInput, { name: 'AbortError' })

  } finally {
    await server.close()
    delete globalThis.fetch
    delete globalThis.localStorage
  }
})
