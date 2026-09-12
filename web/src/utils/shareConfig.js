/** 判断知识库是否固定为个人可见。 */
export function isPersonalKnowledgeConfig(config) {
  return config?.version === 2 && config.read_scope === null && config.manage_scope === null
}

/** 按共享配置把知识库归入团队或个人入口。 */
export function isKnowledgeBaseInScope(database, scope) {
  return isPersonalKnowledgeConfig(database.share_config) === (scope === 'mine')
}

export function getShareConfigLabel(shareConfig) {
  const config = shareConfig || {}
  const readScope = config.version === 2 ? config.read_scope : config
  const manageScope = config.manage_scope
  if (config.version === 2 && !config.read_scope && !manageScope) return '仅本人可见'
  const scopeLabel = (scope) => {
    if (!scope) return '无'
    if (scope.access_level === 'global') return '全局'
    if (scope.access_level === 'department') return `部门(${scope.department_ids?.length || 0})`
    return `用户(${scope.user_uids?.length || 0})`
  }
  return manageScope
    ? `读${scopeLabel(readScope)} · 管${scopeLabel(manageScope)}`
    : `只读${scopeLabel(readScope)}`
}
