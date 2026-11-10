import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildDocumentListParams,
  normalizeDocumentSort
} from '../../src/modules/knowledge/model/document_list_params.js'

test('默认文件列表不传排序参数', () => {
  assert.deepEqual(
    buildDocumentListParams({
      page: 1,
      pageSize: 100,
      status: 'all',
      recursive: false,
      parentId: null,
      sortBy: null,
      sortOrder: null
    }),
    {
      page: 1,
      page_size: 100,
      status: 'all',
      recursive: false
    }
  )
})

test('排序参数只在字段和方向都合法时发送，筛选目录仍带 parent_id', () => {
  assert.deepEqual(
    buildDocumentListParams({
      page: 2,
      pageSize: 100,
      status: 'indexed',
      recursive: false,
      parentId: 'folder-1',
      sortBy: 'content_amount',
      sortOrder: 'descend'
    }),
    {
      page: 2,
      page_size: 100,
      status: 'indexed',
      recursive: false,
      parent_id: 'folder-1',
      sort_by: 'content_amount',
      sort_order: 'descend'
    }
  )

  const partial = buildDocumentListParams({
    page: 1,
    pageSize: 100,
    status: 'all',
    recursive: true,
    parentId: 'folder-1',
    sortBy: 'filename',
    sortOrder: null
  })
  assert.equal(partial.parent_id, undefined)
  assert.equal(partial.sort_by, undefined)
  assert.equal(partial.sort_order, undefined)
})

test('取消排序或未知列不产生服务端排序参数', () => {
  assert.deepEqual(normalizeDocumentSort({ columnKey: 'created_at', order: undefined }), {
    sortBy: null,
    sortOrder: null
  })
  assert.deepEqual(normalizeDocumentSort({ columnKey: 'created_by', order: 'ascend' }), {
    sortBy: null,
    sortOrder: null
  })
  assert.deepEqual(normalizeDocumentSort({ columnKey: 'status', order: 'descend' }), {
    sortBy: 'status',
    sortOrder: 'descend'
  })
})
