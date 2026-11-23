import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, disposePinia } from 'pinia'
import { createRenderer, effectScope, h, nextTick, ref, ssrContextKey, toRaw } from 'vue'

import { useShareUserPicker } from '../../src/modules/agents/model/shareUserPicker.js'
import { createTestServer } from '../helpers/createTestServer.js'

const row = (uid, role = 'user', username = uid) => ({ uid, username, role })

const createPicker = (t, { ids = [], manage = false, admin = true } = {}) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const scope = effectScope()
  t.after(() => scope.stop())
  const selectedUids = ref(ids)
  const requests = []
  const picker = scope.run(() =>
    useShareUserPicker({
      canSearch: () => admin,
      manage,
      selectedUids: () => selectedUids.value,
      getOptions: (options) =>
        new Promise((resolve, reject) => requests.push({ options, resolve, reject }))
    })
  )
  const tickSearch = () => t.mock.timers.tick(300)
  const labelList = (options) => options.value.map(({ label }) => label)
  return { picker, selectedUids, requests, tickSearch, labelList }
}

test('输入变化立即作废旧结果；A→B→A 只接受最后一次 A', async (t) => {
  const { picker, requests, tickSearch, labelList } = createPicker(t)
  assert.equal(requests.length, 0)
  picker.setOpen(true)
  requests[0].resolve([row('default')])
  await Promise.resolve()
  assert.deepEqual(labelList(picker.candidates), ['default'])

  picker.query.value = 'A'
  tickSearch()
  picker.query.value = 'B'
  assert.deepEqual(labelList(picker.candidates), [])
  requests[1].resolve([row('old-A')])
  await Promise.resolve()
  assert.deepEqual(labelList(picker.candidates), [])

  tickSearch()
  picker.query.value = 'A'
  tickSearch()
  picker.query.value = 'B'
  tickSearch()
  picker.query.value = 'A'
  tickSearch()
  requests[5].resolve([row('new-A')])
  requests[3].resolve([row('older-A')])
  requests[4].resolve([row('older-B')])
  requests[2].reject(new Error('过期错误'))
  await Promise.resolve()
  assert.deepEqual(labelList(picker.candidates), ['new-A'])
  assert.equal(picker.status.value, 'ready')
})

test('清空立即加载首页；关闭取消定时器且晚到响应不能污染重开后的结果', async (t) => {
  const { picker, requests, tickSearch, labelList } = createPicker(t)
  picker.setOpen(true)
  picker.query.value = 'term'
  tickSearch()
  picker.query.value = ''
  assert.deepEqual(requests[2].options, { search: '', limit: 50 })
  requests[1].resolve([row('stale-keyword')])
  requests[0].resolve([row('stale-default')])
  requests[2].resolve([row('clear-default')])
  await Promise.resolve()
  assert.deepEqual(labelList(picker.candidates), ['clear-default'])

  picker.query.value = 'sent'
  tickSearch()
  picker.query.value = 'not-sent'
  picker.setOpen(false)
  requests[3].resolve([row('closed-stale')])
  tickSearch()
  await Promise.resolve()
  assert.equal(requests.length, 4)
  assert.deepEqual(labelList(picker.candidates), [])
  assert.equal(picker.query.value, '')
  picker.setOpen(true)
  assert.deepEqual(requests[4].options, { search: '', limit: 50 })
})

test('管理候选与已选标签分读；降级和丢失项可移除，点击候选不重复查标签', async (t) => {
  const { picker, selectedUids, requests, tickSearch, labelList } = createPicker(t, {
    ids: ['former', 'missing'],
    manage: true
  })
  picker.setOpen(true)
  assert.deepEqual(requests[0].options, { search: '', roles: ['admin', 'superadmin'], limit: 50 })
  assert.deepEqual(requests[1].options, { includeUids: ['former', 'missing'], limit: 0 })
  requests[0].resolve([row('candidate', 'admin'), row('former', 'admin'), row('missing', 'admin')])
  requests[1].resolve([row('former', 'user', 'Former Admin')])
  await Promise.resolve()
  assert.deepEqual(labelList(picker.selected), ['Former Admin（已非管理员）', 'missing'])
  assert.deepEqual(labelList(picker.candidates), ['candidate'])

  selectedUids.value = []
  assert.deepEqual(labelList(picker.selected), [])
  assert.deepEqual(labelList(picker.candidates), ['candidate'])
  selectedUids.value = ['candidate']
  selectedUids.value = ['candidate'] // 父层 v-model 回填相同 UID 不重置弹层或重查。
  assert.deepEqual(labelList(picker.selected), ['candidate'])
  assert.equal(picker.open.value, true)
  assert.equal(requests.length, 2)

  picker.query.value = 'phone-match'
  tickSearch()
  assert.deepEqual(requests[2].options, {
    search: 'phone-match', roles: ['admin', 'superadmin'], limit: 50
  })
  requests[2].resolve([row('no-name-match', 'superadmin')])
  await Promise.resolve()
  assert.deepEqual(labelList(picker.candidates), ['no-name-match'])
  assert.deepEqual(labelList(picker.selected), ['candidate'])

  selectedUids.value = ['candidate', 'external']
  assert.deepEqual(requests[3].options, { includeUids: ['external'], limit: 0 })
  selectedUids.value = ['candidate', 'changed']
  requests[3].resolve([row('external', 'user', 'Old selected response')])
  requests[4].resolve([row('changed', 'admin', 'New selected response')])
  await Promise.resolve()
  assert.deepEqual(labelList(picker.selected), ['candidate', 'New selected response'])
})

test('非管理员不请求目录，仍保留已选 UID', (t) => {
  const { picker, requests, labelList } = createPicker(t, { ids: ['saved'], admin: false })
  picker.setOpen(true)
  assert.equal(requests.length, 0)
  assert.deepEqual(labelList(picker.selected), ['saved'])
})

test('标签未返回时移除旧授权，不会从在途候选响应中重新出现', async (t) => {
  const { picker, selectedUids, requests, tickSearch, labelList } = createPicker(t, {
    ids: ['saved'],
    manage: true
  })
  picker.setOpen(true)
  selectedUids.value = []
  requests[0].resolve([row('saved', 'admin'), row('eligible', 'admin')])
  requests[1].resolve([row('saved', 'user')])
  await Promise.resolve()
  assert.deepEqual(labelList(picker.candidates), ['eligible'])

  picker.query.value = 'saved'
  tickSearch()
  requests[2].resolve([row('saved', 'admin')])
  await Promise.resolve()
  assert.deepEqual(labelList(picker.candidates), ['saved'])
})

test('共享表单自身 v-model 回传不关闭列表，替换外部配置会关闭并重置旧请求', async (t) => {
  const server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  const pinia = createPinia()
  const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: { getItem: () => null, setItem() {}, removeItem() {} }
  })
  let app
  t.after(async () => {
    app?.unmount()
    disposePinia(pinia)
    await server.close()
    if (storage) Object.defineProperty(globalThis, 'localStorage', storage)
    else delete globalThis.localStorage
  })
  const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/index.js')
  useUserStore(pinia).userRole = 'admin'
  const { authApi } = await server.ssrLoadModule('/src/apis/auth_api.js')
  t.mock.method(authApi, 'getUserAccessOptions', async () => [row('candidate', 'admin')])
  const { default: ShareConfigForm } = await server.ssrLoadModule(
    '/src/modules/agents/ui/ShareConfigForm.vue'
  )
  const renderedForm = { ...ShareConfigForm, render: () => h('div') }
  const config = (uid) => ({
    version: 2,
    read_scope: { access_level: 'global' },
    manage_scope: { access_level: 'user', user_uids: uid ? [uid] : [] }
  })
  const model = ref(config())
  const renderer = createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
    parentNode: () => null, nextSibling: () => null
  })
  app = renderer.createApp({
    setup: () => () => h(renderedForm, {
      modelValue: model.value,
      'onUpdate:modelValue': (value) => { model.value = value }
    })
  })
  app.provide(ssrContextKey, { modules: new Set() })
  app.use(pinia)
  app.mount({})
  const state = toRaw(app._instance.subTree.component.setupState)
  await nextTick()
  await state.handleSelectionOpen(true, 'manage_scope', 'user')
  state.toggleSelection('manage_scope', 'user', 'candidate')
  await nextTick()
  await nextTick()
  assert.deepEqual(model.value.manage_scope.user_uids, ['candidate'])
  const picker = state.userPickers.manage_scope
  assert.equal(picker.open.value, true)

  picker.query.value = 'previous model search'
  model.value = config('other')
  await nextTick()
  assert.equal(picker.open.value, false)
  assert.equal(picker.query.value, '')
  assert.deepEqual(picker.candidates.value, [])
  assert.deepEqual(picker.selected.value, [{ label: 'other', value: 'other' }])
})
