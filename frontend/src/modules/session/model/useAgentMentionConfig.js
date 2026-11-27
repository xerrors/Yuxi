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
  ['plugins', 'plugins'],
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

  if (kind === 'plugins') {
    return { id: value, name, description }
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
  sessionResources,
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
    const directMcpSlugs = new Set(
      getAgentConfigOptions(configItems.mcps).map(getAgentConfigOptionValue)
    )
    const resources = {}
    const sessionOptions = sessionResources?.value
    for (const [field, output] of MENTION_FIELDS) {
      let selectedOptions
      if (sessionOptions == null) {
        const options = getAgentConfigOptions(configItems[field])
        const byValue = new Map(options.map((option) => [getAgentConfigOptionValue(option), option]))
        const selected = getVisibleAgentResourceSelection(
          currentConfig[field], configItems[field], [...byValue.keys()]
        )
        selectedOptions = [...new Set(selected)].map((value) => byValue.get(value))
      } else {
        selectedOptions = sessionOptions[field] || []
      }
      resources[output] = selectedOptions
        .map((option) => normalizeMentionResource(option, field))
        .filter(Boolean)
    }

    return {
      files,
      ...resources,
      mcps: (mcpServers?.value || [])
        .filter((server) => server.available && directMcpSlugs.has(server.slug))
        .map(({ slug, name, description }) => ({ slug, name, description: description || '' })),
      agents: (agents?.value || []).filter((agent) => agent.can_run && agent.agent_id)
    }
  })

  return {
    mentionConfig
  }
}
