<template>
  <div class="file-table-container">
    <!-- 入库/重新入库参数配置模态框 -->
    <a-modal
      v-model:open="indexConfigModalVisible"
      :title="indexConfigModalTitle"
      :confirm-loading="indexConfigModalLoading"
      width="600px"
    >
      <template #footer>
        <a-button key="back" @click="handleIndexConfigCancel">取消</a-button>
        <a-button key="submit" type="primary" @click="handleIndexConfigConfirm">确定</a-button>
      </template>
      <div class="index-params">
        <ChunkParamsConfig
          :temp-chunk-params="indexParams"
          :show-qa-split="true"
          :show-chunk-size-overlap="true"
          :show-preset="true"
          :allow-preset-follow-default="true"
          :database-preset-id="store.database?.additional_params?.chunk_preset_id || 'general'"
        />
      </div>
    </a-modal>

    <!-- 新建文件夹模态框 -->
    <a-modal
      v-model:open="createFolderModalVisible"
      title="新建文件夹"
      :confirm-loading="createFolderLoading"
      @ok="handleCreateFolder"
    >
      <a-input
        v-model:value="newFolderName"
        placeholder="请输入文件夹名称"
        @pressEnter="handleCreateFolder"
      />
    </a-modal>

    <FileBrowserTable
      class="knowledge-file-browser"
      :rows="files"
      :columns="columnsCompact"
      row-key="file_id"
      :breadcrumbs="fileBreadcrumbItems"
      :loading="store.fileBrowser.loading"
      :pagination="tablePagination"
      :selection="tableSelection"
      :empty-text="emptyText"
      refreshable
      :refreshing="refreshing"
      @refresh="handleRefresh"
      @open-row="handleOpenRow"
      @breadcrumb-click="handleBreadcrumbPayloadClick"
      @page-change="handleTablePageChange"
    >
      <template #breadcrumb-suffix>
        <span v-if="isFilteredView" class="file-breadcrumb-filter">筛选结果</span>
      </template>

      <template #toolbar-actions>
        <div class="panel-actions">
          <div class="panel-actions-default">
            <a-dropdown trigger="click">
              <a-button
                type="text"
                class="panel-action-btn"
                :class="{ active: statusFilter !== 'all' }"
                title="筛选状态"
              >
                <template #icon><Filter size="16" /></template>
              </a-button>
              <template #overlay>
                <a-menu :selectedKeys="[statusFilter]" @click="handleStatusMenuClick">
                  <a-menu-item key="all">全部状态</a-menu-item>
                  <a-menu-item v-for="opt in statusOptions" :key="opt.value">
                    {{ opt.label }}
                  </a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>

            <a-button
              type="text"
              @click="toggleSelectionMode"
              title="多选"
              class="panel-action-btn"
              :class="{ active: isSelectionMode }"
            >
              <template #icon><CheckSquare size="16" /></template>
            </a-button>
          </div>

          <a-dropdown
            trigger="click"
            v-model:open="overflowMenuOpen"
            :overlayStyle="{ minWidth: '220px' }"
            overlayClassName="panel-overflow-popover"
          >
            <a-button type="text" class="panel-action-btn overflow-trigger" title="更多">
              <template #icon><MoreHorizontal size="16" /></template>
            </a-button>
            <template #overlay>
              <div class="overflow-menu-panel" @click.stop>
                <div class="overflow-actions">
                  <div
                    class="overflow-action-item"
                    :class="{ 'is-loading': refreshing }"
                    @click="handleRefresh"
                  >
                    <RotateCw size="16" :class="{ spin: refreshing }" />
                    <span>刷新</span>
                  </div>

                  <a-dropdown trigger="click" placement="bottomLeft">
                    <div class="overflow-action-item" :class="{ active: statusFilter !== 'all' }">
                      <Filter size="16" />
                      <span>筛选</span>
                      <span class="overflow-action-hint">{{ currentStatusLabel }}</span>
                    </div>
                    <template #overlay>
                      <a-menu :selectedKeys="[statusFilter]" @click="handleStatusMenuClick">
                        <a-menu-item key="all">全部状态</a-menu-item>
                        <a-menu-item v-for="opt in statusOptions" :key="opt.value">
                          {{ opt.label }}
                        </a-menu-item>
                      </a-menu>
                    </template>
                  </a-dropdown>

                  <div
                    class="overflow-action-item"
                    :class="{ active: isSelectionMode }"
                    @click="toggleSelectionMode"
                  >
                    <CheckSquare size="16" />
                    <span>多选</span>
                  </div>
                </div>
              </div>
            </template>
          </a-dropdown>
        </div>
      </template>

      <template #before-table>
        <div class="batch-actions" v-if="isSelectionMode">
          <div class="batch-info">
            <a-checkbox
              :checked="isAllSelected"
              :indeterminate="isPartiallySelected"
              @change="onSelectAllChange"
              style="margin-right: 8px"
            />
            <span>{{ selectedRowKeys.length }} 项</span>
          </div>
          <div style="display: flex; gap: 2px">
            <a-button
              type="link"
              @click="handleBatchParse"
              :loading="batchParsing"
              :disabled="!canBatchParse"
              :icon="h(FileText, { size: 16 })"
            >
              批量解析
            </a-button>
            <a-button
              type="link"
              @click="handleBatchIndex"
              :loading="batchIndexing"
              :disabled="!canBatchIndex"
              :icon="h(Database, { size: 16 })"
            >
              批量入库
            </a-button>
            <a-button
              type="link"
              danger
              @click="handleBatchDelete"
              :loading="batchDeleting"
              :disabled="!canBatchDelete"
              :icon="h(Trash2, { size: 16 })"
            >
              批量删除
            </a-button>
          </div>
        </div>
      </template>

      <template #name="{ row }">
        <span class="file-name-cell">
          <template v-if="row.is_folder">
            <span class="folder-row" :title="row.filename" @click.stop="openFolder(row)">
              <FileTypeIcon is-dir :size="16" :style="{ marginRight: '8px' }" />
              <span class="file-name-text">{{ row.filename }}</span>
            </span>
          </template>
          <a-button
            v-else
            class="main-btn"
            type="link"
            :title="row.displayName || row.filename"
            @click.stop="openFileDetail(row)"
          >
            <FileTypeIcon
              :name="row.displayName || row.filename"
              :size="16"
              :style="{ marginRight: '8px' }"
            />
            <span class="file-name-text">{{ row.displayName || row.filename }}</span>
          </a-button>
        </span>
      </template>

      <template #status="{ row, text }">
        <div class="file-status-cell">
          <template v-if="!row.is_folder">
            <button
              v-if="hasStatusAction(row)"
              type="button"
              class="file-status-pill file-status-action"
              :disabled="lock"
              :title="getStatusActionTitle(row)"
              @click.stop="handleStatusAction(row)"
            >
              <span v-if="getStatusIcon(text)" :class="['file-status-icon', getStatusTone(text)]">
                <component :is="getStatusIcon(text)" />
              </span>
              <span>{{ getStatusText(text) }}</span>
            </button>
            <span v-else class="file-status-pill file-status-static">
              <span v-if="getStatusIcon(text)" :class="['file-status-icon', getStatusTone(text)]">
                <component :is="getStatusIcon(text)" />
              </span>
              <span>{{ getStatusText(text) }}</span>
            </span>
          </template>
        </div>
      </template>

      <template #cell-created_at="{ row, text }">
        <span class="file-time-cell">
          {{ row.is_folder ? '-' : formatFileTableTime(text) }}
        </span>
      </template>

      <template #row-actions="{ row }">
        <div class="table-row-actions">
          <a-popover
            v-if="!row.is_virtual_folder"
            placement="bottomRight"
            trigger="click"
            overlayClassName="file-action-popover"
            v-model:open="popoverVisibleMap[row.file_id]"
          >
            <template #content>
              <div class="file-action-list">
                <template v-if="row.is_folder">
                  <a-button type="text" block @click="showCreateFolderModal(row.file_id)">
                    <template #icon><component :is="h(FolderPlus)" size="14" /></template>
                    新建子文件夹
                  </a-button>
                  <a-button type="text" block danger @click="handleDeleteFolder(row)">
                    <template #icon><component :is="h(Trash2)" size="14" /></template>
                    删除文件夹
                  </a-button>
                </template>
                <template v-else>
                  <a-button
                    type="text"
                    block
                    @click="handleDownloadFile(row)"
                    :disabled="lock || !canDownloadFile(row)"
                  >
                    <template #icon><component :is="h(Download)" size="14" /></template>
                    下载文件
                  </a-button>

                  <!-- Parse Action -->
                  <a-button
                    v-if="canParseFile(row)"
                    type="text"
                    block
                    @click="handleParseFile(row)"
                    :disabled="lock"
                  >
                    <template #icon><component :is="h(FileText)" size="14" /></template>
                    {{ getFilePrimaryAction(row)?.label || '解析文件' }}
                  </a-button>

                  <!-- Index Action -->
                  <a-button
                    v-if="getFilePrimaryAction(row)?.type === FILE_ACTIONS.INDEX"
                    type="text"
                    block
                    @click="handleIndexFile(row)"
                    :disabled="lock"
                  >
                    <template #icon><component :is="h(Database)" size="14" /></template>
                    {{ getFilePrimaryAction(row)?.label || '入库' }}
                  </a-button>

                  <!-- Reindex Action -->
                  <a-button
                    v-if="canReindexFile(row)"
                    type="text"
                    block
                    @click="handleReindexFile(row)"
                    :disabled="lock"
                  >
                    <template #icon><component :is="h(RotateCw)" size="14" /></template>
                    重新入库
                  </a-button>

                  <a-button
                    type="text"
                    block
                    danger
                    @click="handleDeleteFile(row.file_id)"
                    :disabled="!canDeleteFile(row, lock)"
                  >
                    <template #icon><component :is="h(Trash2)" size="14" /></template>
                    删除文件
                  </a-button>
                </template>
              </div>
            </template>
            <a-button type="text" :icon="h(Ellipsis)" class="action-trigger-btn" />
          </a-popover>
          <span v-else class="action-placeholder"></span>
        </div>
      </template>
    </FileBrowserTable>
  </div>
</template>

<script setup>
import { ref, computed, h, watch } from 'vue'
import { useDatabaseStore } from '@/stores/database'
import { message, Modal } from 'ant-design-vue'
import { documentApi } from '@/apis/knowledge_api'
import {
  FILE_ACTIONS,
  FILE_STATUS_FILTER_OPTIONS,
  canDeleteFile,
  canDownloadFile,
  canIndexFile,
  canParseFile,
  canReindexFile,
  canSelectFile,
  getFilePrimaryAction,
  getFileStatusSortWeight,
  getFileStatusView
} from '@/utils/knowledge_file_policy'
import {
  CheckCircleFilled,
  HourglassFilled,
  CloseCircleFilled,
  ClockCircleFilled,
  FileTextFilled
} from '@ant-design/icons-vue'
import {
  Trash2,
  Download,
  RotateCw,
  Ellipsis,
  FolderPlus,
  CheckSquare,
  FileText,
  Database,
  Filter,
  MoreHorizontal
} from 'lucide-vue-next'

const store = useDatabaseStore()

const applyFilters = async (overrides = {}) => {
  const nextStatus = overrides.status ?? statusFilter.value
  const recursive = nextStatus !== 'all'
  const currentFolder = folderBreadcrumbs.value[folderBreadcrumbs.value.length - 1]
  const isVirtualFolder = Boolean(currentFolder?.is_virtual_folder)
  const parentId = isVirtualFolder ? currentFolder?.parent_id || null : currentFolder?.file_id || null
  const pathPrefix = isVirtualFolder ? currentFolder?.path_prefix || '' : ''
  await store.loadDocumentFiles({
    page: 1,
    parentId: recursive ? null : parentId,
    pathPrefix: recursive ? '' : pathPrefix,
    status: nextStatus,
    recursive
  })
}

const handleStatusMenuClick = async (e) => {
  statusFilter.value = e.key
  await applyFilters({ status: e.key })
}

const statusIconMap = {
  success: CheckCircleFilled,
  progress: HourglassFilled,
  error: CloseCircleFilled,
  clock: ClockCircleFilled,
  file: FileTextFilled
}

const getStatusText = (status) => getFileStatusView(status).label

const getStatusTone = (status) => getFileStatusView(status).tone

const getStatusIcon = (status) => {
  const icon = getFileStatusView(status).icon
  return statusIconMap[icon] || null
}

const hasStatusAction = (record) => {
  return Boolean(getFilePrimaryAction(record))
}

const getStatusActionTitle = (record) => {
  const action = getFilePrimaryAction(record)
  if (action) return action.label
  return getStatusText(record.status)
}

const files = computed(() => store.documentFiles || [])
const folderBreadcrumbs = computed(() => store.folderBreadcrumbs || [])
const fileBreadcrumbItems = computed(() =>
  folderBreadcrumbs.value.map((item, index) => ({
    ...item,
    key: item.file_id || `root-${index}`,
    name: item.filename || '全部文件'
  }))
)
const isFilteredView = computed(() => Boolean(store.fileBrowser.recursive))
const refreshing = computed(() => store.state.databaseLoading || store.fileBrowser.loading)
const lock = computed(() => store.state.lock)
const batchDeleting = computed(() => store.state.batchDeleting)
const batchParsing = computed(() => store.state.chunkLoading)
const batchIndexing = computed(() => store.state.chunkLoading)
const selectedRowKeys = computed({
  get: () => store.selectedRowKeys,
  set: (keys) => (store.selectedRowKeys = keys)
})

const isSelectionMode = ref(false)
const overflowMenuOpen = ref(false)

const currentStatusLabel = computed(() => {
  if (statusFilter.value === 'all') return ''
  const opt = statusOptions.find((o) => o.value === statusFilter.value)
  return opt ? opt.label : ''
})

const allSelectableFiles = computed(() => files.value.filter((file) => canSelectFile(file, lock.value)))

const isAllSelected = computed(() => {
  const selectableIds = allSelectableFiles.value.map((f) => f.file_id)
  if (selectableIds.length === 0) return false
  return selectableIds.every((id) => selectedRowKeys.value.includes(id))
})

const isPartiallySelected = computed(() => {
  const selectableIds = allSelectableFiles.value.map((f) => f.file_id)
  const selectedCount = selectableIds.filter((id) => selectedRowKeys.value.includes(id)).length
  return selectedCount > 0 && selectedCount < selectableIds.length
})

const onSelectAllChange = (e) => {
  if (e.target.checked) {
    selectedRowKeys.value = allSelectableFiles.value.map((f) => f.file_id)
  } else {
    selectedRowKeys.value = []
  }
}

const popoverVisibleMap = ref({})
const closePopover = (fileId) => {
  if (fileId) {
    popoverVisibleMap.value[fileId] = false
  }
}

// 新建文件夹相关
const createFolderModalVisible = ref(false)
const newFolderName = ref('')
const createFolderLoading = ref(false)
const currentParentId = ref(null)

const showCreateFolderModal = (parentId = null) => {
  if (typeof parentId === 'string') {
    closePopover(parentId)
  }
  newFolderName.value = ''
  // 如果是事件对象（来自顶部按钮点击），则设为null
  if (parentId && typeof parentId === 'object') {
    parentId = store.fileBrowser.parentId
  }
  currentParentId.value = parentId ?? store.fileBrowser.parentId
  createFolderModalVisible.value = true
}

defineExpose({
  showCreateFolderModal,
  applyStatusFilter: async (status) => {
    statusFilter.value = status
    await applyFilters({ status })
  },
  getCurrentFolderId: () => store.fileBrowser.parentId,
  refresh: () => handleRefresh()
})

const openFolder = async (record) => {
  statusFilter.value = 'all'
  await store.enterFolder(record)
}

const toggleSelectionMode = () => {
  isSelectionMode.value = !isSelectionMode.value
  if (!isSelectionMode.value) {
    selectedRowKeys.value = []
  }
}

const handleCreateFolder = async () => {
  if (!newFolderName.value.trim()) {
    message.warning('请输入文件夹名称')
    return
  }

  createFolderLoading.value = true
  try {
    await documentApi.createFolder(store.kbId, newFolderName.value, currentParentId.value)
    message.success('创建成功')
    createFolderModalVisible.value = false
    handleRefresh()
  } catch (error) {
    console.error(error)
    message.error('创建失败: ' + (error.message || '未知错误'))
  } finally {
    createFolderLoading.value = false
  }
}

// 入库/重新入库参数配置相关
const indexConfigModalVisible = ref(false)
const indexConfigModalLoading = computed(() => store.state.chunkLoading)
const indexConfigModalTitle = ref('入库参数配置')

const createDefaultIndexParams = () => ({
  chunk_preset_id: '',
  chunk_parser_config: {}
})

const indexParams = ref(createDefaultIndexParams())

const buildIndexParamsPayload = () => {
  return buildChunkParamsPayload(indexParams.value, {
    includeSizeOverlap: true
  })
}
const currentIndexFileIds = ref([])
const isBatchIndexOperation = ref(false)

const pageSizeOptions = ['100', '300', '500']

// 表格分页配置
const tablePagination = computed(() => ({
  current: store.fileBrowser.page,
  pageSize: store.fileBrowser.pageSize,
  total: store.fileBrowser.total,
  showSizeChanger: true,
  showTotal: (total) => `共 ${total} 项`,
  pageSizeOptions,
  hideOnSinglePage: true
}))

// 处理页码和每页条数切换
const handleTablePageChange = ({ page, pageSize }) => {
  store.loadDocumentFiles({
    page,
    pageSize
  })
}

const statusFilter = ref('all')
const statusOptions = FILE_STATUS_FILTER_OPTIONS

// 紧凑表格列定义
const columnsCompact = [
  {
    title: '文件名',
    dataIndex: 'filename',
    key: 'filename',
    ellipsis: true,
    width: undefined, // 不设置宽度，让它占据剩余空间
    sorter: (a, b) => {
      if (a.is_folder && !b.is_folder) return -1
      if (!a.is_folder && b.is_folder) return 1
      return (a.filename || '').localeCompare(b.filename || '')
    },
    sortDirections: ['ascend', 'descend']
  },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 104,
    sorter: (a, b) => {
      return getFileStatusSortWeight(a) - getFileStatusSortWeight(b)
    },
    sortDirections: ['ascend', 'descend']
  },
  {
    title: '时间',
    dataIndex: 'created_at',
    key: 'created_at',
    width: 180,
    sorter: (a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0),
    sortDirections: ['ascend', 'descend']
  },
  { title: '操作', key: 'action', dataIndex: 'file_id', width: 64, align: 'center' }
]

// 空状态文本
const emptyText = computed(() => {
  return '暂无文件'
})

// 计算是否可以批量删除
const canBatchDelete = computed(() => {
  return selectedRowKeys.value.some((key) => {
    const file = files.value.find((f) => f.file_id === key)
    return canSelectFile(file, lock.value)
  })
})

// 计算是否可以批量解析
const canBatchParse = computed(() => {
  return selectedRowKeys.value.some((key) => {
    const file = files.value.find((f) => f.file_id === key)
    return !lock.value && canParseFile(file)
  })
})

// 计算是否可以批量入库
const canBatchIndex = computed(() => {
  return selectedRowKeys.value.some((key) => {
    const file = files.value.find((f) => f.file_id === key)
    return !lock.value && canIndexFile(file)
  })
})

const handleRefresh = () => {
  store.getDatabaseInfo(undefined, true, true)
  store.loadDocumentFiles()
}

const handleBreadcrumbClick = async (index) => {
  statusFilter.value = 'all'
  await store.goToFolder(index)
}

const handleBreadcrumbPayloadClick = async ({ index }) => {
  await handleBreadcrumbClick(index)
}

const handleOpenRow = (record) => {
  if (record.is_folder) {
    openFolder(record)
    return
  }
  openFileDetail(record)
}

const onSelectChange = (keys, selectedRows) => {
  // 只保留非文件夹的文件ID
  const fileKeys = selectedRows.filter((row) => !row.is_folder).map((row) => row.file_id)

  selectedRowKeys.value = fileKeys
}

const getCheckboxProps = (record) => ({
  disabled: !canSelectFile(record, lock.value)
})

const tableSelection = computed(() => {
  if (!isSelectionMode.value) return null
  return {
    selectedRowKeys: selectedRowKeys.value,
    onChange: onSelectChange,
    getCheckboxProps
  }
})

const handleDeleteFile = (fileId) => {
  store.handleDeleteFile(fileId)
  closePopover(fileId)
}

const handleDeleteFolder = (record) => {
  closePopover(record.file_id)
  Modal.confirm({
    title: '删除文件夹',
    content: `确定要删除文件夹 "${record.filename}" 及其包含的所有内容吗？`,
    okText: '确认',
    cancelText: '取消',
    onOk: async () => {
      try {
        await store.deleteFile(record.file_id)
        message.success('删除成功')
      } catch {
        // Error handled in store but we can add extra handling if needed
      }
    }
  })
}

const handleBatchDelete = () => {
  store.handleBatchDelete()
}

const handleBatchParse = async () => {
  const validKeys = selectedRowKeys.value.filter((key) => {
    const file = files.value.find((f) => f.file_id === key)
    return canParseFile(file)
  })

  if (validKeys.length === 0) {
    message.warning('没有可解析的文件')
    return
  }

  await store.parseFiles(validKeys)
  selectedRowKeys.value = []
}

const handleBatchIndex = async () => {
  const validKeys = selectedRowKeys.value.filter((key) => {
    const file = files.value.find((f) => f.file_id === key)
    return canIndexFile(file)
  })

  if (validKeys.length === 0) {
    message.warning('没有可入库的文件')
    return
  }

  currentIndexFileIds.value = [...validKeys]
  isBatchIndexOperation.value = true
  indexConfigModalTitle.value = '批量入库参数配置'
  indexConfigModalVisible.value = true
}

const openFileDetail = (record) => {
  console.log('openFileDetail', record)
  store.openFileDetail(record)
}

const handleDownloadFile = async (record) => {
  closePopover(record.file_id)
  const kbId = store.kbId
  if (!kbId) {
    console.error('无法获取数据库ID，数据库ID:', store.kbId, '记录:', record)
    message.error('无法获取数据库ID，请刷新页面后重试')
    return
  }

  console.log('开始下载文件:', { kbId, fileId: record.file_id, record })

  try {
    const response = await documentApi.downloadDocument(kbId, record.file_id)

    // 获取文件名
    const contentDisposition = response.headers.get('content-disposition')
    let filename = record.filename
    if (contentDisposition) {
      // 首先尝试匹配RFC 2231格式 filename*=UTF-8''...
      const rfc2231Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/)
      if (rfc2231Match) {
        try {
          filename = decodeURIComponent(rfc2231Match[1])
        } catch (error) {
          console.warn('Failed to decode RFC2231 filename:', rfc2231Match[1], error)
        }
      } else {
        // 回退到标准格式 filename="..."
        const filenameMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/)
        if (filenameMatch && filenameMatch[1]) {
          filename = filenameMatch[1].replace(/['"]/g, '')
          // 解码URL编码的文件名
          try {
            filename = decodeURIComponent(filename)
          } catch (error) {
            console.warn('Failed to decode filename:', filename, error)
            // 如果解码失败，使用原文件名
          }
        }
      }
    }

    // 创建blob并下载
    const blob = await response.blob()
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.style.display = 'none'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
  } catch (error) {
    console.error('下载文件时出错:', error)
    const errorMessage = error.message || '下载失败，请稍后重试'
    message.error(errorMessage)
  }
}

const handleParseFile = async (record) => {
  closePopover(record.file_id)
  await store.parseFiles([record.file_id])
}

const handleStatusAction = async (record) => {
  if (lock.value || !hasStatusAction(record)) return

  const action = getFilePrimaryAction(record)
  if (action?.type === FILE_ACTIONS.PARSE) {
    await handleParseFile(record)
    return
  }

  if (action?.type === FILE_ACTIONS.INDEX) {
    await handleIndexFile(record)
  }
}

const resetIndexParams = (processingParams = null) => {
  if (!processingParams) {
    indexParams.value = createDefaultIndexParams()
    return
  }

  const chunkParserConfig = processingParams.chunk_parser_config
  indexParams.value = {
    chunk_preset_id: processingParams.chunk_preset_id || '',
    chunk_parser_config: isPlainObject(chunkParserConfig) ? { ...chunkParserConfig } : {}
  }
}

const loadRecordProcessingParams = async (record) => {
  if (record?.processing_params) {
    return record.processing_params
  }

  const detail = await documentApi.getDocumentInfo(store.kbId, record.file_id)
  return detail?.processing_params || null
}

const handleIndexFile = async (record) => {
  closePopover(record.file_id)
  currentIndexFileIds.value = [record.file_id]
  isBatchIndexOperation.value = false
  indexConfigModalTitle.value = '入库参数配置'

  const processingParams = await loadRecordProcessingParams(record)
  resetIndexParams(processingParams)

  indexConfigModalVisible.value = true
}

const handleReindexFile = async (record) => {
  closePopover(record.file_id)
  currentIndexFileIds.value = [record.file_id]
  isBatchIndexOperation.value = false
  indexConfigModalTitle.value = '重新入库参数配置'

  const processingParams = await loadRecordProcessingParams(record)
  resetIndexParams(processingParams)

  indexConfigModalVisible.value = true
}

// 入库确认 (统一处理 Index 和 Reindex)
const handleIndexConfigConfirm = async () => {
  try {
    // 调用 indexFiles 接口 (支持 params)
    const result = await store.indexFiles(currentIndexFileIds.value, buildIndexParamsPayload())
    if (result) {
      currentIndexFileIds.value = []
      // 清空选择
      if (isBatchIndexOperation.value) {
        selectedRowKeys.value = []
      }
      // 关闭模态框
      indexConfigModalVisible.value = false

      resetIndexParams()
    } else {
      // message.error(`入库失败: ${result.message}`); // store already shows message
    }
  } catch (error) {
    console.error('入库失败:', error)
    const errorMessage = error.message || '入库失败，请稍后重试'
    message.error(errorMessage)
  }
}

// 入库取消
const handleIndexConfigCancel = () => {
  indexConfigModalVisible.value = false
  currentIndexFileIds.value = []
  isBatchIndexOperation.value = false
  resetIndexParams()
}

watch(
  () => store.kbId,
  async (nextKbId) => {
    if (!nextKbId) return
    statusFilter.value = 'all'
    store.resetFileBrowser()
    await store.loadDocumentFiles({ kbId: nextKbId, page: 1 })
  },
  { immediate: true }
)

const formatFileTableTime = (value) => {
  const parsed = parseToShanghai(value)
  if (!parsed) return '-'

  const oneYearAgo = parseToShanghai(Date.now()).subtract(1, 'year')
  if (parsed.isAfter(oneYearAgo)) {
    return parsed.format('MM月DD日 HH:mm:ss')
  }

  return parsed.format('YYYY年MM月DD日')
}

// 导入工具函数
import { parseToShanghai } from '@/utils/time'
import { buildChunkParamsPayload, isPlainObject } from '@/utils/chunk_presets'
import ChunkParamsConfig from '@/components/ChunkParamsConfig.vue'
import FileBrowserTable from '@/components/common/FileBrowserTable.vue'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'
</script>

<style scoped lang="less">
.file-table-container {
  display: flex;
  flex-grow: 1;
  flex-direction: column;
  max-height: 100%;
  background: var(--gray-0);
  overflow: hidden;
  border-radius: 12px;
  border: 1px solid var(--gray-150);
  container-type: inline-size;
  container-name: file-table;
}

.knowledge-file-browser {
  flex: 1 1 auto;
  min-height: 0;
}

.file-breadcrumb-filter {
  color: var(--main-color);
  font-size: 13px;
  line-height: 24px;
  white-space: nowrap;
}

.panel-actions {
  display: flex;
  align-items: center;
  gap: 8px;

  .panel-actions-default {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .overflow-trigger {
    display: none;
  }
}

@container file-table (max-width: 480px) {
  .panel-actions {
    .panel-actions-default {
      display: none;
    }

    .overflow-trigger {
      display: flex;
    }
  }
}

.batch-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 12px;
  background-color: var(--main-10);
  border-radius: 4px;
  margin-bottom: 4px;
  flex-shrink: 0;
}

.batch-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.batch-info span {
  font-size: 12px;
  font-weight: 500;
  color: var(--gray-700);
}

.batch-actions .ant-btn {
  font-size: 12px;
  padding: 4px 8px;
  height: auto;
  border-radius: 4px;
  display: flex;
  align-items: center;
  gap: 4px;

  svg {
    width: 14px;
    height: 14px;
  }
}

.file-name-cell,
.folder-row,
.main-btn {
  align-items: center;
  min-width: 0;
  max-width: 100%;
}

.file-name-cell {
  display: inline-flex;
  vertical-align: middle;
  width: auto;
}

.main-btn {
  display: inline-flex;
  justify-content: flex-start;
  padding: 0;
  height: auto;
  line-height: 1.4;
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
  text-decoration: none;
}

.folder-row {
  display: inline-flex;
}

.file-name-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.main-btn:hover {
  cursor: pointer;
  color: var(--main-color);
}

.table-row-actions {
  display: flex;
}

.table-row-actions button {
  display: flex;
  align-items: center;
}

.table-row-actions button svg {
  width: 16px;
  height: 16px;
}

.file-status-cell {
  display: inline-flex;
  align-items: center;
  color: var(--gray-700);
  white-space: nowrap;
}

.file-status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  box-sizing: border-box;
  min-height: 24px;
  max-width: 100%;
  padding: 0 6px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-700);
  font-family: inherit;
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
  appearance: none;
}

.file-status-action {
  cursor: pointer;
}

.file-status-action:hover:not(:disabled) {
  background: var(--gray-100);
  border-color: var(--gray-200);
  color: var(--gray-900);
}

.file-status-action:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.file-status-icon {
  display: inline-flex;
  align-items: center;
}

.status-success {
  color: var(--color-success-500);
}

.status-error {
  color: var(--color-error-500);
}

.status-info {
  color: var(--color-info-500);
}

.status-warning {
  color: var(--color-warning-500);
}

.status-primary {
  color: var(--color-primary-500);
}

.file-time-cell {
  color: var(--gray-600);
  white-space: nowrap;
}

.panel-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  padding: 4px;
  color: var(--gray-600);
  background-color: var(--gray-0);
  box-shadow: 0 0 0 1px var(--shadow-1);
  transition: all 0.1s ease;
  font-size: 12px;
  width: auto;
  height: auto;

  &.expand {
    transform: scaleX(-1);
  }

  &.expanded {
    transform: scaleX(1);
  }
}

.panel-action-btn.auto-refresh-btn.ant-btn-primary {
  background-color: var(--main-color);
  border-color: var(--main-color);
  color: var(--gray-0);
}

.panel-action-btn:hover {
  background-color: var(--gray-50);
  color: var(--main-color);
  /* border: 1px solid var(--main-100); */
}

.panel-action-btn.active {
  color: var(--main-color);
  background-color: var(--main-10);
  font-weight: 600;
  box-shadow: 0 0 0 1px var(--main-200);
}

.action-trigger-btn {
  padding: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  color: var(--gray-500);
  transition: all 0.2s;

  &:hover {
    background-color: var(--gray-100);
    color: var(--main-color);
  }

  svg {
    width: 16px;
    height: 16px;
  }
}

.folder-row {
  cursor: pointer;

  &:hover {
    color: var(--main-color);
  }
}
</style>

<style lang="less">
.file-action-popover {
  .ant-popover-inner {
    padding: 4px;
  }

  .ant-popover-inner {
    border-radius: 8px;
    border: 1px solid var(--gray-150);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
    overflow: hidden;
  }

  .ant-popover-arrow {
    display: none;
  }
}

.file-action-list {
  display: flex;
  flex-direction: column;
  gap: 2px;

  .ant-btn {
    text-align: left;
    height: 30px;
    font-size: 14px;
    display: flex;
    align-items: center;
    border-radius: 6px;
    padding: 0 8px;
    border: none;
    box-shadow: none;

    &:hover {
      background-color: var(--gray-50);
      color: var(--main-color);
    }

    &.ant-btn-dangerous:hover {
      background-color: var(--color-error-50);
      color: var(--color-error-500);
    }

    .anticon,
    .lucide {
      margin-right: 10px;
    }

    span {
      font-size: 13px;
    }
  }

  .ant-btn:disabled {
    background-color: transparent;
    color: var(--gray-300);
    cursor: not-allowed;
  }
}

.panel-overflow-popover {
  .ant-popover-inner {
    padding: 0;
    border-radius: 8px;
    border: 1px solid var(--gray-150);
    background: var(--gray-0);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
    overflow: hidden;
  }

  .ant-popover-arrow {
    display: none;
  }
}

.overflow-menu-panel {
  width: 160px;
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 8px;

  .overflow-actions {
    display: flex;
    flex-direction: column;
    padding: 4px;
  }

  .overflow-action-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    color: var(--gray-700);
    transition: background-color 0.1s ease;

    &:hover {
      background-color: var(--gray-50);
      color: var(--main-color);
    }

    &.active {
      color: var(--main-color);
      background-color: var(--main-10);
      font-weight: 500;
    }

    .overflow-action-hint {
      margin-left: auto;
      font-size: 12px;
      color: var(--gray-400);
    }

    .spin {
      animation: spin 1s linear infinite;
    }
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
