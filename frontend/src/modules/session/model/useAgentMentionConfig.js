import { computed } from 'vue'
import {
  getAgentConfigOptionDescription,
  getAgentConfigOptionLabel,
  getAgentConfigOptions,
  getAgentConfigOptionValue,
  getVisibleAgentResourceSelection
} from '@/modules/agents'

const MENTION_FIELDS = [
  ['knowledges', 'knowledgeBases'],
  ['skills', 'skills']
]

const normalizeMentionResource = (option, kind) => {
  const value = getAgentConfigOptionValue(option)
  if (!value) return null

  const name = getAgentConfigOptionLabel(option) || value
  const description = getAgentConfigOptionDescription(option)

  if (kind === 'knowledges') {
    return {
      kb_id: value,
      name,
      description
    }
  }

  return {
    slug: value,
    name,
    description
  }
}

export function useAgentMentionConfig({
  currentThreadAttachments,
  configurableItems,
  agentConfig,
  mcpServers,
  agents
}) {
  const mentionConfig = computed(() => {
    const files = []
    const seenPaths = new Set()

    const pushFile = (entry) => {
      const path = entry?.path || ''
      if (!path || seenPaths.has(path)) return
      seenPaths.add(path)
      files.push(entry)
    }

    const attachments = Array.isArray(currentThreadAttachments?.value)
      ? currentThreadAttachments.value
      : []
    attachments.forEach((attachment) => {
      const path = attachment?.path || ''
      if (!path) return
      pushFile({
        path,
        size: attachment.file_size,
        modified_at: attachment.uploaded_at,
        artifact_url: attachment.artifact_url,
        file_name: attachment.file_name,
        status: attachment.status
      })
    })

    const configItems = configurableItems.value || {}
    const currentConfig = agentConfig.value || {}
    const resources = {}
    MENTION_FIELDS.forEach(([field, output]) => {
      const options = getAgentConfigOptions(configItems[field])
      const byValue = new Map(options.map((option) => [getAgentConfigOptionValue(option), option]))
      const selected = getVisibleAgentResourceSelection(currentConfig[field], configItems[field], [
        ...byValue.keys()
      ])
      resources[output] = [...new Set(selected)]
        .map((value) => normalizeMentionResource(byValue.get(value), field))
        .filter(Boolean)
    })

    return {
      files,
      ...resources,
      mcps: (mcpServers?.value || [])
        .filter((server) => server.available)
        .map(({ slug, name, description }) => ({ slug, name, description: description || '' })),
      agents: (agents?.value || []).filter((agent) => agent.can_run && agent.agent_id)
    }
  })

  return {
    mentionConfig
  }
}
