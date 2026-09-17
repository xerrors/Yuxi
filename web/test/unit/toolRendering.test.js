import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { after, before, test } from 'node:test'
import { createServer } from 'vite'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

let server
let BaseToolCall
let ToolCallRenderer
let ReasoningBlock
let ToolCallsGroup
let parseToolCallResult

before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }
  globalThis.document = {
    documentElement: { classList: { add() {}, remove() {} } },
    getElementsByTagName: () => []
  }
  globalThis.window = { addEventListener() {}, removeEventListener() {} }
  server = await createServer({
    root: path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..'),
    server: { middlewareMode: true }
  })
  ;({ default: BaseToolCall } = await server.ssrLoadModule(
    '/src/components/ToolCallingResult/BaseToolCall.vue'
  ))
  ;({ default: ToolCallRenderer } = await server.ssrLoadModule(
    '/src/components/ToolCallingResult/ToolCallRenderer.vue'
  ))
  ;({ default: ReasoningBlock } = await server.ssrLoadModule(
    '/src/components/ReasoningBlockComponent.vue'
  ))
  ;({ default: ToolCallsGroup } = await server.ssrLoadModule(
    '/src/components/ToolCallsGroupComponent.vue'
  ))
  ;({ parseToolCallResult } = await server.ssrLoadModule(
    '/src/components/ToolCallingResult/toolRegistry.js'
  ))
})

after(async () => {
  await server?.close()
  delete globalThis.localStorage
  delete globalThis.document
  delete globalThis.window
})

/** 渲染真实组件并检查用户可见状态与结果。 */
const render = (component, props) =>
  renderToString(
    createSSRApp({ render: () => h(component, props) })
      .use(createPinia())
      .use(createRouter({ history: createMemoryHistory(), routes: [] }))
  )

for (const [name, fields] of Object.entries({
  调用错误且没有结果: { status: 'error', error_message: '无法执行' },
  调用错误且结果为空: {
    status: 'error',
    error_message: '无法执行',
    tool_call_result: { content: '' }
  },
  'failed 状态': { status: 'failed', error_message: '无法执行' },
  结果别名错误: { result: '{"status":"error","message":"无法执行"}' },
  调用错误同时有结果: { status: 'error', tool_call_result: { content: '无法执行' } },
  'ToolMessage 错误': { tool_call_result: { status: 'error', content: '无法执行' } },
  'JSON 业务错误': {
    tool_call_result: { status: 'success', content: '{"status":"error","message":"无法执行"}' }
  },
  对象业务错误: { tool_call_result: { content: { status: 'error', message: '无法执行' } } }
})) {
  test(`${name} 显示失败图标、失败文案和错误详情`, async () => {
    const toolCall = { id: 'failed-call', name: 'unknown_tool', args: {}, ...fields }
    const html = await render(BaseToolCall, {
      toolCall,
      defaultExpanded: true,
      appearance: 'timeline'
    })
    assert.match(html, /tool-error/)
    assert.match(html, /执行失败/)
    assert.match(html, /无法执行/)
    assert.doesNotMatch(html, /执行完成|tool-loading/)
    const group = await render(ToolCallsGroup, { toolCalls: [toolCall], isActive: true })
    assert.match(group, /1 失败/)
    assert.doesNotMatch(group, /1 进行中/)
  })
}

test('知识库专用模板保留错误详情，不把错误渲染成空检索结果', async () => {
  const html = await render(ToolCallRenderer, {
    toolCall: {
      id: 'kb-error',
      name: 'query_kb',
      args: {},
      tool_call_result: { status: 'error', content: '{"status":"error","message":"知识库不可用"}' }
    },
    defaultExpanded: true
  })
  assert.match(html, /tool-error/)
  assert.match(html, /知识库不可用/)
  assert.doesNotMatch(html, /未找到相关内容|未找到结果/)
})

test('有效的 0 和 false 结果可见，普通结果文本中的 error 不冒充状态', async () => {
  for (const content of [0, false, 'status: error', { rows: [{ status: 'error' }] }]) {
    const html = await render(BaseToolCall, {
      toolCall: { name: 'unknown_tool', tool_call_result: { content } },
      defaultExpanded: true
    })
    assert.match(html, /tool-success/)
    assert.match(html, /tool-result-renderer/)
    assert.doesNotMatch(html, /tool-error/)
  }
})

test('专用子智能体工具保留 0 和 false 结果', async () => {
  for (const [name, content] of [
    ['task', 0],
    ['task', false],
    ['subagent_status', 0],
    ['subagent_status', false]
  ]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name, tool_call_result: { content } },
      defaultExpanded: true
    })
    assert.equal(parseToolCallResult({ tool_call_result: { content } }), content)
    assert.match(html, /tool-result/)
    assert.match(html, /tool-success/)
  }
})

test('明确的 ToolMessage 错误不会被专用工具覆盖为完成', async () => {
  for (const name of ['task', 'subagent_start']) {
    const html = await render(ToolCallRenderer, {
      toolCall: {
        id: 'subagent-error',
        name,
        args: {},
        tool_call_result: { status: 'error', content: '{"status":"started","message":"启动失败"}' }
      },
      defaultExpanded: true
    })
    assert.match(html, /tool-error/)
    assert.match(html, /启动失败/)
    assert.doesNotMatch(html, /已完成|已启动/)
    assert.match(html, /失败/)
  }
  const html = await render(BaseToolCall, {
    toolCall: { status: 'error', error_message: '调用失败' },
    status: 'completed'
  })
  assert.match(html, /tool-error/)
  assert.doesNotMatch(html, /执行完成/)
})

test('思考描述显示最后一行，展开后保留完整内容', async () => {
  const content = '先检查文件。\n再确认结果。'
  const html = await render(ReasoningBlock, { content, isActive: true })
  assert.match(html, /is-timeline/)
  assert.match(html, /lucide-brain/)
  assert.match(html, /思考/)
  assert.match(html, /再确认结果。/)
  assert.doesNotMatch(html, /先检查文件。/)
  assert.match(html, /aria-expanded="false"/)
  assert.doesNotMatch(html, /<p class="reasoning-content"/)
  const expanded = await render(ReasoningBlock, { content, defaultExpanded: true })
  assert.match(expanded, /<p class="reasoning-content"/)
  assert.match(expanded, /先检查文件。\n再确认结果。/)
  for (const text of ['先检查文件。\r\n再确认结果。\r\n  ', '再确认结果。', '先检查文件。\r再确认结果。']) {
    const preview = await render(ReasoningBlock, { content: text, isActive: true })
    assert.match(preview, /再确认结果。/)
    assert.doesNotMatch(preview, /先检查文件。/)
  }
})

test('子智能体运行失败在专用行和分组摘要中一致展示', async () => {
  for (const toolCall of [
    {
      id: 'task-failed',
      name: 'task',
      args: {},
      subagent_run: { status: 'failed' },
      tool_call_result: { content: '子任务失败' }
    },
    {
      id: 'task-cancelled',
      name: 'task',
      args: {},
      subagent_run: { status: 'cancelled' },
      tool_call_result: { content: '子任务已取消' }
    },
    {
      id: 'task-interrupted',
      name: 'task',
      args: {},
      subagent_run: { status: 'interrupted' },
      tool_call_result: { content: '子任务已中断' }
    },
    {
      id: 'child-failed',
      name: 'subagent_status',
      args: {},
      tool_call_result: { content: { status: 'ok', run_status: 'failed' } }
    },
    {
      id: 'child-interrupted',
      name: 'subagent_await',
      args: {},
      tool_call_result: { content: { status: 'ok', active_run_status: 'interrupted' } }
    }
  ]) {
    const row = await render(ToolCallRenderer, { toolCall })
    const group = await render(ToolCallsGroup, { toolCalls: [toolCall] })
    assert.match(row, /tool-error/)
    assert.match(group, /1 失败/)
    assert.doesNotMatch(group, /1 进行中/)
  }
})

test('知识库列表错误不显示暂无知识库', async () => {
  const html = await render(ToolCallRenderer, {
    toolCall: {
      name: 'list_kbs',
      args: {},
      tool_call_result: { status: 'error', content: '列表读取失败' }
    },
    defaultExpanded: true
  })
  assert.match(html, /执行失败/)
  assert.match(html, /列表读取失败/)
  assert.doesNotMatch(html, /暂无知识库/)
})
