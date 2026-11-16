<template>
  <ActionDropdown
    :upward="upward"
    :block="resolvedSize !== 'nano'"
    :open="dropdownOpen"
    v-model:search="modelSearchKeyword"
    search-placeholder="搜索模型"
    :disabled="props.disabled"
    :width="300"
    @update:open="handleOpenChange"
  >
    <template #trigger>
      <ActionTrigger
        class="model-select"
        :class="modelSelectClasses"
        :variant="resolvedSize === 'nano' ? 'plain' : 'field'"
        :label="displayModelText"
        :title="displayModelTitle"
        :open="dropdownOpen"
        :disabled="props.disabled"
      >
        <template
          v-if="
            (props.clearable && props.model_spec && !props.disabled) ||
            (resolvedSize !== 'nano' && modelType === 'chat')
          "
          #actions
        >
          <button
            v-if="props.clearable && props.model_spec && !props.disabled"
            type="button"
            class="model-clear-btn"
            @mousedown.prevent.stop
            @click.stop="handleClear"
            title="清空选择"
            aria-label="清空模型选择"
          >
            <X :size="14" />
          </button>
          <div v-if="resolvedSize !== 'nano' && modelType === 'chat'" class="model-status-controls">
            <a-button
              :size="buttonSize"
              type="text"
              :class="state.checkingStatus ? '' : state.currentModelStatus?.status"
              :aria-busy="state.checkingStatus"
              :aria-label="state.checkingStatus ? '正在检查模型' : modelCheckLabel"
              :title="getCurrentModelStatusTitle()"
              @click.stop="checkCurrentModelStatus"
              :disabled="props.disabled || !props.model_spec || state.checkingStatus"
              class="status-check-button"
            >
              <a-spin v-if="state.checkingStatus" size="small" aria-hidden="true" />
              <span v-else>{{ modelCheckLabel }}</span>
            </a-button>
          </div>
        </template>
      </ActionTrigger>
    </template>
    <template #search-suffix>
      <button
        type="button"
        :disabled="props.disabled || state.refreshingCache || loadingV2Models"
        :title="state.refreshingCache ? '刷新中...' : '刷新模型'"
        aria-label="刷新模型"
        class="cache-refresh-button"
        @mousedown.prevent.stop
        @click.stop="refreshCache"
      >
        <RefreshCw :size="13" :class="{ spin: state.refreshingCache || loadingV2Models }" />
      </button>
    </template>
    <div v-if="loadingV2Models || state.refreshingCache" class="model-list-state" role="status">
      <a-spin size="small" />正在加载模型…
    </div>
    <div v-else-if="modelsError" class="model-list-state" role="alert">
      <span>{{ modelsError }}</span>
      <button type="button" class="config-dropdown-item model-retry" @click="fetchV2Models">
        重新加载
      </button>
    </div>
    <div v-else-if="!hasFilteredModels" class="model-list-state" role="status">
      {{ modelSearchKeyword ? '暂无匹配模型' : '暂无可用模型' }}
    </div>
    <template v-else>
      <div
        v-for="(providerData, providerId) in filteredV2Models"
        :key="providerId"
        class="model-provider-group"
      >
        <div class="model-provider-title" :title="providerId">
          {{ getProviderDisplayName(providerId, providerData) }}
        </div>
        <button
          v-for="model in providerData.models"
          :key="model.spec"
          type="button"
          class="config-dropdown-item model-option"
          :class="{ selected: model.spec === props.model_spec }"
          @click="handleSelectV2Model(model.spec)"
        >
          <span class="config-dropdown-item-label" :title="model.display_name"
            >{{ model.display_name }}
            <span v-if="modelType === 'embedding' && model.dimension" class="model-dimension"
              >({{ model.dimension }})</span
            >
          </span>
          <span class="model-option-signals">
            <span
              v-if="modelType === 'embedding'"
              class="model-status-icon"
              :class="getStatusClass(model.spec)"
              :title="getStatusTooltip(model.spec)"
              >{{ getStatusIcon(model.spec) }}</span
            >
            <span
              v-if="getModelInfo(model).vision"
              class="model-signal-icon"
              role="img"
              aria-label="支持图像输入"
              title="支持图像输入"
              ><Eye :size="13"
            /></span>
            <span
              v-if="getModelInfo(model).isOneMillionContext"
              class="model-context-badge"
              title="约 1M tokens 上下文窗口"
              >1M</span
            >
            <Check
              v-if="model.spec === props.model_spec"
              :size="14"
              class="config-dropdown-item-check"
            />
          </span>
        </button>
      </div>
    </template>
    <template #footer>
      <div
        v-if="
          modelType === 'chat' &&
          !modelMetadataNoticeDismissed &&
          (userStore.isAdmin || hasModelMetadata)
        "
        class="model-metadata-source"
      >
        <div class="model-metadata-source-content">
          <template v-if="userStore.isAdmin">
            没有合适的模型？
            <RouterLink :to="{ path: '/api-services', query: { tab: 'providers' } }" @click.stop>
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
        <button
          type="button"
          class="model-metadata-source-close"
          title="不再显示"
          aria-label="关闭模型信息提示"
          @click.stop="dismissModelMetadataNotice"
        >
          <X :size="13" />
        </button>
      </div>
    </template>
  </ActionDropdown>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import ActionDropdown from '@/shared/ui/ActionDropdown.vue'
import ActionTrigger from '@/shared/ui/ActionTrigger.vue'
import { modelProviderApi } from '@/apis/system_api'
import { Eye, RefreshCw, X, Check } from '@lucide/vue'
import { useModelStatus } from '@/modules/settings'
import { useUserStore } from '@/modules/identity'
import {
  invalidateModelDirectory,
  loadModelMetadataCatalog,
  resolveModelDisplayMetadata,
  useModelDirectory
} from '@/modules/settings'

const props = defineProps({
  modelType: {
    type: String,
    default: 'chat',
    validator: (value) => ['chat', 'embedding', 'rerank'].includes(value)
  },
  upward: { type: Boolean, default: false },
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
const modelDirectory = useModelDirectory(props.modelType)
const { getStatusIcon, getStatusClass, getStatusTooltip, checkV2Statuses } = useModelStatus()
const MODEL_METADATA_NOTICE_DISMISSED_KEY = 'yuxi_model_metadata_notice_dismissed'

// v2 模型数据：每次展开下拉时实时从后端拉取
const v2Models = ref({})
const loadingV2Models = ref(false)
const modelsError = ref('')
const dropdownOpen = ref(false)
const modelSearchKeyword = ref('')
const modelMetadataBySpec = ref({})
const modelMetadataNoticeDismissed = ref(
  localStorage.getItem(MODEL_METADATA_NOTICE_DISMISSED_KEY) === 'true'
)
let fetchV2ModelsPromise = null

const dismissModelMetadataNotice = () => {
  modelMetadataNoticeDismissed.value = true
  localStorage.setItem(MODEL_METADATA_NOTICE_DISMISSED_KEY, 'true')
}

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

/** 加载真实模型目录，可选元数据独立补齐，不阻塞选项。 */
const fetchV2Models = async () => {
  if (fetchV2ModelsPromise) return fetchV2ModelsPromise
  loadingV2Models.value = true
  modelsError.value = ''
  fetchV2ModelsPromise = (async () => {
    try {
      const response = await modelProviderApi.getV2Models(props.modelType)
      if (!response.success) throw new Error('模型目录请求失败')
      v2Models.value = response.data || {}
      if (props.modelType === 'embedding') {
        const models = Object.values(v2Models.value).flatMap((provider) => provider.models || [])
        void checkV2Statuses(models)
      }
      if (props.modelType === 'chat') {
        loadModelMetadataCatalog()
          .then((catalog) => {
            modelMetadataBySpec.value = catalog
              ? buildModelMetadataBySpec(v2Models.value, catalog.providers)
              : {}
          })
          .catch((error) => {
            console.warn('Failed to load model metadata catalog:', error)
          })
      }
    } catch (error) {
      modelsError.value = '模型加载失败，请重试'
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

/** 先展开再加载，关闭后完成请求不会重新打开弹层。 */
const handleOpenChange = (open) => {
  dropdownOpen.value = open && !props.disabled
  if (dropdownOpen.value) void fetchV2Models()
}

// 强制刷新缓存
const refreshCache = async () => {
  if (props.disabled || state.refreshingCache) return
  state.refreshingCache = true
  try {
    await modelProviderApi.refreshModelCache()
    invalidateModelDirectory(props.modelType)
    // 刷新后重新拉取模型列表与别名
    await fetchV2Models()
    await syncResolvedModelAlias()
  } catch (error) {
    modelsError.value = '模型刷新失败，请重试'
    console.error('Failed to refresh cache:', error)
  } finally {
    state.refreshingCache = false
  }
}

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

// 选中态优先展示后端配置的别名，目录未就绪时回退到供应商原始模型名。
const resolvedModelAlias = ref('')
const syncResolvedModelAlias = async () => {
  const spec = props.model_spec
  if (!spec) {
    resolvedModelAlias.value = ''
    return
  }
  try {
    await modelDirectory.load()
    // 目录加载期间 spec 已切换时放弃本次结果，由 watch 重新触发解析。
    if (props.model_spec !== spec) return
    resolvedModelAlias.value = modelDirectory.resolveDisplayName({ spec })
  } catch {
    resolvedModelAlias.value = ''
  }
}

watch(() => props.model_spec, syncResolvedModelAlias)
onMounted(syncResolvedModelAlias)

const displayModelText = computed(() => {
  const spec = props.model_spec
  if (!spec) return props.placeholder
  const modelName = resolvedModelAlias.value || extractModelName(spec)
  if (props.displayName === 'mini') {
    return modelName.includes('/') ? modelName.split('/').pop() : modelName
  }
  if (resolvedModelAlias.value || props.displayName === 'short') return modelName
  return spec
})

// 悬停标题保持完整 spec（别名命中时优先别名），不回退到截取名。
const displayModelTitle = computed(
  () => resolvedModelAlias.value || props.model_spec || props.placeholder
)

// 检查当前模型状态
const checkCurrentModelStatus = async () => {
  if (props.disabled || state.checkingStatus) return
  const spec = props.model_spec
  if (!spec) return

  try {
    state.checkingStatus = true
    const response = await modelProviderApi.getModelStatusBySpec(spec)
    if (props.model_spec !== spec) return
    if (response.data) {
      state.currentModelStatus = response.data
    } else {
      state.currentModelStatus = null
    }
  } catch (error) {
    console.error(`检查模型 ${spec} 状态失败:`, error)
    if (props.model_spec === spec) state.currentModelStatus = { status: 'error', message: error.message }
  } finally {
    state.checkingStatus = false
  }
}

const modelCheckLabel = computed(() => {
  const status = state.currentModelStatus
  if (!status) return '检查'
  return status.status === 'available' ? '通过' : '异常'
})

const getCurrentModelStatusTitle = () => {
  const status = state.currentModelStatus
  if (!status) return '检查模型是否可用'

  let statusText = ''
  if (status.status === 'available') statusText = '可用'
  else if (status.status === 'unavailable') statusText = '不可用'
  else if (status.status === 'error') statusText = '错误'

  const message = status.message || '无详细信息'
  return `${statusText}: ${message}；点击重新检查`
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
.model-dimension {
  color: var(--gray-500);
  font-size: 12px;
}
.model-status-icon {
  font-size: 11px;
  &.available {
    color: var(--color-success-500);
  }
  &.unavailable,
  &.error {
    color: var(--color-error-500);
  }
}

.model-select {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  max-width: 100%;
}
.model-status-controls {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
}
.model-select--middle :deep(.config-dropdown-trigger) {
  font-size: 15px;
}
.model-select--large :deep(.config-dropdown-trigger) {
  font-size: 16px;
}
.model-list-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 96px;
  padding: 16px 8px;
  color: var(--gray-500);
  font-size: 13px;
}
.model-list-state[role='alert'] {
  flex-direction: column;
}
.model-retry {
  justify-content: center;
  color: var(--main-600);
}
.model-provider-title {
  padding: 8px;
  color: var(--gray-500);
  font-size: 12px;
}
.spin {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

// 状态检查按钮
.status-check-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 36px;
  font-size: 12px;
  padding: 0 4px;

  &.available {
    color: var(--color-success-700);
  }

  &.unavailable,
  &.error {
    color: var(--color-error-700);
  }
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
  color: var(--gray-400);
  cursor: pointer;
  transition: color 0.15s ease;

  &:hover:not(:disabled) {
    color: var(--gray-600);
  }
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

.model-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
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
  display: flex;
  align-items: flex-start;
  gap: 6px;
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

.model-metadata-source-content {
  flex: 1;
  min-width: 0;
}

.model-metadata-source-close {
  appearance: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  margin: -2px -3px 0 0;
  padding: 0;
  border: none;
  border-radius: 4px;
  color: var(--gray-500);
  background: transparent;
  cursor: pointer;
  transition:
    color 0.15s ease,
    background-color 0.15s ease;

  &:hover {
    color: var(--gray-800);
    background: var(--gray-100);
  }

  &:focus-visible {
    outline: 2px solid var(--main-400);
    outline-offset: 1px;
  }
}
</style>
