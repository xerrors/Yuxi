import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { agentApi, databaseApi, mcpApi, skillApi } from '@/apis'
import { handleChatError } from '@/utils/errorHandler'
import { useUserStore } from '@/stores/user'

export const useAgentStore = defineStore(
  'agent',
  () => {
    const userStore = useUserStore()
    // ==================== 状态定义 ====================
    // 智能体相关状态
    const agents = ref([])
    const selectedAgentId = ref(null)
    const defaultAgentId = ref(null)

    // 资源相关状态
    const availableKnowledgeBases = ref([])
    const availableMcps = ref([])
    const availableSkills = ref([])

    // 智能体配置相关状态
    const agentConfig = ref({})
    const originalAgentConfig = ref({})
    const agentConfigs = ref({})
    const selectedAgentConfigId = ref(null)

    // 智能体详情相关状态
    const agentDetails = ref({}) // 存储每个智能体的详细信息（含 configurable_items）

    // 加载状态
    const isLoadingAgents = ref(false)
    const isLoadingConfig = ref(false)
    const isLoadingAgentConfigs = ref(false)
    const isLoadingAgentDetail = ref(false)

    // 错误状态
    const error = ref(null)

    // 初始化状态
    const isInitialized = ref(false)
    const isInitializing = ref(false)

    // ==================== 计算属性 ====================
    const selectedAgent = computed(() =>
      selectedAgentId.value ? agents.value.find((a) => a.id === selectedAgentId.value) : null
    )

    const defaultAgent = computed(() =>
      defaultAgentId.value
        ? agents.value.find((a) => a.id === defaultAgentId.value)
        : agents.value[0]
    )

    const agentsList = computed(() => agents.value)

    const isDefaultAgent = computed(() => selectedAgentId.value === defaultAgentId.value)

    const configurableItems = computed(() => {
      const agentId = selectedAgentId.value
      if (
        !agentId ||
        !agentDetails.value[agentId] ||
        !agentDetails.value[agentId].configurable_items
      ) {
        return {}
      }

      const agentConfigurableItems = agentDetails.value[agentId].configurable_items
      const items = { ...agentConfigurableItems }
      Object.keys(items).forEach((key) => {
        const item = items[key]
        if (item && item.x_oap_ui_config) {
          items[key] = { ...item, ...item.x_oap_ui_config }
          delete items[key].x_oap_ui_config
        }
      })
      return items
    })

    // 工具相关状态
    const availableTools = computed(() => {
      return configurableItems.value.tools?.options || []
    })

    const hasConfigChanges = computed(
      () => JSON.stringify(agentConfig.value) !== JSON.stringify(originalAgentConfig.value)
    )

    const selectedConfigSummary = computed(() => {
      const agentId = selectedAgentId.value
      const configId = selectedAgentConfigId.value
      if (!agentId || !configId) return null
      const list = agentConfigs.value[agentId] || []
      return list.find((c) => c.id === configId) || null
    })

    // ==================== 方法 ====================
    /**
     * 获取可提及的资源（知识库、MCP、Skills）
     */
    async function fetchMentionResources() {
      try {
        const [dbsRes, mcpsRes, skillsRes] = await Promise.all([
          databaseApi.getAccessibleDatabases().catch(() => ({ databases: [] })),
          mcpApi.getMcpServers().catch(() => ({ data: [] })),
          skillApi.listSkills().catch(() => ({ data: [] }))
        ])
        availableKnowledgeBases.value = dbsRes.databases || []
        availableMcps.value = mcpsRes.data || []
        availableSkills.value = skillsRes.data || []
      } catch (e) {
        console.warn('Failed to fetch mention resources:', e)
      }
    }

    /**
     * 初始化 store
     */
    async function initialize() {
      if (isInitialized.value) return

      // 防止并发初始化
      if (isInitializing.value) return
      isInitializing.value = true

      try {
        await fetchAgents()
        await fetchDefaultAgent()
        await fetchMentionResources()

        if (!selectedAgent.value) {
          if (defaultAgent.value) {
            await selectAgent(defaultAgentId.value)
          } else if (agents.value.length > 0) {
            const firstAgentId = agents.value[0].id
            await selectAgent(firstAgentId)
          }
        } else {
          console.log('Condition FALSE: Persisted selected agent is valid. Keeping it.')
          // 确保已缓存的智能体详细信息存在
          if (selectedAgentId.value && !agentDetails.value[selectedAgentId.value]) {
            try {
              await fetchAgentDetail(selectedAgentId.value)
            } catch (err) {
              console.warn(`Failed to fetch agent detail for ${selectedAgentId.value}:`, err)
            }
          }

          if (selectedAgentId.value) {
            try {
              await fetchAgentConfigs(selectedAgentId.value)
              const list = agentConfigs.value[selectedAgentId.value] || []
              const persistedId = selectedAgentConfigId.value
              const persistedItem = persistedId ? list.find((c) => c.id === persistedId) : null
              const defaultItem = list.find((c) => c.is_default) || list[0]
              const pickId = (persistedItem || defaultItem)?.id || null
              selectedAgentConfigId.value = pickId
              if (pickId) {
                await loadAgentConfig(selectedAgentId.value, pickId)
              }
            } catch (err) {
              console.warn(`Failed to init agent configs for ${selectedAgentId.value}:`, err)
            }
          }
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

    /**
     * 获取智能体列表
     */
    async function fetchAgents() {
      isLoadingAgents.value = true
      error.value = null

      try {
        const response = await agentApi.getAgents()
        agents.value = response.agents
      } catch (err) {
        console.error('Failed to fetch agents:', err)
        handleChatError(err, 'fetch')
        error.value = err.message
        throw err
      } finally {
        isLoadingAgents.value = false
      }
    }

    /**
     * 获取单个智能体的详细信息（包含配置选项）
     * @param {string} agentId - 智能体ID
     */
    async function fetchAgentDetail(agentId, forceRefresh = false) {
      if (!agentId) return

      // 如果已经缓存了详细信息且不强制刷新，直接返回
      if (!forceRefresh && agentDetails.value[agentId]) {
        return agentDetails.value[agentId]
      }

      isLoadingAgentDetail.value = true
      error.value = null

      try {
        const response = await agentApi.getAgentDetail(agentId)
        agentDetails.value[agentId] = response
        // availableTools.value[agentId] = response.available_tools || []
        return response
      } catch (err) {
        console.error(`Failed to fetch agent detail for ${agentId}:`, err)
        handleChatError(err, 'fetch')
        error.value = err.message
        throw err
      } finally {
        isLoadingAgentDetail.value = false
      }
    }

    /**
     * 获取默认智能体
     */
    async function fetchDefaultAgent() {
      try {
        const response = await agentApi.getDefaultAgent()
        defaultAgentId.value = response.default_agent_id
      } catch (err) {
        console.error('Failed to fetch default agent:', err)
        handleChatError(err, 'fetch')
        error.value = err.message
      }
    }

    /**
     * 设置默认智能体
     */
    async function setDefaultAgent(agentId) {
      try {
        await agentApi.setDefaultAgent(agentId)
        defaultAgentId.value = agentId
      } catch (err) {
        console.error('Failed to set default agent:', err)
        handleChatError(err, 'save')
        error.value = err.message
        throw err
      }
    }

    /**
     * 选择智能体
     */
    async function selectAgent(agentId) {
      if (agents.value.find((a) => a.id === agentId)) {
        selectedAgentId.value = agentId
        // 清空之前的配置
        agentConfig.value = {}
        originalAgentConfig.value = {}
        selectedAgentConfigId.value = null

        // 并行获取智能体详情和配置列表
        await Promise.all([
          (async () => {
            try {
              await fetchAgentDetail(agentId)
            } catch (err) {
              console.warn(`Failed to fetch agent detail for ${agentId}:`, err)
            }
          })(),
          (async () => {
            try {
              await fetchAgentConfigs(agentId)
              const list = agentConfigs.value[agentId] || []
              const defaultItem = list.find((c) => c.is_default) || list[0]
              selectedAgentConfigId.value = defaultItem ? defaultItem.id : null
              if (selectedAgentConfigId.value) {
                await loadAgentConfig(agentId, selectedAgentConfigId.value)
              }
            } catch (err) {
              console.warn(`Failed to fetch agent configs for ${agentId}:`, err)
            }
          })()
        ])
      }
    }

    /**
     * 加载智能体配置
     */
    async function fetchAgentConfigs(agentId = null) {
      const targetAgentId = agentId || selectedAgentId.value
      if (!targetAgentId) return

      isLoadingAgentConfigs.value = true
      error.value = null

      try {
        const response = await agentApi.getAgentConfigs(targetAgentId)
        agentConfigs.value[targetAgentId] = response.configs || []
      } catch (err) {
        console.error('Failed to load agent configs:', err)
        handleChatError(err, 'load')
        error.value = err.message
        throw err
      } finally {
        isLoadingAgentConfigs.value = false
      }
    }

    async function loadAgentConfig(agentId = null, configId = null) {
      const targetAgentId = agentId || selectedAgentId.value
      const targetConfigId = configId || selectedAgentConfigId.value
      if (!targetAgentId || !targetConfigId) return

      isLoadingConfig.value = true
      error.value = null

      try {
        const response = await agentApi.getAgentConfigProfile(targetAgentId, targetConfigId)
        const configJson = response.config?.config_json || {}
        const contextConfig = configJson.context || configJson
        const loadedConfig = { ...contextConfig }

        // 确保 configurableItems 已加载
        if (!agentDetails.value[targetAgentId]) {
          await fetchAgentDetail(targetAgentId)
        }

        // 使用 configurableItems 中的默认值补全缺失的配置项
        const items = configurableItems.value
        Object.keys(items).forEach((key) => {
          const item = items[key]
          if (loadedConfig[key] === undefined || loadedConfig[key] === null) {
            // 只有当默认值存在时才设置
            if (item.default !== undefined) {
              loadedConfig[key] = item.default
            }
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

        agentConfig.value = loadedConfig
        originalAgentConfig.value = { ...loadedConfig }
      } catch (err) {
        console.error('Failed to load agent config profile:', err)
        handleChatError(err, 'load')
        error.value = err.message
        throw err
      } finally {
        isLoadingConfig.value = false
      }
    }

    async function selectAgentConfig(configId) {
      const targetAgentId = selectedAgentId.value
      if (!targetAgentId || !configId) return
      selectedAgentConfigId.value = configId
      await loadAgentConfig(targetAgentId, configId)
    }

    /**
     * 保存智能体配置
     * @param {Object} options - 额外参数 (e.g., { reload_graph: true })
     */
    async function saveAgentConfig(options = {}) {
      const targetAgentId = selectedAgentId.value
      const targetConfigId = selectedAgentConfigId.value
      if (!targetAgentId || !targetConfigId) return
      if (!userStore.isAdmin) return

      try {
        await agentApi.updateAgentConfigProfile(targetAgentId, targetConfigId, {
          config_json: { context: agentConfig.value }
        })
        originalAgentConfig.value = { ...agentConfig.value }
      } catch (err) {
        console.error('Failed to save agent config:', err)
        handleChatError(err, 'save')
        error.value = err.message
        throw err
      }
    }

    async function createAgentConfigProfile({ name, setDefault = false, fromCurrent = true } = {}) {
      const targetAgentId = selectedAgentId.value
      if (!targetAgentId) return null
      if (!userStore.isAdmin) return null
      if (!name) throw new Error('配置名称不能为空')

      const baseContext = fromCurrent ? { ...agentConfig.value } : {}

      const response = await agentApi.createAgentConfigProfile(targetAgentId, {
        name,
        config_json: { context: baseContext },
        set_default: setDefault
      })

      await fetchAgentConfigs(targetAgentId)
      const created = response?.config
      if (created?.id) {
        await selectAgentConfig(created.id)
      }
      return created
    }

    async function deleteSelectedAgentConfigProfile() {
      const targetAgentId = selectedAgentId.value
      const targetConfigId = selectedAgentConfigId.value
      if (!targetAgentId || !targetConfigId) return
      if (!userStore.isAdmin) return

      await agentApi.deleteAgentConfigProfile(targetAgentId, targetConfigId)
      await fetchAgentConfigs(targetAgentId)
      const list = agentConfigs.value[targetAgentId] || []
      const defaultItem = list.find((c) => c.is_default) || list[0]
      selectedAgentConfigId.value = defaultItem ? defaultItem.id : null
      if (selectedAgentConfigId.value) {
        await loadAgentConfig(targetAgentId, selectedAgentConfigId.value)
      } else {
        agentConfig.value = {}
        originalAgentConfig.value = {}
      }
    }

    async function setSelectedAgentConfigDefault() {
      const targetAgentId = selectedAgentId.value
      const targetConfigId = selectedAgentConfigId.value
      if (!targetAgentId || !targetConfigId) return
      if (!userStore.isAdmin) return

      await agentApi.setAgentConfigDefault(targetAgentId, targetConfigId)
      await fetchAgentConfigs(targetAgentId)
    }

    /**
     * 重置智能体配置
     */
    function resetAgentConfig() {
      agentConfig.value = { ...originalAgentConfig.value }
    }

    /**
     * 更新配置项
     */
    function updateConfigItem(key, value) {
      agentConfig.value[key] = value
    }

    /**
     * 更新智能体配置（支持批量更新）
     */
    function updateAgentConfig(updates) {
      Object.assign(agentConfig.value, updates)
    }

    /**
     * 清除错误状态
     */
    function clearError() {
      error.value = null
    }

    /**
     * 重置 store 状态
     */
    function reset() {
      agents.value = []
      selectedAgentId.value = null
      defaultAgentId.value = null
      availableKnowledgeBases.value = []
      availableMcps.value = []
      availableSkills.value = []
      agentConfig.value = {}
      originalAgentConfig.value = {}
      agentConfigs.value = {}
      selectedAgentConfigId.value = null
      agentDetails.value = {}
      isLoadingAgents.value = false
      isLoadingConfig.value = false
      isLoadingAgentConfigs.value = false
      isLoadingAgentDetail.value = false
      error.value = null
      isInitialized.value = false
      isInitializing.value = false
    }

    return {
      // 状态
      agents,
      selectedAgentId,
      defaultAgentId,
      availableKnowledgeBases,
      availableMcps,
      availableSkills,
      agentConfig,
      originalAgentConfig,
      agentDetails,
      isLoadingAgents,
      isLoadingConfig,
      isLoadingAgentDetail,
      isLoadingAgentConfigs,
      error,
      isInitialized,

      // 计算属性
      selectedAgent,
      defaultAgent,
      agentsList,
      isDefaultAgent,
      configurableItems,
      availableTools,
      hasConfigChanges,
      agentConfigs,
      selectedAgentConfigId,
      selectedConfigSummary,

      // 方法
      initialize,
      fetchAgents,
      fetchAgentDetail,
      fetchDefaultAgent,
      fetchMentionResources,
      setDefaultAgent,
      selectAgent,
      selectAgentConfig,
      fetchAgentConfigs,
      loadAgentConfig,
      saveAgentConfig,
      createAgentConfigProfile,
      deleteSelectedAgentConfigProfile,
      setSelectedAgentConfigDefault,
      resetAgentConfig,
      updateConfigItem,
      updateAgentConfig,
      clearError,
      reset
    }
  },
  {
    // 持久化配置
    persist: {
      key: 'agent-store',
      storage: localStorage,
      pick: ['selectedAgentId', 'selectedAgentConfigId']
    }
  }
)
