import assert from 'node:assert/strict'
import test from 'node:test'

import {
  supportsAllAgentResources,
  normalizeAgent,
  normalizeAgentBackendOption,
  mergeVisibleAgentResourceSelection,
  getVisibleAgentResourceSelection,
  getAgentResourceSelectionOptions
} from '../../src/utils/agentConfigUtils.js'

test('normalizeAgent 按 agent_id、slug、id 顺序统一身份字段', () => {
  const withAllIds = { agent_id: 'agent-id', slug: 'agent-slug', id: 'database-id' }
  assert.deepEqual(normalizeAgent(withAllIds), {
    agent_id: 'agent-id',
    slug: 'agent-slug',
    id: 'agent-id'
  })
  assert.equal(normalizeAgent({ slug: 'agent-slug', id: 'database-id' }).id, 'agent-slug')
  assert.deepEqual(normalizeAgent({ id: 'database-id' }), {
    id: 'database-id',
    agent_id: 'database-id',
    slug: 'database-id'
  })
  const withoutId = { name: '无身份字段' }
  assert.strictEqual(normalizeAgent(withoutId), withoutId)
})

test('编辑可见选择保留不可见引用，取消最后一个可见项不会禁用全部', () => {
  const original = ['visible-a', 'hidden-a', 'visible-b', 'hidden-b']
  assert.deepEqual(
    mergeVisibleAgentResourceSelection(
      original,
      ['visible-a', 'visible-b', 'visible-c'],
      ['visible-c']
    ),
    ['hidden-a', 'hidden-b', 'visible-c']
  )
  assert.deepEqual(mergeVisibleAgentResourceSelection(original, ['visible-a', 'visible-b'], []), [
    'hidden-a',
    'hidden-b'
  ])
  assert.deepEqual(mergeVisibleAgentResourceSelection(original, [], []), original)
  assert.deepEqual(mergeVisibleAgentResourceSelection('all', ['visible-a'], []), [])
  assert.deepEqual(
    mergeVisibleAgentResourceSelection(
      original,
      ['visible-a', 'visible-b'],
      ['visible-b', 'visible-a']
    ),
    original
  )
  assert.deepEqual(
    mergeVisibleAgentResourceSelection(
      original,
      ['visible-a', 'visible-b', 'visible-c'],
      ['visible-a', 'visible-c']
    ),
    ['visible-a', 'hidden-a', 'hidden-b', 'visible-c']
  )
  assert.deepEqual(original, ['visible-a', 'hidden-a', 'visible-b', 'hidden-b'])
})

test('资源缺省按字段契约投影，预加载 Skills 与 MCP 默认关闭', () => {
  const available = ['a', 'b']
  assert.deepEqual(
    getVisibleAgentResourceSelection([], { default: 'all', supports_all: true }, available),
    []
  )
  assert.deepEqual(
    getVisibleAgentResourceSelection('all', { default: 'all', supports_all: true }, available),
    available
  )
  assert.deepEqual(
    getVisibleAgentResourceSelection([], { default: 'all', supports_all: true }, available),
    []
  )
  assert.deepEqual(
    getVisibleAgentResourceSelection('all', { default: 'all', supports_all: true }, available),
    available
  )
  assert.deepEqual(
    getVisibleAgentResourceSelection(undefined, { default: 'all', supports_all: true }, available),
    available
  )
  assert.deepEqual(
    getVisibleAgentResourceSelection(null, { default: [], supports_all: true }, available),
    []
  )
  assert.deepEqual(
    getVisibleAgentResourceSelection(['a'], { default: [], supports_all: true }, available),
    ['a']
  )
  assert.deepEqual(
    getVisibleAgentResourceSelection([], { default: [], supports_all: true }, available),
    []
  )
  assert.deepEqual(
    getVisibleAgentResourceSelection(null, { default: [], supports_all: true }, available),
    []
  )
  assert.deepEqual(
    getVisibleAgentResourceSelection(['a'], { default: [], supports_all: true }, available),
    ['a']
  )
  assert.deepEqual(
    getVisibleAgentResourceSelection(
      ['hidden', 'b'],
      { default: 'all', supports_all: true },
      available
    ),
    ['b']
  )
  assert.deepEqual(
    getVisibleAgentResourceSelection(['hidden'], { default: 'all', supports_all: true }, []),
    []
  )
})

test('预加载候选项只包含 Agent 当前启用的 Skill', () => {
  const options = [{ key: 'a' }, { key: 'b' }]
  const items = { skills: { options }, preload_skills: { options } }
  assert.deepEqual(
    getAgentResourceSelectionOptions(
      'preload_skills',
      items.preload_skills,
      { skills: ['a'] },
      items
    ),
    [{ key: 'a' }]
  )
  assert.deepEqual(
    getAgentResourceSelectionOptions('preload_skills', items.preload_skills, { skills: [] }, items),
    []
  )
  assert.deepEqual(
    getAgentResourceSelectionOptions(
      'preload_skills',
      items.preload_skills,
      { skills: 'all' },
      items
    ),
    options
  )
  assert.deepEqual(
    getAgentResourceSelectionOptions('mcps', { options }, { skills: [] }, items),
    options
  )
})

test('normalizeAgentBackendOption 缺少名称时回退到 backend_id', () => {
  assert.deepEqual(normalizeAgentBackendOption({ backend_id: 'ChatbotAgent' }), {
    label: 'ChatbotAgent',
    value: 'ChatbotAgent'
  })
  assert.deepEqual(
    normalizeAgentBackendOption({ backend_id: 'ChatbotAgent', name: '对话智能体' }),
    { label: '对话智能体', value: 'ChatbotAgent' }
  )
})

test('all 跟随新增资源，逐项全选仍保持固定列表', () => {
  assert.deepEqual(
    getVisibleAgentResourceSelection('all', { default: [], supports_all: true }, ['a', 'b']),
    ['a', 'b']
  )
  const fixed = mergeVisibleAgentResourceSelection('all', ['a'], ['a'])
  assert.deepEqual(fixed, ['a'])
  assert.deepEqual(
    getVisibleAgentResourceSelection(fixed, { default: 'all', supports_all: true }, ['a', 'b']),
    ['a']
  )
  assert.deepEqual(
    getVisibleAgentResourceSelection('all', { default: 'all', supports_all: true }, ['a', 'b']),
    ['a', 'b']
  )
})

test('MCP 和预加载默认关闭且支持全部，预加载范围受启用 Skill 约束', () => {
  const options = [{ key: 'a' }, { key: 'b' }]
  const items = { skills: { options }, preload_skills: { options } }
  {
    assert.equal(supportsAllAgentResources({ supports_all: true }), true)
    assert.deepEqual(getVisibleAgentResourceSelection(undefined, { default: [] }, ['a', 'b']), [])
    assert.deepEqual(getVisibleAgentResourceSelection('all', { default: [] }, ['a', 'b']), [
      'a',
      'b'
    ])
  }
  for (const [skills, expected] of [
    [[], []],
    [['a'], ['a']],
    ['all', ['a', 'b']]
  ]) {
    const candidates = getAgentResourceSelectionOptions(
      'preload_skills',
      items.preload_skills,
      { skills },
      items
    )
    assert.deepEqual(
      getVisibleAgentResourceSelection(
        'all',
        items.preload_skills,
        candidates.map((item) => item.key)
      ),
      expected
    )
  }
})

test('选择能力和默认值由 schema 决定，不依赖字段名', () => {
  assert.equal(supportsAllAgentResources({ type: 'list' }), false)
  assert.equal(supportsAllAgentResources({ supports_all: true }), true)
  assert.deepEqual(getVisibleAgentResourceSelection(undefined, { default: [] }, ['a']), [])
  assert.deepEqual(getVisibleAgentResourceSelection(undefined, { default: 'all' }, ['a']), ['a'])
})

test('省略配置使用 schema 的固定默认范围，新增资源不自动加入', () => {
  const item = { default: ['general-purpose'], supports_all: true }
  assert.deepEqual(
    getVisibleAgentResourceSelection(undefined, item, ['general-purpose', 'specialist']),
    ['general-purpose']
  )
  assert.deepEqual(getVisibleAgentResourceSelection(undefined, item, ['specialist']), [])
  assert.deepEqual(getVisibleAgentResourceSelection([], item, ['general-purpose']), [])
})
