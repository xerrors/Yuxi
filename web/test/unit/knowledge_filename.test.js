import assert from 'node:assert/strict'
import test from 'node:test'

import { createServer } from 'vite'

/**
 * 与服务端扩展名判据的对照表。
 *
 * 期望值由 Python 侧生成：`os.path.splitext(name)[1].lower()`。
 * 后端 `yuxi.knowledge.base` 的改名守卫同时校验 splitext 与 pathlib 两种后缀，
 * 前端只复刻 splitext 一侧——它决定「用户能不能立刻看到具体原因」，
 * 而 pathlib 独有的差异会退化成服务端返回的通用 400 文案。
 */
const SPLITEXT_CASES = [
  ['a.pdf', '.pdf'],
  ['b.txt', '.txt'],
  ['x.', '.'], // 末尾点是后缀
  ['x', ''],
  ['.env', ''], // 前导点不算后缀
  ['..env', ''],
  ['..', ''],
  ['...', ''],
  ['a.b.c', '.c'],
  ['中文.文档', '.文档'],
  ['A.PDF', '.pdf'], // 统一小写，与服务端 .lower() 对齐
  ['a..b', '.b'],
  ['a.tar.gz', '.gz'],
  ['..pdf', ''],
  ['.pdf', ''],
  ['x..', '.'],
  ['y.', '.'],
  ['.envrc', ''],
  ['..env2', ''],
  ['a b.pdf', '.pdf'],
  ['a.b', '.b'],
  ['.hidden.md', '.md'],
  ['...env', ''],
  ['', '']
]

test('filenameExtension 与 Python os.path.splitext 逐字一致', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    const { filenameExtension } = await server.ssrLoadModule('/src/utils/knowledge_filename.js')
    for (const [name, expected] of SPLITEXT_CASES) {
      assert.equal(filenameExtension(name), expected, `name=${JSON.stringify(name)}`)
    }
  } finally {
    await server.close()
  }
})

test('isAllDotsName 只认全点组成的名字', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    const { isAllDotsName } = await server.ssrLoadModule('/src/utils/knowledge_filename.js')
    for (const name of ['.', '..', '...', '....']) {
      assert.equal(isAllDotsName(name), true, `name=${name}`)
    }
    for (const name of ['', 'a', '.a', 'a.', '..a', 'a..', '中文']) {
      assert.equal(isAllDotsName(name), false, `name=${name}`)
    }
  } finally {
    await server.close()
  }
})

test('canRenameFileRecord 拒绝只读、虚拟视图与带目录前缀的名字', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    const { canRenameFileRecord } = await server.ssrLoadModule('/src/utils/knowledge_filename.js')

    assert.equal(canRenameFileRecord({ filename: 'a.pdf' }), true)
    assert.equal(canRenameFileRecord({ filename: 'a.pdf', readonly: true }), false)
    // 虚拟视图里行名被裁掉了目录前缀，改名会把文件搬出目录
    assert.equal(canRenameFileRecord({ filename: 'a.pdf', isVirtualPathView: true }), false)
    // 历史虚拟目录残留：文件名本身带前缀，改名同样会丢目录语义
    assert.equal(canRenameFileRecord({ filename: 'dir/a.pdf' }), false)
    assert.equal(canRenameFileRecord({}), false)
    assert.equal(canRenameFileRecord(), false)
  } finally {
    await server.close()
  }
})
