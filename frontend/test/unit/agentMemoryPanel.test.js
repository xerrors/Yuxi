import assert from 'node:assert/strict'
import test from 'node:test'
import { createSSRApp, h, reactive } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { Modal, message } from 'ant-design-vue'
import { createTestServer } from '../helpers/createTestServer.js'

/** 装配真实面板，仅替换文件 API 与确认弹窗边界。 */
async function setupPanel(t) {
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
    plugins: [
      {
        name: 'memory-file-boundary',
        enforce: 'pre',
        load(id) {
          if (id.endsWith('/src/apis/workspace_api.js'))
            return `
        export const getWorkspaceFileContent = (...args) => globalThis.__memoryPanelTest.read(...args);
        export const saveWorkspaceFileContent = (...args) => globalThis.__memoryPanelTest.write(...args);`
        }
      }
    ]
  })
  const originals = { confirm: Modal.confirm, error: message.error, success: message.success }
  const files = new Map([
    ['/agents/MEMORY.md', '# 长期记忆\n喜欢简洁回答\n'],
    ['/agents/USER.md', '# 用户档案\n使用中文\n'],
    ['/agents/AGENTS.md', '# 行为约定\n先核对事实\n']
  ])
  const errors = [],
    busy = [],
    confirmations = []
  const boundary = {
    read: async (path) =>
      new Response(files.get(path), { headers: { 'content-type': 'text/markdown' } }),
    write: async (path, content) => {
      files.set(path, content)
      return { success: true }
    }
  }
  globalThis.__memoryPanelTest = boundary
  Modal.confirm = (options) => {
    confirmations.push(options)
  }
  message.error = (error) => errors.push(error)
  message.success = () => {}
  t.after(async () => {
    Modal.confirm = originals.confirm
    message.error = originals.error
    message.success = originals.success
    delete globalThis.__memoryPanelTest
    await server.close()
  })
  const { default: component } = await server.ssrLoadModule(
    '/src/modules/agents/ui/AgentMemoryPanel.vue'
  )
  const props = reactive({ disabled: false })
  let panel
  await renderToString(
    createSSRApp({
      setup() {
        panel = component.setup(props, {
          expose() {},
          emit: (name, value) => {
            assert.equal(name, 'busy')
            busy.push(value)
          }
        })
        return () => h('div')
      }
    })
  )
  await panel.load()
  return { panel, props, boundary, files, errors, busy, confirmations }
}

test('三个记忆页读取对应个人文件，保存后以重新读取的内容展示', async (t) => {
  const { panel, files, boundary } = await setupPanel(t)
  assert.equal(panel.content.value, '# 长期记忆\n喜欢简洁回答\n')
  await panel.selectFile('user')
  assert.equal(panel.content.value, '# 用户档案\n使用中文\n')
  panel.startEdit()
  panel.draft.value = '# 用户档案\n优先给出结论\n'
  await panel.save()
  assert.equal(files.get('/agents/USER.md'), '# 用户档案\n优先给出结论\n')
  assert.equal(panel.content.value, files.get('/agents/USER.md'))
  assert.equal(panel.editing.value, false)
  assert.equal(files.get('/agents/MEMORY.md'), '# 长期记忆\n喜欢简洁回答\n')
  await panel.selectFile('agents')
  assert.equal(panel.content.value, '# 行为约定\n先核对事实\n')
  files.set('/agents/AGENTS.md', '')
  await panel.refresh()
  assert.equal(panel.content.value, '')
  panel.startEdit()
  assert.equal(panel.editing.value, true, '空文件仍可编辑')
  boundary.read = async () =>
    new Response('# 外部修改', { headers: { 'content-type': 'text/markdown' } })
  panel.draft.value = '保存请求正文'
  await panel.save()
  assert.equal(panel.content.value, '# 外部修改', '保存后的显示必须来自服务端回读')
})

test('切换、刷新和离开时拒绝丢弃均保留草稿，确认后才切换', async (t) => {
  const { panel, confirmations } = await setupPanel(t)
  panel.startEdit()
  panel.draft.value = '尚未保存'
  for (const action of [
    () => panel.selectFile('user'),
    () => panel.refresh(),
    () => panel.confirmLeave()
  ]) {
    const pending = action()
    confirmations.at(-1).onCancel()
    await pending
    assert.equal(panel.selectedKey.value, 'memory')
    assert.equal(panel.draft.value, '尚未保存')
    assert.equal(panel.editing.value, true)
  }
  const switching = panel.selectFile('user')
  confirmations.at(-1).onOk()
  await switching
  assert.equal(panel.selectedKey.value, 'user')
  assert.equal(panel.editing.value, false)
  assert.equal(panel.content.value, '# 用户档案\n使用中文\n')
})

test('保存失败或回读失败保留草稿，保存期间不能离开或重复提交', async (t) => {
  const { panel, boundary, errors, busy, files } = await setupPanel(t)
  panel.startEdit()
  panel.draft.value = '要记住的内容'
  boundary.write = async () => {
    throw new Error('写入失败')
  }
  await panel.save()
  assert.equal(files.get('/agents/MEMORY.md'), '# 长期记忆\n喜欢简洁回答\n')
  assert.equal(panel.draft.value, '要记住的内容')
  assert.equal(panel.editing.value, true)
  assert.equal(errors.at(-1), '写入失败')
  let finish
  boundary.write = () =>
    new Promise((resolve) => {
      finish = resolve
    })
  const pending = panel.save()
  assert.equal(await panel.confirmLeave(), false)
  await panel.selectFile('user')
  await panel.save()
  assert.equal(panel.selectedKey.value, 'memory')
  assert.deepEqual(busy, [true, false, true])
  boundary.read = async () => {
    throw new Error('回读失败')
  }
  finish({ success: true })
  await pending
  assert.equal(panel.draft.value, '要记住的内容')
  assert.equal(panel.editing.value, true)
  assert.equal(errors.at(-1), '回读失败')
  assert.equal(busy.at(-1), false)
})

test('不支持的预览和读取失败不能进入编辑，重试恢复；旧响应不覆盖新页', async (t) => {
  const { panel, boundary } = await setupPanel(t)
  const originalRead = boundary.read
  boundary.read = async () =>
    Response.json({ supported: false, message: '文件过大', previewType: 'unsupported' })
  await panel.refresh()
  panel.startEdit()
  assert.equal(panel.editing.value, false)
  assert.equal(panel.error.value, '文件过大')
  boundary.read = async () =>
    Response.json({
      supported: true,
      truncated: true,
      content: '仅有前缀',
      previewType: 'markdown'
    })
  await panel.refresh()
  panel.startEdit()
  assert.equal(panel.editing.value, false, '截断预览不能覆盖保存完整文件')
  assert.match(panel.error.value, /预览已截断/)
  boundary.read = async () => {
    throw new Error('网络断开')
  }
  await panel.load()
  assert.equal(panel.error.value, '网络断开')
  panel.startEdit()
  assert.equal(panel.editing.value, false)
  let resolveOld
  boundary.read = () =>
    new Promise((resolve) => {
      resolveOld = resolve
    })
  const oldRead = panel.load()
  boundary.read = originalRead
  await panel.selectFile('user')
  resolveOld(new Response('过期的长期记忆', { headers: { 'content-type': 'text/markdown' } }))
  await oldRead
  assert.equal(panel.error.value, '')
  assert.equal(panel.loading.value, false)
  assert.equal(panel.content.value, '# 用户档案\n使用中文\n')
})

test('智能体配置保存期间不能产生或提交新的记忆草稿', async (t) => {
  const { panel, props, files } = await setupPanel(t)
  props.disabled = true
  panel.startEdit()
  assert.equal(panel.editing.value, false)
  props.disabled = false
  panel.startEdit()
  panel.draft.value = '未提交的草稿'
  props.disabled = true
  await panel.save()
  assert.equal(files.get('/agents/MEMORY.md'), '# 长期记忆\n喜欢简洁回答\n')
  assert.equal(panel.draft.value, '未提交的草稿')
})
