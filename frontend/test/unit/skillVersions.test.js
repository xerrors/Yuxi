import assert from 'node:assert/strict'
import test from 'node:test'
import { createRenderer, createSSRApp, h, nextTick, reactive, ref, ssrContextKey } from 'vue'
import { matchedRouteKey, routeLocationKey, routerKey } from 'vue-router'
import { renderToString } from 'vue/server-renderer'
import { createPinia, disposePinia, getActivePinia, setActivePinia } from 'pinia'
import { message, Modal } from 'ant-design-vue'
import { createTestServer } from '../helpers/createTestServer.js'

/** 加载完整组件，在真实 Vue 上下文中运行，替身只控制 API 与导航。 */
async function loadComponent(t, path, deps) {
  globalThis.__skillVersionBoundary = deps
  for (const [key, value] of Object.entries(deps.message || {})) t.mock.method(message, key, value)
  if (deps.Modal?.confirm) t.mock.method(Modal, 'confirm', deps.Modal.confirm)
  const server = await createTestServer({
    server: { middlewareMode: true },
    appType: 'custom',
    plugins: [
      {
        name: 'skill-version-boundaries',
        enforce: 'pre',
        load(id) {
          if (id.endsWith('/src/shared/model/theme.js') && path.endsWith('AgentFilePreview.vue'))
            return 'export const useThemeStore = () => ({ isDark: false })'
          if (id.endsWith('/src/apis/skill_api.js'))
            return 'export const skillApi = globalThis.__skillVersionBoundary.skillApi'
        }
      }
    ]
  })
  t.after(async () => {
    await server.close()
    delete globalThis.__skillVersionBoundary
  })
  const { default: component } = await server.ssrLoadModule(path.replace('../../src/', '/src/'))
  return async (props, context) => {
    let state
    const root = {
      setup() {
        state = component.setup(props, context)
        return () => h('div')
      }
    }
    const live = path.endsWith('AgentFilePreview.vue')
    const renderer =
      live &&
      createRenderer({
        createElement: () => ({}),
        createText: () => ({}),
        createComment: () => ({}),
        insert() {},
        remove() {},
        setText() {},
        setElementText() {},
        patchProp() {},
        parentNode: () => null,
        nextSibling: () => null
      })
    const app = live ? renderer.createApp(root) : createSSRApp(root)
    const previousPinia = getActivePinia()
    const pinia = createPinia()
    app.use(pinia)
    t.after(() => {
      disposePinia(pinia)
      setActivePinia(previousPinia)
    })
    const record = { leaveGuards: new Set() }
    if (deps.useRoute) {
      app.provide(routeLocationKey, deps.useRoute())
      app.provide(routerKey, deps.useRouter())
      app.provide(matchedRouteKey, ref(record))
    }
    if (live) {
      const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
      const document = Object.getOwnPropertyDescriptor(globalThis, 'document')
      globalThis.document = { body: { style: {} } }
      globalThis.localStorage = { getItem: () => null }
      t.after(() => {
        app.unmount()
        if (storage) Object.defineProperty(globalThis, 'localStorage', storage)
        else delete globalThis.localStorage
        if (document) Object.defineProperty(globalThis, 'document', document)
        else delete globalThis.document
      })
      app.provide(ssrContextKey, { modules: new Set() })
      app.mount({})
    } else await renderToString(app)
    if (deps.onBeforeRouteLeave) deps.onBeforeRouteLeave([...record.leaveGuards][0])
    return state
  }
}

test('版本恢复与删除均需确认，恢复传整包修订，冲突保留列表并显示失败', async (t) => {
  let confirmation
  let restoreFails = true
  const errors = []
  const events = []
  const calls = []
  const deps = {
    message: { error: (value) => errors.push(value), success() {}, warning() {} },
    Modal: {
      confirm: (value) => {
        confirmation = value
      }
    },
    skillApi: {
      listSkillVersions: async () => ({
        data: { versions: [{ version: '20261004-12345678' }], revision: 'package-before' }
      }),
      restoreSkillVersion: async (...args) => {
        calls.push(args)
        if (restoreFails) throw new Error('409 stale')
        return { data: {} }
      },
      deleteSkillVersion: async (...args) => {
        calls.push(args)
      }
    }
  }
  const component = await loadComponent(
    t,
    '../../src/modules/extensions/ui/SkillVersionPanel.vue',
    deps
  )
  const panel = await component(
    { slug: 'latest', canManage: true },
    { expose() {}, emit: (...args) => events.push(args) }
  )
  await panel.load()
  panel.confirmRestore('20261004-12345678')
  assert.equal(calls.length, 0)
  assert.match(confirmation.content, /覆盖当前内容/)
  await assert.rejects(confirmation.onOk(), /409 stale/)
  assert.deepEqual(calls[0], ['latest', '20261004-12345678', 'package-before'])
  assert.equal(panel.versions.value.length, 1)
  assert.equal(panel.busy.value, false)
  assert.deepEqual(events.splice(0), [
    ['restoring', true],
    ['restoring', false]
  ])
  assert.equal(errors[0], '409 stale')
  restoreFails = false
  panel.confirmRestore('20261004-12345678')
  await confirmation.onOk()
  assert.deepEqual(events, [['restoring', true], ['restored'], ['restoring', false]])
  panel.confirmDelete('20261004-12345678')
  assert.equal(calls.length, 2)
  assert.match(confirmation.content, /当前内容保持不变/)
  await confirmation.onOk()
  assert.deepEqual(calls[2], ['latest', '20261004-12345678'])
})

test('完整保存保留跨文件草稿，409保留全部修改', async (t) => {
  const errors = []
  let payload
  let shouldFail = false
  let contentLoads = 0
  let pendingRefresh
  let resumeSave
  let pendingSave
  let leaveRoute
  const root = '---\nslug: latest\nname: latest\ndescription: guide\n---\nbody'
  let files = { 'SKILL.md': root, 'notes.md': 'old notes' }
  const skill = {
    slug: 'latest',
    bound_agent_id: 7,
    dir_path: 'packages/latest/content',
    source_type: 'upload',
    can_manage: true,
    tool_dependencies: [],
    mcp_dependencies: [],
    skill_dependencies: []
  }
  const deps = {
    onBeforeRouteLeave(callback) {
      leaveRoute = callback
    },
    useRoute: () => ({ params: { slug: 'latest' }, query: {} }),
    useRouter: () => ({ push() {} }),
    message: { error: (value) => errors.push(value), success() {}, warning() {} },
    Modal: { confirm() {} },
    cloneShareConfig: (value) => value || {},
    skillApi: {
      listSkills: async () => ({ data: [] }),
      getSkillDetail: async () => ({ data: skill }),
      getSkillDependencyOptions: async () => ({ data: {} }),
      getSkillContent: async () => {
        contentLoads += 1
        if (pendingRefresh) await pendingRefresh
        return { data: { files, tree: [], revision: 'after' } }
      },
      saveSkillContent: async (_slug, value) => {
        payload = value
        if (pendingSave) await pendingSave
        if (shouldFail) throw { response: { status: 409, data: { detail: '内容已修改' } } }
        files = Object.fromEntries(
          value.changes
            .filter((change) => change.action === 'write')
            .map((change) => [change.path, change.content])
        )
        return {
          data: {
            skill: { ...skill },
            revision: 'after',
            published_version: { version: '20261005-12345678' }
          }
        }
      }
    }
  }
  const component = await loadComponent(
    t,
    '../../src/modules/extensions/ui/SkillDetailView.vue',
    deps
  )
  const page = await component({}, { expose() {} })
  page.currentSkill.value = skill
  page.syncShareConfigFromSkill(skill)
  page.savedFiles.value = { 'SKILL.md': root, 'notes.md': 'old notes' }
  page.draftFiles.value = { 'SKILL.md': root, 'notes.md': 'edited notes' }
  page.contentRevision.value = 'before'
  page.treeData.value = [
    { key: 'SKILL.md', is_dir: false },
    { key: 'references', is_dir: true, children: [] }
  ]
  for (const path of ['references', 'SKILL.md/note.md']) {
    page.createForm.path = path
    page.createForm.isDir = true
    await page.handleCreateNode()
    assert.equal(page.nodeChanges.value.length, 0)
    assert.deepEqual(Object.keys(page.draftFiles.value), ['SKILL.md', 'notes.md'])
  }
  assert.deepEqual(errors.splice(0), ['路径已存在', '父路径是文件，不能新建子节点'])
  page.selectedPath.value = 'SKILL.md'
  page.updateDraftFile(root + ' edited')
  await page.loadSkillFile('latest', 'notes.md')
  assert.equal(page.draftFiles.value['notes.md'], 'edited notes')
  assert.equal(page.draftFiles.value['SKILL.md'], root + ' edited')
  assert.equal(page.toggleDependency, undefined)
  pendingSave = new Promise((resolve) => {
    resumeSave = resolve
  })
  const saving = page.saveContent()
  await page.reloadTree()
  assert.equal(contentLoads, 0)
  page.openCreateModal(false)
  assert.equal(page.createModalVisible.value, false)
  page.createForm.path = 'during-save.md'
  page.createForm.isDir = false
  await page.handleCreateNode()
  assert.equal(page.nodeChanges.value.length, 0)
  assert.equal('during-save.md' in page.draftFiles.value, false)
  resumeSave()
  await saving
  pendingSave = null
  assert.equal(payload.expected_revision, 'before')
  assert.equal(payload.release, false)
  assert.equal(payload.changes.length, 2)
  assert.equal(payload.changes.find((change) => change.path === 'notes.md').content, 'edited notes')
  assert.equal(page.hasContentChanges.value, false)
  let resumeRefresh
  pendingRefresh = new Promise((resolve) => {
    resumeRefresh = resolve
  })
  const refreshing = page.reloadTree()
  await nextTick()
  assert.equal(page.pendingFileLoad.value, true)
  const draftBeforeRefresh = { ...page.draftFiles.value }
  page.openCreateModal(false)
  page.createForm.path = 'during-refresh.md'
  await page.handleCreateNode()
  page.updateDraftFile('during refresh')
  assert.deepEqual(page.draftFiles.value, draftBeforeRefresh)
  assert.equal(page.nodeChanges.value.length, 0)
  resumeRefresh()
  await refreshing
  pendingRefresh = null
  assert.equal(page.pendingFileLoad.value, false)
  await page.loadSkillFile('latest', 'notes.md')
  shouldFail = true
  page.updateDraftFile('conflicting notes')
  await page.saveContent()
  assert.equal(page.draftFiles.value['notes.md'], 'conflicting notes')
  assert.equal(page.savedFiles.value['notes.md'], 'edited notes')
  assert.equal(page.contentRevision.value, 'after')
  assert.equal(page.hasContentChanges.value, true)
  assert.equal(errors[0], '内容已修改')
  assert.equal(page.skillDetailTabs.value.at(-1).key, 'versions')
  page.draftFiles.value = { ...page.savedFiles.value }
  page.activeTab.value = 'versions'
  page.restoringVersion.value = true
  assert.equal(page.savingAny.value, true)
  await page.changeTab('editor')
  assert.equal(page.activeTab.value, 'versions', '恢复请求期间不能离开历史页')
  page.restoringVersion.value = false
  let resumeRestoreLoad
  pendingRefresh = new Promise((resolve) => {
    resumeRestoreLoad = resolve
  })
  const reloading = page.fetchSkillDetail()
  await nextTick()
  assert.equal(page.loading.value, true)
  assert.equal(page.savingAny.value, true, '恢复后的重载也属于同一编辑阻塞范围')
  assert.equal(await leaveRoute(), false, '重载完成前不能离开页面')
  const beforeRestoreLoad = { ...page.draftFiles.value }
  page.updateDraftFile('would be silently lost')
  page.openCreateModal(false)
  await page.changeTab('editor')
  assert.deepEqual(page.draftFiles.value, beforeRestoreLoad)
  assert.equal(page.createModalVisible.value, false)
  assert.equal(page.activeTab.value, 'versions')
  resumeRestoreLoad()
  await reloading
  pendingRefresh = null
  assert.equal(page.savingAny.value, false)
  page.currentSkill.value = { ...skill, bound_agent_id: null }
  assert.equal(
    page.skillDetailTabs.value.some((tab) => tab.key === 'versions'),
    false
  )
})

test('父级接收逐字草稿后，文件编辑器保持编辑态；切换文件才重置预览', async (t) => {
  const props = reactive({
    filePath: 'SKILL.md',
    file: { content: 'baseline', previewType: 'markdown' },
    draft: 'baseline',
    editable: true,
    saving: false
  })
  const component = await loadComponent(
    t,
    '../../src/modules/session/ui/workspace/AgentFilePreview.vue',
    {}
  )
  const editor = await component(props, { expose() {}, emit() {} })
  editor.startEditing()
  assert.equal(editor.editMode.value, 'edit')
  props.draft = 'first keystroke'
  await nextTick()
  assert.equal(editor.editMode.value, 'edit')
  assert.equal(editor.draftContent.value, 'first keystroke')
  props.filePath = 'notes.md'
  props.file = { content: 'other file', previewType: 'markdown' }
  props.draft = 'other draft'
  await nextTick()
  assert.equal(editor.editMode.value, 'preview')
  assert.equal(editor.draftContent.value, 'other draft')
})

test('历史页只发布已保存修订，脏草稿或无管理权限不能发版，冲突保留历史', async (t) => {
  const props = reactive({ slug: 'latest', canManage: true, canRelease: false })
  const calls = [],
    errors = []
  let fails = true
  const component = await loadComponent(
    t,
    '../../src/modules/extensions/ui/SkillVersionPanel.vue',
    {
      Modal: {},
      message: { success() {}, error: (value) => errors.push(value) },
      skillApi: {
        listSkillVersions: async () => ({
          data: { versions: [{ version: 'prior' }], revision: 'saved' }
        }),
        releaseSkillVersion: async (...args) => {
          calls.push(args)
          if (fails) throw new Error('409 stale')
        }
      }
    }
  )
  const panel = await component(props, { expose() {}, emit() {} })
  await panel.load()
  await panel.release()
  assert.deepEqual(calls, [])
  props.canRelease = true
  props.canManage = false
  await panel.release()
  assert.deepEqual(calls, [])
  props.canManage = true
  await panel.release()
  assert.deepEqual(calls, [['latest', 'saved']])
  assert.deepEqual(errors, ['409 stale'])
  assert.equal(panel.versions.value[0].version, 'prior')
  assert.equal(panel.busy.value, false)
  fails = false
  await panel.release()
  assert.equal(calls.length, 2)
})
