import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { effectScope, ref } from 'vue'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { createTestServer } from '../helpers/createTestServer.js'

let server, useFilePreviewSession, useUserStore, settlePreviewCacheLoad
before(async () => {
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  server = await createTestServer({ server: { middlewareMode: true } })
  ;({ useFilePreviewSession } = await server.ssrLoadModule('/src/modules/workspace/index.js'))
  ;({ useUserStore } = await server.ssrLoadModule('/src/modules/identity/index.js'))
  ;({ settlePreviewCacheLoad } = await server.ssrLoadModule('/src/modules/workspace/model/filePreviewCache.js'))
})
after(async () => { await server.close(); delete globalThis.localStorage })

function setup(t) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const threadId = ref('a')
  const scope = effectScope()
  const revoked = []
  globalThis.window = { URL: { revokeObjectURL: (url) => revoked.push(url) } }
  const session = scope.run(() => useFilePreviewSession(threadId))
  t.after(() => { scope.stop(); disposePinia(pinia); delete globalThis.window })
  return { session, threadId, scope, revoked, user: useUserStore() }
}

test('删除只关闭对应 Thread 的目录子项，同路径的个人空间和成员产物保留', (t) => {
  const { session, revoked } = setup(t)
  for (const path of ['/folder/a.pdf', '/folder-next/a.pdf']) session.open({ path, workdir: true })
  session.open({ path: '/folder/a.pdf', workspace: true })
  session.open({ path: '/folder/a.pdf' }, 'b')
  for (const key of ['a:/folder/a.pdf', 'workspace:/folder/a.pdf', 'a:/folder-next/a.pdf', 'b:/folder/a.pdf'])
    session.cache.set(key, { status: 'ready', file: { previewUrl: `blob:${key}` } })
  assert.deepEqual(session.closePath('/folder/', 'a'), ['a:/folder/a.pdf'])
  const expected = ['a:/folder-next/a.pdf', 'b:/folder/a.pdf', 'workspace:/folder/a.pdf']
  assert.deepEqual(session.tabs.value.map((tab) => tab.key).sort(), expected)
  assert.deepEqual([...session.cache.keys()].sort(), expected)
  assert.deepEqual(revoked, ['blob:a:/folder/a.pdf'])
})

for (const boundary of ['thread', 'identity', 'dispose', 'close']) {
  test(`${boundary}释放后迟到预览不能回填缓存，并回收新生成的对象URL`, (t) => {
    const { session, threadId, scope, user, revoked } = setup(t)
    session.open({ path: '/pending.pdf' })
    session.cache.set('workspace:/ready.pdf', { status: 'ready', file: { previewUrl: 'blob:ready' } })
    const entry = { status: 'loading', promise: Promise.resolve() }
    session.cache.set('a:/pending.pdf', entry)
    if (boundary === 'thread') threadId.value = 'b'
    else if (boundary === 'identity') user.token = 'another-user'
    else if (boundary === 'dispose') scope.stop()
    else session.close('a:/pending.pdf')
    assert.equal(session.tabs.value.length, 0)
    const published = settlePreviewCacheLoad({ previewCache: session.cache,
      cacheKey: 'a:/pending.pdf', loadingEntry: entry, nextFile: { previewUrl: 'blob:late' },
      lastAccessed: 1, revokeObjectURL: (url) => revoked.push(url) })
    assert.equal(published, false)
    assert.equal(session.cache.has('a:/pending.pdf'), false)
    assert.equal(revoked.includes('blob:late'), true)
    assert.equal(revoked.includes('blob:ready'), boundary !== 'close')
  })
}
