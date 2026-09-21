import assert from 'node:assert/strict'
import test from 'node:test'

import { getChunkCite, groupKnowledgeChunks } from '../../src/utils/kbResultGroups.js'

test('同名文件按知识库和文件身份分别聚合', () => {
  const groups = groupKnowledgeChunks([
    { kb_id: 'kb-1', file_id: 'file-1', content: 'A', metadata: { source: 'guide.md' } },
    { kb_id: 'kb-2', file_id: 'file-2', content: 'B', metadata: { source: 'guide.md' } },
    { kb_id: 'kb-1', file_id: 'file-1', content: 'C', metadata: { source: 'guide.md' } }
  ])

  assert.equal(groups.length, 2)
  assert.deepEqual(
    groups.map((group) => [group.kb_id, group.file_id, group.chunks.length]),
    [
      ['kb-1', 'file-1', 2],
      ['kb-2', 'file-2', 1]
    ]
  )
})

test('分组收集该文件的引用编号，并升序排列', () => {
  const groups = groupKnowledgeChunks([
    { kb_id: 'kb-1', file_id: 'file-1', cite: 5, content: 'A', metadata: { source: 'a.pdf' } },
    { kb_id: 'kb-1', file_id: 'file-1', cite: 2, content: 'B', metadata: { source: 'a.pdf' } },
    { kb_id: 'kb-1', file_id: 'file-2', cite: 3, content: 'C', metadata: { source: 'b.pdf' } }
  ])

  assert.deepEqual(groups.find((g) => g.file_id === 'file-1').cites, [2, 5])
  assert.deepEqual(groups.find((g) => g.file_id === 'file-2').cites, [3])
})

test('没有引用编号的片段不产生编号，也不影响聚合', () => {
  const groups = groupKnowledgeChunks([
    { kb_id: 'kb-1', file_id: 'file-1', content: 'A', metadata: { source: 'a.pdf' } },
    { kb_id: 'kb-1', file_id: 'file-1', cite: 'x', content: 'B', metadata: { source: 'a.pdf' } }
  ])

  assert.deepEqual(groups[0].cites, [])
  assert.equal(groups[0].chunks.length, 2)
})

test('getChunkCite 只接受正整数编号', () => {
  assert.equal(getChunkCite({ cite: 4 }), 4)
  assert.equal(getChunkCite({ cite: '7' }), 7)
  assert.equal(getChunkCite({ cite: 0 }), null)
  assert.equal(getChunkCite({ cite: -1 }), null)
  assert.equal(getChunkCite({}), null)
  assert.equal(getChunkCite(null), null)
})
