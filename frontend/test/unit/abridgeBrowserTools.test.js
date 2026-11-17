import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { after, before, test } from 'node:test'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createTestServer } from '../helpers/createTestServer.js'

let server, Tool
before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  globalThis.window = { addEventListener() {}, removeEventListener() {} }
  globalThis.document = { documentElement: { classList: { add() {}, remove() {} } }, getElementsByTagName: () => [] }
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' })
  server = await createTestServer({ root: path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..'), server: { middlewareMode: true } })
  ;({ default: Tool } = await server.ssrLoadModule('/src/modules/session/ui/tools/ToolCallRenderer.vue'))
})
after(async () => {
  await server?.close()
  for (const key of ['localStorage', 'window', 'document', 'getComputedStyle']) delete globalThis[key]
})

/** 从实际工具注册入口渲染协议回执，验证用户可见结果。 */
async function render(name, receipt, args = {}, fields = {}) {
  return renderToString(createSSRApp({ render: () => h(Tool, {
    toolCall: { name, args, result: receipt, ...fields }, defaultExpanded: true, appearance: 'timeline'
  }) }).use(createPinia()).use(createRouter({ history: createMemoryHistory(), routes: [] })))
}
const textResult = { mode: 'text', text: '<script>remote()</script>\n页面正文', source_url: 'https://example.com/page', offset: 10, total: 300, next_offset: 42, coverage: 'loaded_main_document', truncated: true, cached: false }
const scriptResult = { value: { count: 2 }, output: ['完成两个步骤'], steps_completed: 2 }

test('不支持 Object.groupBy 的浏览器仍能显示设备与表格回执', async (t) => {
  const original = Object.getOwnPropertyDescriptor(Object, 'groupBy')
  Object.defineProperty(Object, 'groupBy', { value: undefined, configurable: true })
  t.after(() => original ? Object.defineProperty(Object, 'groupBy', original) : delete Object.groupBy)
  const devices = await render('devices_list', { devices: [] })
  assert.match(devices, /查看设备/)
  const table = await render('browser_read', {
    status: 'executed', result: {
      mode: 'tables', rows: [{ table: 0, cells: ['兼容浏览器'] }], offset: 0, total: 1
    }
  })
  assert.match(table, /兼容浏览器/)
})

test('网页读取支持实际文本信封、转义正文，并区分分页与快照截断', async () => {
  for (const receipt of [{ status: 'executed', result: textResult }, [{ type: 'text', text: JSON.stringify({ status: 'executed', result: textResult }) }]]) {
    const html = await render('browser_read', receipt, { selector: 'main' })
    assert.match(html, /读取网页内容/)
    assert.match(html, /https:\/\/example.com\/page/)
    assert.match(html, /已加载的主文档/)
    assert.match(html, /还有后续内容/)
    assert.match(html, /快照已截断/)
    assert.match(html, /&lt;script&gt;remote\(\)&lt;\/script&gt;/)
    assert.doesNotMatch(html, /<script>|workspace\/|<details[^>]* open/)
  }
})

test('表格读取按来源表分组，保留单元格、空结果和缓存范围', async () => {
  const rows = [
    { table: 0, row: 0, cells: [{ text: '项目', row_span: 1, col_span: 2 }] },
    { table: 1, row: 0, cells: [{ text: '<img onerror=remote()>', row_span: 1, col_span: 1 }] }
  ]
  const html = await render('browser_read', { status: 'executed', result: { ...textResult, mode: 'tables', rows, offset: 0, total: 2, next_offset: null, truncated: false, cached: true, coverage: 'loaded_same_origin_frame' } })
  assert.match(html, /表格 1（当前页）/)
  assert.match(html, /表格 2（当前页）/)
  assert.match(html, /colspan="2"/)
  assert.match(html, /已加载的同源框架 · 2\/2 行 · 已保存快照/)
  assert.match(html, /&lt;img onerror=remote\(\)&gt;/)
  assert.doesNotMatch(html, /<img |还有后续内容|快照已截断/)
  const empty = await render('browser_read', { status: 'executed', result: { ...textResult, mode: 'tables', rows: [], total: 0, offset: 0, next_offset: null } })
  assert.match(empty, /未读取到表格行/)
  const text = await render('browser_read', { status: 'executed', result: { ...textResult, text: '', total: 0, offset: 0, next_offset: null, coverage: 'unknown' } })
  assert.match(text, /未读取到网页文本/)
  assert.match(text, /读取范围未说明/)
  assert.doesNotMatch(text, /已加载的主文档/)
})

test('等待只有确认匹配才显示满足，超时沿用失败结果', async () => {
  const html = await render('browser_wait', { status: 'executed', result: { matched: true, state: 'visible' } }, { selector: '#ready', value: '不放入标题' })
  assert.match(html, /等待网页条件/)
  assert.match(html, /#ready/)
  assert.match(html, /等待条件已满足/)
  const unmatched = await render('browser_wait', { status: 'executed', result: { matched: false } })
  assert.doesNotMatch(unmatched, /等待条件已满足/)
  const timeout = await render('browser_wait', { status: 'failed', error: { code: 'wait_timeout', message: '等待条件未在期限内满足' } })
  assert.match(timeout, /tool-status-icon--error/)
  assert.match(timeout, /等待条件未在期限内满足/)
  assert.doesNotMatch(timeout, /等待条件已满足/)
})

for (const value of [0, false, null, '', { count: 2 }]) {
  test(`脚本返回 ${JSON.stringify(value)} 不被真值过滤，代码不进入标题`, async () => {
    const html = await render('browser_exec', { status: 'executed', result: { ...scriptResult, value } }, { code: 'privateCode()' })
    assert.match(html, /执行浏览器脚本/)
    assert.match(html, /已完成 2 个步骤/)
    assert.match(html, /日志：/)
    assert.match(html, /aria-label="浏览器脚本返回值"/)
    assert.doesNotMatch(html.split('tool-content')[0], /privateCode/)
  })
}

test('脚本支持截断预览、异步状态和 command_status 回读', async () => {
  const html = await render('command_status', { status: 'executed', result: { ...scriptResult, value: null, value_truncated: true, value_preview: '长返回值预览' } })
  assert.match(html, /查看浏览器执行结果/)
  assert.match(html, /长返回值预览/)
  assert.match(html, /返回值已截断/)
  for (const status of ['accepted', 'dispatched']) {
    const pending = await render('browser_exec', { status, output: { stdout: '', stderr: '' } })
    assert.match(pending, /脚本处理中/)
    assert.doesNotMatch(pending, /浏览器脚本返回值|已完成 \d+ 个步骤/)
  }
})

test('拒绝、失败、未知结局不能因貌似成功的 result 展示正文或成功返回值', async () => {
  for (const status of ['rejected', 'failed', 'outcome_unknown']) {
    const read = await render('browser_read', { status, result: textResult })
    assert.doesNotMatch(read, /class="device-file-content"|还有后续内容/)
    const wait = await render('browser_wait', { status, result: { matched: true } })
    assert.doesNotMatch(wait, /等待条件已满足/)
    const script = await render('browser_exec', { status, result: scriptResult, error: { message: '请检查已完成的操作' } })
    assert.doesNotMatch(script, /aria-label="浏览器脚本返回值"/)
    if (status === 'outcome_unknown') {
      assert.match(script, /执行结果未知/)
      assert.match(script, /已完成 2 个步骤/)
      assert.match(script, /日志：/)
    }
  }
  const malformed = await render('browser_read', { status: 'executed', result: { mode: 'other' } })
  // 未识别的回执仍可检查原始证据，但不能渲染成可读成功结果。
  assert.match(malformed, /原始结果/)
  assert.doesNotMatch(malformed, /device-file-content/)
})

test('新增网页动作有中文名称，内部步骤和普通工具不进入专用解释', async () => {
  for (const [type, label] of [['hover', '悬停网页元素'], ['press', '网页按键'], ['select', '选择网页选项'], ['scroll', '滚动网页']]) {
    const html = await render('browser_act', { status: 'executed', result: {} }, { action: { type, element_ref: 'ref-42', text: '输入内容' } })
    assert.match(html, new RegExp(label))
    assert.match(html, /ref-42/)
  }
  for (const name of ['browser_evaluate', 'browser_locate_act', 'future_browser_tool']) {
    const html = await render(name, { status: 'executed', result: scriptResult })
    assert.doesNotMatch(html, /abridge-tool-icon|浏览器脚本返回值/)
  }
  const shell = await render('command_status', { status: 'executed', result: { stdout: 'shell输出', exit_code: 0 } })
  assert.match(shell, /shell输出/)
  assert.doesNotMatch(shell, /查看浏览器执行结果|浏览器脚本返回值/)
})


test('真实 MCP isError 信封仍展示未知结局的完成步骤与诊断，不显示成功值', async () => {
  const receipt = { status: 'outcome_unknown', result: scriptResult, error: { message: '部分步骤已执行，请检查页面' } }
  for (const name of ['browser_exec', 'command_status']) {
    const html = await render(name, undefined, {}, { tool_call_result: { status: 'error', content: [{ type: 'text', text: JSON.stringify(receipt) }] } })
    assert.match(html, /执行结果未知/)
    assert.match(html, /已完成 2 个步骤/)
    assert.match(html, /完成两个步骤/)
    assert.match(html, /部分步骤已执行，请检查页面/)
    assert.doesNotMatch(html, /aria-label="浏览器脚本返回值"/)
  }
})

test('输入文本 header 显示文本，不显示坐标或元素编号', async () => {
  for (const text of ['https://github.com/xerrors/aBridge', '<b>输入内容</b>', '']) {
    const html = await render('computer_act', { status: 'executed' }, {
      action: { type: 'type', text, element_ref: 'e10', x: 120, y: 240 }
    })
    const header = html.match(/<div class="sep-header"[^>]*>([\s\S]*?)<\/div>/)[1]
    assert.match(header, /输入文本/)
    assert.doesNotMatch(header, /e10|\(120, 240\)/)
    if (text) assert.ok(header.includes(text.replaceAll('<', '&lt;').replaceAll('>', '&gt;')))
    else assert.doesNotMatch(header, /class="separator"/)
  }
})


test('普通模式仍可查看工具参数，设备与普通工具共享折叠入口', async () => {
  for (const name of ['command_execute', 'calculator']) {
    const html = await render(name, { status: 'executed', result: { stdout: 'ok', exit_code: 0 } }, { command: 'echo hello' })
    assert.match(html, /class="tool-params"/)
    assert.match(html, /参数:/)
  }
})
