import assert from 'node:assert/strict'
import test from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { message } from 'ant-design-vue'
import { createSSRApp, nextTick } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { mountWorkspaceView } from '../helpers/workspaceView.js'

/** 通过真实页面模板读取主区域，子组件只标记页面放置的位置。 */
async function mainRegion(component, state) {
  const app = createSSRApp({ ...component, setup: () => state })
  app.config.warnHandler = () => {}
  const html = await renderToString(app)
  return html.match(/<main\b[^>]*>([\s\S]*?)<\/main>/)[1]
}

test('概览文件在主区域预览，关闭返回概览，重新打开按用户操作记录', async (t) => {
  const { state, component, boundary } = await mountWorkspaceView(t)
  const entry = { path: '/agents/MEMORY.md', name: 'MEMORY.md' }
  const opened = []
  boundary.openWorkspaceFile = async path => { opened.push(path); return {} }
  const before = await mainRegion(component, state)
  assert.match(before, /data-child="WorkspaceOverview"/)
  assert.doesNotMatch(before, /WorkspacePreviewPane/)
  await state.loadWorkspacePreview(entry)
  const reading = await mainRegion(component, state)
  assert.match(reading, /WorkspaceOverview"[^>]*display:none/)
  assert.match(reading, /data-child="WorkspacePreviewPane"/)
  assert.doesNotMatch(reading, /WorkspaceFileList/)
  await state.returnFromPreview()
  assert.equal(await mainRegion(component, state), before)
  assert.deepEqual(opened, [entry.path])
})

test('刷新个人文件内容不推进访问时间，离开后异步响应不能覆盖新位置', async (t) => {
  const { state, boundary, instance } = await mountWorkspaceView(t)
  const path = '/agents/MEMORY.md'
  let userOpens = 0
  let refreshReads = 0
  boundary.openWorkspaceFile = async () => { userOpens++; return {} }
  boundary.getWorkspaceFileContent = async () => { refreshReads++; return {} }
  await state.openFileByPath(path)
  for (const hook of instance.a || []) await hook()
  assert.equal(refreshReads, 1)
  assert.equal(userOpens, 1)

  const pending = Promise.withResolvers()
  boundary.openWorkspaceFile = () => pending.promise
  const reading = state.loadWorkspacePreview({ path, name: 'MEMORY.md' })
  state.selectOverview()
  pending.resolve({})
  await reading
  assert.equal(state.activeSourceKey.value, 'overview')
  assert.equal(state.previewFile.value, null)
})

test('晚返回的目录请求不能覆盖当前目录', async (t) => {
  const { state, boundary } = await mountWorkspaceView(t)
  const oldFolder = Promise.withResolvers()
  boundary.getWorkspaceTree = path => path === '/first' ? oldFolder.promise : Promise.resolve({ entries: [{ name: 'new.md' }] })
  const old = state.selectWorkspacePath('/first')
  await state.selectWorkspacePath('/second')
  oldFolder.resolve({ entries: [{ name: 'old.md' }] })
  await old
  assert.equal(state.currentPath.value, '/second')
  assert.deepEqual(state.entries.value.map(entry => entry.name), ['new.md'])
})

test('概览预览重新激活按原资源身份刷新，个人文件只记录主动打开，晚返回的刷新不覆盖其他文件', async (t) => {
  const { state, boundary, instance } = await mountWorkspaceView(t)
  const entry = { name: 'report.md', path: '/report.md' }
  const reads = []
  boundary.normalizePreviewResponse = async (content, entry) => ({ ...entry, content })
  boundary.openWorkspaceFile = async path => { reads.push(['open', path]); return '个人初版' }
  boundary.getWorkspaceFileContent = async path => { reads.push(['read', path]); return '个人修订' }
  const activate = () => Promise.all(instance.a.map(hook => hook()))
  await state.loadWorkspacePreview(entry)
  await activate()
  assert.equal(state.previewFile.value.content, '个人修订')
  assert.deepEqual(reads, [['open', entry.path], ['read', entry.path]])

  let report = '产物初版'
  boundary.previewThreadArtifact = async (threadId, path) => { reads.push([threadId, path]); return report }
  await state.loadArtifactPreview({ ...entry, thread_id: 'source-thread' })
  report = '产物修订'
  await activate()
  assert.equal(state.previewFile.value.content, '产物修订')
  assert.deepEqual(reads.slice(2), [['source-thread', entry.path], ['source-thread', entry.path]])
  const pending = Promise.withResolvers()
  boundary.previewThreadArtifact = () => pending.promise
  const restoring = activate()
  await state.loadWorkspacePreview(entry)
  pending.reject(new Error('原产物已删除'))
  await restoring
  assert.equal(state.previewFile.value.content, '个人初版')

  t.mock.method(message, 'error', () => {})
  t.mock.method(console, 'warn', () => {})
  boundary.getWorkspaceFileContent = () => Promise.reject(new Error('文件已删除'))
  await activate()
  assert.equal(state.previewFile.value.status, 'error')
  assert.equal(state.previewFile.value.errorMessage, '文件已删除')
  assert.equal(state.previewFile.value.content, '')
})

test('从其他模块通过全局搜索进入已缓存的个人空间，只加载一次目录并消费文件参数', async (t) => {
  const { state, route, replacements, instance, boundary } = await mountWorkspaceView(t)
  const path = '/agents/MEMORY.md'
  const directory = Promise.withResolvers()
  const requests = []
  boundary.getWorkspaceTree = folder => { requests.push(['tree', folder]); return directory.promise }
  boundary.openWorkspaceFile = async path => { requests.push(['open', path]); return {} }
  route.path = '/agent'
  await nextTick()
  route.query = { open: path, other: 'keep' }
  route.path = '/workspace'
  await nextTick()
  const activated = Promise.all(instance.a.map(hook => hook()))
  assert.deepEqual(requests, [['tree', '/agents']])
  directory.resolve({ entries: [{ path, name: 'MEMORY.md', is_dir: false }] })
  await activated
  await setImmediate()
  assert.equal(state.selectedPreviewPath.value, path)
  assert.deepEqual(requests, [['tree', '/agents'], ['open', path]])
  assert.deepEqual(replacements, [{ query: { other: 'keep' } }])
})

test('同名 Agent 产物按来源会话读取，晚返回的内容不能覆盖当前产物', async (t) => {
  const { state, boundary } = await mountWorkspaceView(t)
  const path = '/home/user/workdir/report.md'
  const first = Promise.withResolvers()
  const calls = []
  boundary.previewThreadArtifact = (threadId, filePath) => {
    calls.push([threadId, filePath])
    return threadId === 'first' ? first.promise : Promise.resolve('第二份报告')
  }
  boundary.normalizePreviewResponse = async (content, entry) => ({ ...entry, content })
  const earlier = state.loadArtifactPreview({ thread_id: 'first', path, name: 'report.md' })
  await state.loadArtifactPreview({ thread_id: 'second', path, name: 'report.md' })
  first.resolve('第一份报告')
  await earlier
  assert.deepEqual(calls, [['first', path], ['second', path]])
  assert.equal(state.previewEditable.value, false)
  assert.equal(state.previewFile.value.content, '第二份报告')
  assert.equal(state.previewFile.value.resourceBaseUrl, '/api/v1/agents/sessions/second/artifacts/home/user/workdir/report.md')
  await state.returnFromPreview()
  assert.equal(state.activeSourceKey.value, 'overview')
})
