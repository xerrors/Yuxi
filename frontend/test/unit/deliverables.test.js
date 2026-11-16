import assert from 'node:assert/strict'
import test from 'node:test'
import { createRenderer, h, ssrContextKey, toRaw } from 'vue'
import { message } from 'ant-design-vue'
import { createTestServer } from '../helpers/createTestServer.js'

/** 在 API 与浏览器边界装配完整成果组件。 */
async function mountDeliverables(t, api) {
  t.mock.method(message, 'error', () => {})
  const globals = Object.fromEntries(['window', 'document', '__deliverablesBoundary'].map((name) =>
    [name, Object.getOwnPropertyDescriptor(globalThis, name)]
  ))
  const createdUrls = [], revokedUrls = [], downloads = []
  let app, server
  t.after(async () => {
    app?.unmount()
    await server?.close()
    for (const [name, descriptor] of Object.entries(globals)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else delete globalThis[name]
    }
  })
  globalThis.__deliverablesBoundary = api
  globalThis.window = { URL: {
    createObjectURL: (blob) => { createdUrls.push(blob); return `blob:fixture-${createdUrls.length}` },
    revokeObjectURL: (url) => revokedUrls.push(url)
  } }
  globalThis.document = {
    body: { appendChild() {} },
    createElement: () => ({ click() { downloads.push({ name: this.download, url: this.href }) }, remove() {} })
  }
  server = await createTestServer({
    server: { middlewareMode: true }, appType: 'custom',
    plugins: [{
      name: 'deliverables-boundaries', enforce: 'pre',
      resolveId(id, importer) {
        if (!importer?.includes('/workspace/ui/DeliverablesBrowser.vue')) return
        if (id.endsWith('.vue')) return '\0deliverables-child'
        if (id.endsWith('/apis/deliverables_api')) return '\0deliverables-api'
      },
      load(id) {
        if (id === '\0deliverables-child') return 'export default { render: () => null }'
        if (id === '\0deliverables-api') return 'export const deliverablesApi = globalThis.__deliverablesBoundary'
      }
    }]
  })
  const { default: component } = await server.ssrLoadModule('/src/modules/workspace/ui/DeliverablesBrowser.vue')
  const renderer = createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
    parentNode: () => null, nextSibling: () => null
  })
  app = renderer.createApp({ ...component, render: () => h('div') })
  app.provide(ssrContextKey, { modules: new Set() })
  app.mount({})
  return { state: toRaw(app._instance.setupState), instance: app._instance, createdUrls, revokedUrls, downloads }
}

test('成果搜索分类从第一页读取，翻页和刷新以最新服务端结果为准', async (t) => {
  const calls = []
  let resolveOld, fail = false
  const { state } = await mountDeliverables(t, {
    list: async (params) => {
      calls.push(params)
      if (calls.length === 1) return new Promise((resolve) => { resolveOld = resolve })
      if (fail) throw new Error('成果读取失败')
      return { items: [{ id: 'image', name: '封面.png' }], total: 25, page: params.page, page_size: 20 }
    }
  })
  const oldRequest = state.load()
  state.query.value = ' 封面 '
  state.fileType.value = 'image'
  state.page.value = 3
  await state.search()
  assert.deepEqual(calls[1], { query: '封面', fileType: 'image', page: 1, pageSize: 20 })
  assert.equal(state.items.value[0].name, '封面.png')
  resolveOld({ items: [{ id: 'old', name: '旧文档.md' }], total: 1 })
  await oldRequest
  assert.equal(state.items.value[0].id, 'image', '旧搜索响应不得覆盖当前分类')
  await state.changePage(2)
  assert.equal(calls[2].page, 2)
  assert.equal(state.total.value, 25)
  fail = true
  await state.load()
  assert.equal(state.error.value, '成果读取失败')
  assert.deepEqual(state.items.value, [], '错误状态不得展示上次搜索的文件')
  assert.equal(state.loading.value, false)
  fail = false
  await state.load()
  assert.equal(state.error.value, '')
  assert.equal(state.items.value[0].name, '封面.png')
})

test('成果预览使用认证字节，过时媒体请求不串文件，关闭或离页释放 URL', async (t) => {
  const calls = []
  let resolveMedia, fail = false
  const { state, instance, createdUrls, revokedUrls } = await mountDeliverables(t, {
    content: async (id) => {
      calls.push(['content', id])
      if (id === 'slow-video') return new Promise((resolve) => { resolveMedia = resolve })
      return new Response('video bytes', { headers: { 'content-type': 'video/mp4' } })
    },
    preview: async (id) => {
      calls.push(['preview', id])
      if (fail) throw new Error('文件不可用')
      return new Response(JSON.stringify({ content: '# 已保存的副本', preview_type: 'markdown' }), { headers: { 'content-type': 'application/json' } })
    }
  })
  const slow = state.openPreview({ id: 'slow-video', name: '旧视频.mp4', mime_type: 'video/mp4' })
  await state.openPreview({ id: 'doc', name: '报告.md', mime_type: 'text/markdown' })
  resolveMedia(new Response('old video'))
  await slow
  assert.equal(state.selectedFile.value.id, 'doc')
  assert.equal(state.previewFile.value.content, '# 已保存的副本')
  assert.equal(createdUrls.length, 0, '切换后的媒体请求不得发布 blob URL')
  await state.openPreview({ id: 'video', name: '演示.mp4', mime_type: 'video/mp4' })
  assert.deepEqual(calls.at(-1), ['content', 'video'])
  assert.equal(state.previewFile.value.previewUrl, 'blob:fixture-1')
  assert.equal(state.previewFile.value.previewType, 'video')
  for (const hook of instance.da) hook()
  assert.deepEqual(revokedUrls, ['blob:fixture-1'])
  assert.equal(state.selectedFile.value, null)
  fail = true
  await state.openPreview({ id: 'doc', name: '报告.md', mime_type: 'text/markdown' })
  assert.equal(state.previewError.value, '文件不可用')
  assert.equal(state.previewLoading.value, false)
  fail = false
  await state.openPreview(state.selectedFile.value)
  assert.equal(state.previewError.value, '')
  assert.equal(state.previewFile.value.content, '# 已保存的副本')
})

test('成果下载保留服务端 UTF-8 文件名并释放下载 URL', async (t) => {
  let resolveDownload, requestCount = 0
  const { state, downloads, revokedUrls, createdUrls } = await mountDeliverables(t, {
    download: async (id) => {
      assert.equal(id, 'saved-doc')
      requestCount++
      return new Promise((resolve) => { resolveDownload = resolve })
    }
  })
  const pending = state.download({ id: 'saved-doc', name: 'old.pdf' })
  await state.download({ id: 'saved-doc', name: 'old.pdf' })
  assert.equal(requestCount, 1, '下载中的同一成果不得重复发起请求')
  resolveDownload(new Response('saved document bytes', { headers: {
    'content-disposition': "attachment; filename*=UTF-8''%E6%88%90%E6%9E%9C.pdf"
  } }))
  await pending
  assert.equal(await createdUrls[0].text(), 'saved document bytes')
  assert.deepEqual(downloads, [{ name: '成果.pdf', url: 'blob:fixture-1' }])
  assert.deepEqual(revokedUrls, ['blob:fixture-1'])
  assert.equal(state.downloadingIds.value.size, 0)
})

test('成果列表预览媒体与下载统一经过认证 API，不将凭据放入文件 URL', async (t) => {
  const server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  t.after(() => server.close())
  const { configureApiAuth } = await server.ssrLoadModule('/src/apis/auth_context.js')
  configureApiAuth({ getToken: () => 'fixture-token' })
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url, options })
    return new Response(JSON.stringify({ items: [], total: 0, page: 2, page_size: 20 }), { headers: { 'content-type': 'application/json' } })
  })
  const { deliverablesApi } = await server.ssrLoadModule('/src/apis/deliverables_api.js')
  assert.deepEqual(await deliverablesApi.list({ query: '报告', fileType: 'document', page: 2 }), { items: [], total: 0, page: 2, page_size: 20 })
  await deliverablesApi.preview('saved/id')
  await deliverablesApi.content('saved/id')
  await deliverablesApi.download('saved/id')
  const search = new URL(requests[0].url, 'http://yuxi.local').searchParams
  assert.equal(search.get('query'), '报告')
  assert.equal(search.get('file_type'), 'document')
  assert.equal(search.get('page'), '2')
  assert.deepEqual(requests.slice(1).map(({ url }) => url), [
    '/api/workspace/deliverables/saved%2Fid/preview',
    '/api/workspace/deliverables/saved%2Fid/content',
    '/api/workspace/deliverables/saved%2Fid/download'
  ])
  for (const { url, options } of requests) {
    assert.equal(options.headers.Authorization, 'Bearer fixture-token')
    assert.equal(url.includes('fixture-token'), false)
  }
})
