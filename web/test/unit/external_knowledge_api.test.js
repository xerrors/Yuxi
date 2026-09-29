import assert from 'node:assert/strict'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

test('external 知识库调用使用版本化查询路由并保留方法与参数', async () => {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom',
    plugins: [
      {
        name: 'test-message-api',
        enforce: 'pre',
        resolveId: (id) => (id === 'ant-design-vue' ? '\0test-message-api' : null),
        load: (id) => (id === '\0test-message-api' ? 'export const message = { error() {} }' : null)
      }
    ]
  })
  const calls = []
  globalThis.localStorage = {
    getItem: (key) => (key === 'user_token' ? 'test-token' : null),
    setItem() {},
    removeItem() {}
  }
  globalThis.fetch = async (url, options) => {
    calls.push({ url, method: options.method, body: options.body, authorization: options.headers.Authorization })
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'content-type': 'application/json' }
    })
  }

  try {
    setActivePinia(createPinia())
    const { externalKnowledgeApi } = await server.ssrLoadModule('/src/apis/external_knowledge_api.js')
    await externalKnowledgeApi.listDatabases()
    await externalKnowledgeApi.listFiles('kb 1', { query: '报告', offset: 0 })
    await externalKnowledgeApi.retrieve('kb 1', { query: 'hello', options: {} })
    await externalKnowledgeApi.openFile('kb 1', 'file/1', { offset: 0, limit: 20 })
    await externalKnowledgeApi.findFile('kb 1', 'file/1', { patterns: ['hello'] })

    assert.deepEqual(
      calls.map(({ url, method }) => [method, url]),
      [
        ['GET', '/api/v1/knowledge/databases/external'],
        ['GET', '/api/v1/knowledge/databases/external/kb%201/files?query=%E6%8A%A5%E5%91%8A&offset=0'],
        ['POST', '/api/v1/knowledge/databases/external/kb%201/retrieve'],
        ['GET', '/api/v1/knowledge/databases/external/kb%201/files/file%2F1/open?offset=0&limit=20'],
        ['POST', '/api/v1/knowledge/databases/external/kb%201/files/file%2F1/find']
      ]
    )
    assert.deepEqual(calls.map(({ authorization }) => authorization), Array(5).fill('Bearer test-token'))
    assert.deepEqual(JSON.parse(calls[2].body), { query: 'hello', options: {} })
    assert.deepEqual(JSON.parse(calls[4].body), { patterns: ['hello'] })
  } finally {
    await server.close()
  }
})
