import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import {
  createPersonalShareConfig,
  buildDatabaseRequest
} from '../../src/utils/databaseCreateForm.js'
import { isPersonalKnowledgeConfig } from '../../src/utils/shareConfig.js'

const source = (path) => readFileSync(new URL('../../src/' + path, import.meta.url), 'utf8')

test('个人知识库请求固定为所有者私有，旧共享库保持可识别', () => {
  const request = buildDatabaseRequest(
    { name: '个人资料', kb_type: 'milvus', additional_params: {} },
    {},
    createPersonalShareConfig()
  )
  assert.deepEqual(request.share_config, { version: 2, read_scope: null, manage_scope: null })
  assert.equal(isPersonalKnowledgeConfig(request.share_config), true)
  assert.equal(
    isPersonalKnowledgeConfig({
      version: 2,
      read_scope: { access_level: 'global' },
      manage_scope: null
    }),
    false
  )
  assert.equal(isPersonalKnowledgeConfig(undefined), false)
})

test('登录、刷新与退出同步业务角色，显式空角色不能继承上一账号能力', async () => {
  const script = source('stores/user.js')
    .replace(/^import .*$/gm, '')
    .replaceAll('export ', '')
  let profile = { id: 1, role: 'user', business_roles: ['counselor'] }
  const user = runInNewContext(script + '\nuseUserStore()', {
    defineStore: (_, setup) => setup,
    ref: (value) => ({ value }),
    computed: (getter) => ({
      get value() {
        return getter()
      }
    }),
    localStorage: { getItem: () => '', setItem() {}, removeItem() {} },
    authApi: {
      login: async () => ({ ...profile, access_token: 'test', user_id: profile.id }),
      getCurrentUser: async () => profile
    },
    useAgentStore: () => ({ reset() {} }),
    console
  })
  await user.login({})
  assert.equal(user.canManagePersonalKnowledge.value, true)
  profile = { id: 2, role: 'user', business_roles: [] }
  await user.getCurrentUser()
  assert.equal(user.canManagePersonalKnowledge.value, false)
  profile = { id: 3, role: 'user', business_roles: ['counselor'] }
  await user.getCurrentUser()
  user.logout()
  assert.equal(user.canManagePersonalKnowledge.value, false)
})

test('知识库与文件请求不要求平台管理员，下载保留blob响应，评估仍受限', async () => {
  const calls = []
  const request = (...args) => {
    calls.push(args)
    return args
  }
  const script = source('apis/knowledge_api.js')
    .replace(/^import [\s\S]*?from '\.\/base'\s*/, '')
    .replaceAll('export ', '')
  const apis = runInNewContext(
    script + '\n;({ databaseApi, documentApi, fileApi, typeApi, evaluationApi })',
    {
      apiGet: request,
      apiPost: request,
      apiPut: request,
      apiDelete: request,
      apiRequest: request,
      apiAdminGet: () => {
        throw new Error('需要管理员权限')
      },
      apiAdminPost: () => {
        throw new Error('需要管理员权限')
      },
      apiAdminDelete: () => {
        throw new Error('需要管理员权限')
      },
      buildQuery: () => '',
      FormData
    }
  )
  await apis.databaseApi.getDatabases()
  await apis.databaseApi.createDatabase({ share_config: createPersonalShareConfig() })
  await apis.databaseApi.updateDatabase('own', { name: '更新' })
  await apis.documentApi.listDocuments('own')
  await apis.documentApi.downloadDocument('own', 'file')
  assert.deepEqual(Array.from(calls.at(-1)), [
    '/api/knowledge/databases/own/documents/file/download',
    calls.at(-1)[1],
    true,
    'blob'
  ])
  await apis.fileApi.getSupportedFileTypes()
  await apis.typeApi.getKnowledgeBaseTypes()
  await assert.rejects(apis.evaluationApi.listDatasets('own'), /管理员/)
  await assert.rejects(apis.typeApi.getStatistics(), /管理员/)
})

test('辅导入口不开放工具和评估，个人库隐藏共享编辑并强制私有提交', () => {
  assert.match(source('views/ExtensionsView.vue'), /userStore\.canManagePersonalKnowledge/)
  assert.match(source('views/ExtensionsView.vue'), /userStore\.isAdmin && activeTab === 'tools'/)
  const routes = source('router/index.js')
  assert.match(
    routes,
    /name: 'ExtensionKnowledgeBaseDetail'[\s\S]*?requiresKnowledgeManagement: true/
  )
  assert.match(routes, /name: 'ExtensionEvaluationBenchmarkDetail'[\s\S]*?requiresAdmin: true/)
  const create = source('components/knowledge/DatabaseCreateFlowModal.vue')
  assert.match(create, /const isPersonal = computed\(\(\) => props\.defaultPersonal\)/)
  assert.match(create, /isPersonal\.value \? createPersonalShareConfig\(\) : shareConfig\.value/)
  assert.match(create, /<ShareConfigForm\s+v-else-if="userStore\.isAdmin"/)
  assert.match(
    source('views/DataBaseInfoView.vue'),
    /canManageDatabase\.value && !isPersonal\.value && userStore\.isAdmin/
  )
})
const uploadScript = source('components/FileUploadModal.vue')
test('辅导人员上传选项保留文件与个人空间，隐藏管理员URL抓取', () => {
  const start = uploadScript.indexOf('const uploadModeOptions')
  const end = uploadScript.indexOf('watch(uploadMode', start)
  const setup = uploadScript.slice(start, end)
  for (const admin of [false, true]) {
    const options = runInNewContext(setup + '\n;uploadModeOptions.value', {
      computed: (getter) => ({ value: getter() }),
      uploadUserStore: { isAdmin: admin },
      h: () => ({}),
      FileUp: {},
      FolderUp: {},
      Link: {},
      FolderOpen: {}
    })
    const values = Array.from(options, (option) => option.value)
    assert.equal(values.includes('url'), admin)
    assert.equal(values.includes('file'), true)
    assert.equal(values.includes('workspace'), true)
  }
})

test('账号切换清除个人库缓存，前一账号迟到列表和详情不会重新显示', async () => {
  const script = source('stores/database.js')
    .replace(/^import .*$/gm, '')
    .replaceAll('export ', '')
  const user = { userId: 1, isAdmin: false, canManagePersonalKnowledge: true }
  let clearSession
  let resolveList
  let resolveDetail
  const store = runInNewContext(script + '\n;useDatabaseStore()', {
    defineStore: (_, setup) => setup,
    ref: (value) => ({ value }),
    reactive: (value) => value,
    watch: (_, callback) => {
      clearSession = callback
    },
    useRouter: () => ({}),
    useTaskerStore: () => ({}),
    useUserStore: () => user,
    databaseApi: {
      getDatabases: () =>
        new Promise((resolve) => {
          resolveList = resolve
        }),
      getDatabaseInfo: () =>
        new Promise((resolve) => {
          resolveDetail = resolve
        })
    },
    documentApi: {},
    queryApi: {},
    message: { error() {} },
    Modal: {},
    console,
    clearTimeout,
    setTimeout
  })
  store.databases.value = [{ name: '旧账号资料' }]
  store.database.value = { kb_id: 'old', name: '旧账号资料' }
  store.kbId.value = 'old'
  const list = store.loadDatabases()
  const detail = store.getDatabaseInfo('old', true)
  user.userId = 2
  clearSession()
  assert.equal(store.databases.value.length, 0)
  assert.equal(store.database.value.name, undefined)
  assert.equal(store.kbId.value, null)
  resolveList({ databases: [{ name: '不应重新显示' }] })
  resolveDetail({ kb_id: 'old', name: '不应重新显示' })
  await Promise.all([list, detail])
  assert.equal(store.databases.value.length, 0)
  assert.equal(store.database.value.name, undefined)
})

test('统计修复只渲染给管理员，辅导员不看到无动作按钮', () => {
  const detail = source('views/DataBaseInfoView.vue')
  const repairButtons = detail.match(/<button\s+[^>]*file-stat-repair[^>]*>/g) || []
  assert.equal(repairButtons.length, 2)
  for (const button of repairButtons) {
    assert.match(button, /v-if="canManageDatabase && userStore.isAdmin"/)
  }
})
