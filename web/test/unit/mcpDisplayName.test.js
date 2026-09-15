import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { ref } from 'vue'

const source = readFileSync(new URL('../../src/components/extensions/McpDetailView.vue', import.meta.url), 'utf8')
const body = source.slice(source.indexOf('const agentStore ='), source.indexOf('const route ='))
function panel({ writeFail = false, refreshFail = false } = {}) {
  const calls = []
  const api = {
    async setDisplayName(...args) { calls.push(args); if (writeFail) throw Error('write failed'); return { data: { name: args[1] } } },
    async setToolDisplayName(...args) { calls.push(args); if (writeFail) throw Error('write failed'); return { data: { display_name: args[2] || args[1] } } }
  }
  const server = ref({ slug: 'stable-server', name: 'Original', is_builtin: true })
  const setup = new Function('ref', 'useAgentStore', 'mcpApi', 'server', 'message', `${body}; return {openDisplayName, saveDisplayName, displayName, nameOpen, openToolDisplayName, saveToolDisplayName, toolDisplayName, toolNameOpen, toolNameError}`)
  const result = setup(ref, () => ({ async refreshMcpDisplayNames() { calls.push('refresh'); if (refreshFail) throw Error('offline') } }), api, server, { warning: text => calls.push(text) })
  return { ...result, calls, server }
}

test('内置MCP显示按钮可用，窄接口保存后刷新聊天名称', async () => {
  const p = panel()
  p.openDisplayName()
  p.displayName.value = '中文服务'
  await p.saveDisplayName()
  assert.deepEqual(p.calls, [['stable-server', '中文服务'], 'refresh'])
  assert.equal(p.server.value.name, '中文服务')
  assert.equal(p.server.value.slug, 'stable-server')
  assert.ok(source.includes(':disabled="!server"'))
  assert.ok(source.includes('系统内置 MCP 的连接配置由代码管理'))
})

test('MCP工具只更新display_name，失败保留编辑，刷新失败仍报告已保存', async () => {
  const tool = { name: 'draw_chart', display_name: 'Old', id: 'unchanged', enabled: true }
  const p = panel({ refreshFail: true })
  p.openToolDisplayName(tool)
  p.toolDisplayName.value = '画图'
  await p.saveToolDisplayName()
  assert.deepEqual(tool, { name: 'draw_chart', display_name: '画图', id: 'unchanged', enabled: true })
  assert.deepEqual(p.calls[0], ['stable-server', 'draw_chart', '画图'])
  assert.equal(p.toolNameOpen.value, false)
  assert.ok(p.calls.at(-1).includes('已保存'))
  const failed = panel({ writeFail: true })
  failed.openToolDisplayName(tool)
  failed.toolDisplayName.value = 'Draft'
  await failed.saveToolDisplayName()
  assert.equal(failed.toolNameOpen.value, true)
  assert.equal(failed.toolNameError.value, 'write failed')
  assert.equal(tool.display_name, '画图')
  assert.equal(failed.calls.length, 1)
})
