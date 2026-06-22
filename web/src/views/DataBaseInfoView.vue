<template>
  <div class="database-info-container extension-detail-page">
    <FileDetailModal
      v-model:open="store.state.fileDetailModalVisible"
      :kb-id="kbId"
      :file-id="store.fileDetailFileId"
      @closed="store.closeFileDetail"
    />

    <FileUploadModal
      v-model:visible="addFilesModalVisible"
      :folder-tree="folderTree"
      :current-folder-id="currentFolderId"
      :is-folder-mode="isFolderUploadMode"
      :mode="addFilesMode"
      @success="onFileUploadSuccess"
    />

    <div class="detail-top-bar">
      <button class="detail-back-btn" type="button" @click="backToDatabase">
        <ArrowLeft :size="16" />
        <span>返回</span>
      </button>
      <div class="detail-title-area">
        <div class="detail-icon">
          <component :is="kbTypeIcon" :size="18" />
        </div>
        <div class="detail-title-text">
          <h2>{{ database.name || '知识库加载中' }}</h2>
          <span class="detail-subtitle">{{ databaseSubtitle }}</span>
        </div>
      </div>
      <div class="detail-actions">
        <a-space :size="8">
          <button
            type="button"
            class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
            @click="copyDatabaseId"
          >
            <Copy :size="14" />
            <span>复制 ID</span>
          </button>
          <button
            type="button"
            class="lucide-icon-btn extension-panel-action extension-panel-action-primary"
            @click="showEditModal"
          >
            <Pencil :size="14" />
            <span>编辑</span>
          </button>
        </a-space>
      </div>
    </div>

    <div class="database-detail-body">
      <div class="database-tab-bar" aria-label="知识库功能导航">
        <nav class="database-tab-list" aria-label="知识库功能标签" role="tablist">
          <button
            v-for="tab in visibleTabs"
            :key="tab.key"
            type="button"
            class="database-tab-item"
            :class="{ active: activeTab === tab.key }"
            role="tab"
            :aria-selected="activeTab === tab.key"
            @click="activeTab = tab.key"
          >
            <component :is="tab.icon" :size="17" />
            <span>{{ tab.label }}</span>
          </button>
        </nav>
      </div>

      <main class="database-tab-content">
        <div v-if="isMilvus" v-show="activeTab === 'filetable'" class="tab-panel file-panel">
          <div class="file-management-info">
            <div class="file-info-title">
              <div class="file-info-title-row">
                <button
                  type="button"
                  class="lucide-icon-btn extension-panel-action extension-panel-action-primary"
                  @click="showAddFilesModal()"
                >
                  <FileUp :size="14" />
                  <span>上传</span>
                </button>
                <button
                  type="button"
                  class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
                  @click="showCreateFolderModal"
                >
                  <FolderPlus :size="14" />
                  <span>新建文件夹</span>
                </button>
              </div>
            </div>
            <div class="file-panel-status">
              <button
                v-if="pendingParseCount > 0"
                type="button"
                class="file-stat-card file-stat-action file-stat-summary"
                :disabled="store.state.chunkLoading"
                @click="confirmBatchParse"
              >
                <FileText :size="16" />
                <div class="file-stat-inline">
                  <strong>{{ pendingParseCount }}</strong>
                  <span>待解析</span>
                </div>
              </button>
              <button
                v-if="pendingIndexCount > 0"
                type="button"
                class="file-stat-card file-stat-action file-stat-summary"
                :disabled="store.state.chunkLoading"
                @click="confirmBatchIndex"
              >
                <DatabaseIcon :size="16" />
                <div class="file-stat-inline">
                  <strong>{{ pendingIndexCount }}</strong>
                  <span>待入库</span>
                </div>
              </button>
              <div class="file-stat-card file-stat-summary">
                <FileText :size="16" />
                <div class="file-stat-inline">
                  <strong>{{ fileStats.count }}</strong>
                  <span>文件</span>
                </div>
              </div>
              <div v-if="fileStats.sizeText" class="file-stat-card file-stat-summary">
                <DatabaseIcon :size="16" />
                <div class="file-stat-inline">
                  <strong>{{ fileStats.sizeText }}</strong>
                  <span>总大小</span>
                </div>
              </div>
              <button
                type="button"
                class="file-stat-card file-stat-summary file-stat-repair"
                :disabled="statsRepairing"
                :aria-busy="statsRepairing"
                aria-label="修复缺失的 Chunk/Token 统计"
                title="修复缺失的 Chunk/Token 统计"
                @click="repairDatabaseStats"
              >
                <LoaderCircle v-if="statsRepairing" :size="16" class="file-stat-spinner" />
                <DatabaseIcon v-else :size="16" />
                <div class="file-stat-inline">
                  <strong>{{ fileStats.chunkText }}</strong>
                  <span>Chunks</span>
                </div>
              </button>
              <button
                type="button"
                class="file-stat-card file-stat-summary file-stat-repair"
                :disabled="statsRepairing"
                :aria-busy="statsRepairing"
                aria-label="修复缺失的 Chunk/Token 统计"
                title="修复缺失的 Chunk/Token 统计"
                @click="repairDatabaseStats"
              >
                <LoaderCircle v-if="statsRepairing" :size="16" class="file-stat-spinner" />
                <Hash v-else :size="16" />
                <div class="file-stat-inline">
                  <strong>{{ fileStats.tokenText }}</strong>
                  <span>Tokens</span>
                </div>
              </button>
            </div>
          </div>
          <FileTable ref="fileTableRef" />
        </div>

        <div v-show="activeTab === 'query'" class="tab-panel query-config-panel">
          <div class="query-config-layout">
            <div class="query-test-pane">
              <QuerySection ref="querySectionRef" :visible="true" @toggle-visible="() => {}" />
            </div>
            <aside class="query-config-pane" aria-label="检索配置">
              <div class="search-config-wrapper">
                <div class="search-config-header">
                  <div>
                    <h3>检索配置</h3>
                    <p>调整当前知识库的检索参数。</p>
                  </div>
                  <button
                    type="button"
                    class="lucide-icon-btn extension-panel-action extension-panel-action-primary"
                    :disabled="searchConfigSaving"
                    @click="handleInlineSearchConfigSave"
                  >
                    <Save :size="14" />
                    <span>保存</span>
                  </button>
                </div>
                <div class="search-config-body">
                  <SearchConfigPanel
                    ref="searchConfigPanelRef"
                    :kb-id="kbId"
                    @save="handleSearchConfigSave"
                  />
                </div>
              </div>
            </aside>
          </div>
        </div>

        <div v-if="isMilvus && activeTab === 'graph'" class="tab-panel">
          <KnowledgeGraphSection
            :visible="true"
            :active="activeTab === 'graph'"
            @toggle-visible="() => {}"
          />
        </div>

        <div v-if="isMilvus && activeTab === 'mindmap'" class="tab-panel">
          <MindMapSection v-if="kbId" :kb-id="kbId" ref="mindmapSectionRef" />
        </div>

        <div v-if="isMilvus && activeTab === 'evaluation'" class="tab-panel">
          <RAGEvaluationTab
            v-if="kbId"
            :kb-id="kbId"
            @switch-to-benchmarks="activeTab = 'benchmarks'"
          />
        </div>

        <div v-if="isMilvus && activeTab === 'benchmarks'" class="tab-panel">
          <div class="benchmark-management-container">
            <div class="benchmark-content">
              <EvaluationBenchmarks
                v-if="kbId && isEvaluationSupported"
                :kb-id="kbId"
                @benchmark-selected="activeTab = 'evaluation'"
              />
            </div>
          </div>
        </div>
      </main>
    </div>

    <a-modal v-model:open="editModalVisible" title="编辑知识库信息" width="700px">
      <template #footer>
        <a-button danger @click="deleteDatabase" style="margin-right: auto; margin-left: 0">
          <template #icon>
            <Trash2 :size="16" style="vertical-align: -3px; margin-right: 4px" />
          </template>
          删除数据库
        </a-button>
        <a-button key="back" @click="editModalVisible = false">取消</a-button>
        <a-button key="submit" type="primary" @click="handleEditSubmit">确定</a-button>
      </template>
      <a-form :model="editForm" :rules="rules" ref="editFormRef" layout="vertical">
        <a-form-item label="知识库名称" name="name" required>
          <a-input v-model:value="editForm.name" placeholder="请输入知识库名称" />
        </a-form-item>
        <a-form-item label="知识库描述" name="description">
          <AiTextarea
            v-model="editForm.description"
            :name="editForm.name"
            :files="fileList"
            placeholder="请输入知识库描述"
            action-placement="header"
            :rows="4"
          />
        </a-form-item>

        <a-form-item v-if="!isConnector" label="自动生成问题" name="auto_generate_questions">
          <a-switch
            v-model:checked="editForm.auto_generate_questions"
            checked-children="开启"
            un-checked-children="关闭"
          />
          <span style="margin-left: 8px; font-size: 12px; color: var(--gray-500)">
            上传文件后自动生成测试问题
          </span>
        </a-form-item>

        <a-form-item v-if="!isConnector" name="chunk_preset_id">
          <template #label>
            <span class="chunk-preset-label">
              分块策略
              <a-tooltip :title="editPresetDescription">
                <QuestionCircleOutlined class="chunk-preset-help-icon" />
              </a-tooltip>
            </span>
          </template>
          <a-select v-model:value="editForm.chunk_preset_id" :options="chunkPresetOptions" />
        </a-form-item>

        <template v-if="isDifyKb">
          <a-form-item label="Dify API URL" name="dify_api_url">
            <a-input
              v-model:value="editForm.dify_api_url"
              placeholder="例如: https://api.dify.ai/v1"
            />
          </a-form-item>
          <a-form-item label="Dify Token" name="dify_token">
            <a-input-password
              v-model:value="editForm.dify_token"
              placeholder="请输入 Dify API Token"
            />
          </a-form-item>
          <a-form-item label="Dataset ID" name="dify_dataset_id">
            <a-input
              v-model:value="editForm.dify_dataset_id"
              placeholder="请输入 Dify dataset_id"
            />
          </a-form-item>
        </template>

        <template v-if="isNotionKb">
          <a-form-item label="Notion Token" name="notion_token">
            <a-input-password
              v-model:value="editForm.notion_token"
              placeholder="留空则保持现有 Token 或使用环境变量"
            />
          </a-form-item>
          <a-form-item label="Data Source ID" name="notion_data_source_id">
            <a-input
              v-model:value="editForm.notion_data_source_id"
              placeholder="请输入 Notion data_source_id"
            />
          </a-form-item>
          <a-form-item label="Notion API Version" name="notion_version">
            <a-input v-model:value="editForm.notion_version" placeholder="2026-03-11" />
          </a-form-item>
        </template>

        <a-form-item v-if="canEditShareConfig" label="共享设置" name="share_config">
          <a-form-item-rest>
            <ShareConfigForm
              ref="shareConfigFormRef"
              :model-value="database.share_config"
              :auto-select-user-dept="true"
            />
          </a-form-item-rest>
        </a-form-item>
        <a-form-item
          v-else-if="database.share_config"
          label="共享设置"
          name="share_config_readonly"
        >
          <div class="share-config-readonly">
            <a-tag :color="shareConfigDisplay.color">
              {{ shareConfigDisplay.label }}
            </a-tag>
            <span class="access-names">{{ shareConfigDisplay.detail }}</span>
          </div>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDatabaseStore } from '@/stores/database'
import { useTaskerStore } from '@/stores/tasker'
import { useUserStore } from '@/stores/user'
import {
  ArrowLeft,
  BarChart3,
  ClipboardList,
  Copy,
  Database as DatabaseIcon,
  FileUp,
  FileText,
  FolderPlus,
  Hash,
  LoaderCircle,
  Map as MapIcon,
  Network,
  Pencil,
  Save,
  Search,
  Trash2
} from 'lucide-vue-next'
import { QuestionCircleOutlined } from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import FileTable from '@/components/FileTable.vue'
import FileDetailModal from '@/components/FileDetailModal.vue'
import FileUploadModal from '@/components/FileUploadModal.vue'
import KnowledgeGraphSection from '@/components/KnowledgeGraphSection.vue'
import QuerySection from '@/components/QuerySection.vue'
import MindMapSection from '@/components/MindMapSection.vue'
import RAGEvaluationTab from '@/components/RAGEvaluationTab.vue'
import EvaluationBenchmarks from '@/components/EvaluationBenchmarks.vue'
import SearchConfigPanel from '@/components/SearchConfigPanel.vue'
import AiTextarea from '@/components/AiTextarea.vue'
import ShareConfigForm from '@/components/ShareConfigForm.vue'
import { databaseApi } from '@/apis/knowledge_api'
import { departmentApi } from '@/apis/department_api'
import { authApi } from '@/apis/auth_api'
import { CHUNK_PRESET_OPTIONS, getChunkPresetDescription } from '@/utils/chunk_presets'
import { formatFileSize } from '@/utils/file_utils'
import { getKbTypeIcon, getKbTypeLabel, kbUtils } from '@/utils/kb_utils'

const route = useRoute()
const router = useRouter()
const store = useDatabaseStore()
const taskerStore = useTaskerStore()
const userStore = useUserStore()

const kbId = computed(() => store.kbId)
const database = computed(() => store.database)
const isCurrentDatabaseLoaded = computed(() => database.value?.kb_id === kbId.value)
const kbType = computed(() =>
  isCurrentDatabaseLoaded.value ? database.value.kb_type?.toLowerCase() || 'milvus' : ''
)
const isMilvus = computed(() => kbType.value === 'milvus')
const isDifyKb = computed(() => kbType.value === 'dify')
const isNotionKb = computed(() => kbType.value === 'notion')
const isConnector = computed(
  () => isCurrentDatabaseLoaded.value && kbUtils.isReadOnlyDatabase(database.value)
)
const isEvaluationSupported = computed(() => isMilvus.value)
const kbTypeIcon = computed(() => getKbTypeIcon(kbType.value || 'milvus'))

const databaseSubtitle = computed(() => {
  const typeLabel = getKbTypeLabel(kbType.value || 'milvus')
  if (!isCurrentDatabaseLoaded.value) return '正在加载知识库信息'

  const description = database.value.description?.trim()
  if (description) return description

  if (isConnector.value) return `${typeLabel} 连接器`
  return `${typeLabel} 知识库 · ${database.value.row_count || 0} 文件`
})

const tabs = computed(() => {
  if (isMilvus.value) {
    return [
      { key: 'filetable', label: '文件管理', icon: FileText },
      { key: 'query', label: '检索测试', icon: Search },
      { key: 'graph', label: '知识图谱', icon: Network },
      { key: 'mindmap', label: '知识导图', icon: MapIcon },
      { key: 'evaluation', label: 'RAG 评估', icon: BarChart3 },
      { key: 'benchmarks', label: '评估基准', icon: ClipboardList }
    ]
  }

  return [{ key: 'query', label: '检索测试', icon: Search }]
})

const visibleTabs = computed(() => tabs.value)
const activeTab = ref('filetable')

watch(
  () => [kbId.value, isMilvus.value],
  ([newDbId, isMilvusType]) => {
    if (!newDbId) return
    activeTab.value = isMilvusType ? 'filetable' : 'query'
  },
  { immediate: true }
)

watch(visibleTabs, (nextTabs) => {
  if (!nextTabs.some((tab) => tab.key === activeTab.value)) {
    activeTab.value = nextTabs[0]?.key || 'query'
  }
})

const pendingParseCount = computed(() => {
  return Number(store.database.stats?.pending_parse_count || 0)
})

const formatStatNumber = (value) => {
  const number = Number(value ?? 0)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN') : '0'
}

const formatTokenStatNumber = (value) => {
  const number = Number(value ?? 0)
  if (!Number.isFinite(number)) return '0'
  const absNumber = Math.abs(number)
  if (absNumber > 1024 * 1000) return `${(number / 1_000_000).toFixed(1)} m`
  if (absNumber >= 1000) return `${Math.round(number / 1000).toLocaleString('zh-CN')} k`
  return number.toLocaleString('zh-CN')
}

const statsRepairing = ref(false)

const fileStats = computed(() => {
  const stats = store.database.stats || {}
  const statsFileCount = Number(stats.file_count)
  const totalSize = Number(stats.total_size || 0)

  return {
    count: Number.isFinite(statsFileCount) ? statsFileCount : 0,
    sizeText: totalSize > 0 ? formatFileSize(totalSize) : '',
    chunkText: formatStatNumber(stats.chunk_count),
    tokenText: formatTokenStatNumber(stats.token_count)
  }
})

const repairDatabaseStats = async () => {
  if (!kbId.value || statsRepairing.value) return

  statsRepairing.value = true
  try {
    const result = await databaseApi.repairDatabaseStats(kbId.value)
    await store.getDatabaseInfo(undefined, true, true)

    const updatedTokenFiles = Number(result?.updated_token_files || 0)
    const updatedChunkFiles = Number(result?.updated_chunk_files || 0)
    if (updatedTokenFiles || updatedChunkFiles) {
      message.success(
        `已修复 ${updatedTokenFiles} 个 Token 统计，${updatedChunkFiles} 个 Chunk 统计`
      )
    } else {
      message.info('统计已是最新')
    }
  } catch (error) {
    console.error(error)
    message.error(error.message || '统计修复失败')
  } finally {
    statsRepairing.value = false
  }
}

const pendingIndexCount = computed(() => {
  return Number(store.database.stats?.pending_index_count || 0)
})

const confirmBatchParse = () => {
  const count = pendingParseCount.value
  if (count <= 0) {
    message.info('没有待解析文档')
    return
  }

  Modal.confirm({
    title: '解析待解析文件',
    content: `将提交 ${formatStatNumber(count)} 个待解析文件，任务会在后台按批处理，可在任务中心查看进度。`,
    okText: '提交解析',
    cancelText: '取消',
    onOk: () => store.parsePendingFiles(count)
  })
}

const confirmBatchIndex = () => {
  const count = pendingIndexCount.value
  if (count <= 0) {
    message.info('没有待入库文档')
    return
  }

  const opened = fileTableRef.value?.startPendingIndex?.(count)
  if (!opened) {
    message.error('文件列表尚未加载完成，请稍后再试')
  }
}

const mindmapSectionRef = ref(null)
const querySectionRef = ref(null)
const searchConfigSaving = ref(false)
const searchConfigPanelRef = ref(null)

const handleSearchConfigSave = () => {
  store.getDatabaseInfo()
}

const handleInlineSearchConfigSave = async () => {
  if (!searchConfigPanelRef.value) return
  searchConfigSaving.value = true
  try {
    await searchConfigPanelRef.value.save()
  } finally {
    searchConfigSaving.value = false
  }
}

const addFilesModalVisible = ref(false)
const currentFolderId = ref(null)
const isFolderUploadMode = ref(false)
const addFilesMode = ref('file')
const isInitialLoad = ref(true)
const fileTableRef = ref(null)

const showAddFilesModal = (options = {}) => {
  const { isFolder = false, mode = 'file' } = options
  isFolderUploadMode.value = isFolder
  addFilesMode.value = mode
  addFilesModalVisible.value = true
  currentFolderId.value =
    fileTableRef.value?.getCurrentFolderId?.() || store.fileBrowser.parentId || null
}

const showCreateFolderModal = () => {
  fileTableRef.value?.showCreateFolderModal()
}

const folderTree = computed(() => {
  const roots = []
  let currentLevel = roots
  for (const item of (store.folderBreadcrumbs || [])
    .slice(1)
    .filter((node) => !node.is_virtual_folder)) {
    const node = {
      file_id: item.file_id,
      filename: item.filename,
      is_folder: true,
      children: []
    }
    currentLevel.push(node)
    currentLevel = node.children
  }
  return roots
})

const onFileUploadSuccess = () => {
  taskerStore.loadTasks()
}

const resetFileSelectionState = () => {
  store.selectedRowKeys = []
  store.closeFileDetail()
  store.resetFileBrowser()
}

watch(
  () => route.params.kbId,
  async (nextKbId) => {
    isInitialLoad.value = true
    store.kbId = nextKbId
    resetFileSelectionState()
    store.stopAutoRefresh()
    await store.getDatabaseInfo(nextKbId, false)
    store.startAutoRefresh()
  },
  { immediate: true }
)

const previousFileCount = ref(0)

watch(
  () => database.value?.stats?.file_count,
  (newFileCountValue) => {
    const newFileCount = Number(newFileCountValue || 0)
    const oldFileCount = previousFileCount.value

    if (isInitialLoad.value) {
      previousFileCount.value = newFileCount
      isInitialLoad.value = false
      return
    }

    if (newFileCount !== oldFileCount) {
      if (newFileCount > 0) {
        setTimeout(async () => {
          if (querySectionRef.value) {
            if (database.value.additional_params?.auto_generate_questions) {
              await querySectionRef.value.generateSampleQuestions(true)
            }
          } else {
            setTimeout(async () => {
              if (
                querySectionRef.value &&
                database.value.additional_params?.auto_generate_questions
              ) {
                await querySectionRef.value.generateSampleQuestions(true)
              }
            }, 2000)
          }
        }, 3000)
      } else {
        setTimeout(() => {
          querySectionRef.value?.clearQuestions()
        }, 1000)
      }
    }

    previousFileCount.value = newFileCount
  },
  { deep: false }
)

const backToDatabase = () => {
  router.push({ path: '/extensions', query: { tab: 'knowledge' } })
}

const copyDatabaseId = async () => {
  if (!database.value.kb_id) {
    message.warning('知识库ID为空')
    return
  }

  try {
    await navigator.clipboard.writeText(database.value.kb_id)
    message.success('知识库ID已复制到剪贴板')
  } catch {
    const textArea = document.createElement('textarea')
    textArea.value = database.value.kb_id
    document.body.appendChild(textArea)
    textArea.select()
    document.execCommand('copy')
    document.body.removeChild(textArea)
    message.success('知识库ID已复制到剪贴板')
  }
}

const departments = ref([])
const users = ref([])
const editModalVisible = ref(false)
const editFormRef = ref(null)
const shareConfigFormRef = ref(null)
const editForm = reactive({
  name: '',
  description: '',
  auto_generate_questions: false,
  chunk_preset_id: 'general',
  dify_api_url: '',
  dify_token: '',
  dify_dataset_id: '',
  notion_token: '',
  notion_data_source_id: '',
  notion_version: '2026-03-11'
})

const rules = {
  name: [{ required: true, message: '请输入知识库名称' }]
}

const chunkPresetOptions = CHUNK_PRESET_OPTIONS.map(({ label, value }) => ({ label, value }))
const editPresetDescription = computed(() => getChunkPresetDescription(editForm.chunk_preset_id))
const fileList = computed(() => {
  return (store.documentFiles || []).map((f) => f.filename).filter(Boolean)
})

const canEditShareConfig = computed(() => userStore.isSuperAdmin || userStore.isAdmin)

const shareConfigDisplay = computed(() => {
  const shareConfig = database.value?.share_config || { access_level: 'global' }
  if (shareConfig.access_level === 'department') {
    const departmentIds = shareConfig.department_ids || []
    const names = departmentIds.map((id) => getDepartmentName(id)).join('、') || '无'
    return {
      color: 'blue',
      label: '部门共享',
      detail: `${departmentIds.length} 个部门可访问：${names}`
    }
  }

  if (shareConfig.access_level === 'user') {
    const userUids = shareConfig.user_uids || []
    const names = userUids.map((uid) => getUserName(uid)).join('、') || '无'
    return {
      color: 'purple',
      label: '指定人',
      detail: `${userUids.length} 个用户可访问：${names}`
    }
  }

  return {
    color: 'green',
    label: '全局共享',
    detail: '所有用户可访问'
  }
})

const getDepartmentName = (id) => {
  const dept = departments.value.find((item) => Number(item.id) === Number(id))
  return dept?.name || `部门${id}`
}

const getUserName = (uid) => {
  const user = users.value.find((item) => item.uid === uid)
  return user?.username || uid
}

const loadDepartments = async () => {
  try {
    const res = await departmentApi.getDepartments()
    departments.value = res.departments || res || []
  } catch {
    departments.value = []
  }
}

const loadUsers = async () => {
  try {
    users.value = await authApi.getUserAccessOptions()
  } catch {
    users.value = []
  }
}

const showEditModal = () => {
  editForm.name = database.value.name || ''
  editForm.description = database.value.description || ''
  editForm.auto_generate_questions =
    database.value.additional_params?.auto_generate_questions || false
  editForm.chunk_preset_id = database.value.additional_params?.chunk_preset_id || 'general'
  editForm.dify_api_url = database.value.additional_params?.dify_api_url || ''
  editForm.dify_token = database.value.additional_params?.dify_token || ''
  editForm.dify_dataset_id = database.value.additional_params?.dify_dataset_id || ''
  editForm.notion_token = ''
  editForm.notion_data_source_id = database.value.additional_params?.notion_data_source_id || ''
  editForm.notion_version = database.value.additional_params?.notion_version || '2026-03-11'
  editModalVisible.value = true
}

const handleEditSubmit = () => {
  editFormRef.value
    .validate()
    .then(async () => {
      if (shareConfigFormRef.value) {
        const validation = shareConfigFormRef.value.validate()
        if (!validation.valid) {
          message.warning(validation.message)
          return
        }
      }

      const formConfig = shareConfigFormRef.value?.config || { access_level: 'global' }
      const updateData = {
        name: editForm.name,
        description: editForm.description,
        additional_params: {},
        share_config: {
          access_level: formConfig.access_level,
          department_ids:
            formConfig.access_level === 'department' ? formConfig.department_ids || [] : [],
          user_uids: formConfig.access_level === 'user' ? formConfig.user_uids || [] : []
        }
      }

      if (isDifyKb.value) {
        if (
          !editForm.dify_api_url?.trim() ||
          !editForm.dify_token?.trim() ||
          !editForm.dify_dataset_id?.trim()
        ) {
          message.error('请完整填写 Dify API URL、Token 和 Dataset ID')
          return
        }
        if (!editForm.dify_api_url.trim().endsWith('/v1')) {
          message.error('Dify API URL 必须以 /v1 结尾')
          return
        }
        updateData.additional_params = {
          dify_api_url: editForm.dify_api_url.trim(),
          dify_token: editForm.dify_token.trim(),
          dify_dataset_id: editForm.dify_dataset_id.trim()
        }
      } else if (isNotionKb.value) {
        if (!editForm.notion_data_source_id?.trim()) {
          message.error('请填写 Notion Data Source ID')
          return
        }
        updateData.additional_params = {
          notion_data_source_id: editForm.notion_data_source_id.trim(),
          notion_version: editForm.notion_version?.trim() || '2026-03-11'
        }
        if (editForm.notion_token?.trim()) {
          updateData.additional_params.notion_token = editForm.notion_token.trim()
        }
      } else {
        updateData.additional_params = {
          auto_generate_questions: editForm.auto_generate_questions,
          chunk_preset_id: editForm.chunk_preset_id || 'general'
        }
      }

      await store.updateDatabaseInfo(updateData)
      editModalVisible.value = false
    })
    .catch((err) => {
      console.error('表单验证失败:', err)
    })
}

const deleteDatabase = () => {
  store.deleteDatabase()
}

onMounted(() => {
  loadDepartments()
  loadUsers()
})
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';
@import '@/assets/css/extension-detail.less';

.database-info-container {
  .detail-content-wrapper {
    flex: 1;
    min-height: 0;
  }

  .detail-subtitle {
    display: block;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.database-detail-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--gray-10);
  overflow: hidden;
}

.database-tab-bar {
  flex-shrink: 0;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-0);
  padding: 8px 12px 0;
  overflow-x: auto;
  overflow-y: hidden;
}

.database-tab-list {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: max-content;
}

.database-tab-item {
  position: relative;
  min-height: 40px;
  border: none;
  border-radius: 8px 8px 0 0;
  background: transparent;
  color: var(--gray-600);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 0 14px 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition:
    background 0.15s,
    color 0.15s;

  svg {
    flex-shrink: 0;
  }

  &:hover {
    color: var(--gray-900);
    background: var(--gray-50);
  }

  &:focus-visible {
    outline: 2px solid var(--main-200);
    outline-offset: 2px;
  }

  &.active {
    color: var(--main-color);
    background: var(--main-20);

    &::before {
      content: '';
      position: absolute;
      left: 12px;
      right: 12px;
      bottom: 0;
      height: 3px;
      border-radius: 3px 3px 0 0;
      background: var(--main-color);
    }
  }
}

.database-tab-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.tab-panel {
  flex: 1;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 16px var(--page-padding);
}

.file-panel {
  gap: 8px;
}

.query-config-panel {
  overflow: hidden;
}

.query-config-layout {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 12px;
}

.query-test-pane {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
}

.query-test-pane :deep(.query-section) {
  flex: 1;
  min-width: 0;
}

.query-config-pane {
  width: 360px;
  flex: 0 0 360px;
  min-height: 0;
  display: flex;
}

.query-config-pane .search-config-wrapper {
  width: 100%;
}

.query-config-pane :deep(.ant-row) {
  margin-right: 0 !important;
  margin-left: 0 !important;
}

.query-config-pane :deep(.ant-col) {
  max-width: 100%;
  flex: 0 0 100%;
  padding-right: 0 !important;
  padding-left: 0 !important;
}

.file-panel-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-shrink: 0;
  padding: 10px 12px;
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}

.file-panel-summary {
  display: flex;
  align-items: baseline;
  min-width: 0;
  gap: 8px;
}

.file-panel-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--gray-900);
  white-space: nowrap;
}

.file-panel-count {
  font-size: 12px;
  color: var(--gray-500);
  white-space: nowrap;
}

.file-management-info {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  flex-shrink: 0;
}

.file-info-title {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 180px;
}

.file-info-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.file-panel-desc {
  font-size: 12px;
  color: var(--gray-500);
}

.file-panel-status {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.file-stat-card {
  min-width: 60px;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--main-0);
  border: 1px solid var(--gray-100);
  color: var(--main-color);
  font: inherit;
  appearance: none;
  text-align: left;

  div {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  strong {
    font-size: 14px;
    line-height: 1.2;
    color: var(--gray-900);
    white-space: nowrap;
  }

  span {
    font-size: 11px;
    color: var(--gray-500);
    white-space: nowrap;
  }
}

.file-stat-summary {
  min-width: 87px;
  min-height: 36px;
  gap: 8px;
  padding: 5px 10px;

  .file-stat-inline {
    flex-direction: row;
    align-items: baseline;
    gap: 4px;
  }
}

.file-stat-action {
  cursor: pointer;
  color: var(--color-warning-500);
  border: 1px solid var(--color-warning-100);
  background-color: var(--color-warning-50);
  transition:
    background 0.15s,
    border-color 0.15s;

  &:hover {
    border-color: var(--color-warning-700);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.6;
  }
}

.file-stat-repair {
  cursor: pointer;
  transition:
    background 0.15s,
    border-color 0.15s;

  &:hover:not(:disabled) {
    border-color: var(--main-300);
    background-color: var(--main-30);
  }

  &:disabled {
    cursor: wait;
    opacity: 0.72;
  }
}

.file-stat-spinner {
  animation: file-stat-spin 0.8s linear infinite;
}

@keyframes file-stat-spin {
  to {
    transform: rotate(360deg);
  }
}

.search-config-wrapper {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  background: var(--gray-0);
  overflow: hidden;
}

.search-config-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--gray-150);
  flex-shrink: 0;

  h3 {
    margin: 0 0 4px;
    font-size: 16px;
    font-weight: 600;
    color: var(--gray-900);
  }

  p {
    margin: 0;
    font-size: 13px;
    color: var(--gray-500);
  }
}

.search-config-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
}

.share-config-readonly {
  display: flex;
  align-items: center;
  gap: 8px;

  .access-names {
    font-size: 13px;
    color: var(--gray-600);
  }
}

.chunk-preset-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.chunk-preset-help-icon {
  color: var(--gray-500);
  cursor: help;
  font-size: 14px;
}

@media (max-width: 1024px) {
  .query-config-layout {
    flex-direction: column;
    overflow-y: auto;
  }

  .query-test-pane {
    min-height: 360px;
  }

  .query-config-pane {
    width: 100%;
    flex: 0 0 auto;
    min-height: 320px;
  }
}

@media (max-width: 767px) {
  .detail-top-bar {
    gap: 10px;
  }

  .detail-actions :deep(.extension-panel-action span) {
    display: none;
  }

  .database-tab-bar {
    padding: 8px 8px 0;
  }

  .database-tab-item {
    min-width: 104px;
  }

  .query-config-layout {
    flex-direction: column;
  }

  .query-config-pane {
    width: 100%;
    flex: 0 0 auto;
    min-height: 320px;
  }

  .file-panel-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>

<style lang="less">
@media (max-width: 767px) {
  .app-layout:has(.database-info-container) {
    min-width: 0;
  }
}

/* 全局样式作为备用方案 */
.ant-popover .query-params-compact {
  width: 220px;
}

.ant-popover .query-params-compact .params-loading {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 80px;
}

.ant-popover .query-params-compact .params-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
}

.ant-popover .query-params-compact .param-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
}

.ant-popover .query-params-compact .param-item label {
  font-weight: 500;
  color: var(--gray-700);
  margin-right: 8px;
}

/* Improve panel transitions */
.panel-section {
  display: flex;
  flex-direction: column;
  border-radius: 4px;
  transition: all 0.3s;
  min-height: 0;

  &.collapsed {
    height: 36px;
    flex: none;
  }

  .section-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 12px;
    border-bottom: 1px solid var(--gray-150);
    background-color: var(--gray-25);

    .header-left {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .section-title {
      font-size: 14px;
      font-weight: 500;
      color: var(--gray-700);
      margin: 0;
    }

    .panel-actions {
      display: flex;
      gap: 0px;
    }
  }

  .content {
    flex: 1;
    min-height: 0;
  }
}

.query-section,
.graph-section {
  .panel-section();

  .content {
    padding: 8px;
    flex: 1;
    overflow: hidden;
  }
}

.graph-section {
  border: 1px solid var(--gray-100);
  border-radius: 12px;
}

.benchmark-management-container {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.benchmark-content {
  flex: 1;
  overflow: hidden;
  min-height: 0;
}
</style>
