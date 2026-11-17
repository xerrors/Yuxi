import assert from 'node:assert/strict'
import test from 'node:test'
import { setImmediate } from 'node:timers'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, toRaw } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

test('身份交接只提交白名单参数，不接受危险跳转，失败后可重试', async () => {
  const requests = [],
    navigations = []
  let redirect = 'javascript:alert(1)'
  globalThis.window = { location: { assign: (url) => navigations.push(url) } }
  globalThis.__providerTest = {
    route: {
      query: {
        client_id: 'test-client',
        state: 'opaque',
        nonce: 'nonce',
        secret: 'excluded',
        scope: ['invalid']
      }
    },
    authorize: async (parameters) => {
      requests.push(parameters)
      return { redirect_url: redirect }
    }
  }
  const server = await createTestServer({
    root: fileURLToPath(new URL('../..', import.meta.url)),
    configFile: false,
    ssr: { noExternal: ['vue-router'] },
    server: { middlewareMode: true },
    appType: 'custom',
    plugins: [
      {
        name: 'provider-boundaries',
        enforce: 'pre',
        resolveId(id) {
          if (['virtual:provider', '@/apis/oidc_provider_api', 'vue-router'].includes(id))
            return '\0' + id
        },
        load(id) {
          if (id === '\0@/apis/oidc_provider_api')
            return 'export const oidcProviderApi = globalThis.__providerTest'
          if (id === '\0vue-router')
            return 'export const useRoute = () => globalThis.__providerTest.route'
          if (id === '\0virtual:provider') {
            const source = readFileSync(
              new URL('../../src/pages/OIDCProviderView.vue', import.meta.url),
              'utf8'
            )
            return compileScript(parse(source).descriptor, { id: 'provider' }).content
          }
        }
      }
    ]
  })
  let app
  try {
    const { default: component } = await server.ssrLoadModule('virtual:provider')
    const renderer = createRenderer({
      createElement: () => ({}),
      createText: () => ({}),
      createComment: () => ({}),
      insert() {},
      remove() {},
      setText() {},
      setElementText() {},
      patchProp() {},
      parentNode: () => null,
      nextSibling: () => null
    })
    app = renderer.createApp({ ...component, render: () => null })
    app.mount({})
    await new Promise((resolve) => setImmediate(resolve))
    const state = toRaw(app._instance.setupState)
    assert.deepEqual(requests, [{ client_id: 'test-client', state: 'opaque', nonce: 'nonce' }])
    assert.deepEqual(navigations, [])
    assert.match(state.errorMessage.value, /登录返回地址无效/)
    redirect = 'https://client.example/callback?code=test'
    await state.authorize()
    assert.deepEqual(navigations, [redirect])
  } finally {
    app?.unmount()
    await server.close()
    delete globalThis.window
    delete globalThis.__providerTest
  }
})
