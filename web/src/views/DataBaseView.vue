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

    <DatabaseCreateFlowModal
      v-model:open="state.openNewDatabaseModel"
      :supported-kb-types="supportedKbTypes"
    />

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
        :disabled="kbUtils.isReadOnlyDatabase(database)"
        @click="navigateToDatabase(database)"
      >
        <template #icon>
          <component :is="getKbTypeIcon(database.kb_type || 'milvus')" :size="20" />
        </template>
        <template #card-more-action-corner>
          <a-menu
            class="database-action-menu"
            @click="({ key }) => handleDatabaseAction(key, database)"
          >
            <a-menu-item key="copy">
              <span class="lucide-menu-item">
                <Copy :size="15" />
                <span>复制 ID</span>
              </span>
            </a-menu-item>
            <a-menu-item v-if="database.can_manage" key="edit">
              <span class="lucide-menu-item">
                <Pencil :size="15" />
                <span>编辑知识库</span>
              </span>
            </a-menu-item>
            <a-menu-divider />
            <a-menu-item v-if="database.can_manage" key="delete" danger>
              <span class="lucide-menu-item">
                <Trash2 :size="15" />
                <span>删除知识库</span>
              </span>
            </a-menu-item>
          </a-menu>
        </template>
      </InfoCard>
    </ExtensionCardGrid>
  </div>
</template>

<script setup>
import { ref, onMounted, reactive, watch, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useDatabaseStore } from '@/stores/database'
import { Copy, Pencil, Plus, Trash2 } from '@lucide/vue'
import { message, Modal } from 'ant-design-vue'
import { databaseApi, typeApi } from '@/apis/knowledge_api'
import PageHeader from '@/components/shared/PageHeader.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import ResourceEmptyState from '@/components/shared/ResourceEmptyState.vue'
import DatabaseCreateFlowModal from '@/components/knowledge/DatabaseCreateFlowModal.vue'
import ExtensionCardGrid from '@/components/extensions/ExtensionCardGrid.vue'
import InfoCard from '@/components/shared/InfoCard.vue'
import dayjs, { parseToShanghai } from '@/utils/time'
import { getKbTypeLabel, getKbTypeIcon, getKbTypeColor, kbUtils } from '@/utils/kb_utils'
import { getShareConfigLabel } from '@/utils/shareConfig'

const route = useRoute()
const router = useRouter()
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

// 支持的知识库类型
const supportedKbTypes = ref({})

// 加载支持的知识库类型
const loadSupportedKbTypes = async () => {
  try {
    const data = await typeApi.getKnowledgeBaseTypes()
    supportedKbTypes.value = data.kb_types || {}
  } catch (error) {
    console.error('加载知识库类型失败:', error)
    supportedKbTypes.value = {}
    message.error('加载知识库类型失败，请稍后重试')
  }
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
    },
    {
      name: getShareConfigLabel(database.share_config),
      color: 'gray'
    }
  ]
  if (database.embedding_model_spec) {
    tags.push({
      name: database.embedding_model_spec.split('/').slice(-1)[0],
      color: 'gray'
    })
  }
  return tags
}

const navigateToDatabase = (database) => {
  if (kbUtils.isReadOnlyDatabase(database)) return
  router.push({ path: `/extensions/knowledgebase/${database.kb_id}` })
}

const copyDatabaseId = async (database) => {
  try {
    await navigator.clipboard.writeText(database.kb_id)
  } catch {
    const textArea = document.createElement('textarea')
    textArea.value = database.kb_id
    document.body.appendChild(textArea)
    textArea.select()
    document.execCommand('copy')
    document.body.removeChild(textArea)
  }
  message.success('知识库 ID 已复制')
}

const deleteDatabase = (database) => {
  Modal.confirm({
    title: '删除知识库',
    content: `确定要删除知识库“${database.name}”吗？此操作不可撤销。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await databaseApi.deleteDatabase(database.kb_id)
        message.success('知识库已删除')
        await databaseStore.loadDatabases()
      } catch (error) {
        message.error(error.message || '删除失败')
        throw error
      }
    }
  })
}

const handleDatabaseAction = (key, database) => {
  if (key === 'copy') {
    copyDatabaseId(database)
    return
  }
  if (key === 'edit') {
    router.push({
      path: `/extensions/knowledgebase/${database.kb_id}`,
      query: { action: 'edit' }
    })
    return
  }
  if (key === 'delete') {
    deleteDatabase(database)
  }
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
</style>

<style lang="less">
/* 下拉层 teleport 到 body，需使用菜单命名空间修正 Ant 默认的 inline 行盒高度。 */
.database-action-menu .ant-dropdown-menu-title-content {
  display: flex;
  align-items: center;
  width: 100%;
}
</style>
