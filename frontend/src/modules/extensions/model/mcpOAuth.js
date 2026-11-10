export function mcpConnectionLabel(status, mode = 'oauth') {
  const isStatic = ['bearer', 'api_key'].includes(mode)
  return ({ connected: isStatic ? '已配置' : '已授权', reconnect_required: isStatic ? '需要更新凭证' : '需要重新授权', disconnected: isStatic ? '未配置' : '未授权', not_required: '无需个人凭证' })[status] || '未配置'
}

/** 展示连接实际使用的认证方式。 */
export function mcpAuthLabel(mode) {
  return ({ none: '无需认证', bearer: '个人 Bearer Token', api_key: '个人 API Key', headers: '共享凭证（所有用户共用）', oauth: '个人 OAuth 授权', delegation: '平台委托认证' })[mode] || '共享凭证（所有用户共用）'
}

/** 需要当前用户配置凭证的认证方式。 */
export function isPersonalMcp(mode) {
  return ['oauth', 'bearer', 'api_key'].includes(mode)
}

/** 返回 MCP 卡片标签：来源、认证方式，以及需要处理的个人连接状态。 */
export function mcpCardTags(server) {
  const tags = [
    { name: server?.is_builtin ? '内置' : '添加', color: 'gray' },
    { name: mcpAuthLabel(server?.auth_mode), color: 'gray' }
  ]
  const status = server?.connection_status
  if (isPersonalMcp(server?.auth_mode) && status && status !== 'connected' && status !== 'not_required') {
    tags.push({ name: mcpConnectionLabel(status, server.auth_mode), color: 'gold' })
  }
  return tags
}
