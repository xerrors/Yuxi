import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import * as vue from 'vue'
import { compileScript, parse } from 'vue/compiler-sfc'
import { isEnterpriseDatabase, readWorkspaceDefault, saveWorkspaceDefault } from '../../src/utils/workspace_sources.js'

const stored = new Map()
globalThis.localStorage = {
  getItem: (key) => stored.get(key),
  setItem: (key, value) => stored.set(key, value)
}

const source = readFileSync(new URL('../../src/views/WorkspaceView.vue', import.meta.url), 'utf8')

function createWorkspace(response, query = {}, uid = 'owner', overrides = {}) {
  let mounted
  const paths = []
  const dependencies = {
    ...vue,
    onMounted: (callback) => { mounted = callback },
    onActivated: () => {},
    onUnmounted: () => {},
    watch: () => {},
    useRoute: () => ({ query }),
    useUserStore: () => ({ uid }),
    databaseApi: { getAccessibleDatabases: async () => response },
    getWorkspaceTree: async (path) => {
      paths.push(path)
      return { entries: [] }
    },
    message: { error: () => {} },
    isEnterpriseDatabase, readWorkspaceDefault, saveWorkspaceDefault,
    ...overrides
  }
  const compiled = compileScript(parse(source).descriptor, { id: 'enterprise-entry' }).content
    .replace(/import\s+\{([\s\S]*?)\}\s+from\s+['"][^'"]+['"]/g,
      (_, names) => `const {${names}} = dependencies`)
    .replace(/import\s+\w+\s+from\s+['"][^'"]+['"]/g, (statement) =>
      `const ${statement.split(/\s+/)[1]} = null`)
    .replace('export default', 'return')
  const component = new Function('dependencies', compiled)(dependencies)
  const state = component.setup({}, { expose: () => {} })
  return { state, paths, mount: () => mounted() }
}

test('企业分组只接受服务端true，创建人、私有、定向共享和缺失字段不推断', () => {
  assert.equal(isEnterpriseDatabase({ is_enterprise_shared: true, created_by: 'owner' }), true)
  for (const database of [null, {}, { created_by: 'other' },
    { is_enterprise_shared: false }, { is_enterprise_shared: 'true' },
    { share_config: { read_scope: { access_level: 'user' } } }]) {
    assert.equal(isEnterpriseDatabase(database), false)
  }
})

test('首次默认企业视图不读取个人树；个人选择后才读取，返回企业清理个人列表', async () => {
  saveWorkspaceDefault('owner', 'enterprise')
  const workspace = createWorkspace({ databases: [
    { kb_id: 'company', is_enterprise_shared: true },
    { kb_id: 'private', created_by: 'owner' }
  ] })
  await workspace.mount()
  assert.equal(workspace.state.activeSourceKey.value, 'enterprise')
  assert.deepEqual(workspace.paths, [])
  assert.deepEqual(workspace.state.enterpriseDatabases.value.map((db) => db.kb_id), ['company'])
  await workspace.state.selectPersonalWorkspace()
  assert.equal(workspace.state.activeSourceKey.value, 'personal')
  assert.deepEqual(workspace.paths, ['/'])
  workspace.state.entries.value = [{ path: '/personal.txt' }]
  await workspace.state.selectEnterpriseWorkspace()
  assert.equal(workspace.state.activeSourceKey.value, 'enterprise')
  assert.deepEqual(workspace.state.entries.value, [])
})

test('文件深链继续主动进入个人目录，空态与服务端失败分别呈现', async () => {
  const linked = createWorkspace({ databases: [] }, { open: '/saved_artifacts/report.txt' })
  await linked.mount()
  assert.equal(linked.state.activeSourceKey.value, 'personal')
  assert.deepEqual(linked.paths, ['/saved_artifacts'])
  const empty = createWorkspace({ databases: [] })
  await empty.mount()
  assert.equal(empty.state.databaseLoadError.value, '')
  const failed = createWorkspace({ databases: [], message: 'backend failed' })
  await failed.mount()
  assert.match(failed.state.databaseLoadError.value, /重试/)
  assert.match(source, /v-else-if="databaseLoadError"/)
  assert.match(source, /@click="loadDatabases">重试/)
})

test('首次个人默认、同浏览器不同账号偏好隔离、个人设置可恢复', async () => {
  const fresh = createWorkspace({ databases: [] }, {}, 'fresh')
  await fresh.mount()
  assert.equal(fresh.state.activeSourceKey.value, 'personal')
  assert.deepEqual(fresh.paths, ['/'])
  assert.equal(saveWorkspaceDefault('alice', 'enterprise'), true)
  const alice = createWorkspace({ databases: [] }, {}, 'alice')
  await alice.mount()
  assert.equal(alice.state.activeSourceKey.value, 'enterprise')
  assert.deepEqual(alice.paths, [])
  assert.equal(readWorkspaceDefault('bob'), 'personal')
  assert.equal(readWorkspaceDefault(''), 'personal')
  assert.equal(saveWorkspaceDefault('', 'enterprise'), false)
  assert.equal(saveWorkspaceDefault('alice', 'database:private'), false)
  assert.equal(saveWorkspaceDefault('alice', 'personal'), true)
  assert.equal(readWorkspaceDefault('alice'), 'personal')
})

test('禁用浏览器存储时回退个人入口且保存明确失败', () => {
  const original = globalThis.localStorage
  globalThis.localStorage = {
    getItem() { throw new Error('blocked') },
    setItem() { throw new Error('blocked') }
  }
  try {
    assert.equal(readWorkspaceDefault('owner'), 'personal')
    assert.equal(saveWorkspaceDefault('owner', 'enterprise'), false)
  } finally {
    globalThis.localStorage = original
  }
})

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

for (const fails of [false, true]) {
  test(`知识库→企业→个人：迟到${fails ? '失败' : '成功'}不污染当前个人列表或loading`, async () => {
    const old = deferred()
    const personal = deferred()
    const errors = []
    let personalCalls = 0
    const { state } = createWorkspace({ databases: [] }, {}, 'race', {
      getWorkspaceKnowledgeTree: () => old.promise,
      getWorkspaceTree: () => { personalCalls++; return personal.promise },
      message: { error: (value) => errors.push(value) }
    })
    const first = state.selectDatabase({ kb_id: 'old', name: 'old' })
    await state.selectEnterpriseWorkspace()
    assert.equal(state.loadingTree.value, false)
    assert.deepEqual(state.entries.value, [])
    const latest = state.selectPersonalWorkspace()
    assert.equal(personalCalls, 1)
    assert.equal(state.loadingTree.value, true)
    if (fails) old.reject(new Error('stale failure'))
    else old.resolve({ entries: [{ path: '/knowledge-old' }], total: 7 })
    await first
    assert.deepEqual(state.entries.value, [])
    assert.equal(state.loadingTree.value, true)
    assert.deepEqual(errors, [])
    personal.resolve({ entries: [{ path: '/personal-current' }] })
    await latest
    assert.deepEqual(state.entries.value, [{ path: '/personal-current' }])
    assert.equal(state.loadingTree.value, false)
  })

  test(`个人→企业：迟到${fails ? '失败' : '成功'}不恢复个人条目或错误`, async () => {
    const old = deferred()
    const errors = []
    const { state } = createWorkspace({ databases: [] }, {}, 'race', {
      getWorkspaceTree: () => old.promise,
      message: { error: (value) => errors.push(value) }
    })
    const pending = state.selectPersonalWorkspace()
    await state.selectEnterpriseWorkspace()
    if (fails) old.reject(new Error('stale failure'))
    else old.resolve({ entries: [{ path: '/private-old' }] })
    await pending
    assert.equal(state.activeSourceKey.value, 'enterprise')
    assert.deepEqual(state.entries.value, [])
    assert.equal(state.loadingTree.value, false)
    assert.deepEqual(errors, [])
  })

  test(`知识库A→B：迟到${fails ? '失败' : '成功'}不覆盖B目录分页或loading`, async () => {
    const old = deferred()
    const latest = deferred()
    const errors = []
    const { state } = createWorkspace({ databases: [] }, {}, 'race', {
      getWorkspaceKnowledgeTree: (kb) => kb === 'A' ? old.promise : latest.promise,
      message: { error: (value) => errors.push(value) }
    })
    const a = state.selectDatabase({ kb_id: 'A', name: 'A' })
    const b = state.selectDatabase({ kb_id: 'B', name: 'B' })
    if (fails) old.reject(new Error('stale failure'))
    else old.resolve({ entries: [{ path: '/old-A' }], total: 99 })
    await a
    assert.equal(state.loadingTree.value, true)
    assert.deepEqual(state.entries.value, [])
    latest.resolve({ entries: [{ path: '/current-B' }], total: 1 })
    await b
    assert.equal(state.loadingTree.value, false)
    assert.deepEqual(state.entries.value, [{ path: '/current-B' }])
    assert.equal(state.knowledgeFileBrowser.total, 1)
    assert.equal(state.knowledgeBreadcrumbItems.value[0].name, 'B')
    assert.deepEqual(errors, [])
  })
}

test('知识库B已完成后A迟到成功仍保留B条目和面包屑', async () => {
  const old = deferred()
  const { state } = createWorkspace({ databases: [] }, {}, 'race', {
    getWorkspaceKnowledgeTree: (kb) => kb === 'A' ? old.promise : Promise.resolve({ entries: [{ path: '/B' }], total: 1 })
  })
  const a = state.selectDatabase({ kb_id: 'A', name: 'A' })
  await state.selectDatabase({ kb_id: 'B', name: 'B' })
  old.resolve({ entries: [{ path: '/A' }], total: 99 })
  await a
  assert.deepEqual(state.entries.value, [{ path: '/B' }])
  assert.equal(state.knowledgeBreadcrumbItems.value[0].name, 'B')
  assert.equal(state.knowledgeFileBrowser.total, 1)
})
