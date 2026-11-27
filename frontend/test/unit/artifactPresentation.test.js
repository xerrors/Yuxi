import assert from 'node:assert/strict'
import test from 'node:test'

import { normalizeArtifacts } from '../../src/modules/session/model/artifacts.js'
import {
  buildArtifactItems,
  visibleArtifactItems
} from '../../src/modules/session/model/artifactPresentation.js'

test('交付物字符串路径保序去重', () => {
  assert.deepEqual(normalizeArtifacts([' /a.png ', '/a.png', '/b.png', '']), ['/a.png', '/b.png'])
})

test('不兼容旧交付物对象', () => {
  assert.throws(() => normalizeArtifacts([{ path: '/a.png', type: 'image' }]), TypeError)
})

test('字符串交付物倒序展示为文件项并按展开状态截断', () => {
  const items = buildArtifactItems(['/image.png', '/a.md', '/b.md', '/c.md'])

  assert.deepEqual(items, [
    { path: '/c.md', name: 'c.md' },
    { path: '/b.md', name: 'b.md' },
    { path: '/a.md', name: 'a.md' },
    { path: '/image.png', name: 'image.png' }
  ])
  assert.deepEqual(visibleArtifactItems(items, false), items)
  const extendedItems = [...items, { path: '/report.html', name: 'report.html' }]
  assert.deepEqual(visibleArtifactItems(extendedItems, false), extendedItems.slice(0, 4))
  assert.deepEqual(visibleArtifactItems(extendedItems, true), extendedItems)
  const overflowingItems = [...extendedItems, { path: '/roadmap.pdf', name: 'roadmap.pdf' }]
  assert.deepEqual(visibleArtifactItems(overflowingItems, false), extendedItems.slice(0, 4))
  assert.deepEqual(visibleArtifactItems(overflowingItems, true), overflowingItems)
  assert.deepEqual(visibleArtifactItems(buildArtifactItems([]), false), [])
})
