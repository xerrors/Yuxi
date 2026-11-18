import assert from 'node:assert/strict'
import test, { before, after } from 'node:test'
import { createRenderer, effectScope, getCurrentScope, h, nextTick, onScopeDispose, reactive, ssrContextKey } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'
import { isPasswordLongEnough, MIN_PASSWORD_LENGTH } from '../../src/modules/identity/model/passwordValidation.js'

let server
const originalDocument = globalThis.document
const components = {}
before(async () => {
  globalThis.document = { activeElement: null, querySelector: () => null }
  globalThis.__guiFeedback = { isPasswordLongEnough, MIN_PASSWORD_LENGTH }
  server = await createTestServer({ server: { middlewareMode: true, hmr: false }, ssr: { noExternal: ['ant-design-vue'] }, plugins: [{
    name: 'gui-feedback-boundaries', enforce: 'pre',
    resolveId(id) { if (id === 'ant-design-vue') return '\0gui-ant' },
    load(id) {
      if (id === '\0gui-ant') return 'export const message = { error: (...args) => globalThis.__guiFeedback.message.error(...args), success: (...args) => globalThis.__guiFeedback.message.success(...args) }; export const Modal = {}'
      if (id.endsWith('/src/apis/index.js')) return 'export const threadApi = { searchThreads: (...args) => globalThis.__guiFeedback.search(...args) }; export const authApi = { getUsersPage: async () => ({ total: 0, items: [] }) }; export const departmentApi = { getDepartments: async () => [] }'
      if (id.endsWith('/src/modules/identity/index.js')) return 'export const useUserStore = () => globalThis.__guiFeedback.user; export const isPasswordLongEnough = globalThis.__guiFeedback.isPasswordLongEnough; export const MIN_PASSWORD_LENGTH = globalThis.__guiFeedback.MIN_PASSWORD_LENGTH'
    }
  }] })
  for (const path of ['/src/modules/session/ui/GlobalSearchModal.vue', '/src/modules/settings/ui/UserManagementComponent.vue', '/src/modules/session/ui/ContextUsagePanel.vue']) {
    components[path] = (await server.ssrLoadModule(path)).default
  }
})
after(async () => {
  await server.close()
  delete globalThis.__guiFeedback
  if (originalDocument === undefined) delete globalThis.document
  else globalThis.document = originalDocument
})

const node = () => ({ children: [], parent: null })
const renderer = createRenderer({
  createElement: node, createText: node, createComment: node,
  insert(child, parent) { child.parent = parent; parent.children.push(child) },
  remove(child) { const i = child.parent?.children.indexOf(child) ?? -1; if (i >= 0) child.parent.children.splice(i, 1) },
  setText() {}, setElementText() {}, parentNode: (n) => n.parent, nextSibling: () => null, patchProp() {}
})

/** 执行完整组件模块，隔离网络边界。 */
function setupComponent(path, props = {}) {
  let form
  const app = renderer.createApp({ setup() {
    form = components[path].setup(props, { expose() {}, emit() {} })
    return () => h('div')
  } })
  app.provide(ssrContextKey, { modules: new Set() })
  app.mount(node())
  if (getCurrentScope()) onScopeDispose(() => app.unmount())
  return form
}

/** 构造真实搜索组件的独立查询边界。 */
function setupSearch(sessionSearch, fileSearch, fileScopeHint = '') {
  globalThis.__guiFeedback.search = sessionSearch
  return setupComponent('/src/modules/session/ui/GlobalSearchModal.vue',
    { open: true, modes: ['session', 'file'], defaultMode: 'session', recentThreads: [], fileSearch, fileScopeHint })
}

test('搜索切换保留关键词，往返切换重新查询正确范围', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const scope = effectScope()
  const queries = []
  const form = scope.run(() => setupSearch(
    async (query) => { queries.push(['session', query]); return { items: [] } },
    async (query) => { queries.push(['file', query]); return { entries: [] } }
  ))
  try {
    form.searchText.value = '  工程报告  '
    await nextTick()
    form.switchMode('file')
    await nextTick()
    t.mock.timers.tick(250)
    await nextTick()
    assert.equal(form.searchText.value, '  工程报告  ')
    assert.deepEqual(queries, [['file', '工程报告']])
    form.switchMode('session')
    await nextTick()
    t.mock.timers.tick(250)
    await nextTick()
    assert.deepEqual(queries, [['file', '工程报告'], ['session', '工程报告']])
    assert.equal(form.isSearching.value, false)
  } finally {
    scope.stop()
  }
})

test('切换搜索模式后迟到的旧响应不显示，也不提前结束新查询的加载态', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const scope = effectScope()
  let resolveSession
  let resolveFile
  const form = scope.run(() => setupSearch(
    () => new Promise((resolve) => { resolveSession = resolve }),
    () => new Promise((resolve) => { resolveFile = resolve })
  ))
  try {
    form.searchText.value = '报告'
    await nextTick()
    t.mock.timers.tick(250)
    form.switchMode('file')
    await nextTick()
    t.mock.timers.tick(250)
    resolveSession({ items: [{ session: { id: 'stale' } }] })
    await nextTick()
    assert.deepEqual(form.sessionResults.value, [])
    assert.equal(form.isSearching.value, true)
    resolveFile({ entries: [{ path: '/report.txt', name: 'report.txt' }] })
    await nextTick()
    assert.equal(form.fileResults.value[0].path, '/report.txt')
    assert.equal(form.isSearching.value, false)
  } finally {
    scope.stop()
  }
})

test('文件空态说明遵循实际调用方的个人空间或对话目录范围', () => {
  for (const [hint, expected] of [
    ['仅搜索个人空间，不包含知识库文件。', '未找到相关文件。仅搜索个人空间，不包含知识库文件。'],
    ['仅搜索当前对话或关联项目的工作目录。', '未找到相关文件。仅搜索当前对话或关联项目的工作目录。']
  ]) {
    const scope = effectScope()
    try {
      const form = scope.run(() => setupSearch(async () => ({ items: [] }), async () => ({ entries: [] }), hint))
      assert.equal(form.fileEmptyMessage.value, `输入关键词搜索文件。${hint}`)
      form.searchText.value = '报告'
      assert.equal(form.fileEmptyMessage.value, expected)
    } finally {
      scope.stop()
    }
  }
})

/** 保留用户表单的实际提交逻辑，观察越过校验后的写操作。 */
function setupUserForm(validate = async () => ({ uid: 'fixture_uid' })) {
  const effects = []
  globalThis.__guiFeedback.user = {
    isSuperAdmin: true,
    validateUsernameAndGenerateUid: async (name) => { effects.push(['uid', name]); return validate(name) },
    createUser: async (data) => effects.push(['create', data]),
    updateUser: async (id, data) => effects.push(['update', id, data]),
    getUsers: async () => []
  }
  globalThis.__guiFeedback.message = { error: (text) => effects.push(['error', text]), success() {} }
  const scope = effectScope()
  const form = scope.run(() => setupComponent('/src/modules/settings/ui/UserManagementComponent.vue'))
  return { form, effects, scope }
}

test('非法用户名在 UID 预览和直接提交处均给出字符规则，且不发起写入', async () => {
  const { form, effects, scope } = setupUserForm()
  try {
    form.userManagement.form.username = '临时-用户'
    await form.validateAndGenerateUid()
    assert.equal(form.userManagement.form.usernameError, '用户名只能包含中文、英文、数字和下划线')
    assert.deepEqual(effects, [])
    await form.handleUserFormSubmit()
    assert.deepEqual(effects, [['error', '用户名只能包含中文、英文、数字和下划线']])
    form.userManagement.editMode = true
    await form.handleUserFormSubmit()
    assert.equal(effects.length, 2)
    assert.ok(effects.every(([kind]) => kind === 'error'))
  } finally {
    scope.stop()
  }
})

test('有效中文用户名继续生成 UID，编辑用户名保留已有登录 ID', async () => {
  const { form, effects, scope } = setupUserForm()
  try {
    form.userManagement.form.username = '临时_用户2'
    await form.validateAndGenerateUid()
    assert.deepEqual(effects, [['uid', '临时_用户2']])
    assert.equal(form.userManagement.form.generatedUid, 'fixture_uid')
    form.userManagement.editMode = true
    form.userManagement.form.generatedUid = 'original_uid'
    form.userManagement.form.username = '修改用户名'
    await form.validateAndGenerateUid()
    assert.equal(form.userManagement.form.generatedUid, 'original_uid')
    assert.equal(effects.length, 1)
  } finally {
    scope.stop()
  }
})

test('旧用户名的 UID 预览迟到时，不覆盖当前非法用户名的提示', async () => {
  for (const failed of [false, true]) {
    let finish
    const pending = new Promise((resolve, reject) => { finish = failed ? reject : resolve })
    const { form, scope } = setupUserForm(() => pending)
    try {
      form.userManagement.form.username = '有效用户名'
      const oldValidation = form.validateAndGenerateUid()
      form.userManagement.form.username = '非法-用户名'
      await form.validateAndGenerateUid()
      finish(failed ? new Error('旧请求失败') : { uid: 'stale_uid' })
      await oldValidation
      assert.equal(form.userManagement.form.generatedUid, '')
      assert.equal(form.userManagement.form.usernameError, '用户名只能包含中文、英文、数字和下划线')
    } finally {
      scope.stop()
    }
  }
})

test('缺失或零累计用量不显示有效累计指标，正数继续按 Token 单位展示', () => {
  const props = reactive({ usage: null, compressing: false, compressionDisabled: false })
  const form = setupComponent('/src/modules/session/ui/ContextUsagePanel.vue', props)
  assert.equal(form.tokenUsageThreadTotalLabel.value, null)
  props.usage = { thread: { total: { total_tokens: 0 } } }
  assert.equal(form.tokenUsageThreadTotalLabel.value, null)
  assert.equal(form.hasTokenUsageMetrics.value, false)
  props.usage.thread.total.total_tokens = 2048
  assert.equal(form.tokenUsageThreadTotalLabel.value, '2K')
  assert.equal(form.hasTokenUsageMetrics.value, true)
})
