import assert from 'node:assert/strict'
import test from 'node:test'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createSessionLinkNavigator, sessionLinkLocation } from '../../src/modules/session/model/sessionLinks.js'

test('根来源打开主页面，二级和三级来源都在所属根页面侧栏打开', () => {
  assert.deepEqual(sessionLinkLocation({ session_id: 'root', tree_root_session_id: 'root', parent_session_id: null }), {
    name: 'AgentCompWithThreadId', params: { thread_id: 'root' }, query: {}
  })
  for (const [session, parent] of [['child', 'root'], ['grandchild', 'child']]) {
    assert.deepEqual(sessionLinkLocation({ session_id: session, tree_root_session_id: 'root', parent_session_id: parent }), {
      name: 'AgentCompWithThreadId', params: { thread_id: 'root' }, query: { member_session_id: session }
    })
  }
})

/** 用真实路由观察导航结果，仅延迟 HTTP 边界。 */
async function navigationFixture() {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/agent/:thread_id', name: 'AgentCompWithThreadId', component: { render: () => null } },
    { path: '/other', component: { render: () => null } }
  ] })
  await router.push('/agent/current')
  const pending = new Map(), opened = [], errors = []
  let active = true
  const navigator = createSessionLinkNavigator({ router,
    resolveTarget: id => new Promise((resolve, reject) => pending.set(id, { resolve, reject })),
    isActive: () => active,
    openTarget: target => opened.push(target.session_id),
    onError: error => errors.push(error)
  })
  return { router, navigator, pending, opened, errors, deactivate() { active = false; navigator.invalidate() } }
}

const rootTarget = id => ({ session_id: id, tree_root_session_id: id, parent_session_id: null })

test('来源导航只完成最新点击，旧响应不覆盖目标页面和侧栏', async () => {
  const h = await navigationFixture()
  const first = h.navigator.navigate('first')
  const latest = h.navigator.navigate('latest')
  h.pending.get('latest').resolve(rootTarget('latest'))
  await latest
  h.pending.get('first').resolve(rootTarget('first'))
  await first
  assert.equal(h.router.currentRoute.value.params.thread_id, 'latest')
  assert.deepEqual(h.opened, ['latest'])
  assert.deepEqual(h.errors, [])
})

test('离开来源页面后迟到的导航响应不能把用户拉回', async () => {
  const h = await navigationFixture()
  const pending = h.navigator.navigate('old')
  await h.router.push('/other')
  h.pending.get('old').resolve(rootTarget('old'))
  await pending
  assert.equal(h.router.currentRoute.value.path, '/other')
  assert.deepEqual(h.opened, [])
})

test('页面失活或新点击后，旧导航错误不再显示', async () => {
  const h = await navigationFixture()
  const old = h.navigator.navigate('old')
  const latest = h.navigator.navigate('latest')
  h.pending.get('old').reject(new Error('迟到错误'))
  await old
  h.deactivate()
  h.pending.get('latest').resolve(rootTarget('latest'))
  await latest
  assert.equal(h.router.currentRoute.value.path, '/agent/current')
  assert.deepEqual(h.opened, [])
  assert.deepEqual(h.errors, [])
})

test('路由恢复不重复导航，最新读取生效，过期错误不显示，当前错误继续抛出', async () => {
  const h = await navigationFixture()
  const route = h.router.currentRoute.value
  const old = h.navigator.navigate('old', { restore: true })
  const latest = h.navigator.navigate('latest', { restore: true })
  h.pending.get('latest').resolve(rootTarget('latest'))
  await latest
  h.pending.get('old').reject(new Error('过期恢复失败'))
  await old
  assert.equal(h.router.currentRoute.value, route)
  assert.deepEqual(h.opened, ['latest'])
  assert.deepEqual(h.errors, [])
  const failed = h.navigator.navigate('failed', { restore: true })
  h.pending.get('failed').reject(new Error('当前恢复失败'))
  await assert.rejects(failed, /当前恢复失败/)
})
