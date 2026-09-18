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

test('只有 parsed 可编辑解析产物（与后端 EDITABLE_MARKDOWN_STATUSES 同集合）', async () => {
  // 第一阶段只放开 parsed：该状态没有派生索引，覆盖即发布。已入库内容的修改要走
  // 「重新入库」Durable Task，前端若仍显示编辑入口，用户点了只会拿到 409。
  await withServer(async (server) => {
    const { canEditParsedContent } = await server.ssrLoadModule(
      '/src/utils/knowledge_file_policy.js'
    )

    assert.equal(canEditParsedContent(file({ status: 'parsed' })), true)
    for (const status of ['indexed', 'error_indexing', 'done']) {
      assert.equal(canEditParsedContent(file({ status })), false, `${status} 不应可编辑`)
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

test('已入库文件不显示编辑入口，但仍可预览分块', async () => {
  // 编辑入口与「能不能看分块」是两件事：已入库文件有分块可看，但产物不再允许编辑。
  await withServer(async (server) => {
    const { canEditParsedContent, canPreviewChunks } = await server.ssrLoadModule(
      '/src/utils/knowledge_file_policy.js'
    )

    assert.equal(canPreviewChunks(file({ status: 'indexed' })), true)
    assert.equal(canEditParsedContent(file({ status: 'indexed' })), false)
    assert.equal(canPreviewChunks(file({ status: 'error_indexing' })), false)
    assert.equal(canEditParsedContent(file({ status: 'error_indexing' })), false)
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

    await documentApi.updateDocumentContent('kb_1', 'file_9', '# 修订后的内容', 'rev-abc')

    assert.equal(calls.length, 1)
    assert.equal(calls[0].url, '/api/knowledge/databases/kb_1/documents/file_9/content')
    assert.equal(calls[0].method, 'PUT')
    // revision 必须随内容一起提交：后端靠它检出「产物已被他人修改」
    assert.deepEqual(JSON.parse(calls[0].body), { content: '# 修订后的内容', revision: 'rev-abc' })
  })
})
