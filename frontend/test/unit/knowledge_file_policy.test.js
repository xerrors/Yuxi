import assert from 'node:assert/strict'
import test from 'node:test'

import {
  canDownloadFile,
  canIndexFile,
  canReindexFile,
  compareFileCreatedAt,
  getBatchIndexButtonLabel,
  getFilePrimaryAction,
  isTerminalFileFailure
} from '../../src/modules/knowledge/model/knowledge_file_policy.js'

test('compareFileCreatedAt 按时间升序，较早的记录排在前面', () => {
  const older = { created_at: '2026-01-01T00:00:00Z' }
  const newer = { created_at: '2026-06-01T00:00:00Z' }
  assert.ok(compareFileCreatedAt(older, newer) < 0)
  assert.ok(compareFileCreatedAt(newer, older) > 0)
  assert.equal(compareFileCreatedAt(older, { created_at: older.created_at }), 0)
})

test('failed 与 error_indexing 一样可以重试入库，文件夹 done 不可入库', () => {
  const failed = { status: 'failed', is_folder: false }
  assert.equal(canIndexFile(failed), true)
  assert.deepEqual(getFilePrimaryAction(failed), { type: 'index', label: '重试入库' })
  assert.equal(canReindexFile(failed), false)
  assert.equal(getFilePrimaryAction({ status: 'error_indexing' }).label, '重试入库')
  assert.equal(canIndexFile({ status: 'done' }), true)
  assert.equal(canReindexFile({ status: 'done' }), true)
  assert.equal(canIndexFile({ status: 'failed', is_folder: true }), false)
  assert.equal(canIndexFile({ status: 'done', is_folder: true }), false)
  assert.equal(getFilePrimaryAction({ status: 'done', is_folder: true }), null)
  assert.equal(canDownloadFile(failed), true)
  assert.equal(canDownloadFile({ status: 'error_indexing' }), true)
  assert.equal(canDownloadFile({ status: 'failed', is_folder: true }), false)
  assert.equal(canDownloadFile({ status: 'failed', file_type: 'url' }), false)
})

test('批量入库文案只在可入库选中项全部可重新入库时切换', () => {
  assert.equal(
    getBatchIndexButtonLabel([{ status: 'indexed' }, { status: 'done' }]),
    '批量重新入库'
  )
  assert.equal(
    getBatchIndexButtonLabel([{ status: 'indexed' }, { status: 'failed' }]),
    '批量入库'
  )
  assert.equal(getBatchIndexButtonLabel([{ status: 'parsed' }]), '批量入库')
  assert.equal(getBatchIndexButtonLabel([{ status: 'error_indexing' }]), '批量入库')
  assert.equal(getBatchIndexButtonLabel([{ status: 'uploaded' }]), '批量入库')
  assert.equal(getBatchIndexButtonLabel([]), '批量入库')
})

test('isTerminalFileFailure 只覆盖已经结束的失败状态', () => {
  assert.equal(isTerminalFileFailure({ status: 'error_parsing' }), true)
  assert.equal(isTerminalFileFailure({ status: 'error_indexing' }), true)
  assert.equal(isTerminalFileFailure({ status: 'failed' }), true)
  assert.equal(isTerminalFileFailure({ status: 'parsing' }), false)
  assert.equal(isTerminalFileFailure({ status: 'indexing' }), false)
  assert.equal(isTerminalFileFailure({ status: 'failed', is_folder: true }), false)
})
