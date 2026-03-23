import { computed } from 'vue'

export function useAgentMentionConfig({
  currentAgentState,
  configurableItems,
  agentConfig,
  availableKnowledgeBases,
  availableMcps,
  availableSkills
}) {
  const mentionConfig = computed(() => {
    const rawFiles = currentAgentState.value?.files || {}
    const files = []

    // 处理 files - 兼容字典格式 {"/path/file": {content: [...]}} 和旧数组格式
    if (typeof rawFiles === 'object' && !Array.isArray(rawFiles) && rawFiles !== null) {
      // 新格式：字典格式 {"/attachments/xxx/file.md": {...}}
      Object.entries(rawFiles).forEach(([filePath, fileData]) => {
        files.push({
          path: filePath,
          ...fileData
        })
      })
    } else if (Array.isArray(rawFiles)) {
      // 旧格式：数组格式
      rawFiles.forEach((item) => {
        if (typeof item === 'object' && item !== null) {
          Object.entries(item).forEach(([filePath, fileData]) => {
            files.push({
              path: filePath,
              ...fileData
            })
          })
        }
      })
    }

    const configItems = configurableItems.value || {}
    const currentConfig = agentConfig.value || {}
    const allowedKbNames = new Set()
    const allowedMcpNames = new Set()
    const allowedSkillNames = new Set()
    const allowedSubagentNames = new Set()
    const subagentOptionMap = new Map()

    Object.entries(configItems).forEach(([key, item]) => {
      const kind = item?.template_metadata?.kind
      const val = currentConfig[key]

      if (Array.isArray(val)) {
        if (kind === 'knowledges') {
          val.forEach((v) => allowedKbNames.add(v))
        } else if (kind === 'mcps') {
          val.forEach((v) => allowedMcpNames.add(v))
        } else if (kind === 'skills' || key === 'skills') {
          val.forEach((v) => allowedSkillNames.add(v))
        } else if (kind === 'subagents' || key === 'subagents') {
          val.forEach((v) => allowedSubagentNames.add(v))
        }
      }

      if (kind === 'subagents' || key === 'subagents') {
        const options = Array.isArray(item?.options) ? item.options : []
        options.forEach((option) => {
          if (option == null) return

          const value =
            typeof option === 'object'
              ? option.id || option.value || option.name || option.label
              : option
          if (!value) return

          subagentOptionMap.set(value, {
            id: value,
            name: typeof option === 'object' ? option.name || option.label || value : value,
            description: typeof option === 'object' ? option.description || '' : ''
          })
        })
      }
    })

    const knowledgeBases = availableKnowledgeBases.value.filter((kb) => allowedKbNames.has(kb.name))
    const mcps = availableMcps.value.filter((mcp) => allowedMcpNames.has(mcp.name))
    const skills = availableSkills.value.filter((skill) => {
      const skillName = skill.name || ''
      const skillSlug = skill.slug || ''
      return allowedSkillNames.has(skillName) || allowedSkillNames.has(skillSlug)
    })
    const subagents = Array.from(allowedSubagentNames)
      .filter((name) => !!name)
      .map(
        (name) =>
          subagentOptionMap.get(name) || {
            id: name,
            name,
            description: ''
          }
      )

    if (
      !files.length &&
      !knowledgeBases.length &&
      !mcps.length &&
      !skills.length &&
      !subagents.length
    )
      return null

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
