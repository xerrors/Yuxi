import assert from 'node:assert/strict'
import test from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createTestServer } from '../helpers/createTestServer.js'
import { mountWorkspaceView } from '../helpers/workspaceView.js'

const owned = { kb_id: 'owned', created_by: 'reader', name: '项目文档' }
const shared = { kb_id: 'shared', created_by: 'colleague', name: '共享手册' }

test('资源、自有和共享知识库并列常显，快捷入口使用各自的图标', async (t) => {
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  t.after(() => server.close())
  const { default: Sidebar } = await server.ssrLoadModule('/src/modules/workspace/ui/WorkspaceSidebar.vue')
  const app = createSSRApp(Sidebar, {
    currentUid: 'reader', currentPath: '/', activeKey: 'knowledge-base:shared', knowledgeBases: [owned, shared]
  })
  app.config.warnHandler = () => {}
  const html = await renderToString(app)
  assert.match(html, /<strong\b[^>]*>个人空间<\/strong>/)
  assert.doesNotMatch(html, /aria-expanded=/)
  const [, nav] = html.match(/<nav\b[^>]*>([\s\S]*?)<\/nav>/)
  assert.doesNotMatch(nav, /<(?:details|summary)\b/)
  const groups = [...nav.matchAll(/<section\b[^>]*><h2\b[^>]*>([^<]+)<\/h2>([\s\S]*?)<\/section>/g)]
  assert.deepEqual(groups.map(([, title]) => title), ['资源', '我的知识库', '共享知识库'])
  const [resources, mine, others] = groups.map(([, , contents]) => contents)
  const buttons = [...resources.matchAll(/<button\b[^>]*>([\s\S]*?)<\/button>/g)]
  assert.equal(buttons.length, 2)
  assert.match(buttons[0][1], /\blucide-layers-plus\b[\s\S]*智能体文件/)
  assert.match(buttons[1][1], /\blucide-bookmark\b[\s\S]*保存的产物/)
  assert.match(mine, /\blucide-folder-user\b[\s\S]*>项目文档<\/span>/)
  assert.doesNotMatch(mine, /aria-current=/)
  assert.match(others, /<button\b[^>]*aria-current="page"[^>]*>[\s\S]*\blucide-folder-users\b[\s\S]*>共享手册<\/span>/)
})

test('重新进入缓存的个人空间刷新可读知识库；失败后可重试，模块缺席与空列表各自呈现', async (t) => {
  const { state, instance, boundary } = await mountWorkspaceView(t)
  t.mock.method(console, 'warn', () => {})
  await setImmediate()
  const activate = async () => {
    for (const hook of instance.a) await hook()
    await setImmediate()
  }
  const failure = (status) => Promise.reject(Object.assign(new Error('HTTP ' + status), { status }))
  boundary.getAccessibleKnowledgeBases = async () => ({ knowledge_bases: [owned, shared, { ...owned, kb_id: 'graph', supports_documents: false }] })
  await activate()
  assert.deepEqual(state.knowledgeBases.value, [owned, shared])
  boundary.getAccessibleKnowledgeBases = () => failure(503)
  await state.loadKnowledgeBases()
  assert.equal(state.knowledgeListStatus.value, 'error')
  boundary.getAccessibleKnowledgeBases = async () => ({ knowledge_bases: [] })
  await state.loadKnowledgeBases()
  assert.equal(state.knowledgeListStatus.value, 'ready')
  assert.deepEqual(state.knowledgeBases.value, [])
  boundary.getAccessibleKnowledgeBases = () => failure(404)
  await state.loadKnowledgeBases()
  assert.equal(state.knowledgeListStatus.value, 'unavailable')
})

test('刷新授权后退出已移除的知识库，早先发出的请求不能把它重新放回侧栏', async (t) => {
  const { state, boundary } = await mountWorkspaceView(t)
  await setImmediate()
  const pending = Promise.withResolvers()
  boundary.getAccessibleKnowledgeBases = () => pending.promise
  state.selectedKnowledgeBase.value = shared
  state.activeSourceKey.value = 'knowledge-base:shared'
  const earlier = state.loadKnowledgeBases()
  boundary.getAccessibleKnowledgeBases = async () => ({ knowledge_bases: [owned] })
  await state.loadKnowledgeBases()
  assert.equal(state.activeSourceKey.value, 'overview')
  assert.equal(state.selectedKnowledgeBase.value, null)
  pending.resolve({ knowledge_bases: [owned, shared] })
  await earlier
  assert.deepEqual(state.knowledgeBases.value, [owned])
})
