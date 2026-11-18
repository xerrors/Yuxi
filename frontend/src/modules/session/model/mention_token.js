export const mentionTypePrefixMap = {
  file: 'file',
  knowledge: 'knowledge',
  mcp: 'mcp',
  plugin: 'plugin',
  skill: 'skill',
  agent: 'agent',
  session: 'session'
}

export const formatMentionToken = (type, value) => {
  const prefix = mentionTypePrefixMap[type] || type
  const rawValue = String(value ?? '')
  if (/\s|["\\]/.test(rawValue)) {
    return `@${prefix}:"${rawValue.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`
  }
  return `@${prefix}:${rawValue}`
}
