<template>
  <div class="search-config-panel">
    <div v-if="loading" class="config-loading">
      <a-spin />
      <p>加载配置参数中...</p>
    </div>

    <a-result v-else-if="error" status="error" title="配置加载失败" :sub-title="error">
      <template #extra>
        <a-button type="primary" @click="loadQueryParams">重新加载</a-button>
      </template>
    </a-result>

    <template v-else>
      <a-empty v-if="visibleQueryParams.length === 0" description="暂无可配置参数" />
      <a-form layout="vertical">
        <a-row :gutter="16">
          <a-col :span="12" v-for="param in visibleQueryParams" :key="param.key">
            <a-form-item :label="param.label">
              <template #extra v-if="param.description">
                <div class="param-description">{{ param.description }}</div>
              </template>
              <a-select
                v-if="param.type === 'select'"
                v-model:value="meta[param.key]"
                style="width: 100%"
              >
                <a-select-option
                  v-for="option in param.options"
                  :key="option.value"
                  :value="option.value"
                >
                  {{ option.label }}
                </a-select-option>
              </a-select>
              <a-select
                v-else-if="param.type === 'boolean'"
                :value="computedMeta[param.key]"
                @update:value="(value) => updateMeta(param.key, value)"
                style="width: 100%"
              >
                <a-select-option value="true">启用</a-select-option>
                <a-select-option value="false">关闭</a-select-option>
              </a-select>
              <a-input-number
                v-else-if="param.type === 'number'"
                v-model:value="meta[param.key]"
                style="width: 100%"
                :min="param.min || 0"
                :max="param.max || 100"
                :step="param.step"
              />
              <a-input v-else v-model:value="meta[param.key]" />
            </a-form-item>
          </a-col>
        </a-row>
      </a-form>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { useDatabaseStore } from '@/stores/database'
import { message } from 'ant-design-vue'
import { queryApi } from '@/apis/knowledge_api'
import {
  createSearchConfigSnapshot,
  isSearchParamVisible,
  searchConfigChanged
} from '@/utils/searchConfig'

const props = defineProps({
  kbId: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['save'])

const store = useDatabaseStore()

const loading = ref(false)
const error = ref('')
const queryParams = ref([])
const meta = reactive({})
const initialConfig = ref({})

const getConfigSnapshot = () => createSearchConfigSnapshot(queryParams.value, meta)
const hasChanges = () => searchConfigChanged(getConfigSnapshot(), initialConfig.value)

const visibleQueryParams = computed(() =>
  queryParams.value.filter((param) => isSearchParamVisible(param, meta))
)

const computedMeta = computed(() => {
  const result = {}
  for (const key in meta) {
    const param = queryParams.value.find((p) => p.key === key)
    if (param?.type === 'boolean') {
      result[key] = meta[key].toString()
    } else {
      result[key] = meta[key]
    }
  }
  return result
})

const updateMeta = (key, value) => {
  const param = queryParams.value.find((p) => p.key === key)
  if (param?.type === 'boolean') {
    meta[key] = value === 'true'
  } else {
    meta[key] = value
  }
}

const loadQueryParams = async () => {
  if (!props.kbId) {
    queryParams.value = []
    initialConfig.value = {}
    return
  }

  loading.value = true
  error.value = ''
  try {
    const response = await queryApi.getKnowledgeBaseQueryParams(props.kbId)
    queryParams.value = (response.params?.options || []).filter(
      (param) => param.key !== 'include_distances'
    )

    const supportedKeys = new Set(queryParams.value.map((param) => param.key))
    for (const key in meta) {
      if (key !== 'include_distances' && !supportedKeys.has(key)) {
        delete meta[key]
      }
    }
    for (const param of queryParams.value) {
      delete meta[param.key]
      if (param.default !== undefined) {
        meta[param.key] = param.type === 'boolean' ? Boolean(param.default) : param.default
      }
    }
    meta.include_distances = true
    initialConfig.value = getConfigSnapshot()
  } catch (err) {
    console.error('Failed to load query params:', err)
    error.value = err.message || '加载查询参数失败'
  } finally {
    loading.value = false
  }
}

const save = async ({ notify = true } = {}) => {
  if (!props.kbId) {
    message.error('无法保存配置：缺少知识库ID')
    return false
  }

  meta.include_distances = true

  try {
    const response = await queryApi.updateKnowledgeBaseQueryParams(props.kbId, { ...meta })
    if (response.message === 'success') {
      Object.assign(store.meta, meta)
      initialConfig.value = getConfigSnapshot()
      if (notify) message.success('配置已保存')
      emit('save', { ...meta })
      return true
    } else {
      throw new Error(response.message || '保存失败')
    }
  } catch (err) {
    console.error('保存配置到知识库失败:', err)
    message.error('保存配置失败：' + (err.message || '未知错误'))
    return false
  }
}

const resetToDefaults = () => {
  queryParams.value.forEach((param) => {
    if (param.default !== undefined) {
      meta[param.key] = param.default
    }
  })
  meta.include_distances = true
  message.success('已重置为默认配置')
}

watch(
  () => props.kbId,
  (newId) => {
    if (newId) {
      loadQueryParams()
    }
  },
  { immediate: true }
)

defineExpose({ save, resetToDefaults, loadQueryParams, hasChanges })
</script>

<style lang="less" scoped>
.search-config-panel {
  width: 100%;
  min-width: 0;
  max-width: 100%;

  :deep(.ant-row) {
    margin-inline: 0 !important;
  }

  :deep(.ant-col) {
    min-width: 0;
  }

  .config-loading {
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    min-height: 200px;
    color: var(--gray-500);

    p {
      margin-top: 16px;
      font-size: 14px;
    }
  }

  .param-description {
    font-size: 12px;
    color: var(--gray-500);
    line-height: 1.5;
    margin-top: 4px;
  }

  :deep(.ant-form-item) {
    margin-bottom: 16px;
  }

  :deep(.ant-form-item-label > label) {
    font-weight: 500;
    color: var(--gray-700);
  }

  :deep(.ant-input),
  :deep(.ant-select-selector) {
    border-radius: 6px;
  }
}
</style>
