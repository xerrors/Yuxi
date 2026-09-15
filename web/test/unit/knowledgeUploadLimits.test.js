import assert from 'node:assert/strict'
import test from 'node:test'
import { assertKnowledgeUploadSize } from '../../src/utils/knowledgeUploadLimits.js'
test('知识文档上传使用服务端实际字节上限，临界值通过，超限与未知限制停止', () => {
  const limits = { effective_upload_max_bytes: 1024 }
  assert.doesNotThrow(() => assertKnowledgeUploadSize({ size: 1024 }, limits))
  assert.throws(() => assertKnowledgeUploadSize({ size: 1025 }, limits), /上传上限/)
  assert.doesNotThrow(() =>
    assertKnowledgeUploadSize({ size: 1025 }, { effective_upload_max_bytes: 2048 })
  )
  for (const limit of [
    null,
    {},
    { effective_upload_max_bytes: 0 },
    { effective_upload_max_bytes: Infinity }
  ]) {
    assert.throws(() => assertKnowledgeUploadSize({ size: 1 }, limit), /暂不可用/)
  }
})
