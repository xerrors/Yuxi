<template>
  <div class="workspace-view layout-container">
    <PageHeader title="工作区" :loading="loadingTree || loadingPreview" :show-border="true">
      <template #actions>
        <a-button :disabled="activeSourceKey !== 'personal'" @click="openCreateDirectoryModal">
          新建文件夹
        </a-button>
        <a-button
          type="primary"
          :loading="uploadingFile"
          :disabled="activeSourceKey !== 'personal'"
          @click="openUploadFilePicker"
        >
          上传文件
        </a-button>
      </template>
    </PageHeader>

    <input
      ref="uploadInputRef"
      class="upload-input"
      type="file"
      multiple
      @change="handleUploadInputChange"
    />

    <div class="workspace-shell" :class="{ 'is-sidebar-collapsed': sidebarCollapsed }">
      <div v-if="!sidebarCollapsed" class="workspace-sidebar-slot">
        <button
          type="button"
          class="sidebar-collapse-action"
          aria-label="收起工作区侧边栏"
          @click="sidebarCollapsed = true"
        >
          <ChevronLeft :size="16" />
        </button>
        <WorkspaceSidebar
          :active-key="activeSourceKey"
          :current-path="currentPath"
          :databases="databases"
          :loading-databases="loadingDatabases"
          :current-uid="userStore.uid"
          @select-personal="selectPersonalWorkspace"
          @select-database="selectDatabase"
          @select-path="selectWorkspacePath"
        />
      </div>
      <button
        v-else
        type="button"
        class="sidebar-expand-action"
        aria-label="展开工作区侧边栏"
        @click="sidebarCollapsed = false"
      >
        <ChevronRight :size="16" />
      </button>

      <main
        ref="workspaceMainRef"
        class="workspace-main"
        :class="{ 'is-inline-preview': showInlinePreview }"
        :style="workspaceMainStyle"
      >
        <template v-if="activeSourceKey === 'personal' || selectedDatabase">
          <WorkspaceFileList
            :entries="entries"
            :current-path="currentPath"
            :selected-path="selectedEntry?.path || ''"
            :selected-paths="selectedPaths"
            :deleting-paths="deletingPaths"
            :selection-mode="selectionMode"
            :loading="loadingTree"
            :readonly="isKnowledgeSource"
            :root-label="selectedDatabase?.name || '工作区'"
            :breadcrumb-items="isKnowledgeSource ? knowledgeBreadcrumbItems : null"
            :pagination="isKnowledgeSource ? knowledgePagination : null"
            @select-entry="handleSelectEntry"
            @breadcrumb-click="handleListBreadcrumbClick"
            @update:selected-paths="selectedPaths = $event"
            @update:selection-mode="handleSelectionModeChange"
            @delete-selected="confirmDeleteEntries(selectedEntries)"
            @delete-entry="(entry) => confirmDeleteEntries([entry])"
            @download-entry="downloadEntry"
            @page-change="handleKnowledgePageChange"
          />
          <div
            v-if="showInlinePreview"
            class="workspace-preview-resizer"
            role="separator"
            aria-label="调整预览宽度"
            tabindex="0"
            @pointerdown="startPreviewResize"
          ></div>
          <WorkspacePreviewPane
            v-if="showInlinePreview"
            :file="previewFile"
            :file-path="selectedPreviewPath"
            :loading="loadingPreview"
            :editable="!isKnowledgeSource"
            :saving="savingPreviewFile"
            @close="closePreview"
            @save="handleSavePreviewFile"
          />
        </template>

        <div v-else class="workspace-placeholder">
          <LibraryBig :size="32" />
          <h2>知识库</h2>
          <p>请选择一个可访问知识库以浏览文件。</p>
        </div>
      </main>
    </div>

    <a-modal
      v-model:open="createDirectoryModalVisible"
      title="新建文件夹"
      okText="创建"
      cancelText="取消"
      :confirm-loading="creatingDirectory"
      @ok="createDirectory"
    >
      <a-input
        v-model:value="newDirectoryName"
        placeholder="请输入文件夹名称"
        :disabled="creatingDirectory"
        @keyup.enter="createDirectory"
      />
    </a-modal>

    <a-modal
      :open="previewModalVisible && !useInlinePreview"
      width="880px"
      :style="{ maxWidth: '92vw', top: '5vh' }"
      :bodyStyle="{ height: '82vh', maxHeight: '90vh', padding: '0', overflow: 'hidden' }"
      :footer="null"
      :closable="false"
      wrapClassName="workspace-file-preview-modal"
      @cancel="closePreview"
    >
      <AgentFilePreview
        :file="previewFile"
        :filePath="selectedPreviewPath"
        :showClose="true"
        :showDownload="false"
        :showFullscreen="true"
        :full-height="true"
        :editable="activeSourceKey === 'personal'"
        :saving="savingPreviewFile"
        container-class="workspace-modal-preview-container"
        content-class="workspace-modal-preview-content"
        @close="closePreview"
        @save="handleSavePreviewFile"
      />
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { ChevronLeft, ChevronRight, LibraryBig } from 'lucide-vue-next'
import PageHeader from '@/components/shared/PageHeader.vue'
import AgentFilePreview from '@/components/AgentFilePreview.vue'
import WorkspaceFileList from '@/components/workspace/WorkspaceFileList.vue'
import WorkspacePreviewPane from '@/components/workspace/WorkspacePreviewPane.vue'
import WorkspaceSidebar from '@/components/workspace/WorkspaceSidebar.vue'
import { databaseApi } from '@/apis/knowledge_api'
import { useUserStore } from '@/stores/user'
import {
  createWorkspaceDirectory,
  deleteWorkspacePath,
  downloadWorkspaceFile,
  downloadWorkspaceKnowledgeFile,
  getWorkspaceFileContent,
  getWorkspaceKnowledgeFileContent,
  getWorkspaceKnowledgeTree,
  getWorkspaceTree,
  saveWorkspaceFileContent,
  uploadWorkspaceFiles
} from '@/apis/workspace_api'
import { normalizePreviewResponse } from '@/utils/file_preview'

const userStore = useUserStore()

const activeSourceKey = ref('personal')
const currentPath = ref('/')
const knowledgeBreadcrumbItems = ref([])
const entries = ref([])
const selectedEntry = ref(null)
const selectedPaths = ref([])
const selectionMode = ref(false)
const previewFile = ref(null)
const previewObjectUrl = ref('')
const previewModalVisible = ref(false)
const loadingTree = ref(false)
const loadingPreview = ref(false)
const savingPreviewFile = ref(false)
const loadingDatabases = ref(false)
const databases = ref([])
const selectedDatabase = ref(null)
const workspaceMainRef = ref(null)
const workspaceMainWidth = ref(0)
const createDirectoryModalVisible = ref(false)
const newDirectoryName = ref('')
const creatingDirectory = ref(false)
const uploadingFile = ref(false)
const uploadInputRef = ref(null)
const deletingPaths = ref([])
const sidebarCollapsed = ref(false)
const previewWidthPercent = ref(50)
const previewRequestId = ref(0)
const INLINE_PREVIEW_MIN_WIDTH = 960
const MAX_WORKSPACE_UPLOAD_FILES = 50
const knowledgeFileBrowser = reactive({
  parentId: null,
  pathPrefix: '',
  page: 1,
  pageSize: 100,
  total: 0,
  hasMore: false
})

const useInlinePreview = computed(() => workspaceMainWidth.value >= INLINE_PREVIEW_MIN_WIDTH)
const isKnowledgeSource = computed(() => activeSourceKey.value.startsWith('database:'))
const selectedPreviewPath = computed(() =>
  selectedEntry.value?.source === 'knowledge'
    ? selectedEntry.value.name || ''
    : selectedEntry.value?.path || ''
)
const showInlinePreview = computed(() => useInlinePreview.value && Boolean(previewFile.value))
const workspaceMainStyle = computed(() => {
  if (!showInlinePreview.value) return {}
  const listWidthPercent = 100 - previewWidthPercent.value
  return {
    gridTemplateColumns: `minmax(0, ${listWidthPercent}%) 3px minmax(280px, ${previewWidthPercent.value}%)`
  }
})

const knowledgePagination = computed(() => ({
  current: knowledgeFileBrowser.page,
  pageSize: knowledgeFileBrowser.pageSize,
  total: knowledgeFileBrowser.total,
  showSizeChanger: true,
  pageSizeOptions: ['100', '300', '500']
}))

const selectedEntries = computed(() => {
  const selectedPathSet = new Set(selectedPaths.value)
  return entries.value.filter((entry) => selectedPathSet.has(entry.path))
})

const revokePreviewObjectUrl = () => {
  if (!previewObjectUrl.value) return
  window.URL.revokeObjectURL(previewObjectUrl.value)
  previewObjectUrl.value = ''
}

const normalizePreviewFile = async (entry, response) => {
  return normalizePreviewResponse(response, entry)
}

const KNOWLEDGE_PREVIEW_LOAD_MESSAGES = {
  log: '加载知识库文件预览失败:',
  resolveUserMessage: () => '加载知识库文件预览失败'
}

const buildPreviewLoadingFile = (entry, baseFile = entry) => ({
  ...baseFile,
  ...entry,
  content: 'Loading...',
  supported: true,
  previewType: 'text',
  message: '',
  previewUrl: ''
})

const buildPreviewErrorFile = (entry, error) => ({
  ...entry,
  content: `Error loading file: ${error?.message || 'unknown error'}`,
  supported: false,
  previewType: 'unsupported',
  message: error?.message || '文件预览失败',
  previewUrl: ''
})

const startPreviewRequest = (entry, baseFile = entry) => {
  const requestId = previewRequestId.value + 1
  previewRequestId.value = requestId
  selectedEntry.value = entry
  revokePreviewObjectUrl()
  previewFile.value = buildPreviewLoadingFile(entry, baseFile)
  previewModalVisible.value = !useInlinePreview.value
  loadingPreview.value = true
  return requestId
}

const isCurrentPreviewEntry = (requestId, entry) => {
  if (previewRequestId.value !== requestId) return false
  if (entry.source === 'knowledge') {
    return selectedEntry.value?.file_id === entry.file_id
  }
  return selectedEntry.value?.path === entry.path
}

const applyPreviewFile = (requestId, entry, file) => {
  if (!isCurrentPreviewEntry(requestId, entry)) {
    if (file.previewUrl) {
      window.URL.revokeObjectURL(file.previewUrl)
    }
    return
  }

  if (file.previewUrl) {
    revokePreviewObjectUrl()
    previewObjectUrl.value = file.previewUrl
  }
  previewFile.value = file
}

const showPreviewError = (requestId, entry, error, logMessage, userMessage) => {
  if (!isCurrentPreviewEntry(requestId, entry)) return
  console.warn(logMessage, error)
  previewFile.value = buildPreviewErrorFile(entry, error)
  message.error(userMessage)
}

const finishPreviewRequest = (requestId) => {
  if (previewRequestId.value === requestId) {
    loadingPreview.value = false
  }
}

const loadWorkspacePreview = async (entry) => {
  const requestId = startPreviewRequest(entry)
  try {
    const response = await getWorkspaceFileContent(entry.path)
    if (!isCurrentPreviewEntry(requestId, entry)) return
    const file = await normalizePreviewFile(entry, response)
    applyPreviewFile(requestId, entry, file)
  } catch (error) {
    showPreviewError(requestId, entry, error, '加载文件预览失败:', '加载文件预览失败')
  } finally {
    finishPreviewRequest(requestId)
  }
}

const loadKnowledgePreview = async (
  entry,
  baseFile = entry,
  messages = KNOWLEDGE_PREVIEW_LOAD_MESSAGES
) => {
  const requestId = startPreviewRequest(entry, baseFile)
  try {
    const response = await getWorkspaceKnowledgeFileContent(entry.kb_id, entry.file_id)
    if (!isCurrentPreviewEntry(requestId, entry)) return
    const file = await normalizePreviewFile(entry, response)
    applyPreviewFile(requestId, entry, file)
  } catch (error) {
    showPreviewError(requestId, entry, error, messages.log, messages.resolveUserMessage(error))
  } finally {
    finishPreviewRequest(requestId)
  }
}

const syncSelectedPaths = () => {
  const entryPathSet = new Set(entries.value.map((entry) => entry.path))
  selectedPaths.value = selectedPaths.value.filter((path) => entryPathSet.has(path))
}

const clearWorkspaceSelection = () => {
  selectedPaths.value = []
}

const handleSelectionModeChange = (enabled) => {
  selectionMode.value = enabled
  if (!enabled) {
    clearWorkspaceSelection()
  }
}

const loadWorkspaceEntries = async (path = '/') => {
  loadingTree.value = true
  try {
    const response = await getWorkspaceTree(path)
    entries.value = response.entries || []
    currentPath.value = path
    knowledgeBreadcrumbItems.value = []
    syncSelectedPaths()
    if (!selectedPaths.value.length) {
      selectionMode.value = false
    }
  } catch (error) {
    console.warn('加载工作区目录失败:', error)
    message.error('加载工作区目录失败')
  } finally {
    loadingTree.value = false
  }
}

const loadKnowledgeEntries = async (
  database,
  {
    parentId = null,
    pathPrefix = '',
    page = 1,
    pageSize = knowledgeFileBrowser.pageSize,
    breadcrumbs = null
  } = {}
) => {
  if (!database?.kb_id) return

  loadingTree.value = true
  try {
    const response = await getWorkspaceKnowledgeTree(database.kb_id, {
      parentId,
      pathPrefix,
      page,
      pageSize
    })
    entries.value = response.entries || []
    knowledgeBreadcrumbItems.value = breadcrumbs || [
      {
        name: database.name || '知识库',
        path: '/',
        parentId: null,
        pathPrefix: '',
        isVirtualFolder: false
      }
    ]
    currentPath.value = knowledgeBreadcrumbItems.value.at(-1)?.path || '/'
    Object.assign(knowledgeFileBrowser, {
      parentId: response.parent_id || parentId || null,
      pathPrefix: response.path_prefix || pathPrefix || '',
      page: response.page || page,
      pageSize: response.page_size || pageSize,
      total: response.total || 0,
      hasMore: Boolean(response.has_more)
    })
    syncSelectedPaths()
    if (!selectedPaths.value.length) {
      selectionMode.value = false
    }
  } catch (error) {
    console.warn('加载知识库目录失败:', error)
    entries.value = []
    message.error(error?.message || '加载知识库目录失败')
  } finally {
    loadingTree.value = false
  }
}

const loadDatabases = async () => {
  loadingDatabases.value = true
  try {
    const response = await databaseApi.getAccessibleDatabases()
    databases.value = (response?.databases || []).filter((database) => {
      return database?.supports_documents !== false
    })
  } catch (error) {
    console.warn('加载可访问知识库失败:', error)
    databases.value = []
  } finally {
    loadingDatabases.value = false
  }
}

const selectPersonalWorkspace = async () => {
  const wasKnowledgeSource = isKnowledgeSource.value
  activeSourceKey.value = 'personal'
  selectedDatabase.value = null
  knowledgeBreadcrumbItems.value = []
  closePreview()
  clearWorkspaceSelection()
  if (wasKnowledgeSource || currentPath.value !== '/' || !entries.value.length) {
    await loadWorkspaceEntries('/')
  }
}

const selectWorkspacePath = async (path) => {
  activeSourceKey.value = 'personal'
  selectedDatabase.value = null
  knowledgeBreadcrumbItems.value = []
  closePreview()
  clearWorkspaceSelection()
  await loadWorkspaceEntries(path)
}

const selectKnowledgeBreadcrumb = async (item, index) => {
  if (!selectedDatabase.value || !item) return
  closePreview()
  clearWorkspaceSelection()
  const breadcrumbs = knowledgeBreadcrumbItems.value.slice(0, index + 1)
  await loadKnowledgeEntries(selectedDatabase.value, {
    parentId: item.parentId || null,
    pathPrefix: item.pathPrefix || '',
    page: 1,
    breadcrumbs
  })
}

const handleListBreadcrumbClick = async ({ item, index }) => {
  if (isKnowledgeSource.value) {
    await selectKnowledgeBreadcrumb(item, index)
    return
  }
  await selectWorkspacePath(item?.path || '/')
}

const selectDatabase = async (database) => {
  if (database?.supports_documents === false) return
  closePreview()
  clearWorkspaceSelection()
  selectedDatabase.value = database
  activeSourceKey.value = `database:${database.kb_id}`
  await loadKnowledgeEntries(database)
}

const openKnowledgeDirectory = async (entry) => {
  closePreview()
  clearWorkspaceSelection()
  const parentPath = knowledgeBreadcrumbItems.value.at(-1)?.path || '/'
  const nextPath = parentPath === '/' ? `/${entry.name}` : `${parentPath}/${entry.name}`
  const currentBreadcrumb = knowledgeBreadcrumbItems.value.at(-1)
  const isVirtualFolder = Boolean(entry.is_virtual_folder)
  const nextBreadcrumb = {
    name: entry.name,
    path: nextPath,
    parentId: isVirtualFolder ? currentBreadcrumb?.parentId || null : entry.file_id,
    pathPrefix: isVirtualFolder ? entry.path_prefix || '' : '',
    isVirtualFolder
  }
  await loadKnowledgeEntries(selectedDatabase.value, {
    parentId: nextBreadcrumb.parentId,
    pathPrefix: nextBreadcrumb.pathPrefix,
    page: 1,
    breadcrumbs: [
      ...knowledgeBreadcrumbItems.value,
      nextBreadcrumb
    ]
  })
}

const handleKnowledgePageChange = async ({ page, pageSize }) => {
  if (!selectedDatabase.value || !isKnowledgeSource.value) return
  closePreview()
  clearWorkspaceSelection()
  const currentBreadcrumb = knowledgeBreadcrumbItems.value.at(-1)
  await loadKnowledgeEntries(selectedDatabase.value, {
    parentId: currentBreadcrumb?.parentId || null,
    pathPrefix: currentBreadcrumb?.pathPrefix || '',
    page,
    pageSize,
    breadcrumbs: [...knowledgeBreadcrumbItems.value]
  })
}

const openWorkspaceDirectory = async (entry) => {
  closePreview()
  clearWorkspaceSelection()
  await loadWorkspaceEntries(entry.path)
}

const handleSelectEntry = async (entry) => {
  if (entry.is_dir) {
    if (isKnowledgeSource.value) {
      await openKnowledgeDirectory(entry)
      return
    }
    await openWorkspaceDirectory(entry)
    return
  }

  if (isKnowledgeSource.value) {
    await loadKnowledgePreview(entry)
    return
  }

  await loadWorkspacePreview(entry)
}

const closePreview = () => {
  previewRequestId.value += 1
  previewModalVisible.value = false
  selectedEntry.value = null
  previewFile.value = null
  loadingPreview.value = false
  revokePreviewObjectUrl()
}

const handleSavePreviewFile = async (content) => {
  if (selectedEntry.value?.source === 'knowledge') {
    message.warning('知识库文件为只读，无法保存')
    return
  }
  if (!selectedEntry.value?.path || savingPreviewFile.value) return

  savingPreviewFile.value = true
  try {
    const response = await saveWorkspaceFileContent(selectedEntry.value.path, content)
    if (response.entry) {
      selectedEntry.value = response.entry
    }
    previewFile.value = {
      ...previewFile.value,
      content
    }
    await loadWorkspaceEntries(currentPath.value)
    message.success('文件保存成功')
  } catch (error) {
    console.warn('保存工作区文件失败:', error)
    message.error(error?.message || '文件保存失败')
  } finally {
    savingPreviewFile.value = false
  }
}

const openCreateDirectoryModal = () => {
  if (activeSourceKey.value !== 'personal') return
  newDirectoryName.value = ''
  createDirectoryModalVisible.value = true
}

const createDirectory = async () => {
  if (creatingDirectory.value) return
  const directoryName = newDirectoryName.value.trim()
  if (!directoryName) {
    message.warning('请输入文件夹名')
    return
  }

  creatingDirectory.value = true
  try {
    await createWorkspaceDirectory(currentPath.value, directoryName)
    await loadWorkspaceEntries(currentPath.value)
    createDirectoryModalVisible.value = false
    newDirectoryName.value = ''
    message.success('文件夹创建成功')
  } catch (error) {
    console.warn('创建文件夹失败:', error)
    message.error(error?.message || '创建文件夹失败')
  } finally {
    creatingDirectory.value = false
  }
}

const openUploadFilePicker = () => {
  if (activeSourceKey.value !== 'personal' || uploadingFile.value) return
  if (uploadInputRef.value) {
    uploadInputRef.value.value = ''
    uploadInputRef.value.click()
  }
}

const handleUploadInputChange = async (event) => {
  const files = Array.from(event.target?.files || [])
  if (!files.length || uploadingFile.value) return
  if (files.length > MAX_WORKSPACE_UPLOAD_FILES) {
    message.warning(`一次最多上传 ${MAX_WORKSPACE_UPLOAD_FILES} 个文件`)
    event.target.value = ''
    return
  }

  uploadingFile.value = true
  try {
    await uploadWorkspaceFiles(currentPath.value, files)
    await loadWorkspaceEntries(currentPath.value)
    message.success(`${files.length} 个文件上传成功`)
  } catch (error) {
    console.warn('上传文件失败:', error)
    message.error(error?.message || '上传文件失败')
  } finally {
    uploadingFile.value = false
    event.target.value = ''
  }
}

const comparablePath = (path) => String(path || '/').replace(/\/$/, '') || '/'

const isSameOrChildPath = (path, targetPath) => {
  const normalizedPath = comparablePath(path)
  const normalizedTargetPath = comparablePath(targetPath)
  return (
    normalizedPath === normalizedTargetPath || normalizedPath.startsWith(`${normalizedTargetPath}/`)
  )
}

const confirmDeleteEntries = (targetEntries) => {
  const validEntries = (targetEntries || []).filter(Boolean)
  if (!validEntries.length) return

  const isBatch = validEntries.length > 1
  const firstEntry = validEntries[0]
  Modal.confirm({
    title: isBatch
      ? `确认删除选中的 ${validEntries.length} 项？`
      : firstEntry.is_dir
        ? `确认删除文件夹「${firstEntry.name}」？`
        : `确认删除文件「${firstEntry.name}」？`,
    content:
      isBatch || firstEntry.is_dir
        ? '将删除文件夹及其所有内容，删除后不可恢复。'
        : '删除后不可恢复。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => deleteEntries(validEntries)
  })
}

const deleteEntries = async (targetEntries) => {
  const paths = targetEntries.map((entry) => entry.path)
  deletingPaths.value = paths
  try {
    await Promise.all(paths.map((path) => deleteWorkspacePath(path)))
    if (
      selectedEntry.value &&
      paths.some((path) => isSameOrChildPath(selectedEntry.value.path, path))
    ) {
      closePreview()
    }
    clearWorkspaceSelection()
    await loadWorkspaceEntries(currentPath.value)
    message.success(paths.length > 1 ? '选中项删除成功' : '删除成功')
  } catch (error) {
    console.warn('删除工作区文件失败:', error)
    message.error(error?.message || '删除失败')
    await loadWorkspaceEntries(currentPath.value)
  } finally {
    deletingPaths.value = []
  }
}

const parseDownloadFilename = (contentDisposition) => {
  if (!contentDisposition) return ''

  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match && utf8Match[1]) {
    try {
      return decodeURIComponent(utf8Match[1])
    } catch (error) {
      console.warn('解析 UTF-8 文件名失败:', error)
    }
  }

  const asciiMatch = contentDisposition.match(/filename="?([^";]+)"?/i)
  if (asciiMatch && asciiMatch[1]) {
    return asciiMatch[1]
  }

  return ''
}

const downloadEntry = async (entry) => {
  if (!entry || entry.is_dir) return

  try {
    const response =
      entry.source === 'knowledge'
        ? await downloadWorkspaceKnowledgeFile(entry.kb_id, entry.file_id)
        : await downloadWorkspaceFile(entry.path)
    const blob = await response.blob()
    const contentDisposition =
      response.headers.get('Content-Disposition') || response.headers.get('content-disposition')
    const filename = parseDownloadFilename(contentDisposition) || entry.name || 'download'
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
  } catch (error) {
    console.warn('下载文件失败:', error)
    message.error(error?.message || '下载文件失败')
  }
}

let resizePointerId = null

const stopPreviewResize = () => {
  resizePointerId = null
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  window.removeEventListener('pointermove', resizePreview)
  window.removeEventListener('pointerup', stopPreviewResize)
  window.removeEventListener('pointercancel', stopPreviewResize)
}

const resizePreview = (event) => {
  if (!workspaceMainRef.value || resizePointerId === null || event.pointerId !== resizePointerId)
    return
  const rect = workspaceMainRef.value.getBoundingClientRect()
  const relativeX = event.clientX - rect.left
  const nextPreviewPercent = Math.round(((rect.width - relativeX) / rect.width) * 100)
  previewWidthPercent.value = Math.min(70, Math.max(30, nextPreviewPercent))
}

const startPreviewResize = (event) => {
  if (!showInlinePreview.value) return
  resizePointerId = event.pointerId
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('pointermove', resizePreview)
  window.addEventListener('pointerup', stopPreviewResize)
  window.addEventListener('pointercancel', stopPreviewResize)
}

let workspaceResizeObserver = null

onMounted(async () => {
  await Promise.all([loadWorkspaceEntries('/'), loadDatabases()])

  if (workspaceMainRef.value && typeof ResizeObserver !== 'undefined') {
    workspaceMainWidth.value = workspaceMainRef.value.clientWidth || 0
    workspaceResizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      workspaceMainWidth.value = entry.contentRect.width
    })
    workspaceResizeObserver.observe(workspaceMainRef.value)
  }
})

onUnmounted(() => {
  workspaceResizeObserver?.disconnect()
  workspaceResizeObserver = null
  stopPreviewResize()
  revokePreviewObjectUrl()
})

watch(useInlinePreview, (isInline, wasInline) => {
  if (!previewFile.value) {
    previewModalVisible.value = false
    return
  }

  if (isInline) {
    previewModalVisible.value = false
    return
  }

  if (wasInline) {
    closePreview()
  }
})
</script>

<style scoped lang="less">
.workspace-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.upload-input {
  display: none;
}

.workspace-shell {
  position: relative;
  display: grid;
  grid-template-columns: 195px minmax(0, 1fr);
  flex: 1 1 auto;
  min-height: 0;
  background: var(--gray-0);
  overflow: hidden;

  &.is-sidebar-collapsed {
    grid-template-columns: minmax(0, 1fr);
  }
}

.workspace-sidebar-slot {
  position: relative;
  min-width: 0;
  min-height: 0;
}

.workspace-sidebar-slot :deep(.workspace-sidebar) {
  height: 100%;
}

.sidebar-collapse-action,
.sidebar-expand-action {
  width: 26px;
  height: 26px;
  position: absolute;
  top: 50%;
  z-index: 4;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--gray-150);
  background: var(--gray-0);
  color: var(--gray-600);
  cursor: pointer;
  transform: translateY(-50%);
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);

  &:hover {
    background: var(--main-20);
    color: var(--main-color);
  }
}

.sidebar-collapse-action {
  right: -13px;
  width: 26px;
  border-radius: 50%;
}

.sidebar-expand-action {
  left: 0;
  width: 22px;
  border-left: 0;
  border-radius: 0 12px 12px 0;
}

.workspace-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
}

.workspace-preview-resizer {
  width: 2px;
  min-width: 2px;
  background: var(--gray-100);
  cursor: col-resize;

  &:hover {
    background: var(--gray-200);
  }
}

.workspace-placeholder {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 360px;
  padding: 32px;
  color: var(--gray-500);
  text-align: center;

  h2 {
    margin: 8px 0 0;
    color: var(--gray-900);
    font-size: 18px;
    font-weight: 600;
  }

  p {
    max-width: 360px;
    margin: 0;
    font-size: 14px;
    line-height: 1.6;
  }
}
</style>

<style lang="less">
.workspace-file-preview-modal {
  .ant-modal {
    z-index: 1050;

    .ant-modal-content {
      padding: 0;
      overflow: hidden;
      border: 1px solid var(--gray-200);
      border-radius: 8px;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
    }

    .ant-modal-body {
      height: 82vh;
      padding: 0;
      overflow: hidden;
    }

    .workspace-modal-preview-container {
      height: 100%;
      max-height: none;
    }

    .workspace-modal-preview-content {
      flex: 1 1 auto;
      max-height: none;
      min-height: 0;
    }

    .workspace-modal-preview-content .html-preview,
    .workspace-modal-preview-content .pdf-preview {
      display: block;
      height: 100%;
      min-height: 100%;
    }
  }
}
</style>
