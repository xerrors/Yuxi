import assert from 'node:assert/strict'
import test from 'node:test'

import {
  COOPERATION_CONCURRENCY_KEY,
  COOPERATION_DEPTH_KEY,
  applyCooperationConcurrency,
  applyCooperationDepth
} from '../../src/modules/settings/model/cooperationDepth.js'

test('协作深度只提交完整的非负整数，清空或非法输入保留原配置', () => {
  assert.equal(COOPERATION_DEPTH_KEY, 'max_cooperation_depth')
  const writes = []
  for (const input of [null, undefined, '', '2', -1, 1.5, 0, 2]) {
    applyCooperationDepth(input, (value) => writes.push([COOPERATION_DEPTH_KEY, value]))
  }

  assert.deepEqual(writes, [
    [COOPERATION_DEPTH_KEY, 0],
    [COOPERATION_DEPTH_KEY, 2]
  ])
})

test('并发执行上限只提交 1 到 20 的整数，非法输入保留原配置', () => {
  assert.equal(COOPERATION_CONCURRENCY_KEY, 'max_cooperation_concurrency')
  const writes = []
  for (const input of [null, '', '4', 0, -2, 2.5, 1, 20, 21]) {
    applyCooperationConcurrency(input, (value) => writes.push([COOPERATION_CONCURRENCY_KEY, value]))
  }

  assert.deepEqual(writes, [
    [COOPERATION_CONCURRENCY_KEY, 1],
    [COOPERATION_CONCURRENCY_KEY, 20]
  ])
})
