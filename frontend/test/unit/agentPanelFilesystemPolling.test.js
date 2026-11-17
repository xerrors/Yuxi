import assert from 'node:assert/strict'
import test from 'node:test'
import { releaseFilePreviewCacheEntry, reloadPreviewAfterOrderedCacheEntryInvalidation, replacePreviewCacheEntryIfCurrent, settlePreviewCacheLoad } from '../../src/modules/workspace/model/filePreviewCache.js'

import {
  createFilesystemRefreshGate,
  expandedKeysAfterFilesystemRefresh,
  refreshExpandedTree,
  shouldRefreshActivePreview,
  startAgentPanelFilesystemPolling
} from '../../src/modules/session/model/agentPanelFilesystemPolling.js'

test('agent panel polls live files every second and stops on cleanup', async () => {
  let callback
  let interval
  let cleared
  let refreshes = 0

  const stop = startAgentPanelFilesystemPolling({
    refresh: async () => {
      refreshes += 1
    },
    setIntervalFn: (next, milliseconds) => {
      callback = next
      interval = milliseconds
      return 42
    },
    clearIntervalFn: (timer) => {
      cleared = timer
    }
  })

  assert.equal(interval, 1000)
  callback()
  await Promise.resolve()
  assert.equal(refreshes, 1)

  stop()
  assert.equal(cleared, 42)
})

test('filesystem refreshes are isolated by thread and stale responses cannot commit', () => {
  const gate = createFilesystemRefreshGate()

  assert.equal(gate.begin('thread-a'), true)
  assert.equal(gate.begin('thread-a'), false)
  assert.equal(gate.begin('thread-b'), true)
  assert.equal(gate.canCommit('thread-a', 'thread-b'), false)
  assert.equal(gate.canCommit('thread-b', 'thread-b'), true)

  gate.finish('thread-a')
  assert.equal(gate.begin('thread-a'), true)
})

test('ensured filesystem refresh is queued while the same thread is in flight', () => {
  const gate = createFilesystemRefreshGate()

  assert.equal(gate.begin('thread-a'), true)
  assert.equal(gate.begin('thread-a', { ensure: true }), false)
  assert.equal(gate.finish('thread-a'), true)
  assert.equal(gate.begin('thread-a'), true)
  assert.equal(gate.finish('thread-a'), false)
})

test('silent filesystem polling preserves expanded directories', () => {
  const expanded = ['/project/outputs', '/project/uploads']

  assert.equal(expandedKeysAfterFilesystemRefresh(expanded, { silent: true }), expanded)
  assert.deepEqual(expandedKeysAfterFilesystemRefresh(expanded, { silent: false }), [])
})

test('silent filesystem polling reloads every visible expanded subtree', async () => {
  const initial = [
    {
      key: '/project/outputs',
      children: [
        {
          key: '/project/outputs/nested',
          children: [{ key: '/project/outputs/nested/old.txt', fileData: { size: 1 } }]
        }
      ]
    }
  ]
  const requested = []
  const refreshed = await refreshExpandedTree(
    initial,
    ['/project/outputs/nested', '/project/outputs'],
    async (path) => {
      requested.push(path)
      if (path === '/project/outputs') {
        return [{ key: '/project/outputs/nested', children: [] }]
      }
      return [{ key: '/project/outputs/nested/new.txt', fileData: { size: 8 } }]
    }
  )

  assert.deepEqual(requested, ['/project/outputs', '/project/outputs/nested'])
  assert.deepEqual(refreshed[0].children[0].children, [
    { key: '/project/outputs/nested/new.txt', fileData: { size: 8 } }
  ])
})

test('silent filesystem polling does not reload directories prefetched in the same pass', async () => {
  const requested = []
  await refreshExpandedTree(
    [
      {
        key: '/outputs',
        children: [{ key: '/outputs/nested', children: [] }]
      }
    ],
    ['/outputs', '/outputs/nested'],
    async (path) => {
      requested.push(path)
      return []
    },
    ['/outputs']
  )

  assert.deepEqual(requested, ['/outputs/nested'])
})

test('active preview refreshes only when Project metadata changes', () => {
  const current = { path: '/project/report.txt', size: 3, modified_at: 'old' }

  assert.equal(
    shouldRefreshActivePreview(current, {
      path: current.path,
      size: 3,
      modified_at: 'new'
    }),
    true
  )
  assert.equal(shouldRefreshActivePreview(current, { ...current }), false)
  assert.equal(shouldRefreshActivePreview({ ...current, artifact: true }, null), false)
  assert.equal(
    shouldRefreshActivePreview(
      { ...current, artifact: true },
      { ...current, modified_at: 'new' }
    ),
    true
  )
})

test('metadata refresh invalidates one ready preview before reload and revokes its URL', async () => {
  const cacheKey = 'thread-a:/project/report.txt'
  const staleFile = { content: 'stale', previewUrl: 'blob:stale' }
  const previewCache = new Map([
    [cacheKey, { status: 'ready', file: staleFile, lastAccessed: 1 }],
    ['thread-b:/project/report.txt', { status: 'ready', file: { content: 'other' } }]
  ])
  const revokedUrls = []
  const events = []

  await reloadPreviewAfterOrderedCacheEntryInvalidation({
    previewCache,
    cacheKey,
    revokeObjectURL: (url) => {
      revokedUrls.push(url)
      events.push('revoke')
    },
    notifyPreviewChanged: () => {
      assert.equal(previewCache.has(cacheKey), false)
      events.push('notify')
    },
    reloadPreview: async () => {
      assert.equal(previewCache.has(cacheKey), false)
      events.push('reload')
    }
  })

  assert.deepEqual(events, ['revoke', 'notify', 'reload'])
  assert.deepEqual(revokedUrls, ['blob:stale'])
  assert.equal(previewCache.has('thread-b:/project/report.txt'), true)
})

test('invalidated in-flight preview cannot delete or overwrite its replacement entry', () => {
  const cacheKey = 'thread-a:/project/report.txt'
  const oldLoadingEntry = { status: 'loading', promise: Promise.resolve() }
  const replacementEntry = { status: 'loading', promise: Promise.resolve() }
  const previewCache = new Map([[cacheKey, oldLoadingEntry]])

  releaseFilePreviewCacheEntry(previewCache, cacheKey, () => {})
  previewCache.set(cacheKey, replacementEntry)

  assert.equal(
    replacePreviewCacheEntryIfCurrent(previewCache, cacheKey, oldLoadingEntry, {
      status: 'ready',
      file: { content: 'stale' }
    }),
    false
  )
  assert.equal(
    replacePreviewCacheEntryIfCurrent(previewCache, cacheKey, oldLoadingEntry, null),
    false
  )
  assert.equal(previewCache.get(cacheKey), replacementEntry)
})

test('a stale preview requester still publishes a shared load for the current waiter', () => {
  const cacheKey = 'thread-a:/project/report.txt'
  const loadingEntry = { status: 'loading', promise: Promise.resolve() }
  const nextFile = { content: 'fresh', previewUrl: 'blob:fresh' }
  const previewCache = new Map([[cacheKey, loadingEntry]])
  const revokedUrls = []

  assert.equal(
    settlePreviewCacheLoad({
      previewCache,
      cacheKey,
      loadingEntry,
      nextFile,
      lastAccessed: 2,
      revokeObjectURL: (url) => revokedUrls.push(url)
    }),
    true
  )
  assert.deepEqual(previewCache.get(cacheKey), {
    status: 'ready',
    file: nextFile,
    lastAccessed: 2
  })
  assert.deepEqual(revokedUrls, [])
})

test('an invalidated preview load cannot publish and revokes only its own result URL', () => {
  const cacheKey = 'thread-a:/project/report.txt'
  const oldLoadingEntry = { status: 'loading', promise: Promise.resolve() }
  const replacementEntry = { status: 'loading', promise: Promise.resolve() }
  const previewCache = new Map([[cacheKey, replacementEntry]])
  const revokedUrls = []

  assert.equal(
    settlePreviewCacheLoad({
      previewCache,
      cacheKey,
      loadingEntry: oldLoadingEntry,
      nextFile: { content: 'stale', previewUrl: 'blob:stale-load' },
      lastAccessed: 2,
      revokeObjectURL: (url) => revokedUrls.push(url)
    }),
    false
  )
  assert.equal(previewCache.get(cacheKey), replacementEntry)
  assert.deepEqual(revokedUrls, ['blob:stale-load'])
})
