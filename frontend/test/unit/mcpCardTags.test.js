import assert from 'node:assert/strict'
import test from 'node:test'
import { mcpCardTags } from '../../src/modules/extensions/model/mcpOAuth.js'

test('MCP 卡片用标签区分内置与添加，并展示认证方式', () => {
  assert.deepEqual(
    mcpCardTags({ is_builtin: true, auth_mode: 'delegation', connection_status: 'not_required' }).map((tag) => tag.name),
    ['内置', '平台委托认证']
  )
  assert.deepEqual(
    mcpCardTags({ is_builtin: false, auth_mode: 'headers' }).map((tag) => tag.name),
    ['添加', '共享凭证（所有用户共用）']
  )
})

test('缺少内置标记时按添加展示，未知认证方式不留空标签', () => {
  const tags = mcpCardTags({ auth_mode: 'custom' })
  assert.equal(tags[0].name, '添加')
  assert.equal(tags[1].name, '共享凭证（所有用户共用）')
  assert.equal(tags.some((tag) => !tag.name), false)
})

test('个人连接只在需要处理时出现，已连接和共享服务不额外提示', () => {
  assert.deepEqual(
    mcpCardTags({ is_builtin: false, auth_mode: 'oauth', connection_status: 'disconnected' }).map((tag) => tag.name),
    ['添加', '个人 OAuth 授权', '未授权']
  )
  assert.equal(
    mcpCardTags({ auth_mode: 'oauth', connection_status: 'disconnected' }).at(-1).color,
    'gold'
  )
  assert.equal(
    mcpCardTags({ auth_mode: 'bearer', connection_status: 'connected' }).some((tag) => tag.name === '已配置'),
    false
  )
  assert.equal(
    mcpCardTags({ auth_mode: 'headers', connection_status: 'disconnected' }).length,
    2
  )
})
