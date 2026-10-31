import assert from 'node:assert/strict'
import test, { before, after } from 'node:test'
import { createRenderer, reactive, ssrContextKey } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

let server, component
before(async () => {
  server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  ;({ default: component } = await server.ssrLoadModule(
    '/src/modules/knowledge/ui/ChunkBrowser.vue'
  ))
})
after(async () => server?.close())

test('浏览原始分块，保留完全重叠块与完整末尾，按正文或 ID 筛选', () => {
  const shared = '完全重叠的内容需要保留独立分块身份'
  const props = reactive({
    chunks: [
      { id: 'chunk-B', content: shared, chunk_order_index: 2 },
      { id: 'chunk-A', content: shared, chunk_order_index: 1 },
      {
        id: 'chunk-C',
        content: 'a'.repeat(5000) + '\n<table><tr><td>末尾标记',
        chunk_order_index: 3
      }
    ]
  })
  let state
  const renderer = createRenderer({
    createComment: () => ({}),
    insert() {},
    remove() {},
    parentNode() {},
    nextSibling() {}
  })
  const app = renderer.createApp({
    setup() {
      state = component.setup(props, { expose() {}, emit() {} })
      return () => null
    }
  })
  app.provide(ssrContextKey, {})
  app.mount({})
  assert.deepEqual(
    state.filteredChunks.value.map((chunk) => chunk.id),
    ['chunk-A', 'chunk-B', 'chunk-C']
  )
  state.query.value = ' CHUNK-b '
  assert.equal(state.selectedChunk.value.id, 'chunk-B')
  state.query.value = '末尾标记'
  assert.equal(state.selectedChunk.value.id, 'chunk-C')
  assert.equal(state.selectedChunk.value.content, props.chunks[2].content)
  state.query.value = '不存在'
  assert.equal(state.filteredChunks.value.length, 0)
  assert.equal(state.selectedChunk.value, undefined)
  state.query.value = ''
  state.selectedId.value = 'chunk-B'
  assert.equal(state.selectedChunk.value.id, 'chunk-B')
  assert.deepEqual(
    props.chunks.map((chunk) => chunk.id),
    ['chunk-B', 'chunk-A', 'chunk-C']
  )
  app.unmount()
})
