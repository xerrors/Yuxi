import assert from 'node:assert/strict'
import test from 'node:test'
import { effectScope, ref } from 'vue'
import { message } from 'ant-design-vue'
import { setImmediate } from 'node:timers/promises'
import { createTestServer } from '../helpers/createTestServer.js'

/** 让请求和响应顺序由用例控制，验证真实组合函数的目录归属。 */
const settle = async () => {
  for (let i = 0; i < 8; i++) await setImmediate()
}

test('资源提及隔离会话和登录身份，草稿回退，隐藏和卸载取消请求', async (t) => {
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false } })
  const scope = effectScope()
  const requests = []
  const authToken = ref('test-token')
  try {
    const { configureApiAuth } = await server.ssrLoadModule('/src/apis/auth_context.js')
    configureApiAuth({ getToken: () => authToken.value })
    t.mock.method(globalThis, 'fetch', (url, options) => new Promise((resolve) => {
      requests.push({ url, signal: options.signal, token: new Headers(options.headers).get('authorization'), resolve })
    }))
    const { useSessionMentionResources } = await server.ssrLoadModule(
      '/src/modules/session/model/useSessionMentionResources.js'
    )
    const thread = ref(null)
    const active = ref(true)
    const resources = scope.run(() => useSessionMentionResources(thread, active, authToken))
    assert.equal(resources.value, null)
    thread.value = 'first'
    await settle()
    assert.deepEqual(resources.value, {})
    assert.equal(new URL(requests[0].url, 'http://localhost').searchParams.get('thread_id'), 'first')
    thread.value = 'second'
    await settle()
    assert.equal(requests[0].signal.aborted, true)
    requests[1].resolve(Response.json({ plugins: [{ key: 'abridge', name: 'aBridge 设备' }] }))
    await settle()
    requests[0].resolve(Response.json({ skills: [{ key: 'stale', name: '旧会话 Skill' }] }))
    await settle()
    assert.deepEqual(resources.value, { plugins: [{ key: 'abridge', name: 'aBridge 设备' }] })
    active.value = false
    assert.deepEqual(resources.value, {})
    assert.equal(requests.length, 2)
    active.value = true
    await settle()
    authToken.value = 'next-test-token'
    assert.deepEqual(resources.value, {})
    assert.equal(requests[2].signal.aborted, true)
    await settle()
    assert.equal(requests[3].token, 'Bearer next-test-token')
    assert.equal(new URL(requests[3].url, 'http://localhost').searchParams.get('thread_id'), 'second')
    requests[2].resolve(Response.json({ knowledges: [{ key: 'previous-account' }] }))
    await settle()
    assert.deepEqual(resources.value, {})
    scope.stop()
    assert.equal(requests[3].signal.aborted, true)
    requests[3].resolve(Response.json({ knowledges: [{ key: 'late' }] }))
    await settle()
    assert.deepEqual(resources.value, {})
  } finally {
    scope.stop()
    await server.close()
  }
})

test('资源目录失败显示错误并保持空列表，回到新会话恢复 Agent 目录', async (t) => {
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false } })
  const scope = effectScope()
  try {
    const { configureApiAuth } = await server.ssrLoadModule('/src/apis/auth_context.js')
    const errors = []
    configureApiAuth({ getToken: () => 'test-token' })
    t.mock.method(message, 'error', (text) => errors.push(text))
    t.mock.method(console, 'error', () => {})
    t.mock.method(globalThis, 'fetch', async () => Response.json({}, { status: 503 }))
    const { useSessionMentionResources } = await server.ssrLoadModule(
      '/src/modules/session/model/useSessionMentionResources.js'
    )
    const thread = ref('existing')
    const resources = scope.run(() => useSessionMentionResources(thread, ref(true), ref('test-token')))
    await settle()
    assert.deepEqual(resources.value, {})
    assert.deepEqual(errors, ['会话资源列表加载失败，请重新打开对话重试'])
    thread.value = null
    assert.equal(resources.value, null)
  } finally {
    scope.stop()
    await server.close()
  }
})
