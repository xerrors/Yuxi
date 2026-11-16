import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { after, before, test } from 'node:test'
import { createTestServer } from '../helpers/createTestServer.js'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

let server
let ToolCallsGroup

before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }
  globalThis.document = {
    documentElement: { classList: { add() {}, remove() {} } },
    getElementsByTagName: () => []
  }
  globalThis.window = { addEventListener() {}, removeEventListener() {} }
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' })
  server = await createTestServer({
    root: path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..'),
    server: { middlewareMode: true }
  })
  ;({ default: ToolCallsGroup } = await server.ssrLoadModule(
    '/src/modules/session/ui/ToolCallsGroupComponent.vue'
  ))
})

after(async () => {
  await server?.close()
  delete globalThis.localStorage
  delete globalThis.document
  delete globalThis.window
  delete globalThis.getComputedStyle
})

const render = (props) =>
  renderToString(
    createSSRApp({ render: () => h(ToolCallsGroup, props) })
      .use(createPinia())
      .use(createRouter({ history: createMemoryHistory(), routes: [] }))
  )

test('单工具直接展示工具行，运行中和完成后均不显示外层分组', async () => {
  for (const isActive of [true, false]) {
    const html = await render({
      isActive,
      toolCalls: [{ id: 'single', name: 'unknown_tool', args: {}, ...(isActive ? {} : { result: 'done' }) }]
    })
    assert.doesNotMatch(html, /tool-calls-heading|tool-calls-summary|live-header/)
    assert.match(html, /class="(?=[^"]*tool-calls-collapse-panel)(?=[^"]*is-expanded)[^"]*"/)
    assert.equal((html.match(/class="tool-call-container"/g) || []).length, 1)
    assert.match(html, /tool-header/)
  }
})

test('流式分组只用最新工具 header 替代汇总，默认收起', async () => {
  const html = await render({
    isActive: true,
    toolCalls: [
      { id: 'old', name: 'old_tool', args: {}, result: 'done' },
      { id: 'latest', name: 'latest_tool', args: {}, display_label: '最新工具' }
    ]
  })
  const heading = html.slice(0, html.indexOf('tool-calls-collapse-panel'))
  assert.match(heading, /live-header/)
  assert.match(heading, /latest_tool/)
  assert.doesNotMatch(heading, /old_tool|已调用|summary-meta|tool-content/)
  assert.match(heading, /aria-expanded="false"/)
  assert.match(heading, /aria-label="最新工具"/)
  assert.match(heading, /inert/)
})

test('流式最新条目是思考时展示思考 header 和最后一行预览', async () => {
  const html = await render({
    isActive: true,
    entries: [
      { type: 'tool', key: 'old', toolCall: { name: 'old_tool' } },
      { type: 'reasoning', key: 'thinking', content: '前一段思考\n正在分析下一步' }
    ]
  })
  const heading = html.slice(0, html.indexOf('tool-calls-collapse-panel'))
  assert.match(heading, /思考/)
  assert.match(heading, /正在分析下一步/)
  assert.doesNotMatch(heading, /old_tool|前一段思考|reasoning-content|summary-title/)
  assert.match(heading, /aria-expanded="false"/)
})
