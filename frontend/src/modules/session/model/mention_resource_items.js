import { formatMentionToken } from './mention_token.js'

const toResourceItem = (type, { value, label, extra = {} } = {}) => {
  if (!value && !label) return null
  const resolvedValue = value || label
  return {
    value: resolvedValue,
    label: label || resolvedValue,
    type,
    tokenLabel: formatMentionToken(type, label || resolvedValue),
    ...extra
  }
}

export const buildMentionResourceItems = (mention = {}) => {
  const { knowledgeBases = [], mcps = [], plugins = [], skills = [], agents = [] } = mention

  return {
    agents: agents
      .map((agent) =>
        toResourceItem('agent', {
          value: agent.agent_id,
          label: agent.name || agent.agent_id,
          extra: { description: agent.description || '', icon: agent.icon || '' }
        })
      )
      .filter(Boolean),
    knowledgeBases: knowledgeBases
      .map((kb) =>
        toResourceItem('knowledge', {
          value: kb.name,
          label: kb.name,
          extra: { description: kb.description || '', resourceId: kb.kb_id, kbType: kb.kb_type }
        })
      )
      .filter(Boolean),
    mcps: mcps
      .map((m) =>
        toResourceItem('mcp', {
          value: m.slug,
          label: m.name,
          extra: { description: m.description || '' }
        })
      )
      .filter(Boolean),
    plugins: plugins
      .map((plugin) =>
        toResourceItem('plugin', {
          value: plugin.id,
          label: plugin.name,
          extra: { description: plugin.description || '' }
        })
      )
      .filter(Boolean),
    skills: skills
      .map((s) =>
        toResourceItem('skill', {
          value: s.slug,
          label: s.name,
          extra: { description: s.description || '' }
        })
      )
      .filter(Boolean)
  }
}
