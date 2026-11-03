<template>
  <div class="knowledge-base-container layout-container">
    <PageHeader
      v-if="!props.embedded"
      title="知识库"
      :active-key="knowledgeActiveView"
      :tabs="knowledgeViewItems"
      :loading="knowledgeBaseState.listLoading"
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
          @click="state.openNewKnowledgeBaseModel = true"
        >
          <Plus :size="16" /> 新建知识库
        </a-button>
      </template>
    </PageShoulder>

    <KnowledgeBaseCreateFlowModal
      v-model:open="state.openNewKnowledgeBaseModel"
      :supported-kb-types="supportedKbTypes"
    />

    <!-- 加载状态 -->
    <div v-if="knowledgeBaseState.listLoading" class="loading-container">
      <a-spin size="large" />
      <p>正在加载知识库...</p>
    </div>

    <!-- 空状态显示 -->
    <ResourceEmptyState
      v-else-if="!knowledgeBases || knowledgeBases.length === 0"
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
          @click="state.openNewKnowledgeBaseModel = true"
        >
          <template #icon>
            <Plus :size="16" />
          </template>
          创建知识库
        </a-button>
      </template>
    </ResourceEmptyState>

    <!-- 知识库列表 -->
    <ExtensionCardGrid v-else>
      <InfoCard
        v-for="knowledgeBase in filteredKnowledgeBases"
        :key="knowledgeBase.kb_id"
        :title="knowledgeBase.name"
        :subtitle="cardSubtitle(knowledgeBase)"
        :description="knowledgeBase.description || '暂无描述'"
        :tags="cardTags(knowledgeBase)"
        :icon-color="getExtensionIconColor(knowledgeBase.kb_id, themeStore.isDark)"
        :disabled="!kbUtils.supportsKnowledgeBaseDetail(knowledgeBase)"
        @click="navigateToKnowledgeBase(knowledgeBase)"
      >
        <template #icon>
          <component :is="getKbTypeIcon(knowledgeBase.kb_type || 'milvus')" :size="20" />
        </template>
        <template #card-more-action-corner>
          <a-menu
            class="knowledge-base-action-menu"
            @click="({ key }) => handleKnowledgeBaseAction(key, knowledgeBase)"
          >
            <a-menu-item key="copy">
              <span class="lucide-menu-item">
                <Copy :size="15" />
                <span>复制 ID</span>
              </span>
            </a-menu-item>
            <a-menu-item v-if="knowledgeBase.can_manage" key="edit">
              <span class="lucide-menu-item">
                <Pencil :size="15" />
                <span>编辑知识库</span>
              </span>
            </a-menu-item>
            <a-menu-divider />
            <a-menu-item v-if="knowledgeBase.can_manage" key="delete" danger>
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
import { useKnowledgeBaseStore } from '@/modules/knowledge/model/knowledgeBase'
import { Copy, Pencil, Plus, Trash2 } from '@lucide/vue'
import { message, Modal } from 'ant-design-vue'
import { knowledgeBaseApi, typeApi } from '@/apis/knowledge_api'
import PageHeader from '@/shared/ui/PageHeader.vue'
import PageShoulder from '@/shared/ui/PageShoulder.vue'
import ResourceEmptyState from '@/shared/ui/ResourceEmptyState.vue'
import KnowledgeBaseCreateFlowModal from '@/modules/knowledge/ui/KnowledgeBaseCreateFlowModal.vue'
import ExtensionCardGrid from '@/modules/extensions/ui/ExtensionCardGrid.vue'
import InfoCard from '@/shared/ui/InfoCard.vue'
import { getExtensionIconColor } from '@/shared/lib/extensionIconColor'
import { useThemeStore } from '@/shared/model/theme'
import dayjs, { parseToShanghai } from '@/shared/lib/time'
import { getKbTypeLabel, getKbTypeIcon, getKbTypeColor, kbUtils } from '@/modules/knowledge/model/kb_utils'
import { getShareConfigLabel } from '@/modules/agents'

const themeStore = useThemeStore()
const route = useRoute()
const router = useRouter()
const knowledgeBaseStore = useKnowledgeBaseStore()

const props = defineProps({
  embedded: { type: Boolean, default: false }
})

// 使用 store 的状态
const { knowledgeBases, state: knowledgeBaseState } = storeToRefs(knowledgeBaseStore)

const knowledgeActiveView = 'documents'
const knowledgeViewItems = [
  { key: 'documents', label: '文档知识库', path: '/extensions?tab=knowledge' }
]

const kbTypes = computed(() => Object.keys(supportedKbTypes.value))
const searchQuery = ref('')
const typeFilter = ref(null)

const filteredKnowledgeBases = computed(() => {
  let list = knowledgeBases.value
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    list = list.filter(
      (kb) =>
        kb.name.toLowerCase().includes(q) ||
        (kb.description && kb.description.toLowerCase().includes(q))
    )
  }
  if (typeFilter.value) {
    list = list.filter((kb) => (kb.kb_type || 'milvus') === typeFilter.value)
  }
  return list
})

const state = reactive({
  openNewKnowledgeBaseModel: false
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

const cardSubtitle = (knowledgeBase) => {
  const parts = []
  if (knowledgeBase.created_at) {
    parts.push(formatCreatedTime(knowledgeBase.created_at))
  }
  if (!kbUtils.isReadOnlyKnowledgeBase(knowledgeBase)) {
    parts.push(`${knowledgeBase.stats?.file_count || 0} 文件`)
  }
  return parts.join(' · ')
}

const cardTags = (knowledgeBase) => {
  const tags = [
    {
      name: getKbTypeLabel(knowledgeBase.kb_type || 'milvus'),
      color: getKbTypeColor(knowledgeBase.kb_type || 'milvus')
    },
    {
      name: getShareConfigLabel(knowledgeBase.share_config),
      color: 'gray'
    }
  ]
  if (knowledgeBase.embedding_model_spec) {
    tags.push({
      name: knowledgeBase.embedding_model_spec.split('/').slice(-1)[0],
      color: 'gray'
    })
  }
  return tags
}

const navigateToKnowledgeBase = (knowledgeBase) => {
  if (!kbUtils.supportsKnowledgeBaseDetail(knowledgeBase)) return
  router.push({ path: `/extensions/knowledge-bases/${knowledgeBase.kb_id}` })
}

const copyKnowledgeBaseId = async (knowledgeBase) => {
  try {
    await navigator.clipboard.writeText(knowledgeBase.kb_id)
  } catch {
    const textArea = document.createElement('textarea')
    textArea.value = knowledgeBase.kb_id
    document.body.appendChild(textArea)
    textArea.select()
    document.execCommand('copy')
    document.body.removeChild(textArea)
  }
  message.success('知识库 ID 已复制')
}

const deleteKnowledgeBase = (knowledgeBase) => {
  Modal.confirm({
    title: '删除知识库',
    content: `确定要删除知识库“${knowledgeBase.name}”吗？此操作不可撤销。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await knowledgeBaseApi.deleteKnowledgeBase(knowledgeBase.kb_id)
        message.success('知识库已删除')
        await knowledgeBaseStore.loadKnowledgeBases()
      } catch (error) {
        message.error(error.message || '删除失败')
        throw error
      }
    }
  })
}

const handleKnowledgeBaseAction = (key, knowledgeBase) => {
  if (key === 'copy') {
    copyKnowledgeBaseId(knowledgeBase)
    return
  }
  if (key === 'edit') {
    router.push({
      path: `/extensions/knowledge-bases/${knowledgeBase.kb_id}`,
      query: { action: 'edit' }
    })
    return
  }
  if (key === 'delete') {
    deleteKnowledgeBase(knowledgeBase)
  }
}

watch(
  () => route.path,
  (newPath) => {
    if (newPath === '/extensions' && route.query.tab === 'knowledge') {
      knowledgeBaseStore.loadKnowledgeBases()
    }
  }
)

onMounted(() => {
  loadSupportedKbTypes()
  knowledgeBaseStore.loadKnowledgeBases()
})

defineExpose({
  loading: computed(() => knowledgeBaseState.value.listLoading)
})
</script>

<style lang="less" scoped>
.knowledge-base-container {
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
.knowledge-base-action-menu .ant-dropdown-menu-title-content {
  display: flex;
  align-items: center;
  width: 100%;
}
</style>
