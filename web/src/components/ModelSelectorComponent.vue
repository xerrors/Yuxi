<template>
  <a-dropdown
    trigger="click"
    :open="dropdownOpen"
    :disabled="props.disabled"
    @open-change="handleOpenChange"
  >
    <div class="model-select" :class="modelSelectClasses" @click.prevent.stop @mousedown.stop>
      <div class="model-select-content">
        <div class="model-info">
          <span class="model-text text" :title="displayModelTitle">{{ displayModelText }}</span>
          <button
            v-if="props.clearable && props.model_spec && !props.disabled"
            class="model-clear-btn"
            @mousedown.prevent.stop
            @click.stop="handleClear"
            title="清空选择"
          >
            <X :size="14" />
          </button>
        </div>
        <div v-if="resolvedSize !== 'nano'" class="model-status-controls">
          <span
            v-if="state.currentModelStatus"
            class="model-status-indicator"
            :class="state.currentModelStatus.status"
            :title="getCurrentModelStatusTitle()"
          >
            {{ modelStatusIcon }}
          </span>
          <a-button
            :size="buttonSize"
            type="text"
            :loading="state.checkingStatus"
            @click.stop="checkCurrentModelStatus"
            :disabled="props.disabled || state.checkingStatus"
            class="status-check-button"
          >
            {{ state.checkingStatus ? '检查中...' : '检查' }}
          </a-button>
        </div>
      </div>
    </div>
    <template #overlay>
      <div class="model-dropdown" @click.stop>
        <div class="model-search">
          <a-input
            v-model:value="modelSearchKeyword"
            placeholder="搜索模型"
            allow-clear
            autocomplete="off"
            autocapitalize="none"
            autocorrect="off"
            spellcheck="false"
            @keydown.stop
          >
            <template #suffix>
              <button
                :disabled="props.disabled || state.refreshingCache"
                :title="state.refreshingCache ? '刷新中...' : '刷新缓存'"
                class="cache-refresh-button"
                @mousedown.prevent.stop
                @click.stop="refreshCache"
              >
                <RefreshCw :size="13" :class="{ spin: state.refreshingCache }" />
              </button>
            </template>
          </a-input>
        </div>
        <a-menu class="scrollable-menu">
          <a-menu-item v-if="loadingV2Models" key="loading" disabled>加载中...</a-menu-item>
          <a-menu-item v-else-if="!hasFilteredModels" key="empty" disabled
            >暂无匹配模型</a-menu-item
          >
          <template v-else>
            <a-menu-item-group
              v-for="(providerData, providerId) in filteredV2Models"
              :key="`v2-${providerId}`"
            >
              <template #title>
                <span :title="providerId">{{
                  getProviderDisplayName(providerId, providerData)
                }}</span>
              </template>
              <a-menu-item
                v-for="model in providerData.models"
                :key="model.spec"
                @click="handleSelectV2Model(model.spec)"
              >
                <div class="model-option">
                  <span class="model-option-name">{{ model.display_name }}</span>
                  <div class="model-option-signals">
                    <a-tooltip v-if="getModelInfo(model).vision" title="支持图像输入">
                      <span class="model-signal-icon" role="img" aria-label="支持图像输入">
                        <Eye :size="13" />
                      </span>
                    </a-tooltip>
                    <a-tooltip
                      v-if="getModelInfo(model).isOneMillionContext"
                      title="约 1M tokens 上下文窗口"
                    >
                      <span class="model-context-badge">1M</span>
                    </a-tooltip>
                  </div>
                </div>
              </a-menu-item>
            </a-menu-item-group>
          </template>
        </a-menu>
        <div v-if="userStore.isAdmin || hasModelMetadata" class="model-metadata-source">
          <template v-if="userStore.isAdmin">
            没有合适的模型？
            <RouterLink :to="{ path: '/agent-manage', query: { tab: 'providers' } }" @click.stop>
              配置模型
            </RouterLink>
          </template>
          <template v-if="hasModelMetadata">
            <span v-if="userStore.isAdmin">。 </span>
            部分信息（价格、能力等）来自
            <a href="https://models.dev" target="_blank" rel="noreferrer" @click.stop>models.dev</a>
            填补。仅供参考，可能和官网有偏差。
          </template>
        </div>
      </div>
    </template>
  </a-dropdown>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { modelProviderApi } from '@/apis/system_api'
import { Eye, RefreshCw, X } from 'lucide-vue-next'
import { useModelStatus } from '@/composables/useModelStatus'
import { useUserStore } from '@/stores/user'
import { loadModelMetadataCatalog, resolveModelDisplayMetadata } from '@/utils/modelMetadata'

const props = defineProps({
  model_spec: {
    type: String,
    default: ''
  },
  placeholder: {
    type: String,
    default: '请选择模型'
  },
  size: {
    type: String,
    default: 'small',
    validator: (value) => ['nano', 'small', 'middle', 'large'].includes(value)
  },
  disabled: {
    type: Boolean,
    default: false
  },
  clearable: {
    type: Boolean,
    default: false
  },
  displayName: {
    type: String,
    default: 'full',
    validator: (value) => ['full', 'short', 'mini'].includes(value)
  }
})

const emit = defineEmits(['select-model'])
const userStore = useUserStore()

// v2 模型数据：每次展开下拉时实时从后端拉取
const v2Models = ref({})
const loadingV2Models = ref(false)
const dropdownOpen = ref(false)
const modelSearchKeyword = ref('')
const modelMetadataBySpec = ref({})
let fetchV2ModelsPromise = null

const filteredV2Models = computed(() => {
  const keyword = modelSearchKeyword.value.trim().toLowerCase()
  if (!keyword) return v2Models.value

  return Object.entries(v2Models.value).reduce((result, [providerId, providerData]) => {
    const models = (providerData.models || []).filter((model) => {
      return [
        providerId,
        getProviderDisplayName(providerId, providerData),
        model.spec,
        model.model_id,
        model.display_name
      ].some((value) =>
        String(value || '')
          .toLowerCase()
          .includes(keyword)
      )
    })

    if (models.length) {
      result[providerId] = { ...providerData, models }
    }

    return result
  }, {})
})

const hasFilteredModels = computed(() => {
  return Object.values(filteredV2Models.value).some((providerData) => providerData.models?.length)
})

const hasModelMetadata = computed(() => Object.keys(modelMetadataBySpec.value).length > 0)

const getProviderDisplayName = (providerId, providerData = {}) => {
  return (
    providerData.provider_display_name ||
    providerData.display_name ||
    providerData.name ||
    providerId
  )
}

// 拉取 v2 模型列表
const fetchV2Models = async () => {
  if (fetchV2ModelsPromise) return fetchV2ModelsPromise

  loadingV2Models.value = true
  fetchV2ModelsPromise = (async () => {
    try {
      const catalogRequest = loadModelMetadataCatalog().catch((error) => {
        console.warn('Failed to load model metadata catalog:', error)
        return null
      })
      const response = await modelProviderApi.getV2Models('chat')
      if (response.success) {
        v2Models.value = response.data || {}
        const catalog = await catalogRequest
        modelMetadataBySpec.value = catalog
          ? buildModelMetadataBySpec(v2Models.value, catalog.providers)
          : {}
      }
    } catch (error) {
      console.warn('Failed to load v2 models:', error)
    } finally {
      loadingV2Models.value = false
      fetchV2ModelsPromise = null
    }
  })()

  return fetchV2ModelsPromise
}

const buildModelMetadataBySpec = (modelsByProvider, providers) => {
  return Object.entries(modelsByProvider).reduce((result, [providerId, providerData]) => {
    for (const model of providerData.models || []) {
      const info = resolveModelDisplayMetadata(providers, providerId, model)
      if (info.matched) result[model.spec] = info
    }
    return result
  }, {})
}

const getModelInfo = (model) => modelMetadataBySpec.value[model.spec] || {}

// 下拉展开前先刷新模型列表，避免弹层打开后再因数据加载发生高度跳变。
const handleOpenChange = async (open) => {
  if (props.disabled) {
    dropdownOpen.value = false
    return
  }

  if (!open) {
    dropdownOpen.value = false
    return
  }

  await fetchV2Models()
  if (!props.disabled) {
    dropdownOpen.value = true
  }
}

// 强制刷新缓存
const refreshCache = async () => {
  if (props.disabled || state.refreshingCache) return
  state.refreshingCache = true
  try {
    await modelProviderApi.refreshModelCache()
    // 刷新后重新拉取模型列表
    await fetchV2Models()
  } catch (error) {
    console.error('Failed to refresh cache:', error)
  } finally {
    state.refreshingCache = false
  }
}

// 状态管理
useModelStatus()
const state = reactive({
  currentModelStatus: null,
  checkingStatus: false,
  refreshingCache: false
})

watch(
  () => props.model_spec,
  (spec, previousSpec) => {
    if (spec !== previousSpec) {
      state.currentModelStatus = null
    }
  }
)

const resolvedSize = computed(() => props.size || 'small')
const modelSelectClasses = computed(() => ({
  'model-select--nano': resolvedSize.value === 'nano',
  'model-select--middle': resolvedSize.value === 'middle',
  'model-select--large': resolvedSize.value === 'large',
  'model-select--disabled': props.disabled
}))
const buttonSize = computed(() => {
  if (resolvedSize.value === 'large') return 'large'
  if (resolvedSize.value === 'middle') return 'middle'
  return 'small'
})

const extractModelName = (spec) => {
  const separatorIndex = spec.indexOf(':')
  return separatorIndex >= 0 ? spec.slice(separatorIndex + 1) : spec
}

const displayModelText = computed(() => {
  const spec = props.model_spec
  if (!spec) return props.placeholder

  const modelName = extractModelName(spec)
  if (props.displayName === 'mini') {
    return modelName.includes('/') ? modelName.split('/').pop() : modelName
  }
  if (props.displayName === 'short') return modelName
  return spec
})

const displayModelTitle = computed(() => props.model_spec || props.placeholder)

// 检查当前模型状态
const checkCurrentModelStatus = async () => {
  if (props.disabled) return
  const spec = props.model_spec
  if (!spec) return

  try {
    state.checkingStatus = true
    const response = await modelProviderApi.getModelStatusBySpec(spec)
    if (response.data) {
      state.currentModelStatus = response.data
    } else {
      state.currentModelStatus = null
    }
  } catch (error) {
    console.error(`检查模型 ${spec} 状态失败:`, error)
    state.currentModelStatus = { status: 'error', message: error.message }
  } finally {
    state.checkingStatus = false
  }
}

const modelStatusIcon = computed(() => {
  const status = state.currentModelStatus
  if (!status) return '○'
  if (status.status === 'available') return '✓'
  if (status.status === 'unavailable') return '✗'
  if (status.status === 'error') return '⚠'
  return '○'
})

const getCurrentModelStatusTitle = () => {
  const status = state.currentModelStatus
  if (!status) return '状态未知'

  let statusText = ''
  if (status.status === 'available') statusText = '可用'
  else if (status.status === 'unavailable') statusText = '不可用'
  else if (status.status === 'error') statusText = '错误'

  const message = status.message || '无详细信息'
  return `${statusText}: ${message}`
}

// 选择 v2 模型的方法
const handleSelectV2Model = (spec) => {
  if (props.disabled) return
  emit('select-model', spec)
  dropdownOpen.value = false
}

// 清空选择
const handleClear = () => {
  if (props.disabled) return
  state.currentModelStatus = null
  emit('select-model', '')
  dropdownOpen.value = false
}
</script>

<style lang="less" scoped>
@import '@/assets/css/model-selector-common.less';

// 状态检查按钮
.status-check-button {
  font-size: 12px;
  padding: 0 4px;
}

// 缓存刷新按钮
.cache-refresh-button {
  font-size: 12px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  background-color: transparent;
  border: none;
  outline: none;
  cursor: pointer;
}

.model-select--nano {
  max-width: 100%;
  height: 30px;
  padding: 6px 8px;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1;
  color: var(--gray-600);
  background: transparent;
  transition: all 0.2s ease;
  user-select: none;
}

.model-select--nano:hover {
  color: var(--gray-900);
  background: var(--gray-50);
}

.model-select--nano .model-text {
  color: currentColor;
}

.model-clear-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  margin-left: 2px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--gray-400);
  cursor: pointer;
  transition: all 0.15s ease;

  &:hover {
    color: var(--gray-700);
    background: var(--gray-100);
  }
}

.model-select--disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.model-dropdown {
  min-width: 320px;
  max-width: min(440px, calc(100vw - 24px));
  overflow: hidden;
  background: var(--gray-0);
  border-radius: 8px;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.08);
}

.model-search {
  padding: 8px;
  border-bottom: 1px solid var(--gray-100);
}

:deep(.ant-dropdown-menu) {
  &.scrollable-menu {
    max-height: 260px;
    overflow-y: auto;
    box-shadow: none;
  }

  .ant-dropdown-menu-item,
  .ant-dropdown-menu-submenu-title {
    border-radius: 6px;
  }

  .ant-dropdown-menu-item:hover,
  .ant-dropdown-menu-submenu-title:hover,
  .ant-dropdown-menu-item-active,
  .ant-dropdown-menu-submenu-title-active {
    background: var(--gray-50);
  }

  .ant-dropdown-menu-item-selected,
  .ant-dropdown-menu-item-selected:hover,
  .ant-dropdown-menu-item-selected.ant-dropdown-menu-item-active {
    color: var(--gray-1000);
    background: var(--main-50);
  }

  .ant-dropdown-menu-item {
    padding-top: 7px;
    padding-bottom: 7px;
  }
}

.model-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.model-option-name {
  display: block;
  min-width: 0;
  overflow: hidden;
  color: var(--gray-1000);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-option-signals {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
  color: var(--gray-500);
}

.model-context-badge {
  display: inline-flex;
  align-items: center;
  height: 17px;
  padding: 0 5px;
  border-radius: 4px;
  color: var(--gray-600);
  background: var(--gray-100);
  font-size: 10px;
  font-weight: 600;
}

.model-signal-icon {
  display: inline-flex;
  align-items: center;
}

.model-metadata-source {
  padding: 7px 10px;
  border-top: 1px solid var(--gray-100);
  color: var(--gray-500);
  background: var(--gray-25);
  font-size: 11px;
  line-height: 16px;

  a {
    color: var(--main-600);
  }
}
</style>
