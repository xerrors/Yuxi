<template>
  <div class="database-container layout-container">
    <PageHeader
      v-if="!props.embedded"
      title="知识库"
      :active-key="knowledgeActiveView"
      :tabs="knowledgeViewItems"
      :loading="dbState.listLoading"
      :show-border="true"
      aria-label="知识库视图切换"
    />

    <PageShoulder v-model:search="searchQuery" search-placeholder="搜索知识库...">
      <template #filters>
        <a-select
          v-model:value="typeFilter"
          style="width: 120px"
          placeholder="全部类型"
          allow-clear
        >
          <a-select-option :value="null">全部类型</a-select-option>
          <a-select-option v-for="t in kbTypes" :key="t" :value="t">
            {{ getKbTypeLabel(t) }}
          </a-select-option>
        </a-select>
      </template>
      <template #actions>
        <a-button
          type="primary"
          class="lucide-icon-btn"
          :disabled="!kbTypes.length"
          @click="state.openNewDatabaseModel = true"
        >
          <Plus :size="16" /> 新建知识库
        </a-button>
      </template>
    </PageShoulder>

    <a-modal
      :open="state.openNewDatabaseModel"
      title="新建知识库"
      :confirm-loading="dbState.creating"
      @ok="handleCreateDatabase"
      @cancel="cancelCreateDatabase"
      class="new-database-modal"
      width="800px"
      destroyOnClose
    >
      <div class="new-database-form">
        <!-- 知识库类型选择 -->
        <div class="form-section">
          <h3 class="section-title">知识库类型<span class="required-mark">*</span></h3>
          <div class="kb-type-cards">
            <div
              v-for="(typeInfo, typeKey) in orderedKbTypes"
              :key="typeKey"
              class="kb-type-card"
              :class="{ active: newDatabase.kb_type === typeKey }"
              :data-type="typeKey"
              @click="handleKbTypeChange(typeKey)"
            >
              <div class="card-header">
                <component :is="getKbTypeIcon(typeKey)" class="type-icon" />
                <span class="type-title">{{ getKbTypeLabel(typeKey) }}</span>
              </div>
              <div class="card-description">{{ getKbTypeDescription(typeInfo) }}</div>
            </div>
          </div>
        </div>

        <div class="form-section">
          <h3 class="section-title">知识库名称<span class="required-mark">*</span></h3>
          <a-input v-model:value="newDatabase.name" placeholder="新建知识库名称" />
        </div>

        <div v-if="selectedKbTypeInfo?.requires_embedding_model" class="form-grid two-columns">
          <div class="form-section compact-section">
            <h3 class="section-title">嵌入模型</h3>
            <EmbeddingModelSelector
              v-model:value="newDatabase.embedding_model_spec"
              class="full-width"
              placeholder="请选择嵌入模型"
            />
          </div>

          <div class="form-section compact-section">
            <div class="chunk-preset-title-row">
              <h3 class="section-title">分块策略</h3>
              <a-tooltip :title="selectedPresetDescription">
                <QuestionCircleOutlined class="chunk-preset-help-icon" />
              </a-tooltip>
            </div>
            <a-select
              v-model:value="newDatabase.chunk_preset_id"
              :options="chunkPresetOptions"
              class="full-width"
            />
          </div>
        </div>

        <div v-if="createParamOptions.length" class="form-grid three-columns">
          <div
            v-for="field in createParamOptions"
            :key="field.key"
            class="form-section compact-section"
          >
            <h3 class="section-title">
              {{ field.label || field.key
              }}<span v-if="field.required" class="required-mark">*</span>
            </h3>
            <a-input-password
              v-if="field.type === 'password'"
              v-model:value="newDatabase.additional_params[field.key]"
              :placeholder="field.placeholder"
            />
            <a-input-number
              v-else-if="field.type === 'number'"
              v-model:value="newDatabase.additional_params[field.key]"
              :min="field.min"
              :max="field.max"
              :step="field.step"
              class="full-width"
            />
            <a-switch
              v-else-if="field.type === 'boolean'"
              v-model:checked="newDatabase.additional_params[field.key]"
            />
            <a-select
              v-else-if="field.type === 'select'"
              v-model:value="newDatabase.additional_params[field.key]"
              :options="field.options || []"
              class="full-width"
            />
            <a-input
              v-else
              v-model:value="newDatabase.additional_params[field.key]"
              :placeholder="field.placeholder"
            />
            <p v-if="field.description" class="field-hint">{{ field.description }}</p>
          </div>
        </div>

        <div class="form-section">
          <h3 class="section-title">知识库描述</h3>
          <p class="field-hint description-hint">
            在智能体流程中，这里的描述会作为工具的描述。智能体会根据知识库的标题和描述来选择合适的工具。所以这里描述的越详细，智能体越容易选择到合适的工具。
          </p>
          <AiTextarea
            v-model="newDatabase.description"
            :name="newDatabase.name"
            placeholder="新建知识库描述"
            :auto-size="{ minRows: 3, maxRows: 10 }"
          />
        </div>

        <!-- 共享配置 -->
        <div class="form-section compact-section">
          <h3 class="section-title">共享设置</h3>
          <ShareConfigForm
            ref="shareConfigFormRef"
            v-model="shareConfig"
            :auto-select-user-dept="true"
          />
        </div>
      </div>
      <template #footer>
        <a-button key="back" @click="cancelCreateDatabase">取消</a-button>
        <a-button
          key="submit"
          type="primary"
          :loading="dbState.creating"
          :disabled="!selectedKbTypeInfo"
          @click="handleCreateDatabase"
          >创建</a-button
        >
      </template>
    </a-modal>

    <!-- 加载状态 -->
    <div v-if="dbState.listLoading" class="loading-container">
      <a-spin size="large" />
      <p>正在加载知识库...</p>
    </div>

    <!-- 空状态显示 -->
    <ResourceEmptyState
      v-else-if="!databases || databases.length === 0"
      title="暂无知识库"
      description="创建知识库后，可以上传文件并配置检索、图谱和评估能力。"
      :icon="getKbTypeIcon('milvus')"
    >
      <template #actions>
        <a-button
          type="primary"
          size="large"
          class="lucide-icon-btn"
          :disabled="!kbTypes.length"
          @click="state.openNewDatabaseModel = true"
        >
          <template #icon>
            <Plus :size="16" />
          </template>
          创建知识库
        </a-button>
      </template>
    </ResourceEmptyState>

    <!-- 数据库列表 -->
    <ExtensionCardGrid v-else>
      <InfoCard
        v-for="database in filteredDatabases"
        :key="database.kb_id"
        :title="database.name"
        :subtitle="cardSubtitle(database)"
        :description="database.description || '暂无描述'"
        :tags="cardTags(database)"
        @click="navigateToDatabase(database)"
      >
        <template #icon>
          <component :is="getKbTypeIcon(database.kb_type || 'milvus')" :size="20" />
        </template>
        <template #status />
      </InfoCard>
    </ExtensionCardGrid>
  </div>
</template>

<script setup>
import { ref, onMounted, reactive, watch, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useConfigStore } from '@/stores/config'
import { useDatabaseStore } from '@/stores/database'
import { QuestionCircleOutlined } from '@ant-design/icons-vue'
import { Plus } from 'lucide-vue-next'
import { message } from 'ant-design-vue'
import { typeApi } from '@/apis/knowledge_api'
import PageHeader from '@/components/shared/PageHeader.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import ResourceEmptyState from '@/components/shared/ResourceEmptyState.vue'
import EmbeddingModelSelector from '@/components/EmbeddingModelSelector.vue'
import ShareConfigForm from '@/components/ShareConfigForm.vue'
import ExtensionCardGrid from '@/components/extensions/ExtensionCardGrid.vue'
import InfoCard from '@/components/shared/InfoCard.vue'
import dayjs, { parseToShanghai } from '@/utils/time'
import AiTextarea from '@/components/AiTextarea.vue'
import { getKbTypeLabel, getKbTypeIcon, getKbTypeColor, kbUtils } from '@/utils/kb_utils'
import { CHUNK_PRESET_OPTIONS, getChunkPresetDescription } from '@/utils/chunk_presets'

const route = useRoute()
const router = useRouter()
const configStore = useConfigStore()
const databaseStore = useDatabaseStore()

const props = defineProps({
  embedded: { type: Boolean, default: false }
})

// 使用 store 的状态
const { databases, state: dbState } = storeToRefs(databaseStore)

const knowledgeActiveView = 'documents'
const knowledgeViewItems = [
  { key: 'documents', label: '文档知识库', path: '/extensions?tab=knowledge' }
]

const kbTypes = computed(() => Object.keys(supportedKbTypes.value))
const searchQuery = ref('')
const typeFilter = ref(null)

const filteredDatabases = computed(() => {
  let list = databases.value
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    list = list.filter(
      (db) =>
        db.name.toLowerCase().includes(q) ||
        (db.description && db.description.toLowerCase().includes(q))
    )
  }
  if (typeFilter.value) {
    list = list.filter((db) => (db.kb_type || 'milvus') === typeFilter.value)
  }
  return list
})

const state = reactive({
  openNewDatabaseModel: false
})

const createDefaultShareConfig = () => ({
  access_level: 'global',
  department_ids: [],
  user_uids: []
})

const shareConfig = ref(createDefaultShareConfig())
const shareConfigFormRef = ref(null)

const chunkPresetOptions = CHUNK_PRESET_OPTIONS.map(({ label, value }) => ({ label, value }))

const createEmptyDatabaseForm = () => ({
  name: '',
  description: '',
  embedding_model_spec: configStore.config?.embed_model,
  kb_type: '',
  storage: '',
  chunk_preset_id: 'general',
  additional_params: {}
})

const newDatabase = reactive(createEmptyDatabaseForm())

const selectedPresetDescription = computed(() =>
  getChunkPresetDescription(newDatabase.chunk_preset_id)
)

// 支持的知识库类型
const supportedKbTypes = ref({})

// 有序的知识库类型
const orderedKbTypes = computed(() => supportedKbTypes.value)

const selectedKbTypeInfo = computed(() => supportedKbTypes.value[newDatabase.kb_type] || null)

const createParamOptions = computed(() => selectedKbTypeInfo.value?.create_params?.options || [])

const getKbTypeDescription = (typeInfo) => typeInfo?.description || ''

const resetCreateParamValues = () => {
  newDatabase.additional_params = {}
  for (const field of createParamOptions.value) {
    if ('default' in field) {
      newDatabase.additional_params[field.key] = field.default
    } else if (field.type === 'boolean') {
      newDatabase.additional_params[field.key] = false
    } else {
      newDatabase.additional_params[field.key] = ''
    }
  }
}

// 加载支持的知识库类型
const loadSupportedKbTypes = async () => {
  try {
    const data = await typeApi.getKnowledgeBaseTypes()
    supportedKbTypes.value = data.kb_types || {}
    newDatabase.kb_type = kbTypes.value[0] || ''
    resetCreateParamValues()
  } catch (error) {
    console.error('加载知识库类型失败:', error)
    supportedKbTypes.value = {}
    newDatabase.kb_type = ''
    resetCreateParamValues()
    message.error('加载知识库类型失败，请稍后重试')
  }
}

const resetNewDatabase = () => {
  Object.assign(newDatabase, createEmptyDatabaseForm())
  newDatabase.kb_type = kbTypes.value[0] || ''
  resetCreateParamValues()
  shareConfig.value = createDefaultShareConfig()
}

const cancelCreateDatabase = () => {
  state.openNewDatabaseModel = false
  resetNewDatabase()
}

// 格式化创建时间
const formatCreatedTime = (createdAt) => {
  if (!createdAt) return ''
  const parsed = parseToShanghai(createdAt)
  if (!parsed) return ''

  const today = dayjs().startOf('day')
  const createdDay = parsed.startOf('day')
  const diffInDays = today.diff(createdDay, 'day')

  if (diffInDays === 0) {
    return '今天创建'
  }
  if (diffInDays === 1) {
    return '昨天创建'
  }
  if (diffInDays < 7) {
    return `${diffInDays} 天前创建`
  }
  if (diffInDays < 30) {
    const weeks = Math.floor(diffInDays / 7)
    return `${weeks} 周前创建`
  }
  if (diffInDays < 365) {
    const months = Math.floor(diffInDays / 30)
    return `${months} 个月前创建`
  }
  const years = Math.floor(diffInDays / 365)
  return `${years} 年前创建`
}

// 处理知识库类型改变
const handleKbTypeChange = (type) => {
  console.log('知识库类型改变:', type)
  resetNewDatabase()
  newDatabase.kb_type = type
  resetCreateParamValues()
}

// 构建请求数据（只负责表单数据转换）
const buildRequestData = () => {
  const requestData = {
    database_name: newDatabase.name.trim(),
    description: newDatabase.description?.trim() || '',
    kb_type: newDatabase.kb_type,
    additional_params: {}
  }

  if (selectedKbTypeInfo.value?.requires_embedding_model) {
    requestData.embedding_model_spec =
      newDatabase.embedding_model_spec || configStore.config.embed_model
    requestData.additional_params.chunk_preset_id = newDatabase.chunk_preset_id || 'general'
  }

  requestData.share_config = {
    access_level: shareConfig.value.access_level,
    department_ids:
      shareConfig.value.access_level === 'department' ? shareConfig.value.department_ids || [] : [],
    user_uids: shareConfig.value.access_level === 'user' ? shareConfig.value.user_uids || [] : []
  }

  // 根据类型添加特定配置
  if (['milvus'].includes(newDatabase.kb_type)) {
    if (newDatabase.storage) {
      requestData.additional_params.storage = newDatabase.storage
    }
  }

  for (const field of createParamOptions.value) {
    const value = newDatabase.additional_params[field.key]
    requestData.additional_params[field.key] = typeof value === 'string' ? value.trim() : value
  }

  return requestData
}

// 创建按钮处理
const handleCreateDatabase = async () => {
  if (!selectedKbTypeInfo.value) {
    message.error('知识库类型加载失败，无法创建知识库')
    return
  }

  for (const field of createParamOptions.value) {
    if (!field.required) continue
    const value = newDatabase.additional_params[field.key]
    if (value === undefined || value === null || (typeof value === 'string' && !value.trim())) {
      message.error(`请填写${field.label || field.key}`)
      return
    }
  }

  if (shareConfigFormRef.value) {
    const validation = shareConfigFormRef.value.validate()
    if (!validation.valid) {
      message.warning(validation.message)
      return
    }
  }

  const requestData = buildRequestData()
  try {
    await databaseStore.createDatabase(requestData)
    resetNewDatabase()
    state.openNewDatabaseModel = false
  } catch {
    // 错误已在 store 中处理
  }
}

const cardSubtitle = (database) => {
  const parts = []
  if (database.created_at) {
    parts.push(formatCreatedTime(database.created_at))
  }
  if (!kbUtils.isReadOnlyDatabase(database)) {
    parts.push(`${database.row_count || 0} 文件`)
  }
  return parts.join(' · ')
}

const cardTags = (database) => {
  const tags = [
    {
      name: getKbTypeLabel(database.kb_type || 'milvus'),
      color: getKbTypeColor(database.kb_type || 'milvus')
    }
  ]
  if (database.embedding_model_spec) {
    tags.push({
      name: database.embedding_model_spec.split('/').slice(-1)[0],
      color: 'blue'
    })
  }
  return tags
}

const navigateToDatabase = (database) => {
  router.push({ path: `/extensions/knowledgebase/${database.kb_id}` })
}

watch(
  () => route.path,
  (newPath) => {
    if (newPath === '/extensions' && route.query.tab === 'knowledge') {
      databaseStore.loadDatabases()
    }
  }
)

onMounted(() => {
  loadSupportedKbTypes()
  databaseStore.loadDatabases()
})

defineExpose({
  loading: computed(() => dbState.value.listLoading)
})
</script>

<style lang="less" scoped>
.database-container {
  :deep(.info-card-icon) {
    background: var(--gray-0);
  }
}

.new-database-modal {
  .new-database-form {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .form-section {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .form-section.compact-section {
    gap: 6px;
  }

  .form-grid {
    display: grid;
    gap: 16px;

    &.two-columns {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    &.three-columns {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }

    @media (max-width: 768px) {
      &.two-columns,
      &.three-columns {
        grid-template-columns: 1fr;
      }
    }
  }

  .full-width {
    width: 100%;
  }

  .compact-model-selector {
    height: 40px;
  }

  .section-title {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--gray-800);
  }

  .required-mark {
    margin-left: 2px;
    color: var(--color-error-500);
  }

  .field-hint {
    margin: 0;
    font-size: 13px;
    line-height: 1.5;
    color: var(--gray-600);
  }

  .description-hint {
    margin-top: -2px;
  }

  .chunk-preset-title-row {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .chunk-preset-help-icon {
    color: var(--gray-500);
    cursor: help;
    font-size: 14px;
  }

  .kb-type-guide {
    margin: 12px 0;
  }

  .privacy-config {
    display: flex;
    align-items: center;
    margin-bottom: 12px;
  }

  .kb-type-cards {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin: 4px 0 0;

    @media (max-width: 768px) {
      grid-template-columns: 1fr;
      gap: 10px;
    }

    .kb-type-card {
      border: 1px solid var(--gray-150);
      border-radius: 12px;
      padding: 14px;
      cursor: pointer;
      transition: all 0.2s ease;
      background: var(--gray-0);
      position: relative;
      overflow: hidden;

      &:hover {
        border-color: var(--main-color);
      }

      &.active {
        border-color: var(--main-color);
        background: var(--main-10);
        box-shadow: 0 0 0 1px var(--main-20);

        .type-icon {
          color: var(--main-color);
        }
      }

      .card-header {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 10px;

        .type-icon {
          width: 20px;
          height: 20px;
          color: var(--main-color);
          flex-shrink: 0;
        }

        .type-title {
          font-size: 15px;
          font-weight: 600;
          color: var(--gray-800);
        }
      }

      .card-description {
        font-size: 13px;
        color: var(--gray-600);
        line-height: 1.5;
        margin-bottom: 0;
      }

      .deprecated-badge {
        background: var(--color-error-100);
        color: var(--color-error-600);
        font-size: 10px;
        font-weight: 600;
        padding: 2px 6px;
        border-radius: 4px;
        margin-left: auto;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        cursor: help;
        transition: all 0.2s ease;

        &:hover {
          background: var(--color-error-200);
          color: var(--color-error-700);
        }
      }
    }
  }

  .chunk-config {
    margin-top: 16px;
    padding: 12px 16px;
    background-color: var(--gray-25);
    border-radius: 6px;
    border: 1px solid var(--gray-150);

    h3 {
      margin-top: 0;
      margin-bottom: 12px;
      color: var(--gray-800);
    }

    .chunk-params {
      display: flex;
      flex-direction: column;
      gap: 12px;

      .param-row {
        display: flex;
        align-items: center;
        gap: 12px;

        label {
          min-width: 80px;
          font-weight: 500;
          color: var(--gray-700);
        }

        .param-hint {
          font-size: 12px;
          color: var(--gray-500);
          margin-left: 8px;
        }
      }
    }
  }
}

.database-container {
  padding: 0;
}

.loading-container {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  height: 300px;
  gap: 16px;
}

.new-database-modal {
  h3 {
    margin-top: 10px;
  }
}
</style>
