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
let BaseToolCall
let ToolCallRenderer
let ReasoningBlock
let ToolCallsGroup
let parseToolCallResult
let ReadFileTool
let ImageTool
let QueryKbTool

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
  ;({ default: BaseToolCall } = await server.ssrLoadModule(
    '/src/modules/session/ui/tools/BaseToolCall.vue'
  ))
  ;({ default: ReadFileTool } = await server.ssrLoadModule(
    '/src/modules/session/ui/tools/renderers/ReadFileTool.vue'
  ))
  ;({ default: ImageTool } = await server.ssrLoadModule(
    '/src/modules/session/ui/tools/renderers/ImageTool.vue'
  ))
  ;({ default: QueryKbTool } = await server.ssrLoadModule(
    '/src/modules/session/ui/tools/renderers/QueryKbTool.vue'
  ))
  ;({ default: ToolCallRenderer } = await server.ssrLoadModule(
    '/src/modules/session/ui/tools/ToolCallRenderer.vue'
  ))
  ;({ default: ReasoningBlock } = await server.ssrLoadModule(
    '/src/modules/session/ui/ReasoningBlockComponent.vue'
  ))
  ;({ default: ToolCallsGroup } = await server.ssrLoadModule(
    '/src/modules/session/ui/ToolCallsGroupComponent.vue'
  ))
  ;({ parseToolCallResult } = await server.ssrLoadModule(
    '/src/modules/session/ui/tools/toolRegistry.js'
  ))
})

after(async () => {
  await server?.close()
  delete globalThis.localStorage
  delete globalThis.document
  delete globalThis.window
  delete globalThis.getComputedStyle
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

test('历史搜索额度失败的卡片与汇总均显示失败，真实零结果保留空状态', async () => {
  for (const name of ['web_search', 'tavily_search', 'doubao_search']) {
    const toolCall = {
      id: 'search-quota', name, args: { query: '官方资料' }, status: 'completed',
      tool_call_result: {
        status: 'success',
        content: JSON.stringify({ query: '官方资料', results: [], error: 'Daily search package quota has been exhausted.' })
      }
    }
    const html = await render(ToolCallRenderer, { toolCall, defaultExpanded: true })
    assert.match(html, /tool-error/)
    assert.match(html, /quota has been exhausted/)
    assert.doesNotMatch(html, /未找到相关搜索结果|tool-success/)
    const group = await render(ToolCallsGroup, { toolCalls: [toolCall], isActive: false })
    assert.match(group, /1 失败/)
  }
  const empty = await render(ToolCallRenderer, {
    toolCall: { name: 'web_search', result: { query: '官方资料', results: [], error: null } },
    defaultExpanded: true
  })
  assert.match(empty, /未找到相关搜索结果/)
  assert.match(empty, /tool-success/)
  assert.doesNotMatch(empty, /tool-error/)
  const ordinary = await render(BaseToolCall, {
    toolCall: { name: 'unknown_tool', result: { error: '业务数据中的字段' } },
    defaultExpanded: true
  })
  assert.match(ordinary, /tool-success/)
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

test('工具结果中的图片块按顺序渲染，编码不会作为文本显示', async () => {
  const image = { type: 'image', base64: 'cGljdHVyZQ==', mime_type: 'image/png' }
  for (const content of [
    [image],
    'data:image/png;base64,cGljdHVyZQ==',
    JSON.stringify([{ type: 'text', text: '读取完成' }, image, { type: 'text', text: '后续说明' }]),
    [{ type: 'input_image', image_url: 'data:image/png;base64,cGljdHVyZQ==' }],
    [{ type: 'image', data: 'cGljdHVyZQ==', mimeType: 'image/png' }]
  ]) {
    const html = await render(BaseToolCall, {
      toolCall: { name: 'read_file', tool_call_result: { content } },
      defaultExpanded: true
    })
    assert.match(html, /<img[^>]+src="data:image\/png;base64,cGljdHVyZQ=="/)
    assert.match(html, /aria-label="放大查看工具结果图片/)
    assert.doesNotMatch(html, /<pre[^>]*>[^<]*cGljdHVyZQ==/)
    if (typeof content === 'string' && content.startsWith('[')) {
      assert.ok(html.indexOf('读取完成') < html.indexOf('<img'))
      assert.ok(html.indexOf('后续说明') > html.indexOf('<img'))
    }
  }
})

test('图片来源不可用时给出提示，普通对象仍显示 JSON', async () => {
  for (const image of [
    { type: 'image', base64: 'private-image-payload' },
    { type: 'image_url', image_url: { url: 'javascript:alert(1)' } }
  ]) {
    const html = await render(BaseToolCall, {
      toolCall: { name: 'read_file', result: [image] },
      defaultExpanded: true
    })
    assert.match(html, /图片无法加载/)
    assert.doesNotMatch(html, /private-image-payload|javascript:|<img/)
  }
  const html = await render(BaseToolCall, {
    toolCall: { name: 'read_file', result: { rows: [1, 2] } },
    defaultExpanded: true
  })
  assert.match(html, /rows/)
  assert.doesNotMatch(html, /<img/)
})

test('实际图片结果显示查看图片和图片图标，隐藏文件名与路径参数', async () => {
  const args = { file_path: '/workspace/private/screenshot.png' }
  const html = await render(ReadFileTool, {
    toolCall: {
      name: 'read_file',
      args,
      result: JSON.stringify([{ type: 'image', base64: 'cGljdHVyZQ==', mime_type: 'image/png' }])
    },
    defaultExpanded: true
  })
  assert.match(html, /查看图片/)
  assert.match(html, /lucide-image/)
  assert.doesNotMatch(html, /读取文件|screenshot\.png|file_path|参数:/)

  for (const result of ['普通文件内容', '图片读取失败']) {
    const html = await render(ReadFileTool, {
      toolCall: {
        name: 'read_file',
        args,
        result,
        status: result === '图片读取失败' ? 'error' : 'completed'
      },
      defaultExpanded: true
    })
    assert.match(html, /读取文件/)
    assert.match(html, /screenshot\.png/)
    assert.match(html, /file_path/)
    assert.doesNotMatch(html, /查看图片/)
  }
})

test('生成图片工具的普通 URL 和内联图片共用图片结果，普通文本不会丢失', async () => {
  for (const content of ['https://example.com/image.png', 'data:image/png;base64,cGljdHVyZQ==']) {
    const html = await render(ImageTool, {
      toolCall: { name: 'image_gen', tool_call_result: { content } },
      defaultExpanded: true
    })
    assert.match(html, /<img/)
    assert.match(html, /放大查看工具结果图片/)
  }
  const html = await render(ImageTool, {
    toolCall: { name: 'image_gen', result: '未生成图片' },
    defaultExpanded: true
  })
  assert.match(html, /未生成图片/)
  assert.doesNotMatch(html, /<img/)
})

test('协作工具的 0 和 false 结果仍视为完成，摘要不展开原文', async () => {
  for (const [name, content] of [
    ['create_session', 0],
    ['create_session', false],
    ['list_sessions', 0],
    ['list_sessions', false]
  ]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name, tool_call_result: { content } },
      defaultExpanded: true
    })
    assert.equal(parseToolCallResult({ tool_call_result: { content } }), content)
    assert.match(html, name === 'list_sessions' ? /tool-call-display[^>]*is-summary/ : /cooperation-event/)
    assert.doesNotMatch(html, /tool-result|进行中|操作失败/)
  }
})

test('明确的 ToolMessage 错误不会被专用工具覆盖为完成', async () => {
  for (const name of ['create_session', 'subagent_start']) {
    const html = await render(ToolCallRenderer, {
      toolCall: {
        id: 'cooperation-error',
        name,
        args: {},
        tool_call_result: { status: 'error', content: '{"status":"started","message":"启动失败"}' }
      },
      defaultExpanded: true
    })
    assert.match(html, /has-error|tool-error/)
    if (name === 'subagent_start') assert.match(html, /启动失败/)
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
  for (const text of [
    '先检查文件。\r\n再确认结果。\r\n  ',
    '再确认结果。',
    '先检查文件。\r再确认结果。'
  ]) {
    const preview = await render(ReasoningBlock, { content: text, isActive: true })
    assert.match(preview, /再确认结果。/)
    assert.doesNotMatch(preview, /先检查文件。/)
  }
})

test('派发成功显示创建摘要，后续会话状态不冒充调用状态', async () => {
  const toolCall = {
    name: 'create_session',
    args: {},
    tool_call_result: { content: { status: 'accepted', session_id: 'child', turn_id: 'turn' } }
  }
  const row = await render(ToolCallRenderer, { toolCall })
  assert.match(row, /创建/)
  assert.doesNotMatch(row, /has-error|进行中|操作失败/)
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

test('incomplete 工具显示中断终态而非加载图标', async () => {
  const html = await render(BaseToolCall, {
    toolCall: { id: 'stopped', name: 'execute', args: {}, status: 'incomplete' },
    defaultExpanded: true,
    appearance: 'timeline'
  })
  assert.match(html, /已中断/)
  assert.doesNotMatch(html, /tool-loading|正在调用工具/)
  const question = await render(ToolCallRenderer, {
    toolCall: {
      id: 'question',
      name: 'ask_user_question',
      args: { questions: [] },
      status: 'incomplete'
    }
  })
  assert.match(question, /已中断/)
  assert.doesNotMatch(question, /tool-loading/)
})


test('知识库检索失败显示错误结果，正常空检索仍显示空状态', async () => {
  const failure = await render(QueryKbTool, {
    toolCall: { name: 'query_kb', tool_call_result: { status: 'error', content: '工具 query_kb 执行失败：RuntimeError' } },
    defaultExpanded: true
  })
  assert.match(failure, /tool-error/)
  assert.match(failure, /query_kb 执行失败/)
  assert.doesNotMatch(failure, /未找到相关知识库内容/)
  const empty = await render(QueryKbTool, {
    toolCall: { name: 'query_kb', tool_call_result: { status: 'success', content: '{"results":[]}' } },
    defaultExpanded: true
  })
  assert.match(empty, /未找到相关知识库内容/)
  assert.doesNotMatch(empty, /tool-error/)
})

test('新增工具只显示有用摘要，即使默认展开也不重复展示正文与原始结果', async () => {
  const cases = [
    ['list_agents', {}, { agents: [{ id: 'research', name: '资料研究员', description: '完整智能体描述' }] }, /查看智能体/, /1 个可用/],
    ['search_thread_messages', { query: '部署' }, { items: [{ title: '部署记录', role: 'user', content: '完整历史消息' }] }, /搜索历史消息/, /部署/],
    ['read_thread_messages', {}, { title: '部署记录', messages: [{ role: 'assistant', content: '完整历史消息' }], tool_calls: [] }, /读取历史消息/, /部署记录/],
    ['install_skill', { source: 'example/research', skill_names: ['research'] }, '已安装 Skill: research\nSkill 路径: /workspace/skills/research/SKILL.md', /安装技能/, /research/],
    ['mark_for_improvement', { reason: '完整改进原因' }, { id: 'feedback-1', saved: true }, /记录改进线索/, /tool-success/],
    ['web_fetch', { url: 'https://example.com' }, { title: '部署指南', content: '完整网页正文', provider: 'tavily' }, /网页抓取/, /部署指南/]
  ]
  for (const [name, args, result, action, target] of cases) {
    const html = await render(ToolCallRenderer, { toolCall: { name, args, result }, defaultExpanded: true, appearance: 'timeline' })
    assert.match(html, action, name)
    assert.match(html, target, name)
    assert.match(html, /is-timeline/, name)
    assert.doesNotMatch(html, /完整|Skill 路径|原始结果|调用详情|tool-content|tool-expand-icon|aria-expanded|role="button"/, name)
  }
})

test('协作工具保留动作、会话入口和超时状态，不展示请求或结果正文', async () => {
  const context = { sessions: [{ session_id: 'child', name: '资料研究员' }], tasks: new Map(), openSession() {} }
  for (const cooperationView of [null, context]) {
    for (const [name, args, result, expected] of [
      ['get_result', {}, { session_id: 'child', status: 'completed', output: '完整任务输出', terminal: true }, /查看结果/],
      ['send_message', { target: 'child', content: '完整请求正文' }, { status: 'received', session_id: 'child' }, /发送消息至/],
      ['wait_inputs', { input_ids: ['input-1'] }, { wait_timed_out: true, results: [{ input_id: 'input-1', session_id: 'child', status: 'running', output: null }] }, /等待超时/],
      ['wait_sessions', { targets: ['child'] }, { updates: [{ sender_session_id: 'child', content: '完整更新正文', kind: 'message' }] }, /等待/]
    ]) {
      const html = await render(ToolCallRenderer, { toolCall: { name, args, result }, cooperationView, defaultExpanded: true })
      assert.match(html, expected)
      assert.doesNotMatch(html, /完整|原始结果|调用详情|<details/)
      if (cooperationView) {
        assert.match(html, /资料研究员/)
        assert.match(html, /<button/)
      } else assert.doesNotMatch(html, /<button/)
    }
  }
})

test('会话查询只展示真实范围和本页计数，历史回执不冒充本项目查询', async () => {
  const sessions = [{ session_id: 'hidden-session', title: '不应展示的会话标题', current_run_id: '不应展示的执行标识' }]
  for (const [args, result, label] of [
    [{ scope: 'all' }, { scope: 'project', sessions }, '本项目'],
    [{ scope: 'project' }, { scope: 'all', sessions }, '历史'],
    [{}, { sessions }, '历史'],
    [{ scope: 'project' }, { sessions }, '本项目']
  ]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name: 'list_sessions', args, result },
      appearance: 'timeline',
      defaultExpanded: true,
      cooperationView: { sessions, tasks: new Map(), openSession() {} }
    })
    assert.match(html, /tool-call-display[^>]*is-timeline[^>]*is-summary/)
    assert.match(html, /class="sep-header"/)
    assert.match(html, /class="note"/)
    assert.match(html, /class="description"/)
    assert.match(html, new RegExp(`查看了${label}会话`))
    assert.match(html, /1 个/)
    assert.doesNotMatch(html, /不应展示|hidden-session|cooperation-event|event-list-toggle|event-session-list|event-target|<button|aria-expanded/)
  }
  for (const [args, label] of [[{}, '本项目'], [{ scope: 'all' }, '历史']]) {
    const html = await render(ToolCallRenderer, { toolCall: { name: 'list_sessions', args } })
    assert.match(html, new RegExp(`查看${label}会话`))
    assert.match(html, /tool-loading/)
    assert.doesNotMatch(html, /查看了|0 个|进行中|event-state/)
  }
})

test('真实工具分组中的会话查询与智能体列表共用摘要结构和状态', async () => {
  const html = await render(ToolCallsGroup, {
    toolCalls: [
      { id: 'agents', name: 'list_agents', args: {}, result: { agents: [] } },
      { id: 'sessions', name: 'list_sessions', args: {}, result: { scope: 'project', sessions: [] } }
    ],
    cooperationView: { sessions: [], tasks: new Map(), openSession() {} }
  })
  assert.equal((html.match(/tool-call-display[^>]*is-timeline[^>]*is-summary/g) || []).length, 2)
  assert.equal((html.match(/class="sep-header"/g) || []).length, 2)
  assert.equal((html.match(/class="note"/g) || []).length, 2)
  assert.equal((html.match(/class="description"/g) || []).length, 2)
  assert.match(html, /查看了本项目会话/)
  assert.doesNotMatch(html, /cooperation-event|event-state|event-list-toggle|event-session-list/)
})

test('摘要保留失败、等待、中断和空计数，不显示回执详情', async () => {
  for (const name of ['list_agents', 'list_sessions', 'get_result', 'search_thread_messages', 'read_thread_messages', 'install_skill', 'mark_for_improvement', 'web_fetch']) {
    for (const [fields, label] of [
      [{ tool_call_result: { status: 'error', content: '完整错误堆栈' } }, /失败/],
      [{ status: 'waiting' }, /等待/],
      [{ status: 'incomplete' }, /已中断/],
      [{}, /tool-loading|进行中/]
    ]) {
      const html = await render(ToolCallRenderer, { toolCall: { name, args: {}, ...fields }, defaultExpanded: true })
      assert.match(html, label, name)
      assert.doesNotMatch(html, /完整错误堆栈|tool-content|改进线索已记录/, name)
      if (name === 'list_sessions') {
        assert.match(html, /tool-call-display[^>]*is-summary/)
        assert.doesNotMatch(html, /event-state|操作失败|进行中/)
        if (fields.tool_call_result?.status === 'error') assert.match(html, /执行失败/)
        if (fields.status === 'waiting') assert.match(html, /等待协作/)
      }
    }
  }
  for (const [name, result, expected] of [
    ['list_agents', { agents: [] }, /0 个可用/],
    ['search_thread_messages', { items: [] }, /0 条/],
    ['read_thread_messages', { messages: [], tool_calls: [] }, /0 条/],
    ['list_sessions', { sessions: [] }, /查看了历史会话.*0 个/],
    ['list_sessions', { scope: 'project', sessions: [] }, /查看了本项目会话.*0 个/]
  ]) {
    assert.match(await render(ToolCallRenderer, { toolCall: { name, result }, defaultExpanded: true }), expected)
  }
})

test('技能安装的纯文本失败回执不被摘要隐藏，成功名称中的错误字样不误判', async () => {
  for (const content of [
    '错误：无法获取当前会话信息',
    '错误：Skill 来源不能为空',
    '安装异常：连接被拒绝',
    '安装失败 (research): 未找到技能',
    '已安装 Skill: research\nSkill 路径: /workspace/skills/research/SKILL.md\n安装失败 (report): 未找到技能'
  ]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name: 'install_skill', args: { source: 'example/research' }, tool_call_result: { status: 'success', content } },
      defaultExpanded: true
    })
    assert.match(html, /tool-error/)
    assert.match(html, /执行失败/)
    assert.doesNotMatch(html, /tool-success|tool-content|Skill 路径/)
  }
  const success = await render(ToolCallRenderer, {
    toolCall: { name: 'install_skill', result: '已安装 Skill: error-helper\nSkill 路径: /workspace/skills/error-helper/SKILL.md' }
  })
  assert.match(success, /tool-success/)
  assert.doesNotMatch(success, /执行失败/)
  const empty = await render(ToolCallRenderer, {
    toolCall: { name: 'install_skill', result: '未发现需要安装的 Skill' }
  })
  assert.match(empty, /未发现技能/)
})


test('多个工具卡片与协作分组共享同一异步组件定义', async () => {
  const { CooperationTool, TOOL_RENDERERS } = await server.ssrLoadModule(
    '/src/modules/session/ui/tools/toolRenderers.js'
  )
  const selected = []
  const grouped = []
  const cooperation = { id: 'shared-cooperation', name: 'send_message', args: {}, result: {} }
  const app = createSSRApp({ render: () => h('div', [
    h(ToolCallRenderer, { toolCall: { name: 'calculator', args: {}, result: 2 } }),
    h(ToolCallRenderer, { toolCall: { name: 'calculator', args: {}, result: 3 } }),
    h(ToolCallRenderer, { toolCall: cooperation }),
    h(ToolCallsGroup, { toolCalls: [cooperation], cooperationView: { sessions: [], tasks: new Map() } })
  ]) })
  app.use(createPinia())
  app.use(createRouter({ history: createMemoryHistory(), routes: [] }))
  app.mixin({
    created() {
      const instance = this.$
      if (instance.type === ToolCallRenderer) selected.push(instance.setupState.currentRenderer)
      if (instance.type === ToolCallsGroup) grouped.push(instance.setupState.CooperationTool)
    }
  })
  await renderToString(app)
  assert.equal(selected.length, 3)
  assert.equal(selected[0], TOOL_RENDERERS.calculator)
  assert.equal(selected[1], selected[0])
  assert.equal(selected[2], CooperationTool)
  assert.deepEqual(grouped, [CooperationTool])
})

test('未知工具回退基础卡片，隐藏工具不产生展示', async () => {
  const unknown = await render(ToolCallRenderer, {
    toolCall: { name: 'future_tool', args: {}, result: '完成' }, defaultExpanded: true
  })
  assert.match(unknown, /future_tool/)
  assert.match(unknown, /tool-call-display/)
  const hidden = await render(ToolCallRenderer, { toolCall: { name: 'present_artifacts' } })
  assert.doesNotMatch(hidden, /tool-call-display|工具详情暂时不可用/)
})

test('Abridge 浏览器工具通过实际入口展示动作、网页摘要和截图，原始结果保留折叠入口', async () => {
  for (const name of ['browser_open', 'browser_observe', 'browser_act']) {
    for (const type of ['text', 'input_text']) {
      const receipt = { status: 'executed', result: {
        url: 'https://example.com/redirected', title: '旧标题',
        observation: { title: '浏览器演示页', url: 'https://example.com/page', text: '原始页面观察正文' }
      } }
      const content = [
        { type, text: JSON.stringify(receipt) },
        { type: 'image', data: 'cGljdHVyZQ==', mimeType: 'image/png' }
      ]
      const html = await render(ToolCallRenderer, {
        toolCall: { name, args: { url: 'https://example.com/requested', action: { type: 'click' } }, result: JSON.stringify(content) },
        appearance: 'timeline', defaultExpanded: true
      })
      assert.match(html, /打开网页|查看网页|点击网页元素/)
      assert.match(html, /浏览器演示页/)
      assert.match(html, /alt="工具结果图片"/)
      assert.match(html, /tool-status-icon--completed/)
      assert.match(html, /参数:/)
      assert.match(html, /<details[^>]*><summary[^>]*>原始结果/)
      assert.match(html, /原始页面观察正文/)
      assert.doesNotMatch(html, /<details[^>]* open|browser-page-title|browser-page-url|resource-list/)
    }
  }
})

test('Abridge 浏览器动作使用中文图案与目标，填写内容不进入折叠标题', async () => {
  for (const [type, label, icon] of [
    ['click', '点击网页元素', 'lucide-mouse-pointer-click'],
    ['fill', '填写网页内容', 'lucide-keyboard']
  ]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name: 'browser_act', args: { action: { type, element_ref: 'ref-42', text: '不应出现在标题的输入' } }, result: { status: 'executed', result: {} } }
    })
    assert.match(html, new RegExp(label))
    assert.match(html, new RegExp(icon))
    assert.match(html, /ref-42/)
    assert.doesNotMatch(html, /不应出现在标题的输入/)
  }
})

test('Abridge 浏览器观察支持直接页面结果，无观察字段保留请求目标和原始回执', async () => {
  const html = await render(ToolCallRenderer, {
    toolCall: { name: 'browser_observe', args: {}, result: { status: 'executed', result: { title: '当前页', url: 'https://example.com/current', text: '当前观察文本' } } },
    defaultExpanded: true
  })
  assert.match(html, /查看网页/)
  assert.match(html, /当前页/)
  assert.match(html, /当前观察文本/)
  assert.match(html, /<details[^>]*><summary[^>]*>原始结果/)
  const empty = await render(ToolCallRenderer, {
    toolCall: { name: 'browser_open', args: { url: 'https://example.com/pending' }, result: { status: 'executed', result: {} } },
    defaultExpanded: true
  })
  assert.match(empty, /https:\/\/example.com\/pending/)
  assert.doesNotMatch(empty, /alt="工具结果图片"|browser-page-title|browser-page-url/)
})

test('Abridge 浏览器复用拒绝、失败与执行结果未知状态，普通工具不扩大协议解释', async () => {
  for (const name of ['browser_open', 'browser_observe', 'browser_act']) {
    for (const receipt of [{ status: 'rejected' }, { error: '浏览器不可用' }, { status: 'outcome_unknown' }]) {
      const html = await render(ToolCallRenderer, {
        toolCall: { name, result: [{ type: 'text', text: JSON.stringify(receipt) }] }, appearance: 'timeline'
      })
      assert.match(html, receipt.status === 'outcome_unknown' ? /执行结果未知/ : /执行失败/)
      assert.doesNotMatch(html, /tool-status-icon--completed/)
    }
  }
  const unknown = await render(ToolCallRenderer, { toolCall: { name: 'browser_future', result: [{ type: 'text', text: '{"error":"业务字段"}' }] } })
  assert.match(unknown, /browser_future/)
  assert.match(unknown, /tool-status-icon--completed/)
  assert.doesNotMatch(unknown, /原始结果/)
})

test('Abridge 连接按声明能力显示浏览器或设备，设备和桌面文字保留原始结果', async () => {
  for (const [capabilities, label] of [[['browser'], '浏览器'], [['screen'], '设备']]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name: 'session_open', args: { capabilities }, result: { status: 'active', capabilities } }, defaultExpanded: true
    })
    assert.match(html, new RegExp(`连接${label}`))
    assert.match(html, /<details[^>]*><summary[^>]*>原始结果/)
  }
  for (const [name, result] of [
    ['devices_list', { devices: [{ id: 'device-fixture', name: '演示设备' }] }],
    ['computer_observe', { status: 'executed', result: { app_name: '演示应用', window_title: '桌面窗口' } }]
  ]) {
    const html = await render(ToolCallRenderer, { toolCall: { name, args: {}, result }, defaultExpanded: true })
    assert.match(html, /<details[^>]*><summary[^>]*>原始结果/)
    assert.doesNotMatch(html, /resource-list|command-stderr|abridge-result|退出码：/)
  }
})


test('Abridge 设备命令参考命令工具直接展示标准输出，参数和折叠回执同时保留', async () => {
  const html = await render(ToolCallRenderer, {
    toolCall: {
      name: 'command_execute', args: { command: 'example-command' },
      result: [{ type: 'input_text', text: JSON.stringify({ status: 'executed', result: { stdout: 'Finder, Chrome', stderr: '', exit_code: 0 } }) }]
    }, defaultExpanded: true, appearance: 'timeline'
  })
  assert.match(html, /<pre[^>]*>Finder, Chrome<\/pre>/)
  assert.match(html, /参数:/)
  assert.match(html, /<details[^>]*><summary[^>]*>原始结果/)
  assert.doesNotMatch(html, /标准错误：|输出已截断|命令执行超时/)
})

test('Abridge 设备命令失败仍展示输出与标准错误，超时和截断不被原始 JSON 掩盖', async () => {
  const html = await render(ToolCallRenderer, {
    toolCall: {
      name: 'command_execute', args: {},
      result: { status: 'executed', result: { stdout: '部分输出', stderr: '命令失败', exit_code: 1, timed_out: true }, output_truncated: true }
    }, defaultExpanded: true
  })
  assert.match(html, /执行失败/)
  assert.match(html, /<pre[^>]*>部分输出\n标准错误：\n命令失败\n输出已截断\n命令执行超时<\/pre>/)
  assert.match(html, /<details[^>]*><summary[^>]*>原始结果/)
})

test('Abridge 命令没有执行回执时保留基础卡片的真实错误和默认兜底', async () => {
  for (const fields of [
    { status: 'error', error_message: '设备未连接' },
    { status: 'error', result: null, error_message: '设备未连接' },
    { tool_call_result: { status: 'error', content: '' }, error_message: '设备未连接' },
    { status: 'error' }
  ]) {
    const html = await render(ToolCallRenderer, { toolCall: { name: 'command_execute', ...fields }, defaultExpanded: true })
    assert.match(html, fields.error_message ? /<pre[^>]*>设备未连接<\/pre>/ : /<pre[^>]*>工具执行失败<\/pre>/)
    assert.doesNotMatch(html, /原始结果|<pre[^>]*>undefined<\/pre>|<pre[^>]*>null<\/pre>/)
  }
})
