import assert from 'node:assert/strict'
import test from 'node:test'
import { message } from 'ant-design-vue'
import { createPinia, disposePinia, getActivePinia, setActivePinia } from 'pinia'
import { createSSRApp, h, reactive } from 'vue'
import { createMemoryHistory, createRouter, isNavigationFailure, NavigationFailureType, routerKey, routeLocationKey } from 'vue-router'
import { renderToString } from 'vue/server-renderer'
import { createTestServer } from '../helpers/createTestServer.js'

/** 加载真实导航组件，在 store、路由和请求边界控制竞态。 */
async function setup(t, router = { push() {}, replace() {} }) {
  const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  const previousPinia = getActivePinia()
  let server, pinia
  t.after(async () => {
    if (pinia) disposePinia(pinia)
    setActivePinia(previousPinia)
    await server?.close()
    if (previousStorage) Object.defineProperty(globalThis, 'localStorage', previousStorage)
    else delete globalThis.localStorage
  })
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  server = await createTestServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  pinia = createPinia()
  setActivePinia(pinia)
  const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
  const { useChatThreadsStore } = await server.ssrLoadModule('/src/modules/session/model/chatThreads.js')
  const { useChatUIStore } = await server.ssrLoadModule('/src/modules/session/model/chatUI.js')
  const { useProjectsStore } = await server.ssrLoadModule('/src/modules/projects/model/projects.js')
  const { projectApi } = await server.ssrLoadModule('/src/apis/project_api.js')
  const userStore = useUserStore(pinia), chatThreadsStore = useChatThreadsStore(pinia), projectsStore = useProjectsStore(pinia)
  const chatUIStore = useChatUIStore(pinia)
  userStore.userId = 'a'
  const route = reactive({ params: { thread_id: 'shared' } })
  const { default: component } = await server.ssrLoadModule('/src/modules/session/ui/SessionNavigation.vue')
  let state
  const app = createSSRApp({ setup() {
    state = component.setup({ panel: false, active: true, visible: true }, { expose() {}, emit() {} })
    return () => h('div')
  } })
  app.use(pinia)
  app.provide(routerKey, router)
  app.provide(routeLocationKey, route)
  await renderToString(app)
  return { state, userStore, chatThreadsStore, chatUIStore, projectsStore, projectApi, router }
}

test('旧账号归档响应不能将新账号的同名线程导航到空白页', async (t) => {
  let finish, navigations = 0
  const h = await setup(t)
  t.mock.method(h.chatThreadsStore, 'archiveThread', () => new Promise(resolve => { finish = resolve }))
  t.mock.method(h.router, 'replace', () => { navigations++ })
  const pending = h.state.handleArchiveChat('shared')
  h.userStore.userId = 'b'
  finish()
  await pending
  assert.equal(navigations, 0)
})

test('旧账号重命名响应不能改写新账号项目或解除其等待状态', async (t) => {
  let finish, replacements = 0
  const h = await setup(t)
  t.mock.method(message, 'success', () => assert.fail('过期动作不应通知新账号成功'))
  t.mock.method(h.projectApi, 'renameProject', () => new Promise(resolve => { finish = resolve }))
  t.mock.method(h.projectsStore, 'replaceProject', () => { replacements++ })
  const pending = h.state.handleRenameProject({ projectId: 'a-project', name: '新名称' })
  h.userStore.userId = 'b'
  h.state.projectPendingId.value = 'b-project'
  finish({ id: 'a-project' })
  await pending
  assert.equal(replacements, 0)
  assert.equal(h.state.projectPendingId.value, 'b-project')
})

test('删除项目排队期间切账号不能以新身份请求或解除新操作的等待状态', async (t) => {
  let queuedOperation, finishQueue, deletes = 0
  const h = await setup(t)
  t.mock.method(message, 'success', () => assert.fail('过期动作不应通知新账号成功'))
  t.mock.method(h.projectApi, 'deleteProject', async () => { deletes++ })
  t.mock.method(h.chatThreadsStore, 'withThreadListUpdate', operation => {
    queuedOperation = operation
    return new Promise(resolve => { finishQueue = resolve })
  })
  t.mock.method(h.chatThreadsStore, 'removeThreadsByProject', () => assert.fail('过期动作不能清理当前账号线程'))
  t.mock.method(h.projectsStore, 'removeProject', () => assert.fail('过期动作不能清理当前账号项目'))
  const pending = h.state.handleDeleteProject('a-project')
  h.userStore.userId = 'b'
  h.state.projectPendingId.value = 'b-project'
  finishQueue(await queuedOperation())
  await pending
  assert.equal(deletes, 0)
  assert.equal(h.state.projectPendingId.value, 'b-project')
})

for (const { entry, openDraft, query } of [
  { entry: '搜索', openDraft: (state) => state.handleCreateSessionFromSearch(), query: {} },
  { entry: '项目', openDraft: (state) => state.handleCreateProjectChat('a-project'), query: { project_id: 'a-project' } }
]) {
  test(`${entry}的新建对话导航取消后保留原返回目标`, async (t) => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { name: 'AgentComp', path: '/agent', component: {} },
        { name: 'AgentCompWithThreadId', path: '/agent/:thread_id', component: {} }
      ]
    })
    await router.push('/agent/shared')
    const { state, chatThreadsStore, chatUIStore } = await setup(t, router)
    chatThreadsStore.setCurrentThreadId('shared')
    router.beforeEach(() => false)
    const stopped = new Promise(resolve => router.afterEach((to, _from, failure) => resolve({ to, failure })))

    await openDraft(state)
    const { to, failure } = await stopped

    assert.ok(isNavigationFailure(failure, NavigationFailureType.aborted))
    assert.deepEqual(to.query, query)
    assert.equal(router.currentRoute.value.path, '/agent/shared')
    assert.equal(chatThreadsStore.currentThreadId, 'shared')
    assert.equal(chatUIStore.lastThreadId, 'shared')
  })
}

test('项目删除响应晚于账号切换时不能清除新账号线程或等待状态', async (t) => {
  let finish
  const h = await setup(t)
  t.mock.method(message, 'success', () => assert.fail('过期动作不应通知新账号成功'))
  t.mock.method(h.projectApi, 'deleteProject', () => new Promise(resolve => { finish = resolve }))
  t.mock.method(h.chatThreadsStore, 'withThreadListUpdate', operation => operation())
  t.mock.method(h.chatThreadsStore, 'removeThreadsByProject', () => assert.fail('旧响应不能清理新账号线程'))
  t.mock.method(h.projectsStore, 'removeProject', () => assert.fail('旧响应不能清理新账号项目'))
  const pending = h.state.handleDeleteProject('a-project')
  h.userStore.userId = 'b'
  h.state.projectPendingId.value = 'b-project'
  finish()
  await pending
  assert.equal(h.state.projectPendingId.value, 'b-project')
})
