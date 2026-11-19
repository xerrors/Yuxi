import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildAttributePatch,
  describeMetadataFilter,
  validateMetadataFilter
} from '../../src/modules/knowledge/model/metadata.js'

const fields = [
  { key: 'model', name: '设备型号', type: 'text', operators: ['eq', 'ne', 'exists', 'not_exists'] },
  { key: 'weight', name: '重量', type: 'number', operators: ['eq', 'gt'] },
  { key: 'enabled', name: '启用', type: 'boolean', operators: ['eq'] }
]

test('部分更新保留未修改字段，0与false不等于未填写', () => {
  assert.deepEqual(
    buildAttributePatch(
      fields,
      { model: 'keep', weight: 'set', enabled: 'set' },
      { model: 'unchanged', weight: 0, enabled: false }
    ),
    {
      set: { weight: 0, enabled: false },
      unset: []
    }
  )
  assert.deepEqual(buildAttributePatch(fields, { model: 'unset' }, { model: 'old' }), {
    set: {},
    unset: ['model']
  })
  assert.throws(() => buildAttributePatch(fields, { model: 'set' }, {}), /设备型号/)
})

test('条件分组和实际执行描述保留且或语义', () => {
  const filter = {
    operator: 'and',
    conditions: [
      { field: 'weight', operator: 'gt', value: 0 },
      {
        operator: 'or',
        conditions: [
          { field: 'model', operator: 'eq', value: 'A' },
          { field: 'enabled', operator: 'eq', value: false }
        ]
      }
    ]
  }
  assert.deepEqual(validateMetadataFilter(filter, fields), filter)
  assert.equal(
    describeMetadataFilter(filter, fields),
    '（重量 大于 / 晚于 0 且 （设备型号 等于 A 或 启用 等于 否））'
  )
})

test('移除字段或不完整条件明确拒绝，不静默变成无筛选', () => {
  assert.equal(validateMetadataFilter(null, fields), null)
  for (const filter of [
    { field: 'deleted', operator: 'eq', value: 'A' },
    { field: 'model', operator: 'eq' },
    { operator: 'and', conditions: [] },
    { field: 'enabled', operator: 'gt', value: true }
  ])
    assert.throws(() => validateMetadataFilter(filter, fields))
  assert.deepEqual(
    validateMetadataFilter(
      { field: 'model', operator: 'not_exists', value: 'stale input' },
      fields
    ),
    { field: 'model', operator: 'not_exists' }
  )
})
