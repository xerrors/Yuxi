import assert from 'node:assert/strict'
import test from 'node:test'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'

import { createTestServer } from '../helpers/createTestServer.js'

const KNOWLEDGE_PATH = '/api/v1/knowledge/tools/query_kb'

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' }
  })
}

test('知识库公开端点只在能力明确为 true 时列入', async () => {
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  try {
    const { publicApiEndpoints } = await server.ssrLoadModule('/src/apis/apikey_api.js')
    for (const knowledgeEnabled of [undefined, null, false, 'true']) {
      const paths = publicApiEndpoints(knowledgeEnabled).map((item) => item.path)
      assert.equal(paths.some((path) => path.includes('/knowledge/')), false, String(knowledgeEnabled))
      assert.ok(paths.every((path) => path.startsWith('/api/v1/agents/')))
    }
    const enabled = publicApiEndpoints(true)
    assert.ok(enabled.some((item) => item.path === KNOWLEDGE_PATH))
    assert.equal(enabled.filter((item) => item.path.includes('/knowledge/')).length, 5)
    assert.equal(
      new Set(enabled.map((item) => `${item.method} ${item.path}`)).size,
      enabled.length
    )
  } finally {
    await server.close()
  }
})

test('集成页在能力未加载时不展示知识库端点，发现开启后才加入', async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  globalThis.fetch = async (url) => {
    if (String(url).includes('/api/system/discovery')) {
      return jsonResponse({ capabilities: { features: { knowledge: true } } })
    }
    return jsonResponse({ detail: '未请求' }, 404)
  }
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  try {
    const { default: Panel } = await server.ssrLoadModule(
      '/src/modules/settings/ui/ApiIntegrationPanel.vue'
    )
    let state
    const app = createSSRApp({
      setup() {
        state = Panel.setup({}, { expose() {}, emit() {}, attrs: {}, slots: {} })
        return () => h('div')
      }
    })
    await renderToString(app)
    const paths = () => state.endpoints.value.map((item) => item.path)
    assert.equal(paths().some((path) => path.includes('/knowledge/')), false)
    await state.loadKnowledgeCapability()
    assert.ok(paths().includes(KNOWLEDGE_PATH))

    globalThis.fetch = async () => jsonResponse({ capabilities: { features: { knowledge: false } } })
    await state.loadKnowledgeCapability()
    assert.equal(paths().some((path) => path.includes('/knowledge/')), false)

    globalThis.fetch = async () => {
      throw new Error('discovery down')
    }
    await state.loadKnowledgeCapability()
    assert.equal(state.knowledgeEnabled.value, false)
    assert.equal(paths().some((path) => path.includes('/knowledge/')), false)
  } finally {
    await server.close()
    delete globalThis.fetch
    delete globalThis.localStorage
  }
})
