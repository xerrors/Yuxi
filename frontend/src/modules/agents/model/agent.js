import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { agentApi, knowledgeBaseApi, toolApi } from '@/apis'
import { handleChatError } from '@/shared/lib/errorHandler'
import { normalizeAgentConfigurableItems } from '@/modules/agents/model/agentConfigUtils'

export const BUILTIN_AGENT_ID = 'default-chatbot'

export function isBuiltinAgent(agent) {
  return agent?.is_builtin || agent?.agent_id === BUILTIN_AGENT_ID
}

function sortAgents(agents) {
  return [...agents].sort((a, b) => {
    if (!!a.is_default !== !!b.is_default) return a.is_default ? -1 : 1
    if (isBuiltinAgent(a) !== isBuiltinAgent(b)) return isBuiltinAgent(a) ? -1 : 1
    return String(a.name || a.agent_id).localeCompare(String(b.name || b.agent_id), 'zh-CN')
  })
}

function getPreferredAgentId(agents, persistedId) {
  const chatAgents = agents.filter((agent) => agent.can_run)
  if (persistedId && chatAgents.some((agent) => agent.agent_id === persistedId)) return persistedId
  return chatAgents.find((agent) => agent.is_default)?.agent_id || chatAgents[0]?.agent_id || null
}

function extractContext(agent) {
  const configJson = agent?.config_json || {}
  return { ...(configJson.context || configJson || {}) }
}

export const useAgentStore = defineStore(
  'agent',
  () => {
    const agents = ref([])
    const selectedAgentId = ref(null)

    const availableKnowledgeBases = ref([])
    // 完整工具元数据（含 builtin / knowledge 等全部分类的 display_name），用于工具名称展示映射
    const toolMetadata = ref([])

    const agentConfig = ref({})
    const originalAgentConfig = ref({})
    const agentDetails = ref({})

    const isLoadingAgents = ref(false)
    const isLoadingConfig = ref(false)
    const isLoadingAgentDetail = ref(false)
    const error = ref(null)
    const isInitialized = ref(false)
    let generation = 0

    const selectedAgent = computed(() => {
      const agentId = selectedAgentId.value
      return agentId
        ? agentDetails.value[agentId] || agents.value.find((a) => a.agent_id === agentId) || null
        : null
    })

    const agentsList = computed(() => agents.value)

    const configurableItems = computed(() =>
      normalizeAgentConfigurableItems(selectedAgent.value?.configurable_items)
    )

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
      const currentGeneration = generation
      try {
        const response = await knowledgeBaseApi.getAccessibleKnowledgeBases()
        if (currentGeneration !== generation) return
        availableKnowledgeBases.value = response.knowledge_bases || []
      } catch (e) {
        if (currentGeneration !== generation) return
        console.warn('Failed to fetch accessible knowledge bases:', e)
        availableKnowledgeBases.value = []
      }
    }

    async function fetchToolMetadata() {
      const currentGeneration = generation
      try {
        const result = await toolApi.getTools()
        if (currentGeneration !== generation) return
        toolMetadata.value = result?.data || []
      } catch (e) {
        if (currentGeneration !== generation) return
        console.warn('Failed to fetch tool metadata:', e)
        toolMetadata.value = []
      }
    }

    let initializationPromise = null

    /** 目录初始化的所有调用者等待同一个准备过程。 */
    function initialize() {
      if (isInitialized.value) return Promise.resolve()
      if (initializationPromise) return initializationPromise
      const currentGeneration = generation
      initializationPromise = initializeDirectory(currentGeneration).finally(() => {
        if (currentGeneration === generation) initializationPromise = null
      })
      return initializationPromise
    }

    /** 加载目录并准备当前智能体的详情。 */
    async function initializeDirectory(currentGeneration) {
      try {
        await Promise.all([fetchAgents(), fetchAccessibleKnowledgeBases(), fetchToolMetadata()])

        if (currentGeneration !== generation) return
        const targetAgentId = getPreferredAgentId(agents.value, selectedAgentId.value)
        if (targetAgentId) {
          await selectAgent(targetAgentId)
        }
        if (currentGeneration === generation) isInitialized.value = true
      } catch (err) {
        if (currentGeneration !== generation) return
        console.error('Failed to initialize agent store:', err)
        handleChatError(err, 'initialize')
        error.value = err.message
      }
    }

    async function fetchAgents() {
      const currentGeneration = generation
      isLoadingAgents.value = true
      error.value = null
      try {
        const response = await agentApi.getAgents()
        if (currentGeneration !== generation) return
        agents.value = sortAgents(response.agents || [])
      } catch (err) {
        if (currentGeneration !== generation) return
        console.error('Failed to fetch agents:', err)
        handleChatError(err, 'fetch')
        error.value = err.message
        throw err
      } finally {
        if (currentGeneration === generation) isLoadingAgents.value = false
      }
    }

    function applyConfigDefaults(loadedConfig, configItems) {
      Object.entries(configItems).forEach(([key, item]) => {
        if (loadedConfig[key] === undefined || (loadedConfig[key] === null && !item.supports_all)) {
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

    async function fetchAgentDetail(agentId, forceRefresh = false, { notify = true } = {}) {
      const currentGeneration = generation
      if (!agentId) return null
      if (!forceRefresh && agentDetails.value[agentId]) return agentDetails.value[agentId]

      isLoadingAgentDetail.value = true
      error.value = null
      try {
        const response = await agentApi.getAgentDetail(agentId)
        if (currentGeneration !== generation) return null
        const agent = response.agent || response
        agentDetails.value[agent.agent_id] = agent
        return agent
      } catch (err) {
        if (currentGeneration !== generation) return null
        console.error(`Failed to fetch agent detail for ${agentId}:`, err)
        if (notify) handleChatError(err, 'fetch')
        error.value = err.message
        throw err
      } finally {
        if (currentGeneration === generation) isLoadingAgentDetail.value = false
      }
    }

    async function selectAgent(agentId) {
      const currentGeneration = generation
      if (!agentId) return
      isLoadingConfig.value = true
      try {
        const detail = agentDetails.value[agentId] || (await fetchAgentDetail(agentId))
        if (currentGeneration !== generation) return
        const loadedConfig = applyConfigDefaults(
          extractContext(detail),
          normalizeAgentConfigurableItems(detail?.configurable_items)
        )
        selectedAgentId.value = agentId
        agentConfig.value = loadedConfig
        originalAgentConfig.value = { ...loadedConfig }
      } finally {
        if (currentGeneration === generation) isLoadingConfig.value = false
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

    async function createAgent(payload, skillFile = null) {
      const response = await agentApi.createAgent(payload, skillFile)
      const created = response.agent
      if (created?.agent_id) {
        agentDetails.value[created.agent_id] = created
        agents.value = sortAgents([
          created,
          ...agents.value.filter((item) => item.agent_id !== created.agent_id)
        ])
        await selectAgent(created.agent_id)
      }
      return created
    }

    async function updateAgentProfile(agentId, payload) {
      const response = await agentApi.updateAgent(agentId, payload)
      const updated = response.agent
      agentDetails.value[updated.agent_id] = updated
      const index = agents.value.findIndex((item) => item.agent_id === updated.agent_id)
      if (index >= 0) agents.value.splice(index, 1, updated)
      if (selectedAgentId.value === updated.agent_id) {
        const loadedConfig = applyConfigDefaults(
          extractContext(updated),
          normalizeAgentConfigurableItems(updated?.configurable_items)
        )
        agentConfig.value = loadedConfig
        originalAgentConfig.value = { ...loadedConfig }
      }
      return updated
    }

    async function deleteAgent(agentId) {
      await agentApi.deleteAgent(agentId)
      agents.value = agents.value.filter((item) => item.agent_id !== agentId)
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

    /** 用目标后端配置项重建当前草稿及其默认值。 */
    function replaceAgentConfigDraft(config, configItems) {
      agentConfig.value = applyConfigDefaults({ ...config }, normalizeAgentConfigurableItems(configItems))
    }

    function reset() {
      // 身份重置使旧账户的异步响应失效，后续入口开始新的初始化。
      generation += 1
      initializationPromise = null
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
      replaceAgentConfigDraft,
      reset
    }
  },
  {
    persist: {
      key: 'agent-store',
      storage: typeof localStorage === 'undefined' ? undefined : localStorage,
      pick: ['selectedAgentId']
    }
  }
)
