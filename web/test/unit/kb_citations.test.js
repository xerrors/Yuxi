import assert from 'node:assert/strict'
import test from 'node:test'

import {
  collectKbCitationRegistries,
  parseKbCitationIndex,
  resolveKbCitation
} from '../../src/utils/kbCitations.js'

const kbToolCall = (chunks) => ({
  name: 'query_kb',
  tool_call_result: { content: JSON.stringify({ results: chunks }) }
})

const chunk = (id, source, cite) => ({
  kb_id: 'kb-1',
  file_id: `file-${id}`,
  id,
  content: `内容 ${id}`,
  ...(cite === undefined ? {} : { cite }),
  metadata: { source }
})

test('编号取检索侧写入的 cite，而不是前端推算', () => {
  const registry = collectKbCitationRegistries([
    kbToolCall([chunk('c1', 'a.pdf', 7), chunk('c2', 'b.pdf', 8)])
  ])

  assert.deepEqual([...registry.keys()], [7, 8])
  assert.equal(resolveKbCitation(registry, '8').chunk_id, 'c2')
})

test('多次检索连续编号时不会互相覆盖', () => {
  const registry = collectKbCitationRegistries([
    kbToolCall([chunk('c1', 'first.pdf', 1), chunk('c2', 'first.pdf', 2)]),
    kbToolCall([chunk('c3', 'second.pdf', 3)])
  ])

  // 改造前每次检索都从 1 开始，编号 1 会被第二次检索顶掉
  assert.equal(resolveKbCitation(registry, '1').source, 'first.pdf')
  assert.equal(resolveKbCitation(registry, '2').source, 'first.pdf')
  assert.equal(resolveKbCitation(registry, '3').source, 'second.pdf')
})

test('后端未写入 cite 时退回该次检索内的顺序编号', () => {
  const registry = collectKbCitationRegistries([kbToolCall([chunk('c1', 'a.pdf'), chunk('c2', 'b.pdf')])])

  assert.deepEqual([...registry.keys()], [1, 2])
  assert.equal(resolveKbCitation(registry, '2').chunk_id, 'c2')
})

test('同一编号指向不同片段时按无引用处理，避免跳到错误来源', () => {
  const registry = collectKbCitationRegistries([
    kbToolCall([chunk('c1', 'first.pdf', 1)]),
    kbToolCall([chunk('c2', 'second.pdf', 1)])
  ])

  assert.equal(registry.get(1).ambiguous, true)
  assert.equal(resolveKbCitation(registry, '1'), null)
})

test('同一片段被重复检索时不算冲突', () => {
  const registry = collectKbCitationRegistries([
    kbToolCall([chunk('c1', 'a.pdf', 1)]),
    kbToolCall([chunk('c1', 'a.pdf', 1)])
  ])

  assert.equal(registry.get(1).ambiguous, false)
  assert.equal(resolveKbCitation(registry, '1').chunk_id, 'c1')
})

test('工具结果已是对象时同样能解析', () => {
  const registry = collectKbCitationRegistries([
    { name: 'query_kb', tool_call_result: { content: { results: [chunk('c1', 'a.pdf', 1)] } } }
  ])

  assert.equal(resolveKbCitation(registry, '1').source, 'a.pdf')
})

test('非知识库检索工具不进入引用注册表', () => {
  const registry = collectKbCitationRegistries([
    { name: 'grep', tool_call_result: { content: JSON.stringify({ results: [chunk('c1', 'a.pdf', 1)] } ) } }
  ])

  assert.equal(registry.size, 0)
})

test('结果为空或内容无法解析时不产生条目', () => {
  const registry = collectKbCitationRegistries([
    kbToolCall([]),
    { name: 'query_kb', tool_call_result: { content: 'not json' } },
    { name: 'query_kb' }
  ])

  assert.equal(registry.size, 0)
})

test('解析不到的编号返回 null，编造的引用无法渲染', () => {
  const registry = collectKbCitationRegistries([kbToolCall([chunk('c1', 'a.pdf', 1)])])

  assert.equal(resolveKbCitation(registry, '99'), null)
  assert.equal(resolveKbCitation(new Map(), '1'), null)
  assert.equal(resolveKbCitation(undefined, '1'), null)
})

test('缺少知识库或文件身份的条目会被调用方拒绝', () => {
  const registry = collectKbCitationRegistries([
    kbToolCall([{ id: 'c1', cite: 1, content: '内容', metadata: { source: 'a.pdf' } }])
  ])

  const entry = resolveKbCitation(registry, '1')

  assert.equal(entry.kb_id, '')
  assert.equal(entry.file_id, '')
})

test('引用编号只接受纯数字文本', () => {
  assert.equal(parseKbCitationIndex(' 3 '), 3)
  assert.equal(parseKbCitationIndex('a.pdf'), null)
  assert.equal(parseKbCitationIndex(''), null)
  assert.equal(parseKbCitationIndex(null), null)
})
