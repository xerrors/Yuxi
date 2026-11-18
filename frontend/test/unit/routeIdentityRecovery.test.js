import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { createTestServer } from '../helpers/createTestServer.js'

test('身份校验在服务异常时保留凭据并拒绝导航，恢复后重试，401 仍退出', async (t) => {
  const storage = new Map()
  globalThis.localStorage = globalThis.sessionStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key)
  }
  const server = await createTestServer({ server: { middlewareMode: true }, ssr: { noExternal: ['ant-design-vue', 'vue-router'] }, plugins: [{
    name: 'identity-route-browser-boundaries', enforce: 'pre',
    resolveId(id, importer) {
      if (id === 'vue-router' && importer?.endsWith('/app/router/index.js')) return '\0memory-history'
      if (id.endsWith('.vue')) return '\0route-page'
      if (id === 'ant-design-vue') return '\0route-feedback'
    },
    load(id) {
      if (id === '\0memory-history') return "export { createRouter, createMemoryHistory as createWebHistory } from 'vue-router'"
      if (id === '\0route-page') return 'export default { render: () => null }'
      if (id === '\0route-feedback') return 'export const message = { error() {}, warning() {} }'
    }
  }] })
  const pinia = createPinia()
  setActivePinia(pinia)
  t.after(async () => {
    disposePinia(pinia)
    await server.close()
    delete globalThis.localStorage
    delete globalThis.sessionStorage
  })
  const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
  const { authApi } = await server.ssrLoadModule('/src/apis/auth_api.js')
  const { default: router } = await server.ssrLoadModule('/src/app/router/index.js')
  const user = useUserStore()
  const errors = []
  router.onError((error, to) => errors.push({ error, target: to.fullPath }))
  await router.push('/')
  user.applySession({ access_token: 'fixture-session', user_id: 1, uid: 'fixture', username: 'Fixture', role: 'superadmin' })
  user.userId = null
  for (const status of [502, undefined]) {
    const error = Object.assign(new Error('服务暂不可用'), { status })
    t.mock.method(authApi, 'getCurrentUser', async () => { throw error })
    await assert.rejects(router.push('/extensions?tab=knowledge'), (actual) => actual === error)
    assert.equal(router.currentRoute.value.fullPath, '/')
    assert.equal(storage.get('user_token'), 'fixture-session')
    assert.equal(user.userId, null)
    assert.equal(errors.at(-1).target, '/extensions?tab=knowledge')
  }
  t.mock.method(authApi, 'getCurrentUser', async () => ({ id: 1, uid: 'fixture', username: 'Fixture', role: 'superadmin' }))
  await router.push('/extensions?tab=knowledge')
  assert.equal(router.currentRoute.value.fullPath, '/extensions?tab=knowledge')
  assert.equal(user.isSuperAdmin, true)
  user.userId = null
  t.mock.method(authApi, 'getCurrentUser', async () => { throw Object.assign(new Error('认证失效'), { status: 401 }) })
  await router.push('/workspace')
  assert.equal(router.currentRoute.value.path, '/login')
  assert.equal(storage.has('user_token'), false)
  assert.equal(storage.get('redirect'), '/workspace')

  user.applySession({ access_token: 'fixture-user', user_id: 2, uid: 'ordinary', username: 'Fixture', role: 'user' })
  await router.push('/dashboard')
  assert.equal(router.currentRoute.value.path, '/agent', '保留凭据不能绕过角色守卫')
})
