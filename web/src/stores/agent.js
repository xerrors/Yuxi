import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { agentApi, databaseApi, toolApi } from '@/apis'
import { isDefaultAllAgentResourceField, normalizeAgent } from '@/utils/agentConfigUtils'
import { handleChatError } from '@/utils/errorHandler'

export const BUILTIN_AGENT_ID = 'default-chatbot'

export function isBuiltinAgent(agent) {
  return agent?.is_builtin || agent?.id === BUILTIN_AGENT_ID || agent?.slug === BUILTIN_AGENT_ID
}

function sortAgents(agents) {
  return [...agents].sort((a, b) => {
    if (isBuiltinAgent(a) !== isBuiltinAgent(b)) return isBuiltinAgent(a) ? -1 : 1
    return String(a.name || a.id).localeCompare(String(b.name || b.id), 'zh-CN')
  })
}

function getPreferredAgentId(agents, persistedId) {
  const chatAgents = agents.filter((agent) => !agent.is_subagent)
  if (persistedId && chatAgents.some((agent) => agent.id === persistedId)) return persistedId
  return chatAgents.find(isBuiltinAgent)?.id || chatAgents[0]?.id || null
}

function extractContext(agent) {
  const configJson = agent?.config_json || {}
  return { ...(configJson.context || configJson || {}) }
}

/** 将后端声明的 UI 覆盖应用到配置项。 */
function getConfigurableItems(agent) {
  const items = { ...(agent?.configurable_items || {}) }
  Object.keys(items).forEach((key) => {
    const item = items[key]
    if (item?.x_oap_ui_config) {
      items[key] = { ...item, ...item.x_oap_ui_config }
      delete items[key].x_oap_ui_config
    }
  })
  return items
}

export const useAgentStore = defineStore(
  'agent',
  () => {
    const agents = ref([])
    const selectedAgentId = ref(null)

    const availableKnowledgeBases = ref([])
    // 完整工具元数据（含 buildin / knowledge 等全部分类的 display_name），用于工具名称展示映射
    const toolMetadata = ref([])

    const agentConfig = ref({})
    const originalAgentConfig = ref({})
    const agentDetails = ref({})

    const isLoadingAgents = ref(false)
    const isLoadingConfig = ref(false)
    const isLoadingAgentDetail = ref(false)
    const error = ref(null)
    const isInitialized = ref(false)
    const isInitializing = ref(false)

    const selectedAgent = computed(() => {
      const agentId = selectedAgentId.value
      return agentId
        ? agentDetails.value[agentId] || agents.value.find((a) => a.id === agentId) || null
        : null
    })

    const agentsList = computed(() => agents.value)

    const configurableItems = computed(() => getConfigurableItems(selectedAgent.value))

    const availableTools = computed(() => configurableItems.value.tools?.options || [])
    const changedAgentConfig = computed(() =>
      Object.fromEntries(
        Object.entries(agentConfig.value).filter(
          ([key, value]) => JSON.stringify(value) !== JSON.stringify(originalAgentConfig.value[key])
        )
      )
    )
    const hasConfigChanges = computed(() => Object.keys(changedAgentConfig.value).length > 0)

    /** 加载消息来源展示所需的知识库名称。 */
    async function fetchAccessibleKnowledgeBases() {
      try {
        const response = await databaseApi.getAccessibleDatabases()
        availableKnowledgeBases.value = response.databases || []
      } catch (e) {
        console.warn('Failed to fetch accessible knowledge bases:', e)
        availableKnowledgeBases.value = []
      }
    }

    async function fetchToolMetadata() {
      try {
        const result = await toolApi.getTools()
        toolMetadata.value = result?.data || []
      } catch (e) {
        console.warn('Failed to fetch tool metadata:', e)
        toolMetadata.value = []
      }
    }

    async function initialize() {
      if (isInitialized.value || isInitializing.value) return
      isInitializing.value = true
      try {
        await Promise.all([fetchAgents(), fetchAccessibleKnowledgeBases(), fetchToolMetadata()])

        const targetAgentId = getPreferredAgentId(agents.value, selectedAgentId.value)
        if (targetAgentId) {
          await selectAgent(targetAgentId)
        }
        isInitialized.value = true
      } catch (err) {
        console.error('Failed to initialize agent store:', err)
        handleChatError(err, 'initialize')
        error.value = err.message
      } finally {
        isInitializing.value = false
      }
    }

    async function fetchAgents({ includeSubagents = false } = {}) {
      isLoadingAgents.value = true
      error.value = null
      try {
        const response = await agentApi.getAgents({ includeSubagents })
        agents.value = sortAgents((response.agents || []).map(normalizeAgent))
      } catch (err) {
        console.error('Failed to fetch agents:', err)
        handleChatError(err, 'fetch')
        error.value = err.message
        throw err
      } finally {
        isLoadingAgents.value = false
      }
    }

    function applyConfigDefaults(loadedConfig, configItems) {
      Object.entries(configItems).forEach(([key, item]) => {
        const usesAllByDefault = isDefaultAllAgentResourceField(key)
        if (loadedConfig[key] === undefined || (loadedConfig[key] === null && !usesAllByDefault)) {
          if (item.default !== undefined) loadedConfig[key] = item.default
        }
        if (
          loadedConfig[key] !== undefined &&
          loadedConfig[key] !== null &&
          loadedConfig[key] !== '' &&
          (item?.type === 'number' || item?.type === 'int' || item?.type === 'float')
        ) {
          const numericValue = Number(loadedConfig[key])
          if (!Number.isNaN(numericValue)) {
            loadedConfig[key] = item.type === 'int' ? Math.trunc(numericValue) : numericValue
          }
        }
      })
      return loadedConfig
    }

    async function fetchAgentDetail(agentId, forceRefresh = false) {
      if (!agentId) return null
      if (!forceRefresh && agentDetails.value[agentId]) return agentDetails.value[agentId]

      isLoadingAgentDetail.value = true
      error.value = null
      try {
        const response = await agentApi.getAgentDetail(agentId)
        const agent = normalizeAgent(response.agent || response)
        agentDetails.value[agent.id] = agent
        return agent
      } catch (err) {
        console.error(`Failed to fetch agent detail for ${agentId}:`, err)
        handleChatError(err, 'fetch')
        error.value = err.message
        throw err
      } finally {
        isLoadingAgentDetail.value = false
      }
    }

    async function selectAgent(agentId, { allowSubagent = false } = {}) {
      if (!agentId) return
      let knownAgent = agentDetails.value[agentId] || agents.value.find((a) => a.id === agentId)
      if (!knownAgent) {
        knownAgent = await fetchAgentDetail(agentId)
      }
      if (knownAgent?.is_subagent && !allowSubagent) return
      isLoadingConfig.value = true
      try {
        const detail = agentDetails.value[agentId] || (await fetchAgentDetail(agentId))
        const loadedConfig = applyConfigDefaults(
          extractContext(detail),
          getConfigurableItems(detail)
        )
        selectedAgentId.value = agentId
        agentConfig.value = loadedConfig
        originalAgentConfig.value = { ...loadedConfig }
      } finally {
        isLoadingConfig.value = false
      }
    }

    async function saveAgentConfig() {
      const targetAgentId = selectedAgentId.value
      if (!targetAgentId) return
      try {
        await updateAgentProfile(targetAgentId, {
          config_json: { context: changedAgentConfig.value }
        })
      } catch (err) {
        console.error('Failed to save agent config:', err)
        handleChatError(err, 'save')
        error.value = err.message
        throw err
      }
    }

    async function createAgent(payload) {
      const response = await agentApi.createAgent(payload)
      const created = normalizeAgent(response.agent)
      if (created?.id) {
        agentDetails.value[created.id] = created
        agents.value = sortAgents([
          created,
          ...agents.value.filter((item) => item.id !== created.id)
        ])
        if (!created.is_subagent) await selectAgent(created.id)
      }
      return created
    }

    async function updateAgentProfile(agentId, payload) {
      const response = await agentApi.updateAgent(agentId, payload)
      const updated = normalizeAgent(response.agent)
      agentDetails.value[updated.id] = updated
      const index = agents.value.findIndex((item) => item.id === updated.id)
      if (index >= 0) agents.value.splice(index, 1, updated)
      if (selectedAgentId.value === updated.id) {
        const loadedConfig = applyConfigDefaults(
          extractContext(updated),
          getConfigurableItems(updated)
        )
        agentConfig.value = loadedConfig
        originalAgentConfig.value = { ...loadedConfig }
      }
      return updated
    }

    async function deleteAgent(agentId) {
      await agentApi.deleteAgent(agentId)
      agents.value = agents.value.filter((item) => item.id !== agentId)
      delete agentDetails.value[agentId]
      if (selectedAgentId.value === agentId) {
        selectedAgentId.value = null
        agentConfig.value = {}
        originalAgentConfig.value = {}
        const nextAgentId = getPreferredAgentId(agents.value)
        if (nextAgentId) await selectAgent(nextAgentId)
      }
    }

    function resetAgentConfig() {
      agentConfig.value = { ...originalAgentConfig.value }
    }

    function updateAgentConfig(updates) {
      Object.assign(agentConfig.value, updates)
    }

    function reset() {
      agents.value = []
      selectedAgentId.value = null
      availableKnowledgeBases.value = []
      toolMetadata.value = []
      agentConfig.value = {}
      originalAgentConfig.value = {}
      agentDetails.value = {}
      isLoadingAgents.value = false
      isLoadingConfig.value = false
      isLoadingAgentDetail.value = false
      error.value = null
      isInitialized.value = false
      isInitializing.value = false
    }

    return {
      agents,
      selectedAgentId,
      availableKnowledgeBases,
      toolMetadata,
      agentConfig,
      originalAgentConfig,
      agentDetails,
      isLoadingAgents,
      isLoadingConfig,
      isLoadingAgentDetail,
      error,
      isInitialized,
      selectedAgent,
      agentsList,
      configurableItems,
      availableTools,
      hasConfigChanges,
      changedAgentConfig,
      initialize,
      fetchAgents,
      fetchAgentDetail,
      selectAgent,
      saveAgentConfig,
      createAgent,
      updateAgentProfile,
      deleteAgent,
      resetAgentConfig,
      updateAgentConfig,
      reset
    }
  },
  {
    persist: {
      key: 'agent-store',
      storage: localStorage,
      pick: ['selectedAgentId']
    }
  }
)
