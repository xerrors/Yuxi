import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(
  new URL('../../src/components/AgentInputArea.vue', import.meta.url),
  'utf8'
)

const handleKeyDown = source.slice(
  source.indexOf('const handleKeyDown'),
  source.indexOf('defineExpose')
)

test('输入法还在组合状态时，回车不触发发送', () => {
  assert.ok(handleKeyDown.includes('isComposing'), 'handleKeyDown 缺少 e.isComposing 判据')
  assert.ok(handleKeyDown.includes('229'), 'handleKeyDown 缺少 keyCode 229 兜底判据')
  assert.ok(
    handleKeyDown.indexOf('isComposing') < handleKeyDown.indexOf("e.key === 'Enter'"),
    '组合输入判据必须排在回车发送分支之前'
  )
})
