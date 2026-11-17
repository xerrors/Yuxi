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

test('插件提及切换会话丢弃旧响应，草稿回退，隐藏和卸载取消请求', async (t) => {
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false } })
  const scope = effectScope()
  const requests = []
  try {
    const { configureApiAuth } = await server.ssrLoadModule('/src/apis/auth_context.js')
    configureApiAuth({ getToken: () => 'test-token' })
    t.mock.method(globalThis, 'fetch', (url, options) => new Promise((resolve) => {
      requests.push({ url, signal: options.signal, resolve })
    }))
    const { useSessionPluginMentions } = await server.ssrLoadModule(
      '/src/modules/session/model/useSessionPluginMentions.js'
    )
    const thread = ref(null)
    const active = ref(true)
    const plugins = scope.run(() => useSessionPluginMentions(thread, active))
    assert.equal(plugins.value, null)
    thread.value = 'first'
    await settle()
    assert.deepEqual(plugins.value, [])
    assert.equal(new URL(requests[0].url, 'http://localhost').searchParams.get('thread_id'), 'first')
    thread.value = 'second'
    await settle()
    assert.equal(requests[0].signal.aborted, true)
    requests[1].resolve(Response.json([{ id: 'abridge', name: 'aBridge 设备' }]))
    await settle()
    requests[0].resolve(Response.json([{ id: 'stale', name: '旧会话插件' }]))
    await settle()
    assert.deepEqual(plugins.value, [{ id: 'abridge', name: 'aBridge 设备' }])
    active.value = false
    assert.deepEqual(plugins.value, [])
    assert.equal(requests.length, 2)
    active.value = true
    await settle()
    scope.stop()
    assert.equal(requests[2].signal.aborted, true)
    requests[2].resolve(Response.json([{ id: 'late' }]))
    await settle()
    assert.deepEqual(plugins.value, [])
  } finally {
    scope.stop()
    await server.close()
  }
})

test('插件目录失败显示错误并保持空列表，回到新会话恢复 Agent 目录', async (t) => {
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false } })
  const scope = effectScope()
  try {
    const { configureApiAuth } = await server.ssrLoadModule('/src/apis/auth_context.js')
    const errors = []
    configureApiAuth({ getToken: () => 'test-token' })
    t.mock.method(message, 'error', (text) => errors.push(text))
    t.mock.method(console, 'error', () => {})
    t.mock.method(globalThis, 'fetch', async () => Response.json({}, { status: 503 }))
    const { useSessionPluginMentions } = await server.ssrLoadModule(
      '/src/modules/session/model/useSessionPluginMentions.js'
    )
    const thread = ref('existing')
    const plugins = scope.run(() => useSessionPluginMentions(thread, ref(true)))
    await settle()
    assert.deepEqual(plugins.value, [])
    assert.deepEqual(errors, ['插件列表加载失败，请重新打开对话重试'])
    thread.value = null
    assert.equal(plugins.value, null)
  } finally {
    scope.stop()
    await server.close()
  }
})
