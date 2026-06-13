<template>
  <div class="file-table-container">
    <div class="panel-header">
      <div class="panel-actions">
        <a-input
          v-model:value="filenameFilter"
          placeholder="搜索"
          size="small"
          class="action-searcher"
          allow-clear
          @change="onFilterChange"
        >
          <template #prefix>
            <Search size="14" style="color: var(--gray-400)" />
          </template>
        </a-input>

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
            @click="handleRefresh"
            :loading="refreshing"
            title="刷新"
            class="panel-action-btn"
          >
            <template #icon><RotateCw size="16" /></template>
          </a-button>
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
              <div class="overflow-search">
                <a-input
                  v-model:value="filenameFilter"
                  placeholder="搜索文件名"
                  size="small"
                  allow-clear
                  @change="onFilterChange"
                >
                  <template #prefix>
                    <Search size="14" style="color: var(--gray-400)" />
                  </template>
                </a-input>
              </div>
              <div class="overflow-actions">
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
                  :class="{ 'is-loading': refreshing }"
                  @click="handleRefresh"
                >
                  <RotateCw size="16" :class="{ spin: refreshing }" />
                  <span>刷新</span>
                </div>

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
    </div>

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

    <a-table
      :columns="columnsCompact"
      :data-source="paginatedFiles"
      row-key="file_id"
      class="my-table"
      size="small"
      :pagination="tablePagination"
      @change="handleTableChange"
      v-model:expandedRowKeys="expandedRowKeys"
      :custom-row="customRow"
      :row-selection="
        isSelectionMode
          ? {
              selectedRowKeys: selectedRowKeys,
              onChange: onSelectChange,
              getCheckboxProps: getCheckboxProps
            }
          : null
      "
      :locale="{
        emptyText: emptyText
      }"
    >
      <template #bodyCell="{ column, text, record }">
        <div v-if="column.key === 'filename'">
          <template v-if="record.is_folder">
            <span class="folder-row" @click="toggleExpand(record)">
              <FileTypeIcon is-dir :size="16" :style="{ marginRight: '8px' }" />
              {{ record.filename }}
            </span>
          </template>
          <a-button v-else class="main-btn" type="link" @click="openFileDetail(record)">
            <FileTypeIcon
              :name="record.displayName || text"
              :size="16"
              :style="{ marginRight: '8px' }"
            />
            {{ record.displayName || text }}
          </a-button>
        </div>
        <span v-else-if="column.key === 'type'">
          <span v-if="!record.is_folder" :class="['span-type', text]">{{
            text?.toUpperCase()
          }}</span>
        </span>
        <div v-else-if="column.key === 'status'" class="file-status-cell">
          <template v-if="!record.is_folder">
            <span
              v-if="text === 'done' || text === 'indexed'"
              class="file-status-icon status-success"
              ><CheckCircleFilled
            /></span>
            <span
              v-else-if="text === 'failed' || text === 'error_parsing' || text === 'error_indexing'"
              class="file-status-icon status-error"
              ><CloseCircleFilled
            /></span>
            <span
              v-else-if="text === 'processing' || text === 'parsing' || text === 'indexing'"
              class="file-status-icon status-info"
              ><HourglassFilled
            /></span>
            <span
              v-else-if="text === 'waiting' || text === 'uploaded'"
              class="file-status-icon status-warning"
              ><ClockCircleFilled
            /></span>
            <span v-else-if="text === 'parsed'" class="file-status-icon status-primary"
              ><FileTextFilled
            /></span>
            <span>{{ getStatusText(text) }}</span>
          </template>
        </div>

        <span v-else-if="column.key === 'created_at'" class="file-time-cell">
          {{ record.is_folder ? '-' : formatFileTableTime(text) }}
        </span>

        <div v-else-if="column.key === 'action'" class="table-row-actions">
          <a-popover
            placement="bottomRight"
            trigger="click"
            overlayClassName="file-action-popover"
            v-model:open="popoverVisibleMap[record.file_id]"
          >
            <template #content>
              <div class="file-action-list">
                <template v-if="record.is_folder">
                  <a-button type="text" block @click="showCreateFolderModal(record.file_id)">
                    <template #icon><component :is="h(FolderPlus)" size="14" /></template>
                    新建子文件夹
                  </a-button>
                  <a-button type="text" block danger @click="handleDeleteFolder(record)">
                    <template #icon><component :is="h(Trash2)" size="14" /></template>
                    删除文件夹
                  </a-button>
                </template>
                <template v-else>
                  <a-button
                    type="text"
                    block
                    @click="handleDownloadFile(record)"
                    :disabled="
                      lock ||
                      record.file_type === 'url' ||
                      !['done', 'indexed', 'parsed', 'error_indexing'].includes(record.status)
                    "
                  >
                    <template #icon><component :is="h(Download)" size="14" /></template>
                    下载文件
                  </a-button>

                  <!-- Parse Action -->
                  <a-button
                    v-if="record.status === 'uploaded' || record.status === 'error_parsing'"
                    type="text"
                    block
                    @click="handleParseFile(record)"
                    :disabled="lock"
                  >
                    <template #icon><component :is="h(FileText)" size="14" /></template>
                    {{ record.status === 'error_parsing' ? '重试解析' : '解析文件' }}
                  </a-button>

                  <!-- Index Action -->
                  <a-button
                    v-if="record.status === 'parsed' || record.status === 'error_indexing'"
                    type="text"
                    block
                    @click="handleIndexFile(record)"
                    :disabled="lock"
                  >
                    <template #icon><component :is="h(Database)" size="14" /></template>
                    {{ record.status === 'error_indexing' ? '重试入库' : '入库' }}
                  </a-button>

                  <!-- Reindex Action -->
                  <a-button
                    v-if="record.status === 'done' || record.status === 'indexed'"
                    type="text"
                    block
                    @click="handleReindexFile(record)"
                    :disabled="lock"
                  >
                    <template #icon><component :is="h(RotateCw)" size="14" /></template>
                    重新入库
                  </a-button>

                  <a-button
                    type="text"
                    block
                    danger
                    @click="handleDeleteFile(record.file_id)"
                    :disabled="
                      lock || ['processing', 'parsing', 'indexing'].includes(record.status)
                    "
                  >
                    <template #icon><component :is="h(Trash2)" size="14" /></template>
                    删除文件
                  </a-button>
                </template>
              </div>
            </template>
            <a-button type="text" :icon="h(Ellipsis)" class="action-trigger-btn" />
          </a-popover>
        </div>
        <span v-else>{{ text }}</span>
      </template>
    </a-table>
  </div>
</template>

<script setup>
import { ref, computed, h } from 'vue'
import { useDatabaseStore } from '@/stores/database'
import { message, Modal } from 'ant-design-vue'
import { documentApi } from '@/apis/knowledge_api'
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
  Search,
  Filter,
  MoreHorizontal
} from 'lucide-vue-next'

const store = useDatabaseStore()

const handleStatusMenuClick = (e) => {
  statusFilter.value = e.key
  // 状态筛选变化时重置到第一页
  paginationConfig.value.current = 1
}

// Status text mapping
const getStatusText = (status) => {
  const map = {
    uploaded: '已上传',
    parsing: '解析中',
    parsed: '已解析',
    error_parsing: '解析失败',
    indexing: '入库中',
    indexed: '已入库',
    error_indexing: '入库失败',
    done: '已入库',
    failed: '入库失败',
    processing: '处理中',
    waiting: '等待中'
  }
  return map[status] || status
}

const files = computed(() => Object.values(store.database.files || {}))
const refreshing = computed(() => store.state.databaseLoading)
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

const allSelectableFiles = computed(() => {
  const nameFilter = filenameFilter.value.trim().toLowerCase()
  const status = statusFilter.value

  return files.value.filter((file) => {
    if (file.is_folder) return false
    // Follow getCheckboxProps logic
    if (lock.value || file.status === 'processing' || file.status === 'waiting') return false

    if (nameFilter || status !== 'all') {
      const nameMatch =
        !nameFilter || (file.filename && file.filename.toLowerCase().includes(nameFilter))
      const statusMatch =
        status === 'all' ||
        file.status === status ||
        (status === 'indexed' && file.status === 'done') ||
        (status === 'error_indexing' && file.status === 'failed')
      return nameMatch && statusMatch
    }
    return true
  })
})

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

const expandedRowKeys = ref([])

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
    parentId = null
  }
  currentParentId.value = parentId
  createFolderModalVisible.value = true
}

defineExpose({
  showCreateFolderModal
})

const toggleExpand = (record) => {
  if (!record.is_folder) return

  const index = expandedRowKeys.value.indexOf(record.file_id)
  if (index > -1) {
    expandedRowKeys.value.splice(index, 1)
  } else {
    expandedRowKeys.value.push(record.file_id)
  }
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

// 拖拽相关逻辑
const customRow = (record) => {
  return {
    draggable: true,
    onClick: () => {
      console.log('Clicked file record:', record)
    },
    onDragstart: (event) => {
      // 检查是否是真实文件/文件夹（存在于 store 中）
      const files = store.database?.files || {}
      if (!files[record.file_id]) {
        event.preventDefault()
        return
      }

      event.dataTransfer.setData(
        'application/json',
        JSON.stringify({
          file_id: record.file_id,
          filename: record.filename
        })
      )
      event.dataTransfer.effectAllowed = 'move'
      // 可以设置一个更好看的拖拽图像
    },
    onDragover: (event) => {
      // 仅允许放置到真实文件夹中
      if (record.is_folder) {
        const files = store.database?.files || {}
        // 确保是真实的文件夹（有 ID 且在 store 中）
        if (files[record.file_id]) {
          event.preventDefault()
          event.dataTransfer.dropEffect = 'move'
          event.currentTarget.classList.add('drop-over-folder')
        }
      }
    },
    onDragleave: (event) => {
      event.currentTarget.classList.remove('drop-over-folder')
    },
    onDrop: async (event) => {
      event.preventDefault()
      event.currentTarget.classList.remove('drop-over-folder')

      const data = event.dataTransfer.getData('application/json')
      if (!data) return

      try {
        const { file_id, filename } = JSON.parse(data)
        if (file_id === record.file_id) return

        // 确认移动
        Modal.confirm({
          title: '移动文件',
          content: `确定要将 "${filename}" 移动到 "${record.filename}" 吗？`,
          onOk: async () => {
            try {
              await store.moveFile(file_id, record.file_id)
            } catch {
              // error handled in store
            }
          }
        })
      } catch (e) {
        console.error('Drop error:', e)
      }
    }
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

// 分页配置
const paginationConfig = ref({
  current: 1,
  pageSize: 100,
  pageSizeOptions: ['100', '300', '500', '1000']
})

// 文件总数
const totalFiles = computed(() => files.value.length)

// 是否显示分页
const showPagination = computed(() => totalFiles.value > paginationConfig.value.pageSize)

// 分页后的数据
const paginatedFiles = computed(() => {
  const list = filteredFiles.value
  if (!showPagination.value) return list

  const start = (paginationConfig.value.current - 1) * paginationConfig.value.pageSize
  const end = start + paginationConfig.value.pageSize
  return list.slice(start, end)
})

// 表格分页配置
const tablePagination = computed(() => ({
  current: paginationConfig.value.current,
  pageSize: paginationConfig.value.pageSize,
  total: filteredFiles.value.length,
  showSizeChanger: true,
  showTotal: (total) => `共 ${total} 项`,
  pageSizeOptions: paginationConfig.value.pageSizeOptions,
  hideOnSinglePage: true
}))

// 处理表格变化（分页、每页条数切换）
const handleTableChange = (pagination) => {
  paginationConfig.value.current = pagination.current
  paginationConfig.value.pageSize = pagination.pageSize
}

// 文件名过滤
const filenameFilter = ref('')
const statusFilter = ref('all')
const statusOptions = [
  { label: '已上传', value: 'uploaded' },
  { label: '解析中', value: 'parsing' },
  { label: '已解析', value: 'parsed' },
  { label: '解析失败', value: 'error_parsing' },
  { label: '入库中', value: 'indexing' },
  { label: '已入库', value: 'indexed' },
  { label: '入库失败', value: 'error_indexing' }
]

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
    width: 90,
    sorter: (a, b) => {
      const statusOrder = {
        done: 1,
        indexed: 1,
        processing: 2,
        indexing: 2,
        parsing: 2,
        waiting: 3,
        uploaded: 3,
        parsed: 3,
        failed: 4,
        error_indexing: 4,
        error_parsing: 4
      }
      return (statusOrder[a.status] || 5) - (statusOrder[b.status] || 5)
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

// 构建文件树
const buildFileTree = (fileList) => {
  const nodeMap = new Map()
  const roots = []
  const processedIds = new Set()

  // 1. 初始化节点映射，确保 explicit folder 有 children
  fileList.forEach((file) => {
    const item = { ...file, displayName: file.filename }
    if (item.is_folder && !item.children) {
      item.children = []
    }
    nodeMap.set(item.file_id, item)
  })

  // 2. 处理 parent_id (强关联)
  fileList.forEach((file) => {
    if (file.parent_id && nodeMap.has(file.parent_id)) {
      const parent = nodeMap.get(file.parent_id)
      const child = nodeMap.get(file.file_id)
      if (parent && child) {
        if (!parent.children) parent.children = []
        parent.children.push(child)
        processedIds.add(file.file_id)
      }
    }
  })

  // 3. 处理剩余项 (Roots 或 路径解析)
  fileList.forEach((file) => {
    if (processedIds.has(file.file_id)) return

    const item = nodeMap.get(file.file_id)
    const normalizedName = file.filename.replace(/\\/g, '/')
    const parts = normalizedName.split('/')

    // 检测是否是 URL（URL 不应该被解析为文件夹层级）
    const isUrl = file.filename.startsWith('http://') || file.filename.startsWith('https://')

    if (isUrl || parts.length === 1) {
      // Root item
      // Check if it's an explicit folder that should merge with an existing implicit one?
      if (item.is_folder) {
        const existingIndex = roots.findIndex((n) => n.is_folder && n.filename === item.filename)
        if (existingIndex !== -1) {
          const existing = roots[existingIndex]
          // Merge children from implicit to explicit
          if (existing.children && existing.children.length > 0) {
            item.children = [...(item.children || []), ...existing.children]
          }
          // Replace implicit with explicit
          roots[existingIndex] = item
        } else {
          roots.push(item)
        }
      } else {
        roots.push(item)
      }
    } else {
      // Path based logic for files like "A/B.txt"
      let currentLevel = roots
      let currentPath = ''

      for (let i = 0; i < parts.length - 1; i++) {
        const part = parts[i]
        currentPath = currentPath ? `${currentPath}/${part}` : part

        // Find existing node in currentLevel
        let node = currentLevel.find((n) => n.filename === part && n.is_folder)

        if (!node) {
          node = {
            file_id: `folder-${currentPath}`,
            filename: part,
            displayName: part,
            is_folder: true,
            children: [],
            created_at: file.created_at,
            status: 'done'
          }
          currentLevel.push(node)
        }
        currentLevel = node.children
      }

      const fileName = parts[parts.length - 1]
      item.displayName = fileName
      currentLevel.push(item)
    }
  })

  // 排序：文件夹在前，文件在后，按名称排序
  const sortNodes = (nodes) => {
    nodes.sort((a, b) => {
      if (a.is_folder && !b.is_folder) return -1
      if (!a.is_folder && b.is_folder) return 1

      return (a.filename || '').localeCompare(b.filename || '')
    })
    nodes.forEach((node) => {
      if (node.children) sortNodes(node.children)
    })
  }

  sortNodes(roots)
  return roots
}

// 过滤后的文件列表
const filteredFiles = computed(() => {
  let filtered = files.value
  const nameFilter = filenameFilter.value.trim().toLowerCase()
  const status = statusFilter.value

  // 应用过滤
  if (nameFilter || status !== 'all') {
    // 搜索/过滤模式下使用扁平列表
    return files.value
      .filter((file) => {
        const nameMatch =
          !nameFilter || (file.filename && file.filename.toLowerCase().includes(nameFilter))
        const statusMatch =
          status === 'all' ||
          file.status === status ||
          (status === 'indexed' && file.status === 'done') ||
          (status === 'error_indexing' && file.status === 'failed')
        return nameMatch && statusMatch
      })
      .map((f) => ({ ...f, displayName: f.filename }))
  }

  return buildFileTree(filtered)
})

// 空状态文本
const emptyText = computed(() => {
  return filenameFilter.value ? `没有找到包含"${filenameFilter.value}"的文件` : '暂无文件'
})

// 计算是否可以批量删除
const canBatchDelete = computed(() => {
  return selectedRowKeys.value.some((key) => {
    const file = files.value.find((f) => f.file_id === key)
    return file && !(lock.value || file.status === 'processing' || file.status === 'waiting')
  })
})

// 计算是否可以批量解析
const canBatchParse = computed(() => {
  return selectedRowKeys.value.some((key) => {
    const file = filteredFiles.value.find((f) => f.file_id === key)
    return file && !lock.value && (file.status === 'uploaded' || file.status === 'error_parsing')
  })
})

// 计算是否可以批量入库
const canBatchIndex = computed(() => {
  return selectedRowKeys.value.some((key) => {
    const file = filteredFiles.value.find((f) => f.file_id === key)
    return (
      file &&
      !lock.value &&
      (file.status === 'parsed' ||
        file.status === 'error_indexing' ||
        file.status === 'done' ||
        file.status === 'indexed')
    )
  })
})

const handleRefresh = () => {
  // 刷新时重置分页
  paginationConfig.value.current = 1
  store.getDatabaseInfo(undefined, true) // Skip query params for manual refresh
}

const onSelectChange = (keys, selectedRows) => {
  // 只保留非文件夹的文件ID
  const fileKeys = selectedRows.filter((row) => !row.is_folder).map((row) => row.file_id)

  selectedRowKeys.value = fileKeys
}

const getCheckboxProps = (record) => ({
  disabled:
    lock.value || record.status === 'processing' || record.status === 'waiting' || record.is_folder
})

const onFilterChange = (e) => {
  filenameFilter.value = e.target.value
  // 过滤变化时重置到第一页
  paginationConfig.value.current = 1
}

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
    return file && (file.status === 'uploaded' || file.status === 'error_parsing')
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
    return (
      file &&
      (file.status === 'parsed' ||
        file.status === 'error_indexing' ||
        file.status === 'done' ||
        file.status === 'indexed')
    )
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
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'
</script>

<style scoped>
.file-table-container {
  display: flex;
  flex-grow: 1;
  flex-direction: column;
  max-height: 100%;
  background: var(--gray-10);
  overflow: hidden;
  border-radius: 12px;
  border: 1px solid var(--gray-150);
  container-type: inline-size;
  container-name: file-table;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-shrink: 0;
  padding: 12px;
}

.panel-actions {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;

  .panel-actions-default {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-left: auto;
  }

  .overflow-trigger {
    display: none;
    margin-left: auto;
  }

  .action-searcher {
    width: 220px;
    border-radius: 6px;
    padding: 4px 8px;
    border: none;
    box-shadow: 0 0 0 1px var(--shadow-1);
  }
}

@container file-table (max-width: 480px) {
  .panel-actions {
    .action-searcher,
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

.my-table {
  flex: 1;
  overflow: auto;
  background-color: transparent;
  min-height: 0;
  table-layout: fixed;
  padding: 0 8px;
}

.my-table .main-btn {
  display: flex;
  justify-content: center;
  padding: 0;
  height: auto;
  line-height: 1.4;
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
  text-decoration: none;
}

.my-table .main-btn:hover {
  cursor: pointer;
  color: var(--main-color);
}

.my-table .del-btn {
  color: var(--gray-500);
}

.my-table .download-btn {
  color: var(--gray-500);
}

.my-table .download-btn:hover {
  color: var(--main-color);
}

.my-table .rechunk-btn {
  color: var(--gray-500);
}

/* 统一设置表格操作按钮的图标尺寸 */
.my-table .table-row-actions {
  display: flex;
}

.my-table .table-row-actions button {
  display: flex;
  align-items: center;
}

.my-table .table-row-actions button svg {
  width: 16px;
  height: 16px;
}

.file-status-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--gray-700);
  white-space: nowrap;
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

.my-table .rechunk-btn:hover {
  color: var(--color-warning-500);
}

.my-table .del-btn:hover {
  color: var(--color-error-500);
}

.my-table .del-btn:disabled {
  cursor: not-allowed;
}

.my-table .span-type {
  display: inline-block;
  padding: 1px 5px;
  font-size: 10px;
  font-weight: bold;
  color: var(--gray-0);
  border-radius: 4px;
  text-transform: uppercase;
  opacity: 0.9;
}

.my-table .span-type.md,
.my-table .span-type.markdown {
  background-color: var(--gray-200);
  color: var(--gray-800);
}

.auto-refresh-btn {
  height: 24px;
  padding: 0 8px;
  font-size: 12px;
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

/* Table row selection styling */
:deep(.ant-table-tbody > tr.ant-table-row-selected > td) {
  background-color: var(--main-5);
}

:deep(.ant-table-tbody > tr.ant-table-row-selected.ant-table-row:hover > td) {
  background-color: var(--main-20);
}

:deep(.ant-table-tbody > tr:hover > td) {
  background-color: var(--main-5);
}

.folder-row {
  display: flex;
  align-items: center;
  cursor: pointer;

  &:hover {
    color: var(--main-color);
  }
}

:deep(.drop-over-folder) {
  background-color: var(--primary-50) !important;
  outline: 2px dashed var(--main-color);
  outline-offset: -2px;
  z-index: 10;

  td {
    background-color: transparent !important;
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

  .overflow-search {
    padding: 10px 12px 8px;
    border-bottom: 1px solid var(--gray-100);
  }

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
