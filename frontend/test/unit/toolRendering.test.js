import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { after, before, test } from 'node:test'
import { createTestServer } from '../helpers/createTestServer.js'
import { createSSRApp, h, ref } from 'vue'
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
let useAgentStore

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
  ;({ useAgentStore } = await server.ssrLoadModule('/src/modules/agents/index.js'))
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
const render = (component, props, configureStore, sessionConfigurableItems) => {
  const pinia = createPinia()
  configureStore?.(useAgentStore(pinia))
  const app = createSSRApp({ render: () => h(component, props) })
    .use(pinia)
    .use(createRouter({ history: createMemoryHistory(), routes: [] }))
  if (sessionConfigurableItems) app.provide('sessionAgentConfigurableItems', sessionConfigurableItems)
  return renderToString(app)
}

test('工具参数为空时隐藏整行，结果仍可见，非空参数正常展示', async () => {
  for (const name of ['unknown_tool', 'devices_list']) {
    for (const args of [undefined, null, {}, '', '  \n ', '{}', ' {\n } ', [], '[ ]']) {
      for (const fields of [{ args }, { function: { name, arguments: args } }]) {
        const html = await render(ToolCallRenderer, {
          toolCall: { name, ...fields, result: '工具返回内容' },
          defaultExpanded: true
        })
        assert.doesNotMatch(html, /class="tool-params"|参数:/)
        assert.match(html, /工具返回内容/)
      }
    }
  }
  for (const args of [{ limit: 0, enabled: false }, '{"path":"/Users"}', 'x', '{']) {
    const html = await render(BaseToolCall, {
      toolCall: { name: 'unknown_tool', args }, defaultExpanded: true
    })
    assert.match(html, /class="tool-params"/)
    assert.match(html, /参数:/)
  }
})

test('折叠的文件与命令工具在自定义标题中明确显示失败', async () => {
  for (const [name, args] of [
    ['read_file', { file_path: '/tmp/test.txt' }],
    ['write_file', { file_path: '/tmp/test.txt', content: '一行内容' }],
    ['bash', { command: 'false' }]
  ]) {
    const failed = await render(ToolCallRenderer, {
      toolCall: { name, args, status: 'error', error_message: '操作未完成' }
    })
    assert.match(failed, /执行失败/)
    assert.doesNotMatch(failed, /class="tool-content"/)
    const completed = await render(ToolCallRenderer, {
      toolCall: { name, args, status: 'completed' }
    })
    assert.doesNotMatch(completed, /执行失败/)
  }
})

test('加载插件 header 使用目录名称和共享图标，参数与结果保持基础渲染', async () => {
  const toolCall = {
    id: 'plugin-load', name: 'load_plugin', args: { plugin_id: 'abridge' },
    result: '插件原始返回内容'
  }
  const props = { toolCall, defaultExpanded: true }
  const html = await render(ToolCallRenderer, props, (store) => {
    store.selectedAgentId = 'plugin-agent'
    store.agents = [{ agent_id: 'plugin-agent', configurable_items: {
      plugins: { options: [{ key: 'abridge', name: 'aBridge 设备' }] }
    } }]
  })
  assert.match(html, /加载插件/)
  assert.match(html, /aBridge 设备/)
  assert.match(html, /lucide-computer/)
  assert.doesNotMatch(html, /执行完成/)
  const base = await render(BaseToolCall, props)
  assert.equal(html.slice(html.indexOf('<div class="tool-content"')), base.slice(base.indexOf('<div class="tool-content"')))
})

test('加载插件名称以当前会话目录为准，不借用全局选中的另一 Agent 目录', async () => {
  for (const options of [[{ key: 'abridge', name: '会话 B 的设备' }], []]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name: 'load_plugin', args: { plugin_id: 'abridge' }, result: '完成' }
    }, (store) => {
      store.selectedAgentId = 'agent-a'
      store.agents = [{ agent_id: 'agent-a', configurable_items: {
        plugins: { options: [{ key: 'abridge', name: '全局 A 的设备' }] }
      } }]
    }, ref({ plugins: { options } }))
    assert.ok(html.includes(`title="${options.length ? '会话 B 的设备' : 'abridge'}"`))
    assert.doesNotMatch(html, /全局 A 的设备/)
  }
})

for (const [label, args, expectedName, expectedIcon] of [
  ['未知插件', { plugin_id: 'custom-extension' }, 'custom-extension', 'puzzle'],
  ['字符串参数', '{"plugin_id":"abridge"}', 'abridge', 'computer'],
  ['无参数', undefined, '', 'puzzle'],
  ['流式未完成参数', '{"plugin_id":"ab', '', 'puzzle'],
  ['非字符串 ID', { plugin_id: {} }, '', 'puzzle']
]) {
  test(`加载插件 ${label} 安全显示参数身份，不从结果猜测插件`, async () => {
    const html = await render(ToolCallRenderer, {
      toolCall: { name: 'load_plugin', args, result: 'abridge aBridge 设备' }
    })
    assert.match(html, /加载插件/)
    assert.match(html, new RegExp(`lucide-${expectedIcon}`))
    if (expectedName) assert.ok(html.includes(`title="${expectedName}"`))
    assert.doesNotMatch(html, /aBridge 设备|\[object Object\]/)
  })
}

test('加载插件 header 保持运行、失败与中断状态', async () => {
  for (const [status, expected] of [['running', 'tool-status-icon--running'], ['error', '执行失败'], ['incomplete', '已中断']]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name: 'load_plugin', args: { plugin_id: 'abridge' }, status }
    })
    assert.match(html, /加载插件/)
    assert.ok(html.includes(expected))
    assert.doesNotMatch(html, /执行完成/)
  }
})

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
    assert.match(html, /tool-status-icon--error/)
    assert.match(html, /执行失败/)
    assert.match(html, /无法执行/)
    assert.doesNotMatch(html, /执行完成|tool-status-icon--(?:running|waiting)/)
    const group = await render(ToolCallsGroup, { toolCalls: [toolCall], isActive: false })
    assert.match(group, /tool-status-icon--error/)
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
  assert.match(html, /tool-status-icon--error/)
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
    assert.match(html, /tool-status-icon--error/)
    assert.match(html, /quota has been exhausted/)
    assert.doesNotMatch(html, /未找到相关搜索结果|tool-status-icon--completed/)
    const group = await render(ToolCallsGroup, { toolCalls: [toolCall], isActive: false })
    assert.match(group, /tool-status-icon--error/)
  }
  const empty = await render(ToolCallRenderer, {
    toolCall: { name: 'web_search', result: { query: '官方资料', results: [], error: null } },
    defaultExpanded: true
  })
  assert.match(empty, /未找到相关搜索结果/)
  assert.match(empty, /tool-status-icon--completed/)
  assert.doesNotMatch(empty, /tool-status-icon--error/)
  const ordinary = await render(BaseToolCall, {
    toolCall: { name: 'unknown_tool', result: { error: '业务数据中的字段' } },
    defaultExpanded: true
  })
  assert.match(ordinary, /tool-status-icon--completed/)
})

test('有效的 0 和 false 结果可见，普通结果文本中的 error 不冒充状态', async () => {
  for (const content of [0, false, 'status: error', { rows: [{ status: 'error' }] }]) {
    const html = await render(BaseToolCall, {
      toolCall: { name: 'unknown_tool', tool_call_result: { content } },
      defaultExpanded: true
    })
    assert.match(html, /tool-status-icon--completed/)
    assert.match(html, /tool-result-renderer/)
    assert.doesNotMatch(html, /tool-status-icon--error/)
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
    assert.match(html, /操作失败|tool-status-icon--error/)
    if (name === 'subagent_start') assert.match(html, /启动失败/)
    assert.doesNotMatch(html, /已完成|已启动/)
    assert.match(html, /失败/)
  }
  const html = await render(BaseToolCall, {
    toolCall: { status: 'error', error_message: '调用失败' },
    status: 'completed'
  })
  assert.match(html, /tool-status-icon--error/)
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
  assert.doesNotMatch(row, /进行中|操作失败/)
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
  assert.doesNotMatch(html, /tool-status-icon--(?:running|waiting)|正在调用工具/)
  const question = await render(ToolCallRenderer, {
    toolCall: {
      id: 'question',
      name: 'ask_user_question',
      args: { questions: [] },
      status: 'incomplete'
    }
  })
  assert.match(question, /已中断/)
  assert.doesNotMatch(question, /tool-status-icon--(?:running|waiting)/)
})


test('知识库检索失败显示错误结果，正常空检索仍显示空状态', async () => {
  const failure = await render(QueryKbTool, {
    toolCall: { name: 'query_kb', tool_call_result: { status: 'error', content: '工具 query_kb 执行失败：RuntimeError' } },
    defaultExpanded: true
  })
  assert.match(failure, /tool-status-icon--error/)
  assert.match(failure, /query_kb 执行失败/)
  assert.doesNotMatch(failure, /未找到相关知识库内容/)
  const empty = await render(QueryKbTool, {
    toolCall: { name: 'query_kb', tool_call_result: { status: 'success', content: '{"results":[]}' } },
    defaultExpanded: true
  })
  assert.match(empty, /未找到相关知识库内容/)
  assert.doesNotMatch(empty, /tool-status-icon--error/)
})

test('新增工具只显示有用摘要，即使默认展开也不重复展示正文与原始结果', async () => {
  const cases = [
    ['list_agents', {}, { agents: [{ id: 'research', name: '资料研究员', description: '完整智能体描述' }] }, /查看智能体/, /1 个可用/],
    ['search_thread_messages', { query: '部署' }, { items: [{ title: '部署记录', role: 'user', content: '完整历史消息' }] }, /搜索历史消息/, /部署/],
    ['read_thread_messages', {}, { title: '部署记录', messages: [{ role: 'assistant', content: '完整历史消息' }], tool_calls: [] }, /读取历史消息/, /部署记录/],
    ['install_skill', { source: 'example/research', skill_names: ['research'] }, '已安装 Skill: research\nSkill 路径: /workspace/skills/research/SKILL.md', /安装技能/, /research/],
    ['mark_for_improvement', { reason: '完整改进原因' }, { id: 'feedback-1', saved: true }, /记录改进线索/, /tool-status-icon--completed/],
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
    assert.match(html, /tool-status-icon--(?:running|waiting)/)
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
      [{}, /tool-status-icon--(?:running|waiting)|进行中/]
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
    assert.match(html, /tool-status-icon--error/)
    assert.match(html, /执行失败/)
    assert.doesNotMatch(html, /tool-status-icon--completed|tool-content|Skill 路径/)
  }
  const success = await render(ToolCallRenderer, {
    toolCall: { name: 'install_skill', result: '已安装 Skill: error-helper\nSkill 路径: /workspace/skills/error-helper/SKILL.md' }
  })
  assert.match(success, /tool-status-icon--completed/)
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

test('补齐的设备工具 device_get 与 command_cancel 使用专用标题而非通用兜底', async () => {
  for (const [name, label, icon] of [
    ['device_get', '查看设备详情', 'monitor'],
    ['command_cancel', '取消设备命令', 'square']
  ]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name, args: { device_id: 'device-fixture' }, result: { status: 'executed', result: {} } },
      appearance: 'timeline'
    })
    assert.ok(html.includes(`>${label}</span>`), name)
    assert.match(html, new RegExp(`lucide-${icon}`), name)
    assert.doesNotMatch(html, new RegExp(`工具 ${name}`), name)
  }
})

test('设备详情读取顶层设备回执，展示名称、状态与能力并折叠诊断数据', async () => {
  const device = {
    id: 'detail-fixture', name: '演示工作站', kind: 'computer', platform: 'darwin',
    status: 'online', paused: false, capabilities: ['files', 'screen'],
    available: { files: true, screen: false }, sessions: []
  }
  for (const result of [device, [{ type: 'text', text: JSON.stringify(device) }]]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name: 'device_get', args: { device_id: device.id }, result },
      appearance: 'timeline', defaultExpanded: true
    })
    assert.match(html, /lucide-monitor/)
    assert.match(html, /alt="macOS · 电脑"/)
    assert.match(html, />演示工作站<\/span>/)
    assert.match(html, /<dd[^>]*>在线<\/dd>/)
    assert.match(html, /文件访问、屏幕查看（暂不可用）/)
    assert.doesNotMatch(html, /参数:/)
    assert.match(html, /<details[^>]*><summary[^>]*>原始结果/)
    assert.doesNotMatch(html, /<details[^>]*\bopen/)
    assert.match(html, /detail-fixture/)
  }
  for (const [status, paused, expected] of [
    ['online', true, '已暂停'], ['offline', false, '离线'], ['revoked', true, '已撤销']
  ]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name: 'device_get', result: { ...device, status, paused } }, defaultExpanded: true
    })
    assert.ok(html.includes(`>${expected}</dd>`))
  }
})

test('设备详情的失败、未完成及缺失设备回执不生成设备摘要，保留参数诊断', async () => {
  for (const toolCall of [
    { result: { error: '设备不可访问', id: 'detail-fixture', name: '不可采信设备' } },
    { status: 'waiting', result: { id: 'detail-fixture', name: '不可采信设备' } },
    { result: { status: 'executed', result: {} } },
    {}
  ]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name: 'device_get', args: { device_id: 'detail-fixture' }, ...toolCall },
      defaultExpanded: true
    })
    assert.doesNotMatch(html, /class="device-name"|<dl/)
    assert.match(html, /参数:/)
    if (toolCall.result?.error) assert.match(html, /执行失败/)
  }
})

test('查看设备的 header 使用平台图标和名称，空列表不生成设备描述', async () => {
  const devices = [
    { id: 'browser-fixture', kind: 'browser', name: 'Chrome' },
    { id: 'computer-fixture', kind: 'computer', platform: 'darwin', name: 'air' },
    { id: 'future-fixture', kind: 'future', name: '测试终端' }
  ]
  const expected = '3 台设备 · Chrome、air、测试终端'
  for (const result of [{ devices }, [{ type: 'text', text: JSON.stringify({ devices }) }]]) {
    const html = await render(ToolCallRenderer, { toolCall: { name: 'devices_list', result } })
    assert.ok(html.includes(`title="${expected}"`))
    for (const name of ['Chrome', 'air', '测试终端']) assert.ok(html.includes(`>${name}</span>`))
    assert.match(html, /alt="Chrome · 浏览器"/)
    assert.match(html, /alt="macOS · 电脑"/)
    assert.match(html, /lucide-monitor/)
    assert.doesNotMatch(html, /浏览器 Chrome|电脑 air/)
    assert.doesNotMatch(html, /browser-fixture|computer-fixture|future-fixture/)
  }
  const empty = await render(ToolCallRenderer, { toolCall: { name: 'devices_list', result: { devices: [] } } })
  assert.match(empty, /title="0 台设备"/)
  assert.doesNotMatch(empty, /undefined|未命名设备/)
})

test('Abridge 关闭设备连接使用专用标题和图标，保留折叠回执且不误报成功', async () => {
  for (const content of [
    { session_id: 'session-close-fixture', status: 'stopped' },
    [{ type: 'text', text: '{"session_id":"session-close-fixture","status":"stopped"}' }]
  ]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name: 'session_close', args: { session_id: 'session-close-fixture' }, result: content },
      defaultExpanded: true, appearance: 'timeline'
    })
    assert.match(html, /断开设备连接/)
    assert.match(html, /连接已关闭/)
    assert.match(html, /lucide-unplug/)
    assert.match(html, /<details[^>]*><summary[^>]*>原始结果/)
    assert.doesNotMatch(html, /<details[^>]*\bopen/)
    assert.match(html, /session-close-fixture/)
  }
  for (const result of [undefined, { status: 'failed', error: '无法关闭连接' }, { status: 'outcome_unknown' }]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name: 'session_close', args: { session_id: 'session-close-fixture' }, result }
    })
    assert.match(html, /断开设备连接/)
    assert.doesNotMatch(html, /连接已关闭/)
    if (result) assert.match(html, result.status === 'failed' ? /执行失败/ : /执行结果未知/)
  }
})

test('连接设备 header 展示回执设备与本次能力，不把设备全部能力当作会话能力', async () => {
  const receipt = {
    status: 'active', capabilities: ['shell'],
    device: { id: 'device-fixture', name: 'air', kind: 'computer', platform: 'darwin', capabilities: ['files', 'input', 'screen', 'shell'] }
  }
  for (const result of [receipt, [{ type: 'text', text: JSON.stringify(receipt) }]]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name: 'session_open', args: { device_id: 'device-fixture', capabilities: ['browser'] }, result }
    })
    assert.match(html, /alt="macOS · 电脑"/)
    assert.match(html, />air<\/span>/)
    assert.match(html, /命令执行/)
    assert.doesNotMatch(html, /device-fixture|连接浏览器|网页操作|文件访问|键鼠操作|屏幕查看/)
  }
  const empty = await render(ToolCallRenderer, {
    toolCall: { name: 'session_open', args: { capabilities: ['browser'] }, result: { ...receipt, capabilities: [] } }
  })
  assert.doesNotMatch(empty, /连接浏览器|网页操作|命令执行/)
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

test('桌面点击 header 在坐标前展示操作标识，缺少标识时保留坐标', async () => {
  for (const [operationId, x, y, expected] of [
    ['op_click_moments_icon', 88, 513, '(88, 513)'],
    [undefined, 88, 513, '(88, 513)'],
    ['op_click_origin', 0, 0, '(0, 0)']
  ]) {
    const html = await render(ToolCallRenderer, {
      toolCall: {
        id: 'desktop-click', name: 'computer_act',
        args: { operation_id: operationId, action: { type: 'click', x, y } }
      },
      appearance: 'timeline'
    })
    assert.match(html, /class="note"[^>]*>点击<\/span>/)
    assert.ok(html.includes(`title="${expected}"`))
    assert.ok(html.includes(`>${expected}</span>`))
    assert.doesNotMatch(html, /undefined/)
  }
})


test('设备与浏览器工具 header 只展示目标，隐藏操作标识', async () => {
  for (const [name, args, result, description] of [
    ['computer_act', { action: { type: 'key', key: 'Enter' } }, undefined, 'Enter'],
    ['computer_act', { action: { type: 'key', key: 'g', modifiers: ['cmd', 'shift'] } }, undefined, 'cmd+shift+g'],
    ['computer_act', { action: { type: 'key', key: 'a', modifiers: [] } }, undefined, 'a'],
    ['computer_act', { action: { type: 'scroll' } }, undefined, ''],
    ['browser_act', { action: { type: 'click', element_ref: 'ref-42' } }, undefined, 'ref-42'],
    ['browser_open', { url: 'https://example.com' }, undefined, 'https://example.com'],
    ['browser_observe', {}, { status: 'executed', result: { title: '当前页面' } }, '当前页面'],
    ['computer_observe', {}, { status: 'executed', result: { observation: { app_name: '微信' } } }, '微信'],
    ['session_open', { device_id: 'device-1' }, undefined, 'device-1'],
    ['files_list', { path: '/tmp/example' }, undefined, '/tmp/example'],
    ['file_write', { path: '/tmp/example/note.txt' }, undefined, '/tmp/example/note.txt'],
    ['file_read', { path: '/tmp/example/note.txt' }, undefined, '/tmp/example/note.txt'],
    ['devices_list', {}, undefined, '']
  ]) {
    const operationId = `op_${name}`
    const html = await render(ToolCallRenderer, {
      toolCall: { id: name, name, args: { ...args, operation_id: operationId }, result },
      appearance: 'timeline'
    })
    if (description) {
      assert.ok(html.includes(`title="${description}"`), name)
      assert.ok(html.includes(`>${description}</span>`), name)
    } else {
      assert.doesNotMatch(html, /class="description"/)
    }
    assert.ok(!html.includes(`title="${operationId}`), name)
  }
})


test('失败工具保留工具图标与明确的失败文字，未知工具使用扳手而非叉号', async () => {
  for (const [name, icon] of [['web_search', 'globe'], ['unknown_tool', 'wrench']]) {
    const html = await render(BaseToolCall, { toolCall: { name, status: 'error', error_message: '执行失败' } })
    assert.match(html, /tool-status-icon--error/)
    assert.ok(html.includes(`lucide-${icon}`))
    assert.doesNotMatch(html, /lucide-circle-x/)
  }
})

test('设备命令状态展示已结束和运行中输出，并保留超时与截断提示', async () => {
  for (const [receipt, expected, label] of [
    [{ status: 'executed', result: { stdout: 'done-output', stderr: '', exit_code: 0 }, output_truncated: true }, ['done-output', '输出已截断'], '命令已结束'],
    [{ status: 'dispatched', output: { stdout: 'partial-output', stderr: 'partial-error' } }, ['partial-output', '标准错误：', 'partial-error'], '命令处理中'],
    [{ status: 'accepted' }, [], '命令处理中'],
    [{ status: 'executed', result: { stdout: 'failure-output', stderr: 'failure-error', exit_code: 1, timed_out: true } }, ['failure-output', 'failure-error', '命令执行超时', '执行失败'], '命令已结束'],
    [{ status: 'executed', result_available: false }, ['命令输出已过期'], '输出已过期']
  ]) {
    const html = await render(ToolCallRenderer, {
      toolCall: { name: 'command_status', args: { operation_id: 'operation-status-fixture', stdout_offset: 20 }, result: [{ type: 'text', text: JSON.stringify(receipt) }] },
      defaultExpanded: true
    })
    assert.match(html, /查看命令输出/)
    assert.ok(html.includes(`title="${label}"`))
    for (const text of expected) assert.ok(html.includes(text), text)
    assert.match(html, /<details[^>]*><summary[^>]*>原始结果/)
    assert.doesNotMatch(html, /<details[^>]*\bopen/)
  }
})

test('设备文件工具展示目录、写入回执和纯文本内容，保留折叠原始结果', async () => {
  const path = '/tmp/example/note.txt'
  const cases = [
    ['files_list', { path: '/tmp/example', entries: [{ name: 'note.txt', is_directory: false, size: 13 }, { name: 'folder', is_directory: true }, { name: 'shortcut', is_directory: false, is_symlink: true }], truncated: true }, ['列出设备目录', 'note.txt', 'folder', 'shortcut*', '目录列表已截断']],
    ['file_write', { path, size: 13, overwritten: true }, ['写入设备文件', '写入成功', '13 B', '已覆盖']],
    ['file_read', { path, content: '<script>hello</script>\n', size: 23, truncated: true }, ['读取设备文件', '&lt;script&gt;hello&lt;/script&gt;', '内容已截断']]
  ]
  for (const [name, output, expected] of cases) {
    const receipt = { status: 'executed', result: output }
    for (const result of [JSON.stringify(receipt), [{ type: 'text', text: JSON.stringify(receipt) }]]) {
      const html = await render(ToolCallRenderer, {
        toolCall: { name, args: { path: '/requested/path' }, result }, defaultExpanded: true
      })
      for (const text of expected) assert.ok(html.includes(text), `${name}: ${text}`)
      assert.ok(html.includes(`title="${output.path}"`))
      if (name === 'files_list') {
        const list = html.match(/<ul[^>]*class="device-file-list"[^>]*>([\s\S]*?)<\/ul>/)?.[1]
        assert.ok(list)
        assert.doesNotMatch(list, /13 B|<img|<svg|class="device-file-meta"/)
      }
      assert.match(html, /<details[^>]*><summary[^>]*>原始结果/)
      assert.doesNotMatch(html, /<details[^>]*\bopen|<script>|href="\/tmp/)
    }
  }
  for (const [name, output, expected] of [
    ['files_list', { entries: [] }, '目录为空'],
    ['file_read', { content: '', size: 0 }, '文件为空'],
    ['file_write', { size: 0, overwritten: false }, '0 B']
  ]) {
    const html = await render(ToolCallRenderer, { toolCall: { name, result: { status: 'executed', result: output } }, defaultExpanded: true })
    assert.ok(html.includes(expected))
    assert.doesNotMatch(html, /已覆盖/)
  }
})

test('设备文件失败和结果未知不展示成功输出，未知工具保持通用渲染', async () => {
  for (const name of ['files_list', 'file_read', 'file_write']) {
    for (const status of ['failed', 'rejected', 'outcome_unknown']) {
      const html = await render(ToolCallRenderer, {
        toolCall: { name, result: [{ type: 'text', text: JSON.stringify({ status, error: status === 'outcome_unknown' ? undefined : '拒绝文件访问', result: { content: '不应展示正文', entries: [], size: 13 } }) }] }, defaultExpanded: true
      })
      assert.match(html, status === 'outcome_unknown' ? /执行结果未知/ : /执行失败/)
      assert.doesNotMatch(html, /写入成功|目录为空|class="device-file-content"/)
    }
  }
  const html = await render(ToolCallRenderer, { toolCall: { name: 'files_future', result: { error: '业务字段' } } })
  assert.match(html, /files_future/)
  assert.doesNotMatch(html, /执行失败|原始结果/)
})

test('执行设备命令的 header 仅显示命令，不拼接操作标识', async () => {
  const html = await render(ToolCallRenderer, {
    toolCall: {
      name: 'command_execute',
      args: { operation_id: 'op_check_path', command: 'pwd' }
    },
    appearance: 'timeline'
  })
  assert.ok(html.includes('title="pwd"'))
  assert.ok(html.includes('>pwd</span>'))
  assert.doesNotMatch(html, /op_check_path/)
})
