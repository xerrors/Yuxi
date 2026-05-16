<template>
  <div ref="panelRef" class="agent-panel" :class="{ resizing: isResizing }">
    <!-- 拖拽手柄 -->
    <div class="resize-handle" @pointerdown="startResize"></div>
    <div class="panel-header">
      <div class="panel-header-main">
        <div class="panel-title">
          <span><strong>文件系统</strong></span>
        </div>
        <div class="window-actions">
          <button class="header-action-btn" title="刷新" @click="emitRefresh">
            <RefreshCw :size="15" />
          </button>
        </div>
      </div>
    </div>
    <div class="tab-content">
      <div class="files-display">
        <div v-if="!threadId" class="empty">创建对话后可查看工作区</div>
        <div v-else-if="loadingFiles" class="empty">正在加载文件系统...</div>
        <div v-else-if="filesystemError" class="empty error-state">
          <div>{{ filesystemError }}</div>
          <a-button type="link" size="small" @click="refreshFileSystem">重试</a-button>
        </div>
        <div v-else-if="!fileTreeData.length" class="empty">当前工作区为空</div>
        <div v-else class="files-workspace" :class="{ 'is-inline-preview': useInlinePreview }">
          <div class="file-tree-pane">
            <div class="file-tree-container">
              <FileTreeComponent
                v-model:selectedKeys="selectedKeys"
                v-model:expandedKeys="expandedKeys"
                :tree-data="fileTreeData"
                :load-data="loadData"
                @select="onFileSelect"
              >
                <template #title="{ node }">
                  <div class="tree-node-name" :title="node.title">
                    <span class="name-start">{{ node.nameStart || node.title }}</span>
                    <span class="name-end" v-if="node.nameEnd">{{ node.nameEnd }}</span>
                  </div>
                </template>
                <template #actions="{ node }">
                  <div class="node-actions-container">
                    <button
                      v-if="node.isLeaf"
                      class="tree-action-btn tree-download-btn"
                      @click.stop="downloadFile(node.fileData)"
                      title="下载文件"
                    >
                      <Download :size="14" />
                    </button>
                    <button
                      class="tree-action-btn tree-delete-btn"
                      :disabled="deletingPaths.has(node.key)"
                      @click.stop="confirmDeleteNode(node)"
                      :title="node.isLeaf ? '删除文件' : '删除文件夹'"
                    >
                      <Trash2 :size="14" />
                    </button>
                  </div>
                </template>
              </FileTreeComponent>
            </div>
          </div>

          <div v-if="useInlinePreview" class="inline-preview-pane">
            <AgentFilePreview
              v-if="currentFile"
              containerClass="inline-preview-shell"
              contentClass="inline-file-content"
              :file="currentFile"
              :filePath="currentFilePath"
              :fullHeight="true"
              :showClose="true"
              closeVariant="collapse-right"
              :showDownload="true"
              :showFullscreen="true"
              @download="downloadFile"
              @close="closePreview"
            />
            <div v-else class="inline-preview-empty">
              <div class="inline-preview-empty-title">选择文件后可在此预览</div>
              <div class="inline-preview-empty-desc">
                当前宽度足够，预览会直接显示在工作台右侧。
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <a-modal
      v-model:open="modalVisible"
      width="800px"
      :style="{ maxWidth: '90vw', top: '5vh' }"
      :bodyStyle="{ maxHeight: '90vh', overflow: 'auto' }"
      :footer="null"
      :closable="false"
      wrapClassName="agent-file-preview-modal"
      @cancel="closePreview"
    >
      <AgentFilePreview
        :file="currentFile"
        :filePath="currentFilePath"
        :showClose="true"
        :showDownload="true"
        :showFullscreen="true"
        @download="downloadFile"
        @close="closePreview"
      />
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Download, RefreshCw, Trash2 } from 'lucide-vue-next'
import { Modal, message } from 'ant-design-vue'
import FileTreeComponent from '@/components/FileTreeComponent.vue'
import AgentFilePreview from '@/components/AgentFilePreview.vue'
import {
  deleteViewerFile,
  downloadViewerFile,
  getViewerFileContent,
  getViewerFileSystemTree
} from '@/apis/viewer_filesystem'

const props = defineProps({
  agentState: {
    type: Object,
    default: () => ({})
  },
  threadFiles: {
    type: Array,
    default: () => []
  },
  threadId: {
    type: String,
    default: null
  },
  agentId: {
    type: String,
    default: null
  },
  agentConfigId: {
    type: [String, Number],
    default: null
  },
  panelRatio: {
    type: Number,
    default: 0.35
  }
})

const emit = defineEmits(['refresh', 'resize', 'resizing'])
const INLINE_PREVIEW_MIN_WIDTH = 920
const DISPLAY_ROOT_DIRECTORY_NAME = 'user-data'

const panelRef = ref(null)
const modalVisible = ref(false)
const currentFile = ref(null)
const currentFilePath = ref('')
const loadingFiles = ref(false)
const filesystemError = ref('')
const panelWidth = ref(0)

const dynamicTreeData = ref([])
const selectedKeys = ref([])
const expandedKeys = ref([])
const deletingPaths = ref(new Set())

const useInlinePreview = computed(() => panelWidth.value >= INLINE_PREVIEW_MIN_WIDTH)

const buildDisplayName = (fullPath) => {
  const normalized = String(fullPath || '').replace(/\/+$/, '')
  if (!normalized || normalized === '/') return '/'
  const parts = normalized.split('/').filter(Boolean)
  return parts[parts.length - 1] || normalized
}

const sortEntries = (entries) => {
  return [...entries].sort((left, right) => {
    const leftIsDir = Boolean(left?.is_dir)
    const rightIsDir = Boolean(right?.is_dir)
    if (leftIsDir !== rightIsDir) {
      return leftIsDir ? -1 : 1
    }

    const leftName = buildDisplayName(left?.path).toLowerCase()
    const rightName = buildDisplayName(right?.path).toLowerCase()
    return leftName.localeCompare(rightName, 'zh-Hans-CN')
  })
}

const createTreeNode = (entry) => {
  const fullPath = String(entry?.path || '')
  const title = buildDisplayName(fullPath)
  const isLeaf = !entry?.is_dir

  let nameStart = title
  let nameEnd = ''

  if (isLeaf && title.length > 5) {
    nameEnd = title.slice(-5)
    nameStart = title.slice(0, -5)
  }

  return {
    key: fullPath,
    title,
    nameStart,
    nameEnd,
    isLeaf,
    children: isLeaf ? undefined : [],
    fileData: {
      ...entry,
      path: fullPath,
      name: title,
      type: isLeaf ? 'file' : 'directory'
    },
    class: isLeaf ? 'file-node' : 'folder-node'
  }
}

const updateTreeChildren = (nodes, targetKey, children) => {
  return nodes.map((node) => {
    if (node.key === targetKey) {
      return { ...node, children }
    }
    if (!node.children?.length) {
      return node
    }
    return {
      ...node,
      children: updateTreeChildren(node.children, targetKey, children)
    }
  })
}

const removeTreeNode = (nodes, targetKey) => {
  return nodes.reduce((result, node) => {
    if (node.key === targetKey) {
      return result
    }

    const nextNode = node.children?.length
      ? {
          ...node,
          children: removeTreeNode(node.children, targetKey)
        }
      : node

    result.push(nextNode)
    return result
  }, [])
}

const normalizePathKey = (path) => String(path || '').replace(/\/+$/, '')

const isSameOrChildPath = (path, targetPath) => {
  const normalizedPath = normalizePathKey(path)
  const normalizedTargetPath = normalizePathKey(targetPath)
  if (!normalizedPath || !normalizedTargetPath) return false
  return (
    normalizedPath === normalizedTargetPath || normalizedPath.startsWith(`${normalizedTargetPath}/`)
  )
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

const getFileName = (fileItem) => {
  if (fileItem?.path) {
    return String(fileItem.path).split('/').pop() || String(fileItem.path)
  }
  return '未知文件'
}

const loadDirectoryChildren = async (directoryPath) => {
  const res = await getViewerFileSystemTree(
    props.threadId,
    directoryPath,
    props.agentId,
    props.agentConfigId
  )
  return sortEntries(res?.entries || []).map((entry) => createTreeNode(entry))
}

const refreshFileSystem = async () => {
  if (!props.threadId) {
    dynamicTreeData.value = []
    filesystemError.value = ''
    return
  }

  loadingFiles.value = true
  filesystemError.value = ''

  try {
    const res = await getViewerFileSystemTree(
      props.threadId,
      '/',
      props.agentId,
      props.agentConfigId
    )
    if (res?.entries) {
      const displayRootEntry = res.entries.find(
        (entry) => entry?.is_dir && entry.name === DISPLAY_ROOT_DIRECTORY_NAME
      )

      dynamicTreeData.value = displayRootEntry ? await loadDirectoryChildren(displayRootEntry.path) : []
      expandedKeys.value = []
      selectedKeys.value = []
    } else {
      dynamicTreeData.value = []
    }
  } catch (error) {
    dynamicTreeData.value = []
    filesystemError.value = error?.message || '加载文件系统失败'
    console.error('Failed to load root files', error)
  } finally {
    loadingFiles.value = false
  }
}

const loadData = async (treeNode) => {
  if (treeNode.isLeaf || treeNode.children?.length || !props.threadId) return

  try {
    const children = await loadDirectoryChildren(treeNode.key)
    dynamicTreeData.value = updateTreeChildren(dynamicTreeData.value, treeNode.key, children)
  } catch (error) {
    console.error('Failed to load children for', treeNode.key, error)
  }
}

const fileTreeData = computed(() => dynamicTreeData.value)

let panelResizeObserver = null

const revokeCurrentPreviewUrl = () => {
  const previewUrl = currentFile.value?.previewUrl
  if (previewUrl) {
    window.URL.revokeObjectURL(previewUrl)
  }
}

const onFileSelect = async (nextSelectedKeys, { node }) => {
  selectedKeys.value = nextSelectedKeys
  if (!node?.isLeaf || !props.threadId) return

  revokeCurrentPreviewUrl()
  currentFilePath.value = node.key
  currentFile.value = {
    ...node.fileData,
    content: 'Loading...',
    supported: true,
    previewType: 'text',
    message: '',
    previewUrl: ''
  }
  modalVisible.value = !useInlinePreview.value

  try {
    const res = await getViewerFileContent(
      props.threadId,
      node.key,
      props.agentId,
      props.agentConfigId
    )
    const previewType = res?.preview_type || 'text'
    let previewUrl = ''

    if ((previewType === 'image' || previewType === 'pdf') && res?.supported) {
      const response = await downloadViewerFile(
        props.threadId,
        node.key,
        props.agentId,
        props.agentConfigId
      )
      const blob = await response.blob()
      previewUrl = window.URL.createObjectURL(blob)
    }

    currentFile.value = {
      ...node.fileData,
      content: res?.content ?? '',
      supported: res?.supported !== false,
      previewType,
      message: res?.message || '',
      previewUrl
    }
  } catch (error) {
    currentFile.value = {
      ...node.fileData,
      content: `Error loading file: ${error?.message || 'unknown error'}`,
      supported: false,
      previewType: 'unsupported',
      message: error?.message || '文件预览失败',
      previewUrl: ''
    }
  }
}

const closePreview = () => {
  revokeCurrentPreviewUrl()
  modalVisible.value = false
  currentFile.value = null
  currentFilePath.value = ''
  selectedKeys.value = []
}

const pruneTreeStateAfterDelete = (targetPath) => {
  selectedKeys.value = selectedKeys.value.filter((key) => !isSameOrChildPath(key, targetPath))
  expandedKeys.value = expandedKeys.value.filter((key) => !isSameOrChildPath(key, targetPath))

  if (isSameOrChildPath(currentFilePath.value, targetPath)) {
    closePreview()
  }
}

const confirmDeleteNode = (node) => {
  const fileName = node?.title || getFileName(node?.fileData)
  const isDirectory = !node?.isLeaf
  Modal.confirm({
    title: isDirectory ? `确认删除文件夹「${fileName}」？` : `确认删除文件「${fileName}」？`,
    content: isDirectory ? '将删除该文件夹及其所有内容，删除后不可恢复。' : '删除后不可恢复。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      const nextDeletingPaths = new Set(deletingPaths.value)
      nextDeletingPaths.add(node.key)
      deletingPaths.value = nextDeletingPaths

      try {
        await deleteViewerFile(props.threadId, node.key, props.agentId, props.agentConfigId)
        dynamicTreeData.value = removeTreeNode(dynamicTreeData.value, node.key)
        pruneTreeStateAfterDelete(node.key)
        message.success(isDirectory ? '文件夹删除成功' : '文件删除成功')
      } catch (error) {
        console.error(isDirectory ? '删除文件夹失败:' : '删除文件失败:', error)
        message.error(error?.message || (isDirectory ? '删除文件夹失败' : '删除文件失败'))
      } finally {
        const latestDeletingPaths = new Set(deletingPaths.value)
        latestDeletingPaths.delete(node.key)
        deletingPaths.value = latestDeletingPaths
      }
    }
  })
}

const downloadFile = async (fileItem) => {
  if (!props.threadId || !fileItem?.path) return

  try {
    const response = await downloadViewerFile(
      props.threadId,
      fileItem.path,
      props.agentId,
      props.agentConfigId
    )
    const blob = await response.blob()
    const contentDisposition =
      response.headers.get('Content-Disposition') || response.headers.get('content-disposition')
    const filename = parseDownloadFilename(contentDisposition) || getFileName(fileItem)
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
  } catch (error) {
    console.error('下载文件失败:', error)
  }
}

const emitRefresh = () => {
  refreshFileSystem()
  emit('refresh', props.threadId)
}

const isResizing = ref(false)

let resizePointerId = null
let pendingClientX = 0
let resizeFrameId = 0

const flushResize = () => {
  resizeFrameId = 0
  if (!isResizing.value) return
  emit('resize', pendingClientX)
}

const queueResize = (clientX) => {
  pendingClientX = clientX
  if (resizeFrameId) return
  resizeFrameId = window.requestAnimationFrame(flushResize)
}

const startResize = (e) => {
  if (e.button !== 0) return

  isResizing.value = true
  resizePointerId = e.pointerId
  pendingClientX = e.clientX
  emit('resizing', true, e.clientX)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'

  e.currentTarget?.setPointerCapture?.(e.pointerId)
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', stopResize)
  window.addEventListener('pointercancel', stopResize)
}

const onPointerMove = (e) => {
  if (!isResizing.value || e.pointerId !== resizePointerId) return
  queueResize(e.clientX)
}

const stopResize = (e) => {
  if (!isResizing.value || (e && e.pointerId !== resizePointerId)) return

  if (resizeFrameId) {
    window.cancelAnimationFrame(resizeFrameId)
    resizeFrameId = 0
  }

  if (e) {
    pendingClientX = e.clientX
    emit('resize', pendingClientX)
  }

  isResizing.value = false
  resizePointerId = null
  emit('resizing', false)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', stopResize)
  window.removeEventListener('pointercancel', stopResize)
}

onMounted(() => {
  refreshFileSystem()

  if (panelRef.value && typeof ResizeObserver !== 'undefined') {
    panelWidth.value = panelRef.value.clientWidth || 0
    panelResizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      panelWidth.value = entry.contentRect.width
    })
    panelResizeObserver.observe(panelRef.value)
  }
})

onUnmounted(() => {
  panelResizeObserver?.disconnect()
  panelResizeObserver = null
  if (resizeFrameId) {
    window.cancelAnimationFrame(resizeFrameId)
    resizeFrameId = 0
  }
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', stopResize)
  window.removeEventListener('pointercancel', stopResize)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  revokeCurrentPreviewUrl()
})

watch([() => props.threadId, () => props.agentId, () => props.agentConfigId], ([threadId]) => {
  if (threadId) {
    refreshFileSystem()
  } else {
    dynamicTreeData.value = []
    expandedKeys.value = []
    selectedKeys.value = []
    filesystemError.value = ''
  }
})

watch(useInlinePreview, (isInline) => {
  if (!currentFile.value) {
    modalVisible.value = false
    return
  }

  modalVisible.value = !isInline
})
</script>

<style scoped lang="less">
.resize-handle {
  position: absolute;
  left: -2px;
  top: 50%;
  transform: translateY(-50%);
  height: 32px;
  width: 4px;
  cursor: col-resize;
  background: var(--gray-300);
  border-radius: 2px;
  z-index: 10;
  transition: background 0.2s;
  touch-action: none;

  &:hover {
    background: var(--main-400);
  }
}

.agent-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--gray-0);
  transition: none;

  &.resizing {
    transition: none;
  }
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 2px;
  padding: 4px 16px;
  min-height: 44px;
  background: var(--gray-25);
  flex-shrink: 0;
}

.panel-header-main {
  display: contents;
}

.header-action-btn {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-600);
  cursor: pointer;
  padding: 0;
  transition: all 0.15s ease;

  &:hover {
    background: var(--gray-100);
    color: var(--gray-900);
  }

  &:disabled {
    color: var(--gray-300);
    cursor: not-allowed;
    background: transparent;
  }
}

.panel-title {
  display: flex;
  align-items: center;
  gap: 12px;
  order: 1;
  flex: 1;
  min-width: 0;
  font-weight: 600;
  font-size: 14px;
  color: var(--gray-900);

  span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .header-icon {
    flex-shrink: 0;
    color: var(--gray-700);
  }
}

.file-toolbar,
.window-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.file-toolbar {
  order: 2;
}

.window-actions {
  order: 3;
  flex-shrink: 0;
}


.tab-content {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  min-height: 0; /* Important for flex child scroll */

  /* 自定义滚动条 */
  &::-webkit-scrollbar {
    width: 6px;
  }

  &::-webkit-scrollbar-track {
    background: var(--gray-50);
  }

  &::-webkit-scrollbar-thumb {
    background: var(--gray-300);
    border-radius: 3px;

    &:hover {
      background: var(--gray-400);
    }
  }
}

.error-state {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.files-display {
  height: 100%;
  min-height: 0;
}

.files-workspace {
  height: 100%;
  min-height: 0;
}

.files-workspace.is-inline-preview {
  display: flex;
  gap: 12px;
}

.file-tree-pane {
  min-width: 0;
  min-height: 0;
}

.files-workspace.is-inline-preview .file-tree-pane {
  flex: 0 0 27%;
}

.inline-preview-pane {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
}

.inline-preview-shell {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  background: var(--gray-0);
  overflow: hidden;
}

.inline-preview-header {
  min-height: 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 12px;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-25);
}

.inline-file-content {
  flex: 1;
  min-height: 0;
  max-height: none;
}

.inline-preview-empty {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  border: 1px dashed var(--gray-200);
  border-radius: 12px;
  background: linear-gradient(180deg, var(--gray-25) 0%, var(--gray-0) 100%);
  color: var(--gray-500);
  padding: 24px;
}

.inline-preview-empty-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--gray-800);
}

.inline-preview-empty-desc {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
}

.empty {
  text-align: center;
  color: var(--gray-500);
  padding: 60px 0;
  font-size: 14px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;

  &::before {
    content: '📋';
    font-size: 32px;
    opacity: 0.6;
  }
}

/* File Tree Styles - VS Code Style Refined */
.file-tree-container {
  margin: 0 -4px;
  min-height: 0;
}

.files-workspace.is-inline-preview .file-tree-container {
  height: 100%;
  overflow-y: auto;
}

.tree-node-name {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
  font-family:
    -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  font-size: 14px;
  color: var(--gray-800);
}

.name-start {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.name-end {
  flex-shrink: 0;
  white-space: nowrap;
}

.node-actions-container {
  display: flex;
  align-items: center;
  gap: 4px;
}

.tree-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;
  padding: 0;

  &:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }
}

.tree-download-btn:hover {
  color: var(--main-600);
}

.tree-delete-btn:hover:not(:disabled) {
  color: var(--error-600, #dc2626);
}

/* 附件列表专用样式 */
.attachment-tree :deep(.ant-tree-node-content-wrapper) {
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  margin-bottom: 4px;

  &:hover {
    background-color: var(--gray-50);
    border-color: var(--gray-300);
  }

  &.ant-tree-node-selected {
    background-color: var(--gray-100);
    border-color: var(--main-300);
  }
}
</style>

<style lang="less">
.agent-file-preview-modal {
  .ant-modal {
    z-index: 1050;
    .ant-modal-content {
      border-radius: 8px;
      padding: 0;
      overflow: hidden;
      border: 1px solid var(--gray-200);
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
    }

    :deep(.ant-modal-header) {
      background: var(--main-5);
      border-bottom: 1px solid var(--gray-200);
      padding: 16px 20px;
    }

    :deep(.ant-modal-title) {
      font-weight: 600;
      color: var(--gray-1000);
      font-family: 'JetBrains Mono', 'Fira Code', 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
    }

    :deep(.ant-modal-body) {
      padding: 0;
    }
  }
}
</style>
