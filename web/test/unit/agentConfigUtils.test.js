import assert from 'node:assert/strict'
import test from 'node:test'

import {
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
  assert.deepEqual(mergeVisibleAgentResourceSelection(null, ['visible-a'], []), [])
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
  assert.deepEqual(getVisibleAgentResourceSelection([], 'subagents', available), available)
  assert.deepEqual(getVisibleAgentResourceSelection(null, 'subagents', available), available)
  assert.deepEqual(getVisibleAgentResourceSelection([], 'skills', available), [])
  assert.deepEqual(getVisibleAgentResourceSelection(null, 'skills', available), available)
  assert.deepEqual(getVisibleAgentResourceSelection(undefined, 'skills', available), available)
  assert.deepEqual(getVisibleAgentResourceSelection(null, 'preload_skills', available), [])
  assert.deepEqual(getVisibleAgentResourceSelection(['a'], 'preload_skills', available), ['a'])
  assert.deepEqual(getVisibleAgentResourceSelection([], 'mcps', available), [])
  assert.deepEqual(getVisibleAgentResourceSelection(null, 'mcps', available), [])
  assert.deepEqual(getVisibleAgentResourceSelection(['a'], 'mcps', available), ['a'])
  assert.deepEqual(getVisibleAgentResourceSelection(['hidden', 'b'], 'skills', available), ['b'])
  assert.deepEqual(getVisibleAgentResourceSelection(['hidden'], 'skills', []), [])
})

test('预加载候选项只包含 Agent 当前启用的 Skill', () => {
  const options = [{ key: 'a' }, { key: 'b' }]
  const items = { skills: { options }, preload_skills: { options } }
  assert.deepEqual(getAgentResourceSelectionOptions('preload_skills', items.preload_skills, { skills: ['a'] }, items), [
    { key: 'a' }
  ])
  assert.deepEqual(
    getAgentResourceSelectionOptions('preload_skills', items.preload_skills, { skills: [] }, items),
    []
  )
  assert.deepEqual(
    getAgentResourceSelectionOptions('preload_skills', items.preload_skills, { skills: null }, items),
    options
  )
  assert.deepEqual(getAgentResourceSelectionOptions('mcps', { options }, { skills: [] }, items), options)
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
