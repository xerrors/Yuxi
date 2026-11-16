export const DEFAULT_ALL_AGENT_RESOURCE_KINDS = Object.freeze([
  'tools',
  'knowledges',
  'mcps',
  'skills',
  'subagents'
])

export const MENTION_AGENT_RESOURCE_KINDS = Object.freeze([
  'knowledges',
  'mcps',
  'skills',
  'subagents'
])

/** 统一智能体身份字段，按 agent_id、slug、id 顺序选择规范 ID。 */
export const normalizeAgent = (agent) => {
  const agentId = agent?.agent_id || agent?.slug || agent?.id
  return agentId
    ? { ...agent, id: agentId, agent_id: agentId, slug: agent?.slug || agentId }
    : agent
}

/** 将智能体后端描述转换为下拉选项，并以 backend_id 作为名称兜底。 */
export const normalizeAgentBackendOption = (backend) => ({
  label: backend.name || backend.backend_id,
  value: backend.backend_id
})

export const isDefaultAllAgentResourceKind = (kind) =>
  DEFAULT_ALL_AGENT_RESOURCE_KINDS.includes(kind)

export const isMentionAgentResourceKind = (kind) => MENTION_AGENT_RESOURCE_KINDS.includes(kind)

export const getAgentConfigOptions = (item) => (Array.isArray(item?.options) ? item.options : [])

export const isSingleSelectAgentConfig = (item) =>
  getAgentConfigOptions(item).length > 0 && ['str', 'string', 'select'].includes(item?.type)

export const getAgentConfigOptionValue = (option) => {
  if (typeof option !== 'object' || option === null) return option
  return (
    option.key ||
    option.id ||
    option.value ||
    option.name ||
    option.db_id ||
    option.slug ||
    option.label
  )
}

export const getAgentConfigOptionLabel = (option) => {
  if (typeof option !== 'object' || option === null) return option
  return option.name || option.label || getAgentConfigOptionValue(option)
}

export const getAgentConfigOptionDescription = (option) =>
  typeof option === 'object' && option !== null ? option.description || '' : ''

/** 修改可见选择时保留不可见引用，避免局部取消被误读为清空全部。 */
export const mergeVisibleAgentResourceSelection = (current, available, selected) => {
  const visible = new Set(available.map(String))
  const requested = new Set(selected.map(String))
  const retained = Array.isArray(current)
    ? current.filter((value) => !visible.has(String(value)) || requested.has(String(value)))
    : []
  const retainedKeys = new Set(retained.map(String))
  return [...retained, ...selected.filter((value) => !retainedKeys.has(String(value)))]
}

/** 按各资源的空值契约投影可见选择，子智能体空列表表示使用全部。 */
export const getVisibleAgentResourceSelection = (current, kind, available) => {
  if (
    isDefaultAllAgentResourceKind(kind) &&
    (current === null || (kind === 'subagents' && Array.isArray(current) && current.length === 0))
  ) {
    return [...available]
  }
  if (!Array.isArray(current)) return []
  const visible = new Set(available.map(String))
  return current.filter((value) => visible.has(String(value)))
}
