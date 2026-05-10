<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  Globe,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Trash2,
  CheckCircle2,
  Edit3
} from 'lucide-vue-next'

import { modelProviderApi } from '@/apis/system_api'
import { modelIcons } from '@/utils/modelIcon'
import PageHeader from '@/components/shared/PageHeader.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import InfoCard from '@/components/shared/InfoCard.vue'
import ExtensionCardGrid from '@/components/extensions/ExtensionCardGrid.vue'

// ============ State ============
const loading = ref(false)
const remoteLoading = ref(false)
const saving = ref(false)
const providers = ref([])
const searchQuery = ref('')

// Provider form state
const showProviderModal = ref(false)
const editingProviderId = ref(null) // null = creating, string = editing
const providerForm = reactive({
  provider_id: '',
  display_name: '',
  provider_type: 'openai',
  default_protocol: 'openai_compatible',
  base_url: '',
  embedding_base_url: '',
  rerank_base_url: '',
  models_endpoint: '/models',
  embedding_models_endpoint: '/embeddings/models',
  rerank_models_endpoint: '',
  api_key_env: '',
  api_key: '',
  capabilities: ['chat'],
  is_enabled: true,
  headers_text: '{}',
  extra_text: '{}'
})

// Model form state
const showModelModal = ref(false)
const isCreating = ref(false) // true=手动添加新模型，false=编辑已有模型
const editingModel = ref({
  id: '',
  display_name: '',
  type: 'chat',
  source: 'remote',
  protocol_override: null,
  base_url_override: null,
  context_length: null,
  dimension: null,
  batch_size: null,
  supported_parameters: [],
  extra: {}
})

// Models modal state (per provider)
const showModelsModal = ref(false)
const currentProviderForModels = ref(null)

// Remote models per provider
const remoteModelsMap = ref({})

// Remote model loading state per provider
const remoteModelsLoaded = ref({})

// Remote model search state per provider
const remoteModelSearch = ref({})
const remoteModelTypeFilter = ref({})

// ============ Computed ============
const filteredProviders = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  const filtered = keyword
    ? providers.value.filter(
        (p) =>
          p.provider_id.toLowerCase().includes(keyword) ||
          p.display_name.toLowerCase().includes(keyword)
      )
    : providers.value
  return [...filtered].sort((a, b) => {
    if (a.is_enabled !== b.is_enabled) return a.is_enabled ? -1 : 1
    if (a.is_enabled && b.is_enabled && a.credential_status !== b.credential_status) {
      return a.credential_status === 'warning' ? 1 : -1
    }
    return a.provider_id.localeCompare(b.provider_id)
  })
})

const providerStats = computed(() => {
  let enabled = 0,
    warning = 0,
    models = 0
  for (const p of providers.value) {
    if (p.is_enabled) {
      enabled++
      if (p.credential_status === 'warning') warning++
    }
    models += p.enabled_models?.length || 0
  }
  return { total: providers.value.length, enabled, warning, models }
})

// ============ Helpers ============
const getProviderIcon = (provider) => {
  const providerId = provider?.provider_id?.toLowerCase()
  const providerType = provider?.provider_type?.toLowerCase()
  return modelIcons[providerId] || modelIcons[providerType] || modelIcons.default
}

const getIconUrl = (icon) => {
  if (!icon) return modelIcons.default
  if (typeof icon === 'string') return icon
  if (icon instanceof URL || icon?.default) return icon.default || icon
  return modelIcons.default
}

const formatContextLength = (len) => {
  if (!len) return '-'
  if (len >= 1000000) return `${(len / 1000000).toFixed(1)}M`
  if (len >= 1000) return `${(len / 1000).toFixed(0)}K`
  return len.toString()
}

const formatMtokenPrice = (pricing) => {
  if (!pricing) return null
  const prompt = parseFloat(pricing.prompt || pricing.prompt_price || 0)
  const completion = parseFloat(pricing.completion || pricing.completion_price || 0)
  if (prompt < 0 || completion < 0) return null
  if (prompt === 0 && completion === 0) return null
  return {
    prompt: prompt * 100000,
    completion: completion * 100000
  }
}

const formatPriceDisplay = (pricing) => {
  const p = formatMtokenPrice(pricing)
  if (!p) return null
  return `$${p.prompt.toFixed(2)} / $${p.completion.toFixed(2)}`
}

const getModelDisplayName = (model) => {
  return model.name || model.display_name || model.id
}

const getModelId = (model) => {
  return model.id
}

const getInputModalities = (model) => {
  if (!model) return []
  if (model.input_modalities) return model.input_modalities
  if (model.architecture?.input_modalities) return model.architecture.input_modalities
  if (model.raw_metadata?.architecture?.input_modalities)
    return model.raw_metadata.architecture.input_modalities
  return []
}

const remoteIdsMap = computed(() => {
  const map = {}
  for (const [providerId, models] of Object.entries(remoteModelsMap.value)) {
    map[providerId] = new Set(models.map((m) => m.id))
  }
  return map
})

const isModelStale = (model, providerId) => {
  if (model.source === 'manual') return false
  if (!remoteModelsLoaded.value[providerId]) return false
  return model.enabled && !remoteIdsMap.value[providerId]?.has(model.id)
}

// Remote models filtered by search query per provider
const filteredRemoteModels = computed(() => {
  if (!currentProviderForModels.value) return []
  const providerId = currentProviderForModels.value.provider_id
  const query = (remoteModelSearch.value[providerId] || '').trim().toLowerCase()
  const typeFilter = remoteModelTypeFilter.value[providerId] || 'all'
  const models = remoteModelsMap.value[providerId] || []
  return models.filter((m) => {
    const matchesType = typeFilter === 'all' || (m.type || 'chat') === typeFilter
    const matchesQuery = !query || m.id.toLowerCase().includes(query)
    return matchesType && matchesQuery
  })
})

const remoteModelTypeOptions = computed(() => {
  if (!currentProviderForModels.value) return [{ label: '全部', value: 'all' }]
  const providerId = currentProviderForModels.value.provider_id
  const models = remoteModelsMap.value[providerId] || []
  const counts = models.reduce((acc, model) => {
    const type = model.type || 'chat'
    acc[type] = (acc[type] || 0) + 1
    return acc
  }, {})
  return [
    { label: `全部 ${models.length}`, value: 'all' },
    { label: `Chat ${counts.chat || 0}`, value: 'chat' },
    { label: `Embedding ${counts.embedding || 0}`, value: 'embedding' },
    { label: `Rerank ${counts.rerank || 0}`, value: 'rerank' }
  ]
})

// Model Config Modal 的 type 下拉选项：基于 provider.capabilities 限定
// 旧数据 capabilities 为空时回退到全集，保持现状
const editingModelTypeOptions = computed(() => {
  const caps = currentProviderForModels.value?.capabilities
  const types = Array.isArray(caps) && caps.length ? caps : ['chat', 'embedding', 'rerank']
  return types.map((c) => ({ value: c, label: c }))
})

const parseJsonObject = (text, label) => {
  try {
    const parsed = JSON.parse(text || '{}')
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error(`${label} 必须是 JSON 对象`)
    }
    return parsed
  } catch {
    throw new Error(`${label} 格式不正确`)
  }
}

const formatJsonText = (value) => JSON.stringify(value || {}, null, 2)

// ============ Provider Operations ============
const loadProviders = async () => {
  loading.value = true
  try {
    const result = await modelProviderApi.getProviders()
    providers.value = result.data || []
  } catch (error) {
    message.error(error.message || '加载模型供应商失败')
  } finally {
    loading.value = false
  }
}

function getProviderInfo(provider) {
  return [
    { label: 'Base URL', value: provider.base_url || '-' },
    { label: '类型', value: provider.provider_type || 'openai' },
    { label: '已启用', value: `${provider.enabled_models?.length || 0} 个模型` }
  ]
}

function getProviderStatus(provider) {
  if (!provider.is_enabled) return { label: '未启用', level: 'info' }
  if (provider.credential_status === 'warning') return { label: '凭证缺失', level: 'warning' }
  if (provider.is_enabled) return { label: '', level: 'success' }
  return null
}

const openCreateProviderModal = () => {
  editingProviderId.value = null
  Object.assign(providerForm, {
    provider_id: '',
    display_name: '',
    provider_type: 'openai',
    default_protocol: '',
    base_url: '',
    embedding_base_url: '',
    rerank_base_url: '',
    models_endpoint: '/models',
    embedding_models_endpoint: '/embeddings/models',
    rerank_models_endpoint: '',
    api_key_env: '',
    api_key: '',
    capabilities: ['chat'],
    is_enabled: true,
    headers_text: '{}',
    extra_text: '{}'
  })
  showProviderModal.value = true
}

const openEditProviderModal = (provider) => {
  editingProviderId.value = provider.provider_id
  Object.assign(providerForm, {
    provider_id: provider.provider_id,
    display_name: provider.display_name,
    provider_type: provider.provider_type || 'openai',
    default_protocol: '',
    base_url: provider.base_url || '',
    embedding_base_url: provider.embedding_base_url || '',
    rerank_base_url: provider.rerank_base_url || '',
    models_endpoint: provider.models_endpoint || '/models',
    embedding_models_endpoint: provider.embedding_models_endpoint || '/embeddings/models',
    rerank_models_endpoint: provider.rerank_models_endpoint || '',
    api_key_env: provider.api_key_env || '',
    api_key: provider.api_key || '',
    capabilities: provider.capabilities?.length ? provider.capabilities : ['chat'],
    is_enabled: provider.is_enabled !== false,
    headers_text: formatJsonText(provider.headers_json),
    extra_text: formatJsonText(provider.extra_json)
  })
  showProviderModal.value = true
}

const buildProviderPayload = () => ({
  provider_id: providerForm.provider_id || undefined,
  display_name: providerForm.display_name,
  provider_type: providerForm.provider_type,
  default_protocol: null,
  base_url: providerForm.base_url,
  embedding_base_url: providerForm.embedding_base_url || null,
  rerank_base_url: providerForm.rerank_base_url || null,
  models_endpoint: providerForm.models_endpoint || null,
  embedding_models_endpoint: providerForm.embedding_models_endpoint || null,
  rerank_models_endpoint: providerForm.rerank_models_endpoint || null,
  api_key_env: providerForm.api_key_env || null,
  api_key: providerForm.api_key || null,
  capabilities: providerForm.capabilities,
  is_enabled: providerForm.is_enabled,
  headers_json: parseJsonObject(providerForm.headers_text, '请求头'),
  extra_json: parseJsonObject(providerForm.extra_text, '扩展配置')
})

const createProvider = async () => {
  saving.value = true
  try {
    await modelProviderApi.createProvider(buildProviderPayload())
    message.success('供应商已创建')
    showProviderModal.value = false
    await loadProviders()
  } catch (error) {
    message.error(error.message || '创建失败')
  } finally {
    saving.value = false
  }
}

const saveProvider = async () => {
  saving.value = true
  try {
    await modelProviderApi.updateProvider(providerForm.provider_id, buildProviderPayload())
    message.success('供应商已保存')
    showProviderModal.value = false
    await loadProviders()
  } catch (error) {
    message.error(error.message || '保存失败')
  } finally {
    saving.value = false
  }
}

const deleteProvider = async (provider) => {
  Modal.confirm({
    title: `删除 ${provider.display_name}`,
    content: '删除后不会影响当前系统正在使用的旧模型配置。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await modelProviderApi.deleteProvider(provider.provider_id)
        message.success('已删除')
        if (currentProviderForModels.value?.provider_id === provider.provider_id) {
          showModelsModal.value = false
          currentProviderForModels.value = null
        }
        if (editingProviderId.value === provider.provider_id) {
          showProviderModal.value = false
          editingProviderId.value = null
        }
        await loadProviders()
      } catch (error) {
        message.error(error.message || '删除失败')
      }
    }
  })
}

const deleteProviderFromEdit = async () => {
  const provider = providers.value.find((p) => p.provider_id === editingProviderId.value)
  if (provider) {
    deleteProvider(provider)
  }
}

// ============ Models Modal Operations ============
const openModelsModal = (provider) => {
  currentProviderForModels.value = provider
  if (!remoteModelsLoaded.value[provider.provider_id]) {
    remoteModelsMap.value[provider.provider_id] = []
  }
  remoteModelSearch.value[provider.provider_id] =
    remoteModelSearch.value[provider.provider_id] || ''
  remoteModelTypeFilter.value[provider.provider_id] = 'all'
  showModelsModal.value = true
}

// ============ Remote Models Operations ============
const fetchRemoteModels = async (providerId) => {
  remoteLoading.value = true
  try {
    const result = await modelProviderApi.fetchRemoteModels(providerId)
    remoteModelsMap.value = {
      ...remoteModelsMap.value,
      [providerId]: result.data || []
    }
    remoteModelsLoaded.value[providerId] = true
    message.success(`已获取 ${result.data?.length || 0} 个远端模型`)
  } catch (error) {
    message.error(error.message || '获取远端模型失败')
  } finally {
    remoteLoading.value = false
  }
}

// ============ Model Operations ============
const normalizeModel = (model = {}) => ({
  id: model.id || '',
  display_name: model.display_name || model.name || model.id || '',
  type: model.type && model.type !== 'unknown' ? model.type : 'chat',
  source: model.source || 'remote',
  protocol_override: model.protocol_override || null,
  base_url_override: model.base_url_override || null,
  context_length: model.context_length || null,
  dimension: model.dimension || null,
  batch_size: model.batch_size || null,
  supported_parameters: model.supported_parameters || [],
  extra: model.extra || {}
})

const addModelFromRemote = async (providerId, remoteModel) => {
  const provider = providers.value.find((p) => p.provider_id === providerId)
  if (!provider) return

  const enabledModels = provider.enabled_models || []
  if (enabledModels.some((m) => m.id === remoteModel.id)) {
    message.info('模型已存在')
    return
  }

  const newModel = normalizeModel(remoteModel)
  newModel.source = 'remote' // 远端拉取的模型显式标注，避免后续被误判为旧数据
  newModel.enabled = true
  const newEnabledModels = [...enabledModels, newModel]

  try {
    await modelProviderApi.updateProvider(providerId, { enabled_models: newEnabledModels })
    message.success(`已添加模型 ${remoteModel.id}`)
    await loadProviders()
    // Refresh current provider reference if modal is open
    if (currentProviderForModels.value?.provider_id === providerId) {
      currentProviderForModels.value = providers.value.find((p) => p.provider_id === providerId)
    }
  } catch (error) {
    message.error(error.message || '添加模型失败')
  }
}

const openModelConfigModal = (model) => {
  Object.assign(editingModel.value, normalizeModel(model))
  isCreating.value = false
  showModelModal.value = true
}

// 手动添加模型：弹出与编辑共用的 Model Config Modal，但 id 字段可编辑、type 选项受 provider 能力约束
const openCreateModal = (provider) => {
  if (!provider) return
  const types = provider.capabilities?.length ? provider.capabilities : ['chat']
  const defaultType = types[0]
  Object.assign(editingModel.value, {
    id: '',
    display_name: '',
    type: defaultType,
    source: 'manual',
    protocol_override: null,
    base_url_override: null,
    context_length: null,
    dimension: null,
    batch_size: null,
    supported_parameters: [],
    extra: {}
  })
  isCreating.value = true
  showModelModal.value = true
}

const saveModelConfig = async () => {
  if (!currentProviderForModels.value) return
  saving.value = true
  try {
    const provider = providers.value.find(
      (p) => p.provider_id === currentProviderForModels.value.provider_id
    )
    if (!provider) return

    let enabledModels
    if (isCreating.value) {
      const newId = (editingModel.value.id || '').trim()
      if (!newId) {
        message.error('请填写模型 ID')
        return
      }
      if ((provider.enabled_models || []).some((m) => m.id === newId)) {
        message.error('模型 ID 已存在')
        return
      }
      const newModel = { ...editingModel.value, id: newId, source: 'manual', enabled: true }
      enabledModels = [...(provider.enabled_models || []), newModel]
    } else {
      enabledModels = (provider.enabled_models || []).map((m) =>
        m.id === editingModel.value.id ? { ...editingModel.value } : m
      )
    }

    await modelProviderApi.updateProvider(currentProviderForModels.value.provider_id, {
      enabled_models: enabledModels
    })
    message.success(isCreating.value ? '模型已添加' : '模型配置已保存')
    showModelModal.value = false
    isCreating.value = false
    await loadProviders()
    // Refresh current provider reference
    currentProviderForModels.value = providers.value.find(
      (p) => p.provider_id === currentProviderForModels.value.provider_id
    )
  } catch (error) {
    message.error(error.message || '保存失败')
  } finally {
    saving.value = false
  }
}

const removeModel = async (providerId, modelId) => {
  const provider = providers.value.find((p) => p.provider_id === providerId)
  if (!provider) return

  Modal.confirm({
    title: '移除模型',
    content: `确定要移除模型 ${modelId} 吗？`,
    okText: '移除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        const enabledModels = (provider.enabled_models || []).filter((m) => m.id !== modelId)
        await modelProviderApi.updateProvider(providerId, { enabled_models: enabledModels })
        message.success('模型已移除')
        await loadProviders()
        // Refresh current provider reference if modal is open
        if (currentProviderForModels.value?.provider_id === providerId) {
          currentProviderForModels.value = providers.value.find((p) => p.provider_id === providerId)
        }
      } catch (error) {
        message.error(error.message || '移除失败')
      }
    }
  })
}

// ============ Lifecycle ============
onMounted(loadProviders)
</script>

<template>
  <div class="model-config-view">
    <!-- Page Header -->
    <PageHeader title="模型配置" :show-border="true">
      <template #info>
        <div class="summary-strip">
          <span>{{ providerStats.total }} 个供应商</span>
          <span>{{ providerStats.enabled }} 个启用</span>
          <span v-if="providerStats.warning > 0" class="warning-count"
            >{{ providerStats.warning }} 个凭证缺失</span
          >
          <span>{{ providerStats.models }} 个模型</span>
        </div>
      </template>
    </PageHeader>

    <!-- Toolbar -->
    <PageShoulder v-model:search="searchQuery" search-placeholder="搜索供应商...">
      <template #actions>
        <a-button type="primary" class="lucide-icon-btn" @click="openCreateProviderModal">
          <Plus :size="14" />
          新增供应商
        </a-button>
        <a-button class="lucide-icon-btn" @click="loadProviders" :loading="loading">
          <RefreshCw :size="14" :class="{ spinning: loading }" />
        </a-button>
      </template>
    </PageShoulder>

    <!-- Provider Card Grid -->
    <ExtensionCardGrid :min-width="320">
      <InfoCard
        v-for="provider in filteredProviders"
        :key="provider.provider_id"
        :title="provider.display_name"
        :subtitle="provider.provider_id"
        :disabled="!provider.is_enabled"
        :default-icon="Globe"
        :info="getProviderInfo(provider)"
        :status="getProviderStatus(provider)"
        @click="openEditProviderModal(provider)"
      >
        <template #icon>
          <img
            v-if="getProviderIcon(provider)"
            :src="getIconUrl(getProviderIcon(provider))"
            :alt="provider.display_name"
          />
        </template>
        <template #footer>
          <button class="view-models-btn" type="button" @click.stop="openModelsModal(provider)">
            <Settings2 :size="14" />
            管理模型
          </button>
          <a-tooltip title="编辑供应商">
            <a-button
              size="small"
              class="lucide-icon-btn"
              @click.stop="openEditProviderModal(provider)"
            >
              <Edit3 :size="14" />
            </a-button>
          </a-tooltip>
        </template>
      </InfoCard>
    </ExtensionCardGrid>

    <!-- Provider Edit Modal -->
    <a-modal
      v-model:open="showProviderModal"
      :title="editingProviderId ? '编辑供应商' : '新增供应商'"
      :width="560"
      :confirm-loading="saving"
    >
      <template #footer>
        <div class="provider-modal-footer">
          <a-button
            v-if="editingProviderId"
            danger
            class="lucide-icon-btn"
            @click="deleteProviderFromEdit"
          >
            <Trash2 :size="14" />
            删除供应商
          </a-button>
          <span v-else></span>
          <div class="provider-modal-footer-actions">
            <a-button @click="showProviderModal = false">取消</a-button>
            <a-button
              type="primary"
              :loading="saving"
              @click="editingProviderId ? saveProvider() : createProvider()"
            >
              确认
            </a-button>
          </div>
        </div>
      </template>
      <div class="modal-form">
        <div class="form-row">
          <label class="form-label">
            <span>Provider ID</span>
            <a-input
              v-model:value="providerForm.provider_id"
              :disabled="!!editingProviderId"
              placeholder="my-provider"
            />
          </label>
          <label class="form-label">
            <span>展示名称</span>
            <a-input v-model:value="providerForm.display_name" placeholder="My Provider" />
          </label>
        </div>

        <div class="form-row">
          <label class="form-label">
            <span>Base URL</span>
            <a-input
              v-model:value="providerForm.base_url"
              placeholder="https://api.example.com/v1"
            />
          </label>
          <label class="form-label">
            <span>Provider Type</span>
            <a-select v-model:value="providerForm.provider_type">
              <a-select-option value="openai">openai</a-select-option>
              <a-select-option value="anthropic">anthropic</a-select-option>
              <a-select-option value="gemini">gemini</a-select-option>
              <a-select-option value="ollama">ollama</a-select-option>
              <a-select-option value="openrouter">openrouter</a-select-option>
              <a-select-option value="lmstudio">lmstudio</a-select-option>
            </a-select>
          </label>
        </div>

        <div class="form-row">
          <label class="form-label">
            <span>API Key Env</span>
            <a-input v-model:value="providerForm.api_key_env" placeholder="环境变量名" />
          </label>
          <label class="form-label">
            <span>API Key</span>
            <a-input-password v-model:value="providerForm.api_key" />
          </label>
        </div>

        <div class="form-row">
          <label class="form-label">
            <span>Models Endpoint</span>
            <a-input v-model:value="providerForm.models_endpoint" placeholder="/models" />
          </label>
        </div>

        <template v-if="providerForm.capabilities.includes('embedding')">
          <div class="form-row">
            <label class="form-label">
              <span>Embedding Base URL</span>
              <a-input
                v-model:value="providerForm.embedding_base_url"
                placeholder="https://api.example.com/v1/embeddings"
              />
            </label>
            <label class="form-label">
              <span>Embedding Endpoint</span>
              <a-input
                v-model:value="providerForm.embedding_models_endpoint"
                placeholder="/embeddings/models"
              />
            </label>
          </div>
        </template>

        <template v-if="providerForm.capabilities.includes('rerank')">
          <div class="form-row">
            <label class="form-label">
              <span>Rerank Base URL</span>
              <a-input
                v-model:value="providerForm.rerank_base_url"
                placeholder="https://api.example.com/v1/rerank"
              />
            </label>
            <label class="form-label">
              <span>Rerank Endpoint</span>
              <a-input
                v-model:value="providerForm.rerank_models_endpoint"
                placeholder="按供应商文档填写，留空则不自动加载"
              />
            </label>
          </div>
        </template>

        <label class="form-label full-width">
          <span>能力</span>
          <a-select v-model:value="providerForm.capabilities" mode="multiple">
            <a-select-option value="chat">chat</a-select-option>
            <a-select-option value="embedding">embedding</a-select-option>
            <a-select-option value="rerank">rerank</a-select-option>
          </a-select>
        </label>

        <div class="form-switch">
          <span>状态</span>
          <a-switch
            v-model:checked="providerForm.is_enabled"
            checked-children="启用"
            un-checked-children="停用"
          />
        </div>

        <a-collapse expand-icon-position="end" :ghost="true" class="advanced-collapse">
          <a-collapse-panel key="advanced" header="高级配置">
            <label class="form-label full-width">
              <span>请求头 JSON</span>
              <a-textarea v-model:value="providerForm.headers_text" :rows="4" placeholder="{}" />
            </label>

            <label class="form-label full-width">
              <span>扩展配置 JSON</span>
              <a-textarea v-model:value="providerForm.extra_text" :rows="4" placeholder="{}" />
            </label>
          </a-collapse-panel>
        </a-collapse>
      </div>
    </a-modal>

    <!-- Models Management Modal -->
    <a-modal
      v-model:open="showModelsModal"
      :title="
        currentProviderForModels
          ? `${currentProviderForModels.display_name} - 模型配置`
          : '模型配置'
      "
      :width="800"
      :footer="null"
    >
      <div v-if="currentProviderForModels" class="models-modal-content">
        <!-- Enabled Models Section -->
        <div class="models-section">
          <div class="enabled-header">
            <h4 class="models-section-title">
              已启用模型 ({{ currentProviderForModels.enabled_models?.length || 0 }})
            </h4>
            <div class="actions">
              <a-button
                size="small"
                type="primary"
                class="lucide-icon-btn"
                :loading="remoteLoading"
                @click="fetchRemoteModels(currentProviderForModels.provider_id)"
              >
                获取远程模型
              </a-button>
              <a-button
                size="small"
                class="lucide-icon-btn"
                @click="openCreateModal(currentProviderForModels)"
              >
                <Plus :size="14" />
                <span>手动添加</span>
              </a-button>
            </div>
          </div>
          <div class="models-table" v-if="currentProviderForModels.enabled_models?.length">
            <div class="table-head">
              <span class="col-name">模型</span>
              <span class="col-type">类型</span>
              <span class="col-context">上下文</span>
              <span class="col-dim">维度</span>
              <span class="col-ops">操作</span>
            </div>
            <div
              v-for="model in currentProviderForModels.enabled_models"
              :key="model.id"
              class="table-row"
              :class="{ stale: isModelStale(model, currentProviderForModels.provider_id) }"
            >
              <div class="model-info">
                <span class="model-name">{{ getModelDisplayName(model) }}</span>
                <span class="model-id">{{ getModelId(model) }}</span>
              </div>
              <span class="col-type">
                <span class="type-tag" :class="model.type">{{ model.type }}</span>
                <span
                  v-if="model.source === 'manual'"
                  class="type-tag manual"
                  title="管理员手动添加"
                  >手动</span
                >
              </span>
              <span class="col-context">{{ formatContextLength(model.context_length) }}</span>
              <span class="col-dim">
                <span
                  v-if="model.type === 'embedding' && !model.dimension"
                  class="dim-warning"
                  title="缺少维度配置"
                  >⚠</span
                >
                <span v-else>{{ model.dimension || '-' }}</span>
              </span>
              <span class="col-ops">
                <a-button size="small" class="lucide-icon-btn" @click="openModelConfigModal(model)">
                  <Settings2 :size="13" />
                </a-button>
                <a-button
                  size="small"
                  danger
                  class="lucide-icon-btn"
                  @click="removeModel(currentProviderForModels.provider_id, model.id)"
                >
                  <Trash2 :size="13" />
                </a-button>
              </span>
            </div>
          </div>
          <a-empty v-else description="暂无已启用模型" />
        </div>

        <!-- Remote Models Section -->
        <div class="models-section">
          <div class="remote-header">
            <h4 class="models-section-title">远端候选模型 ({{ filteredRemoteModels.length }})</h4>
            <a-input
              v-if="remoteModelsMap[currentProviderForModels.provider_id]?.length"
              v-model:value="remoteModelSearch[currentProviderForModels.provider_id]"
              class="remote-search-input"
              placeholder="搜索模型..."
              allow-clear
            >
              <template #prefix><Search :size="12" /></template>
            </a-input>
            <a-segmented
              v-if="remoteModelsMap[currentProviderForModels.provider_id]?.length"
              v-model:value="remoteModelTypeFilter[currentProviderForModels.provider_id]"
              :options="remoteModelTypeOptions"
              class="remote-type-filter"
            />
          </div>
          <div
            class="remote-list"
            v-if="remoteModelsMap[currentProviderForModels.provider_id]?.length"
          >
            <div
              v-for="remoteModel in filteredRemoteModels"
              :key="remoteModel.id"
              class="remote-row"
            >
              <span class="remote-name">{{ getModelDisplayName(remoteModel) }}</span>
              <div class="remote-tags">
                <span class="type-tag" :class="remoteModel.type || 'chat'">
                  {{ remoteModel.type || 'chat' }}
                </span>
                <template v-for="mod in getInputModalities(remoteModel) || []" :key="mod">
                  <span class="modality-tag">{{ mod }}</span>
                </template>
              </div>
              <span class="remote-context">{{
                formatContextLength(remoteModel.context_length)
              }}</span>
              <span class="remote-price" v-if="formatMtokenPrice(remoteModel.pricing)">
                {{ formatPriceDisplay(remoteModel.pricing) }}
              </span>
              <span class="remote-price placeholder" v-else>N/A</span>
              <a-button
                size="small"
                :type="
                  currentProviderForModels.enabled_models?.some((m) => m.id === remoteModel.id)
                    ? 'primary'
                    : 'default'
                "
                class="lucide-icon-btn"
                :disabled="
                  currentProviderForModels.enabled_models?.some((m) => m.id === remoteModel.id)
                "
                @click="addModelFromRemote(currentProviderForModels.provider_id, remoteModel)"
              >
                <CheckCircle2
                  :size="13"
                  v-if="
                    currentProviderForModels.enabled_models?.some((m) => m.id === remoteModel.id)
                  "
                />
                <Plus :size="13" v-else />
              </a-button>
            </div>
          </div>
          <div class="remote-fetch-actions"></div>
        </div>
      </div>
    </a-modal>

    <!-- Model Config Modal -->
    <a-modal
      v-model:open="showModelModal"
      :title="isCreating ? '手动添加模型' : '模型配置'"
      :width="520"
      :confirm-loading="saving"
      @ok="saveModelConfig"
    >
      <div class="modal-form">
        <div v-if="isCreating" class="form-row">
          <label class="form-label">
            <span>模型 ID <span class="required-mark">*</span></span>
            <a-input v-model:value="editingModel.id" placeholder="例如 BAAI/bge-m3" allow-clear />
          </label>
        </div>
        <div v-else class="model-id-display">
          <span class="info-label">模型 ID</span>
          <code>{{ editingModel.id }}</code>
        </div>

        <div class="form-row">
          <label class="form-label">
            <span>展示名称</span>
            <a-input v-model:value="editingModel.display_name" />
          </label>
          <label class="form-label">
            <span>模型类型</span>
            <a-select
              v-model:value="editingModel.type"
              :options="editingModelTypeOptions"
              :disabled="editingModelTypeOptions.length === 1"
            />
          </label>
        </div>

        <div class="form-row">
          <label class="form-label">
            <span>协议覆盖</span>
            <a-input v-model:value="editingModel.protocol_override" placeholder="可选" />
          </label>
          <label class="form-label">
            <span>Base URL 覆盖</span>
            <a-input v-model:value="editingModel.base_url_override" placeholder="可选" />
          </label>
        </div>

        <div class="form-row">
          <label class="form-label" v-if="editingModel.type === 'embedding'">
            <span>维度</span>
            <a-input-number v-model:value="editingModel.dimension" :min="1" />
          </label>
          <label
            class="form-label"
            v-if="editingModel.type === 'embedding' || editingModel.type === 'rerank'"
          >
            <span>Batch Size</span>
            <a-input-number v-model:value="editingModel.batch_size" :min="1" />
          </label>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<style lang="less" scoped>
.model-config-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

// ============ Provider Summary Strip ============
.summary-strip {
  display: flex;
  gap: 8px;

  span {
    padding: 6px 10px;
    border: 1px solid var(--gray-100);
    border-radius: 7px;
    background: var(--gray-10);
    color: var(--gray-700);
    font-size: 12px;
    line-height: 18px;
  }

  .warning-count {
    background: var(--color-warning-50);
    border-color: var(--color-warning-100);
    color: var(--color-warning-700);
  }
}

// ============ Provider Card Footer ============
.view-models-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border: none;
  background: transparent;
  color: var(--main-700);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  border-radius: 4px;
  transition: background 0.15s;

  &:hover {
    background: var(--main-50);
  }
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.clickable {
  color: var(--main-700);
  cursor: pointer;
  font-weight: 500;

  &:hover {
    text-decoration: underline;
  }
}

.card-actions {
  display: flex;
  gap: 4px;
}

// ============ Models Modal ============
.models-modal-content {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.models-modal-actions {
  display: flex;
  gap: 8px;
}

.models-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.models-section-title {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--gray-700);
}

.models-actions {
  display: flex;
  gap: 8px;
}

// ============ Models Table ============
.models-table {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  overflow: hidden;
}

.table-head {
  display: grid;
  grid-template-columns: 1fr 80px 70px 60px 110px;
  gap: 8px;
  padding: 10px 12px;
  background: var(--gray-50);
  font-size: 11px;
  font-weight: 600;
  color: var(--gray-500);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.table-row {
  display: grid;
  grid-template-columns: 1fr 80px 70px 60px 110px;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid var(--gray-100);
  font-size: 13px;
  align-items: center;
  transition: background 0.1s;

  &:hover {
    background: var(--gray-10);
  }

  &.stale {
    background: var(--color-warning-50);
  }
}

.model-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.model-name {
  font-weight: 500;
  color: var(--gray-900);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-id {
  font-size: 11px;
  color: var(--gray-500);
  font-family: monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.col-id {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: monospace;
  font-size: 12px;
}

.col-type {
  display: flex;
  align-items: center;
}

.col-context,
.col-dim {
  color: var(--gray-600);
  font-size: 12px;
}

.col-status {
  display: flex;
  align-items: center;
}

.col-ops {
  display: flex;
  gap: 4px;
  justify-content: flex-end;
}

// ============ Type Tags ============
.type-tag {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;

  & + & {
    margin-left: 4px;
  }

  &.chat {
    background: var(--color-info-50);
    color: var(--color-info-700);
  }

  &.embedding {
    background: var(--color-success-50);
    color: var(--color-success-700);
  }

  &.rerank {
    background: var(--color-warning-50);
    color: var(--color-warning-900);
  }

  &.manual {
    background: var(--gray-200);
    color: var(--gray-700);
  }
}

// ============ Status Badges ============
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;

  &.active {
    color: var(--color-success-700);
  }

  &.stale {
    color: var(--color-warning-700);
  }
}

// ============ Remote Section ============
.remote-section {
  margin-top: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  overflow: hidden;
}

.remote-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px 12px;
  border: none;
  background: var(--gray-50);
  color: var(--gray-700);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.1s;

  &:hover {
    background: var(--gray-100);
  }
}

.remote-list {
  border-top: 1px solid var(--gray-100);
}

.enabled-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;

  .models-section-title {
    margin: 0;
  }

  .actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }
}

.remote-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;

  .models-section-title {
    margin: 0;
    flex-shrink: 0;
  }
}

.remote-search-input {
  width: 180px;
}

.remote-type-filter {
  flex-shrink: 0;
}

.remote-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  border-top: 1px solid var(--gray-100);
  font-size: 13px;

  &:first-child {
    border-top: none;
  }
}

.remote-name {
  flex: 1;
  min-width: 0;
  font-weight: 500;
  color: var(--gray-900);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.remote-tags {
  display: flex;
  align-items: center;
  gap: 4px;
}

.modality-tag {
  display: inline-flex;
  align-items: center;
  padding: 2px 6px;
  border-radius: 3px;
  background: var(--color-accent-50);
  color: var(--color-accent-700);
  font-size: 10px;
  font-weight: 500;
}

.dim-warning {
  display: inline-flex;
  align-items: center;
  padding: 2px 6px;
  border-radius: 3px;
  background: var(--color-warning-50);
  color: var(--color-warning-700);
  font-size: 10px;
  font-weight: 500;
}

.remote-context {
  width: 60px;
  color: var(--gray-500);
  font-size: 12px;
}

.remote-price {
  width: 100px;
  font-size: 11px;
  color: var(--gray-600);
  font-family: monospace;

  &.placeholder {
    color: var(--gray-400);
  }
}

.models-empty {
  padding: 24px;
}

// ============ Modal Form ============
.modal-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

.form-label {
  display: flex;
  flex-direction: column;
  gap: 6px;

  > span {
    color: var(--gray-700);
    font-size: 12px;
    font-weight: 500;
  }
}

.full-width {
  grid-column: 1 / -1;
}

.form-switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;

  > span {
    color: var(--gray-700);
    font-size: 12px;
    font-weight: 500;
  }
}

.advanced-collapse {
  :deep(.ant-collapse-content-box) {
    padding-inline: 0;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  :deep(.ant-collapse-header) {
    padding-inline: 0;
  }
}

.provider-modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.provider-modal-footer-actions {
  display: flex;
  gap: 8px;
}

.model-id-display {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  background: var(--gray-50);
  border-radius: 6px;
  margin-bottom: 4px;

  .info-label {
    color: var(--gray-500);
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
  }

  code {
    font-family: monospace;
    font-size: 13px;
    color: var(--gray-800);
  }
}

// ============ Responsive ============
@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 16px;
    padding: 20px 16px;
  }

  .toolbar {
    padding: 12px 16px;
    flex-direction: column;
    align-items: stretch;
  }

  .search-input {
    width: 100%;
  }

  .provider-grid {
    grid-template-columns: 1fr;
    padding: 16px;
  }

  .table-head,
  .table-row {
    grid-template-columns: 1fr 60px 60px;
    font-size: 12px;
  }

  .col-context,
  .col-dim {
    display: none;
  }
}
</style>
