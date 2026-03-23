<template>
  <div class="model-providers-section">

    <div class="header-section">
      <div class="header-content">
        <div class="section-title">自定义供应商</div>
        <p class="section-description">
          添加自定义的LLM供应商，支持OpenAI兼容的API格式。
        </p>
      </div>
      <a-button type="primary" @click="openAddCustomProviderModal" class="add-btn lucide-icon-btn">
        <template #icon>
            <Plus :size="16" />
        </template>
        添加自定义供应商
      </a-button>
    </div>

    <div class="custom-providers-section">
      <!-- 自定义供应商列表 -->
      <div
        class="custom-provider-card"
        v-for="(provider, providerId) in customProviders"
        :key="providerId"
      >
        <div class="card-header">
          <div class="provider-info">
            <h4>{{ provider.name }}</h4>
            <span class="provider-id">ID: {{ providerId }}</span>
          </div>
          <div class="provider-actions">
            <a-button
              type="text"
              size="small"
              class="lucide-icon-btn"
              @click="testCustomProvider(providerId, provider.default)"
            >
              <template #icon>
                <PlugZap :size="14" />
              </template>
              测试连接
            </a-button>
            <a-button
              type="text"
              size="small"
              class="lucide-icon-btn"
              @click="openEditCustomProviderModal(providerId, provider)"
            >
              <template #icon>
                <Pencil :size="14" />
              </template>
              编辑
            </a-button>
            <a-popconfirm
              title="确定要删除这个自定义供应商吗？"
              @confirm="deleteCustomProvider(providerId)"
              ok-text="确定"
              cancel-text="取消"
            >
              <a-button type="text" size="small" danger class="lucide-icon-btn">
                <template #icon>
                  <Trash2 :size="14" />
                </template>
                删除
              </a-button>
            </a-popconfirm>
          </div>
        </div>
        <div class="card-content">
          <div class="provider-details">
            <div class="detail-item">
              <span class="label">API地址:</span>
              <span class="value">{{ provider.base_url }}</span>
            </div>
            <div class="detail-item">
              <span class="label">默认模型:</span>
              <span class="value">{{ provider.default }}</span>
            </div>
            <div class="detail-item">
              <span class="label">可用模型:</span>
              <span class="value">{{ provider.models?.join(', ') || '无' }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 无自定义供应商时的提示 -->
      <div v-if="Object.keys(customProviders).length === 0" class="empty-state">
        <a-empty description="暂无自定义供应商">
          <!-- <a-button type="primary" @click="openAddCustomProviderModal">添加自定义供应商</a-button> -->
        </a-empty>
      </div>
    </div>

    <a-divider />

    <!-- 系统内置供应商 -->
    <div class="builtin-providers-section">
      <div class="section-header">
        <div class="section-subtitle">系统内置供应商</div>
        <div class="providers-stats">
          <span class="stats-item available"> {{ modelKeys.length }} 可用 </span>
          <span class="stats-item unavailable"> {{ notModelKeys.length }} 未配置 </span>
        </div>
      </div>
      <p class="section-description">请在 <code>.env</code> 文件中配置对应的 APIKEY，并重新启动服务</p>

      <!-- 已配置的供应商 -->
      <div
        class="model-provider-card configured-provider"
        v-for="(item, key) in modelKeys"
        :key="key"
      >
        <div class="card-header" @click="toggleExpand(item)">
          <div :class="{ 'model-icon': true, available: modelStatus[item] }">
            <img :src="modelIcons[item] || modelIcons.default" alt="模型图标" />
          </div>
          <div class="model-title-container">
            <div class="model-name">{{ modelNames[item].name }}</div>
          </div>
          <div class="provider-meta">
            <a-button
              type="text"
              class="expand-button lucide-icon-btn"
              @click.stop="openProviderConfig(item)"
              title="配置模型"
            >
              <Settings :size="14" /> 已选 {{ modelNames[item].models?.length || 0 }} 个模型
            </a-button>
          </div>
        </div>
        <div class="card-body-wrapper" :class="{ expanded: expandedModels[item] }">
          <div class="card-body" v-if="modelStatus[item]">
            <div class="card-models" v-for="(model, idx) in modelNames[item].models" :key="idx">
              <div class="model_name">{{ model }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 未配置的供应商 -->
      <div
        class="model-provider-card unconfigured-provider"
        v-for="(item, key) in notModelKeys"
        :key="key"
      >
        <div class="card-header">
          <div class="model-icon">
            <img :src="modelIcons[item] || modelIcons.default" alt="模型图标" />
          </div>
          <div class="model-title-container">
            <div class="model-name">{{ modelNames[item].name }}</div>
            <a :href="modelNames[item].url" target="_blank" class="model-url">
              查看信息 <CircleHelp :size="13" />
            </a>
          </div>
          <div class="missing-keys">
            需配置<span>{{ modelNames[item].env }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 模型提供商配置弹窗 -->
    <a-modal
      class="provider-config-modal"
      v-model:open="providerConfig.visible"
      :title="`配置${providerConfig.providerName}模型`"
      @ok="saveProviderConfig"
      @cancel="cancelProviderConfig"
      :okText="'保存配置'"
      :cancelText="'取消'"
      :ok-type="'primary'"
      :width="800"
    >
      <div v-if="providerConfig.loading" class="modal-loading-container">
        <a-spin
          :indicator="h(LoaderCircle, { size: 32, color: 'var(--main-color)' })"
        />
        <div class="loading-text">正在获取模型列表...</div>
      </div>
      <div v-else class="modal-config-content">
        <div class="modal-config-header">
          <p class="description">
            勾选您希望在系统中启用的模型，请注意，列表中可能包含非对话模型，请仔细甄别。
          </p>
        </div>

        <div class="modal-models-section">
          <!-- 警告：检测到失效模型 -->
          <div
            v-if="unsupportedModels.length > 0"
            class="simple-notice warning"
            style="margin-bottom: 20px"
          >
            <p>检测到配置中包含当前供应商列表中不存在的模型。以下模型可能已失效或被供应商移除：</p>
            <div class="unsupported-list">
              <a-tag
                closable
                v-for="model in unsupportedModels"
                :key="model"
                color="error"
                @close="removeModel(model)"
                style="margin-bottom: 4px"
              >
                {{ model }}
              </a-tag>
            </div>
            <a-button
              size="small"
              type="primary"
              danger
              ghost
              @click="removeAllUnsupported"
              class="clear-btn"
            >
              一键移除所有失效模型
            </a-button>
          </div>

          <div class="model-search" v-if="providerConfig.allModels.length > 0">
            <a-input
              v-model:value="providerConfig.searchQuery"
              placeholder="搜索模型..."
              allow-clear
            >
              <template #prefix>
                <Search :size="14" />
              </template>
            </a-input>
          </div>

          <!-- 显示选中统计信息 -->
          <div class="selection-summary" v-if="providerConfig.allModels.length > 0">
            <span>已选择 {{ providerConfig.selectedModels.length }} 个模型</span>
            <span v-if="providerConfig.searchQuery" class="filter-info">
              （当前筛选显示 {{ filteredModels.length }} 个）
            </span>
          </div>

          <div class="modal-checkbox-list" v-if="providerConfig.allModels.length > 0">
            <div v-for="(model, index) in filteredModels" :key="index" class="modal-checkbox-item">
              <a-checkbox
                :checked="providerConfig.selectedModels.includes(model.id)"
                @change="(e) => handleModelSelect(model.id, e.target.checked)"
              >
                {{ model.id }}
              </a-checkbox>
            </div>
          </div>

          <!-- 手动管理模式 (当无法获取模型列表时) -->
          <div v-if="providerConfig.allModels.length === 0" class="modal-manual-manage">
            <div
              v-if="!modelStatus[providerConfig.provider]"
              class="simple-notice warning"
              style="margin-bottom: 16px"
            >
              请在 .env 中配置对应的 APIKEY，并重新启动服务
            </div>

            <div class="manual-manage-container">
              <div class="manual-header">
                <div class="simple-notice info">
                  无法获取模型列表，您可以手动管理模型配置。该提供商暂未适配自动获取模型列表，或者网络请求失败。您可以在下方手动添加或移除模型。
                </div>
              </div>

              <div class="manual-add-box" style="margin: 16px 0">
                <a-input-search
                  v-model:value="manualModelInput"
                  placeholder="请输入模型ID（如：gpt-4）"
                  enter-button="添加模型"
                  @search="addManualModel"
                />
              </div>

              <div class="current-models-list">
                <h4 style="margin-bottom: 10px; font-weight: 600">
                  当前配置的模型 ({{ providerConfig.selectedModels.length }})
                </h4>
                <div
                  v-if="providerConfig.selectedModels.length === 0"
                  class="empty-text"
                  style="color: var(--gray-500); padding: 8px 0"
                >
                  暂无配置模型
                </div>
                <div class="tags-container">
                  <a-tag
                    v-for="model in providerConfig.selectedModels"
                    :key="model"
                    closable
                    color="blue"
                    @close="removeModel(model)"
                    style="margin-bottom: 8px; padding: 4px 8px"
                  >
                    {{ model }}
                  </a-tag>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </a-modal>

    <!-- 自定义供应商配置弹窗 -->
    <a-modal
      v-model:open="customProviderModal.visible"
      :title="customProviderModal.isEdit ? '编辑自定义供应商' : '添加自定义供应商'"
      @ok="saveCustomProvider"
      @cancel="cancelCustomProvider"
      :okText="'保存'"
      :cancelText="'取消'"
      :ok-type="'primary'"
      :width="600"
      :confirmLoading="customProviderModal.loading"
    >
      <a-form
        ref="customProviderForm"
        :model="customProviderModal.data"
        :rules="customProviderRules"
        layout="vertical"
      >
        <a-form-item label="供应商ID" name="providerId" v-if="!customProviderModal.isEdit">
          <a-input
            v-model:value="customProviderModal.data.providerId"
            placeholder="请输入唯一的供应商标识符（如：my-provider）"
            :disabled="customProviderModal.isEdit"
          />
        </a-form-item>

        <a-form-item label="供应商名称" name="name">
          <a-input
            v-model:value="customProviderModal.data.name"
            placeholder="请输入供应商显示名称"
          />
        </a-form-item>

        <a-form-item label="API地址" name="base_url">
          <a-input
            v-model:value="customProviderModal.data.base_url"
            placeholder="请输入API基础地址（如：https://api.example.com/v1）"
          />
        </a-form-item>

        <a-form-item label="默认模型" name="default">
          <a-input
            v-model:value="customProviderModal.data.default"
            placeholder="请输入默认模型名称"
          />
        </a-form-item>

        <a-form-item label="API密钥" name="env">
          <a-input
            v-model:value="customProviderModal.data.env"
            placeholder="请输入API密钥或环境变量名（如：MY_API_KEY）"
          />
          <div class="form-help-text">
            支持直接输入API密钥，或使用环境变量名（如：MY_API_KEY），如无密钥填写"无"。
          </div>
        </a-form-item>

        <a-form-item label="支持的模型" name="models">
          <a-textarea
            v-model:value="customProviderModal.data.modelsText"
            placeholder="请输入支持的模型列表，每行一个模型"
            :rows="4"
          />
          <div class="form-help-text">每行输入一个模型名称，例如：gpt-5</div>
        </a-form-item>

        <a-form-item label="文档地址" name="url">
          <a-input
            v-model:value="customProviderModal.data.url"
            placeholder="请输入供应商文档地址（可选）"
          />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, reactive, watch, h, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  CircleHelp,
  Settings,
  LoaderCircle,
  Search,
  Plus,
  Pencil,
  Trash2,
  PlugZap
} from 'lucide-vue-next'
import { useConfigStore } from '@/stores/config'
import { modelIcons } from '@/utils/modelIcon'
import { agentApi } from '@/apis/agent_api'
import { customProviderApi } from '@/apis/system_api'

const configStore = useConfigStore()

// 计算属性
const modelNames = computed(() => configStore.config?.model_names)
const modelStatus = computed(() => configStore.config?.model_provider_status)

// 自定义供应商计算属性
const customProviders = computed(() => {
  const providers = configStore.config?.model_names || {}
  return Object.fromEntries(
    Object.entries(providers).filter(([key, value]) => value.custom === true)
  )
})

// 提供商配置相关状态
const providerConfig = reactive({
  visible: false,
  provider: '',
  providerName: '',
  models: [],
  allModels: [], // 所有可用的模型
  selectedModels: [], // 用户选择的模型
  loading: false,
  searchQuery: ''
})

// 筛选 modelStatus 中为真的key
const modelKeys = computed(() => {
  return Object.keys(modelStatus.value || {}).filter(
    (key) => modelStatus.value[key] && !customProviders.value[key]
  )
})

// 筛选 modelStatus 中为假的key
const notModelKeys = computed(() => {
  return Object.keys(modelStatus.value || {}).filter((key) => !modelStatus.value[key])
})

// 模型展开状态管理
const expandedModels = reactive({})

// 监听 modelKeys 变化，确保新添加的模型也是默认展开状态
watch(
  modelKeys,
  (newKeys) => {
    newKeys.forEach((key) => {
      if (expandedModels[key] === undefined) {
        expandedModels[key] = true
      }
    })
  },
  { immediate: true }
)

// 切换展开状态
const toggleExpand = (item) => {
  expandedModels[item] = !expandedModels[item]
}

// 处理模型选择
const handleModelSelect = (modelId, checked) => {
  const selectedModels = providerConfig.selectedModels
  const index = selectedModels.indexOf(modelId)

  if (checked && index === -1) {
    selectedModels.push(modelId)
  } else if (!checked && index > -1) {
    selectedModels.splice(index, 1)
  }
}

// 打开提供商配置
const openProviderConfig = (provider) => {
  providerConfig.provider = provider
  providerConfig.providerName = modelNames.value[provider].name
  providerConfig.allModels = []
  providerConfig.visible = true
  providerConfig.loading = true
  providerConfig.searchQuery = '' // 重置搜索关键词

  // 获取当前选择的模型作为初始选中值
  const currentModels = modelNames.value[provider]?.models || []
  providerConfig.selectedModels = [...currentModels]

  // 获取所有可用模型
  fetchProviderModels(provider)
}

// 获取模型提供商的模型列表
const fetchProviderModels = (provider) => {
  providerConfig.loading = true
  agentApi
    .getProviderModels(provider)
    .then((data) => {
      console.log(`${provider} 模型列表:`, data)

      // 处理各种可能的API返回格式
      let modelsList = []

      // 情况1: { data: [...] }
      if (data.data && Array.isArray(data.data)) {
        modelsList = data.data
      }
      // 情况2: { models: [...] } (字符串数组)
      else if (data.models && Array.isArray(data.models)) {
        modelsList = data.models.map((model) => (typeof model === 'string' ? { id: model } : model))
      }
      // 情况3: { models: { data: [...] } }
      else if (data.models && data.models.data && Array.isArray(data.models.data)) {
        modelsList = data.models.data
      }

      console.log('处理后的模型列表:', modelsList)
      providerConfig.allModels = modelsList
      providerConfig.loading = false
    })
    .catch((error) => {
      console.error(`获取${provider}模型列表失败:`, error)
      message.error({ content: `获取${modelNames.value[provider].name}模型列表失败`, duration: 2 })
      providerConfig.loading = false
    })
}

// 保存提供商配置
const saveProviderConfig = async () => {
  if (!modelStatus.value[providerConfig.provider]) {
    message.error('请在 .env 中配置对应的 APIKEY，并重新启动服务')
    return
  }

  message.loading({ content: '保存配置中...', key: 'save-config', duration: 0 })

  try {
    // 发送选择的模型列表到后端
    const data = await agentApi.updateProviderModels(
      providerConfig.provider,
      providerConfig.selectedModels
    )
    console.log('更新后的模型列表:', data.models)

    message.success({ content: '模型配置已保存!', key: 'save-config', duration: 2 })

    // 关闭弹窗
    providerConfig.visible = false

    // 刷新配置
    configStore.refreshConfig()
  } catch (error) {
    console.error('保存配置失败:', error)
    message.error({ content: '保存配置失败: ' + error.message, key: 'save-config', duration: 2 })
  }
}

// 取消提供商配置
const cancelProviderConfig = () => {
  providerConfig.visible = false
}

// 计算筛选后的模型列表
const filteredModels = computed(() => {
  const allModels = providerConfig.allModels || []
  const searchQuery = providerConfig.searchQuery.toLowerCase()
  return allModels.filter((model) => model.id.toLowerCase().includes(searchQuery))
})

// 计算不支持/已失效的模型
const unsupportedModels = computed(() => {
  if (providerConfig.allModels.length === 0) return []
  const availableIds = new Set(providerConfig.allModels.map((m) => m.id))
  return providerConfig.selectedModels.filter((id) => !availableIds.has(id))
})

// 手动管理相关
const manualModelInput = ref('')

// 添加手动输入的模型
const addManualModel = () => {
  const val = manualModelInput.value.trim()
  if (!val) return

  if (providerConfig.selectedModels.includes(val)) {
    message.warning('该模型已存在')
    return
  }

  providerConfig.selectedModels.push(val)
  manualModelInput.value = ''
  message.success('添加成功')
}

// 移除模型
const removeModel = (modelId) => {
  const idx = providerConfig.selectedModels.indexOf(modelId)
  if (idx > -1) {
    providerConfig.selectedModels.splice(idx, 1)
  }
}

// 移除所有不支持的模型
const removeAllUnsupported = () => {
  const toRemove = unsupportedModels.value
  providerConfig.selectedModels = providerConfig.selectedModels.filter(
    (id) => !toRemove.includes(id)
  )
  message.success(`已移除 ${toRemove.length} 个失效模型`)
}

// 自定义供应商管理
const customProviderForm = ref()
const customProviderModal = reactive({
  visible: false,
  isEdit: false,
  loading: false,
  data: {
    providerId: '',
    name: '',
    base_url: '',
    default: '',
    env: '',
    modelsText: '',
    models: [],
    url: ''
  }
})

// 自定义供应商表单验证规则
const customProviderRules = {
  providerId: [
    { required: true, message: '请输入供应商ID', trigger: 'blur' },
    {
      pattern: /^[a-zA-Z0-9_-]+$/,
      message: '供应商ID只能包含字母、数字、下划线和横线',
      trigger: 'blur'
    },
    {
      validator: (rule, value) => {
        if (!value) return Promise.resolve()
        // 检查是否与现有供应商ID重复
        if (modelNames.value && modelNames.value[value]) {
          return Promise.reject('供应商ID已存在，请使用其他ID')
        }
        return Promise.resolve()
      },
      trigger: 'blur'
    }
  ],
  name: [{ required: true, message: '请输入供应商名称', trigger: 'blur' }],
  base_url: [
    { required: true, message: '请输入API地址', trigger: 'blur' },
    { type: 'url', message: '请输入有效的URL地址', trigger: 'blur' }
  ],
  default: [{ required: true, message: '请输入默认模型', trigger: 'blur' }],
  env: [{ required: true, message: '请输入API密钥或环境变量', trigger: 'blur' }]
}

// API密钥掩码显示
const maskApiKey = (apiKey) => {
  if (!apiKey) return '未配置'

  // 如果是环境变量格式，直接显示
  if (apiKey.startsWith('${') && apiKey.endsWith('}')) {
    return apiKey
  }

  // 如果是直接的API密钥，进行掩码处理
  if (apiKey.length > 8) {
    return apiKey.substring(0, 4) + '***' + apiKey.substring(apiKey.length - 4)
  }
  return '***'
}

// 打开添加自定义供应商弹窗
const openAddCustomProviderModal = () => {
  customProviderModal.visible = true
  customProviderModal.isEdit = false
  resetCustomProviderForm()
}

// 打开编辑自定义供应商弹窗
const openEditCustomProviderModal = (providerId, provider) => {
  customProviderModal.visible = true
  customProviderModal.isEdit = true

  // 填充表单数据
  customProviderModal.data.providerId = providerId
  customProviderModal.data.name = provider.name
  customProviderModal.data.base_url = provider.base_url
  customProviderModal.data.default = provider.default
  customProviderModal.data.env = provider.env
  customProviderModal.data.models = provider.models || []
  customProviderModal.data.modelsText = (provider.models || []).join('\n')
  customProviderModal.data.url = provider.url || ''
}

// 重置自定义供应商表单
const resetCustomProviderForm = () => {
  customProviderModal.data = {
    providerId: '',
    name: '',
    base_url: '',
    default: '',
    env: '',
    modelsText: '',
    models: [],
    url: ''
  }
  customProviderForm.value?.resetFields()
}

// 保存自定义供应商
const saveCustomProvider = async () => {
  try {
    await customProviderForm.value.validate()
    customProviderModal.loading = true

    // 处理模型列表
    const models = customProviderModal.data.modelsText
      .split('\n')
      .map((model) => model.trim())
      .filter((model) => model.length > 0)

    // 校验默认模型必须在支持的模型列表中
    if (models.length > 0 && !models.includes(customProviderModal.data.default)) {
      message.error(`默认模型 "${customProviderModal.data.default}" 不在支持的模型列表中`)
      customProviderModal.loading = false
      return
    }

    const providerData = {
      name: customProviderModal.data.name,
      base_url: customProviderModal.data.base_url,
      default: customProviderModal.data.default,
      env: customProviderModal.data.env,
      models: models,
      url: customProviderModal.data.url,
      custom: true
    }

    let result
    if (customProviderModal.isEdit) {
      result = await customProviderApi.updateCustomProvider(
        customProviderModal.data.providerId,
        providerData
      )
      message.success('自定义供应商更新成功')
    } else {
      result = await customProviderApi.addCustomProvider(
        customProviderModal.data.providerId,
        providerData
      )
      message.success(`自定义供应商 ${customProviderModal.data.providerId} 添加成功`)
    }

    // 关闭弹窗并刷新配置
    customProviderModal.visible = false
    await configStore.refreshConfig()
  } catch (error) {
    if (error.errorFields) {
      // 表单验证错误
      return
    }

    // 处理API错误响应
    let errorMessage = '未知错误'
    if (error.response?.data?.detail) {
      errorMessage = error.response.data.detail
    } else if (error.message) {
      errorMessage = error.message
    } else if (typeof error === 'string') {
      errorMessage = error
    }

    message.error(`操作失败: ${errorMessage}`)
  } finally {
    customProviderModal.loading = false
  }
}

// 取消自定义供应商操作
const cancelCustomProvider = () => {
  customProviderModal.visible = false
  resetCustomProviderForm()
}

// 删除自定义供应商
const deleteCustomProvider = async (providerId) => {
  try {
    const result = await customProviderApi.deleteCustomProvider(providerId)
    message.success('自定义供应商删除成功')
    await configStore.refreshConfig()
  } catch (error) {
    message.error(`删除失败: ${error.message || error.response?.data?.detail || '未知错误'}`)
  }
}

// 测试自定义供应商连接
const testCustomProvider = async (providerId, modelName) => {
  try {
    message.loading({ content: '正在测试连接...', key: 'test-connection', duration: 0 })

    const result = await customProviderApi.testCustomProvider(providerId, modelName)

    if (result.status?.status === 'available') {
      message.success({ content: '连接测试成功', key: 'test-connection', duration: 2 })
    } else {
      message.error({
        content: `连接测试失败: ${result.status?.message || '未知错误'}`,
        key: 'test-connection',
        duration: 3
      })
    }
  } catch (error) {
    message.error({
      content: `测试失败: ${error.message || error.response?.data?.detail || '未知错误'}`,
      key: 'test-connection',
      duration: 3
    })
  }
}
</script>

<style lang="less" scoped>

.custom-providers-section {
  margin-bottom: 24px;
  .custom-provider-card {
    border: 1px solid var(--gray-200);
    background: var(--gray-0);
    border-radius: 8px;
    margin-bottom: 12px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);

    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 8px 12px;
      background: var(--gray-25);
      border-bottom: 1px solid var(--gray-200);

      .provider-info {
        display: flex;
        align-items: center;
        gap: 12px;

        h4 {
          margin: 0;
          font-weight: 500;
          color: var(--gray-900);
        }

        .provider-id {
          color: var(--gray-600);
          padding: 2px 8px;
          font-size: 12px;
          font-weight: 500;
        }
      }

      .provider-actions {
        display: flex;
        gap: 0px;
      }
    }

    .card-content {
      padding: 8px 12px;

      .provider-details {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
        gap: 12px 8px;

        .detail-item {
          display: flex;
          flex-direction: column;

          .label {
            font-size: 12px;
            color: var(--gray-600);
            font-weight: 500;
          }

          .value {
            font-size: 14px;
            color: var(--gray-900);
            word-break: break-all;
          }
        }
      }
    }
  }

  .empty-state {
    text-align: center;
    padding: 10px 20px;
    background: var(--gray-25);
    border-radius: 8px;
    border: 1px dashed var(--gray-300);
  }
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;

  .section-subtitle {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
    color: var(--gray-900);
  }
}

.builtin-providers-section {

  .section-header {
    .providers-stats {
      display: flex;
      gap: 12px;
      font-size: 13px;

      .stats-item {
        padding: 4px 8px;
        border-radius: 12px;
        font-size: 12px;

        &.available {
          background: var(--color-success-50);
          color: var(--color-success-700);
        }

        &.unavailable {
          background: var(--color-warning-50);
          color: var(--color-warning-700);
        }
      }
    }
  }
}

// 表单帮助文本样式
.form-help-text {
  font-size: 12px;
  color: var(--gray-600);
  margin-top: 4px;
  line-height: 1.4;
}

.model-provider-card {
  border: 1px solid var(--gray-150);
  background-color: var(--gray-0);
  border-radius: 8px;
  margin: 16px 0;
  padding: 0;
  overflow: hidden;

  // 已配置provider的样式
  &.configured-provider {
    .model-icon {
      filter: grayscale(0%);
    }
  }

  // 未配置provider的样式
  &.unconfigured-provider {
    .card-header {
      background: var(--gray-25);

      .model-name {
        color: var(--gray-700);
        font-weight: 500;
      }

      .model-icon {
        filter: grayscale(100%);
      }
    }
  }

  .card-header {
    display: flex;
    align-items: center;
    gap: 10px;
    cursor: pointer;
    padding: 6px 10px;
    background: var(--gray-0);

    .model-title-container {
      display: flex;
      flex-direction: column;
      flex: 1;

      .model-name {
        margin: 0;
        font-size: 14px;
        font-weight: 600;
        color: var(--gray-900);
      }
    }

    .model-url {
      font-size: 12px;
      width: fit-content;
      color: var(--gray-500);
      transition: color 0.2s ease;

      &:hover {
        color: var(--main-color);
      }
    }

    .model-icon {
      width: 32px;
      height: 32px;
      border-radius: 6px;
      overflow: hidden;
      filter: grayscale(100%);
      flex-shrink: 0;
      background-color: white;
      // border: 1px solid var(--gray-200);

      img {
        width: 100%;
        height: 100%;
        object-fit: contain;
        padding: 4px;
        border-radius: 8px;
      }

      &.available {
        filter: grayscale(0%);
      }
    }

    .expand-button,
    .config-button {
      height: 28px;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 4px 6px;
      cursor: pointer;
      color: var(--gray-800);
      border-radius: 6px;
      transition: all 0.2s ease;
      font-size: 12px;

      &:hover {
        background-color: var(--gray-50);
        color: var(--gray-700);
      }
    }

    a {
      text-decoration: none;
      color: var(--gray-500);
      font-size: 12px;
      transition: all 0.2s ease;

      &:hover {
        color: var(--main-color);
      }
    }

    .missing-keys {
      margin-left: auto;
      color: var(--gray-700);
      font-size: 12px;
      font-weight: 500;

      & > span {
        margin-left: 6px;
        user-select: all;
        background-color: var(--color-warning-50);
        color: var(--color-warning-700);
        padding: 3px 8px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 11px;
        border: 1px solid var(--color-warning-100);
      }
    }
  }

  .card-body-wrapper {
    max-height: 0;
    overflow: hidden;
    background: var(--gray-0);

    &.expanded {
      max-height: 800px;
    }
  }

  .card-body {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 8px;
    padding: 10px;
    padding-top: 4px;

    // 普通模型卡片样式
    .card-models {
      width: 100%;
      border-radius: 6px;
      border: 1px solid var(--gray-150);
      padding: 8px 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      box-sizing: border-box;
      background: var(--gray-0);
      // min-height: 48px;

      .model_name {
        font-size: 14px;
        font-weight: 500;
        color: var(--gray-900);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        line-height: 1.4;
      }
    }
  }
}

.provider-config-modal {
  .ant-modal-body {
    padding: 16px 0 !important;
    .modal-loading-container {
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: center;
      height: 200px;

      .loading-text {
        margin-top: 20px;
        color: var(--gray-700);
        font-size: 14px;
      }
    }

    .modal-config-content {
      max-height: 70vh;
      overflow-y: auto;

      .modal-config-header {
        margin-bottom: 20px;

        .description {
          font-size: 14px;
          color: var(--gray-600);
          margin: 0;
        }
      }

      .modal-models-section {
        .model-search {
          margin-bottom: 10px;
          padding: 0;

          .ant-input-affix-wrapper {
            border-radius: 6px;
          }
        }
        .selection-summary {
          margin: 0 6px 10px;
          font-size: 14px;
          color: var(--gray-600);

          .filter-info {
            color: var(--gray-500);
          }
        }
        .modal-checkbox-list {
          max-height: 50vh;
          overflow-y: auto;
          .modal-checkbox-item {
            display: inline-block;
            margin-bottom: 4px;
            margin-right: 4px;
            padding: 4px 6px;
            border-radius: 6px;
            background-color: var(--gray-0);
            border: 1px solid var(--gray-150);
          }
        }
      }
    }
  }
}

// 针对不同状态的额外样式调整
.unconfigured-provider {
  .card-body {
    .card-models {
      opacity: 0.6;
      pointer-events: none;

      .model_name {
        color: var(--gray-500);
      }
    }
  }
}

// Simple Notice Styles
.simple-notice {
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.5;
  margin-bottom: 12px;
  border: 1px solid transparent; // Keep a subtle border

  &.warning {
    background-color: var(--color-warning-50);
    color: var(--color-warning-700);
    border-color: var(--color-warning-100);
  }

  &.info {
    background-color: var(--color-info-50);
    color: var(--color-info-700);
    border-color: var(--color-info-100);
  }

  p {
    // For warning message, if it's multiline
    margin: 0;
  }

  .unsupported-list {
    margin-top: 8px;
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .clear-btn {
    margin-top: 8px;
    font-size: 12px;
    height: 24px;
    padding: 0 8px;
  }
}
</style>
