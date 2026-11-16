import { computed } from 'vue'
import {
  getAgentConfigOptionDescription,
  getAgentConfigOptionLabel,
  getAgentConfigOptions,
  getAgentConfigOptionValue,
  isDefaultAllAgentResourceKind,
  isMentionAgentResourceKind
} from '@/utils/agentConfigUtils'

const createResourceMap = (createValue) => ({
  knowledges: createValue(),
  mcps: createValue(),
  skills: createValue(),
  subagents: createValue()
})

const getMentionResourceKind = (key, kind) => {
  if (isMentionAgentResourceKind(kind)) return kind
  if (isMentionAgentResourceKind(key)) return key
  return null
}

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

  if (kind === 'subagents') {
    return {
      id: value,
      slug: typeof option === 'object' && option !== null ? option.slug || value : value,
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
  currentAgentState,
  currentThreadAttachments,
  configurableItems,
  agentConfig
}) {
  const mentionConfig = computed(() => {
    const rawFiles = currentAgentState.value?.files || {}
    const files = []
    const seenPaths = new Set()

    const pushFile = (entry) => {
      const path = entry?.path || ''
      if (!path || seenPaths.has(path)) return
      seenPaths.add(path)
      files.push(entry)
    }

    if (typeof rawFiles === 'object' && !Array.isArray(rawFiles) && rawFiles !== null) {
      Object.entries(rawFiles).forEach(([filePath, fileData]) => {
        pushFile({
          path: filePath,
          ...fileData
        })
      })
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
    const includeAllByKind = createResourceMap(() => false)
    const selectedByKind = createResourceMap(() => new Set())
    const optionsByKind = createResourceMap(() => new Map())
    const resourceItems = []

    Object.entries(configItems).forEach(([key, item]) => {
      const kind = getMentionResourceKind(key, item?.kind)
      if (!kind) return

      resourceItems.push({ kind, item })
      const val = currentConfig[key]
      if (val === null && isDefaultAllAgentResourceKind(kind)) {
        includeAllByKind[kind] = true
      } else if (Array.isArray(val)) {
        val.forEach((value) => selectedByKind[kind].add(value))
      }
    })

    resourceItems.forEach(({ kind, item }) => {
      const selectedValues = selectedByKind[kind]
      if (!includeAllByKind[kind] && !selectedValues.size) return

      getAgentConfigOptions(item).forEach((option) => {
        const value = getAgentConfigOptionValue(option)
        if (!value || (!includeAllByKind[kind] && !selectedValues.has(value))) return

        const normalized = normalizeMentionResource(option, kind)
        if (normalized) optionsByKind[kind].set(value, normalized)
      })
    })

    const selectOptions = (kind) => {
      const result = []
      const optionMap = optionsByKind[kind]

      if (includeAllByKind[kind]) {
        optionMap.forEach((option) => result.push(option))
        return result
      }

      selectedByKind[kind].forEach((value) => {
        const option = optionMap.get(value)
        if (option) result.push(option)
      })
      return result
    }

    const knowledgeBases = selectOptions('knowledges')
    const mcps = selectOptions('mcps')
    const skills = selectOptions('skills')
    const subagents = selectOptions('subagents')

    return {
      files,
      knowledgeBases,
      mcps,
      skills,
      subagents
    }
  })

  return {
    mentionConfig
  }
}
