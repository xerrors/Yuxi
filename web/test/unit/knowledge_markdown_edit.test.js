import assert from 'node:assert/strict'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

const storageValues = new Map([['user_token', 'test-token']])
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

async function withServer(run) {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    await run(server)
  } finally {
    await server.close()
  }
}

const file = (overrides = {}) => ({
  file_id: 'file-1',
  is_folder: false,
  status: 'parsed',
  has_parsed_markdown: true,
  ...overrides
})

test('可编辑解析产物的状态集合为 parsed / indexed / error_indexing / done', async () => {
  await withServer(async (server) => {
    const { canEditParsedContent } = await server.ssrLoadModule(
      '/src/utils/knowledge_file_policy.js'
    )

    for (const status of ['parsed', 'indexed', 'error_indexing', 'done']) {
      assert.equal(canEditParsedContent(file({ status })), true, `${status} 应可编辑`)
    }
  })
})

test('不可编辑的状态被拒绝：uploaded 尚未解析，parsing / indexing 正在动作中', async () => {
  await withServer(async (server) => {
    const { canEditParsedContent } = await server.ssrLoadModule(
      '/src/utils/knowledge_file_policy.js'
    )

    for (const status of ['uploaded', 'error_parsing', 'parsing', 'indexing']) {
      assert.equal(canEditParsedContent(file({ status })), false, `${status} 不应可编辑`)
    }
  })
})

test('文件夹与空记录不可编辑', async () => {
  await withServer(async (server) => {
    const { canEditParsedContent } = await server.ssrLoadModule(
      '/src/utils/knowledge_file_policy.js'
    )

    assert.equal(canEditParsedContent(file({ is_folder: true })), false)
    assert.equal(canEditParsedContent(null), false)
    assert.equal(canEditParsedContent(undefined), false)
  })
})

test('状态可编辑但没有解析产物时不可编辑', async () => {
  // 关键边界：状态是 parsed 不代表一定有产物。没有 Markdown 就没有可改的内容，
  // 后端也会以「文件尚未生成解析结果」拒绝，前端必须同判，否则点了才报错。
  await withServer(async (server) => {
    const { canEditParsedContent } = await server.ssrLoadModule(
      '/src/utils/knowledge_file_policy.js'
    )

    assert.equal(
      canEditParsedContent(file({ status: 'parsed', has_parsed_markdown: false })),
      false
    )
    assert.equal(
      canEditParsedContent(file({ status: 'indexed', has_parsed_markdown: false })),
      false
    )
  })
})

test('willPurgeIndexOnSave 与后端 was_indexed 同集合，含 error_indexing', async () => {
  // 后端（base.py）的清索引集合是 INDEXED_STATS_STATUSES ∪ {error_indexing}。
  // 若前端只认 {done, indexed}，error_indexing 文件会被后端清空索引而前端既不弹确认
  // 也不换提示文案——用户以为只是改了几个字。
  await withServer(async (server) => {
    const { willPurgeIndexOnSave, canPreviewChunks } = await server.ssrLoadModule(
      '/src/utils/knowledge_file_policy.js'
    )

    for (const status of ['done', 'indexed', 'error_indexing']) {
      assert.equal(willPurgeIndexOnSave(file({ status })), true, `${status} 保存后会清索引`)
    }
    for (const status of ['parsed', 'uploaded', 'parsing', 'indexing', 'error_parsing']) {
      assert.equal(willPurgeIndexOnSave(file({ status })), false, `${status} 保存后不清索引`)
    }

    // 这正是两者必须分开的理由：error_indexing 会清索引，但没有分块可预览
    assert.equal(canPreviewChunks(file({ status: 'error_indexing' })), false)
    assert.equal(willPurgeIndexOnSave(file({ status: 'error_indexing' })), true)
  })
})

test('保存解析产物走 PUT，URL 与请求体正确', async () => {
  await withServer(async (server) => {
    const calls = []
    globalThis.fetch = async (url, options = {}) => {
      calls.push({ url: String(url), method: options.method, body: options.body })
      return new Response(JSON.stringify({ status: 'success', meta: { status: 'parsed' } }), {
        status: 200,
        headers: { 'content-type': 'application/json' }
      })
    }

    setActivePinia(createPinia())
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    useUserStore().userRole = 'superadmin'
    const { documentApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')

    await documentApi.updateDocumentContent('kb_1', 'file_9', '# 修订后的内容')

    assert.equal(calls.length, 1)
    assert.equal(calls[0].url, '/api/knowledge/databases/kb_1/documents/file_9/content')
    assert.equal(calls[0].method, 'PUT')
    assert.deepEqual(JSON.parse(calls[0].body), { content: '# 修订后的内容' })
  })
})
