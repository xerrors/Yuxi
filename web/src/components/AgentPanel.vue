<template>
  <div class="agent-panel" :class="{ resizing: isResizing, maximized }">
    <div v-if="!maximized" class="resize-handle" @pointerdown="startResize"></div>
    <div class="panel-header side-panel__header">
      <div ref="sectionTabsRef" class="section-tabs" role="tablist" aria-label="侧边栏内容">
        <div
          v-for="section in normalizedSections"
          :key="section.key"
          class="section-tab"
          :class="{ active: section.key === activeSectionKey }"
        >
          <button
            type="button"
            class="section-tab-main"
            role="tab"
            :aria-selected="section.key === activeSectionKey"
            :title="section.title"
            @click="emit('activate-section', section.key)"
          >
            <Folders v-if="section.type === 'file-tree'" :size="15" />
            <FileTypeIcon v-else-if="section.type === 'file'" :name="section.path" :size="15" />
            <Bug v-else-if="section.type === 'message-debug'" :size="15" />
            <FallbackAvatar
              v-else
              :src="section.avatar"
              :default-src="section.defaultAvatar"
              :name="section.title"
              :seed="section.threadId"
              kind="agent"
              :size="18"
              shape="rounded"
            />
            <span>{{ section.title }}</span>
          </button>
          <button
            v-if="section.type !== 'file-tree'"
            type="button"
            class="section-tab-close"
            :aria-label="`关闭 ${section.title}`"
            @click.stop="emit('close-section', section.key)"
          >
            <X :size="13" />
          </button>
        </div>
      </div>
      <div class="window-actions">
        <button
          class="header-action-btn"
          :title="maximized ? '还原面板' : '最大化面板'"
          :aria-label="maximized ? '还原面板' : '最大化面板'"
          :disabled="isResizing"
          @click="emit('toggle-maximize')"
        >
          <Minimize2 v-if="maximized" :size="15" />
          <Maximize2 v-else :size="15" />
        </button>
        <button
          class="header-action-btn"
          title="隐藏侧边栏"
          aria-label="隐藏侧边栏"
          @click="emitClose"
        >
          <PanelRight :size="15" />
        </button>
      </div>
    </div>

    <div class="tab-content">
      <div v-show="activeSectionKey === 'file-tree'" class="tree-pane">
        <div class="tree-toolbar">
          <div class="tree-scope-tabs" role="tablist" aria-label="文件树目录">
            <template v-for="(scope, index) in treeScopes" :key="scope.key">
              <span v-if="index > 0" class="tree-scope-separator" aria-hidden="true"></span>
              <button
                type="button"
                class="tree-scope-tab"
                :class="{ active: activeTreeScope === scope.key }"
                role="tab"
                :aria-selected="activeTreeScope === scope.key"
                @click="setTreeScope(scope.key)"
              >
                {{ scope.label }}
              </button>
            </template>
          </div>
          <div class="tree-toolbar-actions">
            <button
              class="header-action-btn"
              :title="activeTreeScope === 'workspace' ? '搜索个人空间' : '搜索对话目录'"
              :aria-label="activeTreeScope === 'workspace' ? '搜索个人空间' : '搜索对话目录'"
              :disabled="activeTreeScope === 'thread' && !threadId"
              @click="fileSearchOpen = true"
            >
              <Search :size="15" />
            </button>
            <button
              class="header-action-btn"
              :title="activeTreeScope === 'workspace' ? '刷新个人空间' : '刷新文件'"
              :aria-label="activeTreeScope === 'workspace' ? '刷新个人空间' : '刷新文件'"
              @click="emitRefresh"
            >
              <RefreshCw :size="15" />
            </button>
          </div>
        </div>
        <div v-show="activeTreeScope === 'thread'" class="tree-scope-pane">
          <div v-if="!threadId" class="empty">创建对话后可查看对话目录</div>
          <div v-else-if="loadingFiles" class="empty">正在加载文件系统...</div>
          <div v-else-if="filesystemError" class="empty error-state">
            <div>{{ filesystemError }}</div>
            <a-button type="link" size="small" @click="refreshFileSystem">重试</a-button>
          </div>
          <div v-else-if="!fileTreeData.length" class="empty">当前对话目录为空</div>
          <div v-else class="file-tree-container">
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
                    aria-label="下载文件"
                  >
                    <Download :size="14" />
                  </button>
                  <button
                    class="tree-action-btn tree-delete-btn"
                    :disabled="deletingPaths.has(node.key)"
                    @click.stop="confirmDeleteNode(node)"
                    :title="node.isLeaf ? '删除文件' : '删除文件夹'"
                    :aria-label="node.isLeaf ? '删除文件' : '删除文件夹'"
                  >
                    <Trash2 :size="14" />
                  </button>
                </div>
              </template>
            </FileTreeComponent>
          </div>
        </div>
        <div v-show="activeTreeScope === 'workspace'" class="tree-scope-pane">
          <div v-if="workspaceLoading" class="empty">正在加载个人空间...</div>
          <div v-else-if="workspaceError" class="empty error-state">
            <div>{{ workspaceError }}</div>
            <a-button type="link" size="small" @click="refreshWorkspaceTree({ force: true })">
              重试
            </a-button>
          </div>
          <div v-else-if="!workspaceTreeData.length" class="empty">个人空间为空</div>
          <div v-else class="file-tree-container">
            <FileTreeComponent
              v-model:selectedKeys="workspaceSelectedKeys"
              v-model:expandedKeys="workspaceExpandedKeys"
              :tree-data="workspaceTreeData"
              :load-data="loadWorkspaceData"
              @select="onWorkspaceFileSelect"
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
                    aria-label="下载文件"
                  >
                    <Download :size="14" />
                  </button>
                </div>
              </template>
            </FileTreeComponent>
          </div>
        </div>
      </div>
      <div v-show="activeSection?.type === 'file'" class="preview-pane">
        <AgentFilePreview
          v-if="currentFile"
          containerClass="side-preview-shell"
          contentClass="side-file-content"
          :file="currentFile"
          :filePath="currentFilePath"
          :fullHeight="true"
          :showFileIcon="false"
          :borderless="true"
          :showClose="false"
          :showDownload="true"
          :showFullscreen="true"
          @download="downloadFile"
        />
        <div v-else class="preview-empty">正在加载文件内容...</div>
      </div>
      <div
        v-for="section in subagentSections"
        v-show="activeSectionKey === section.key"
        :key="section.key"
        class="subagent-section"
      >
        <SubagentThreadView
          :thread-id="section.threadId"
          :active="activeSectionKey === section.key"
        />
      </div>
      <div
        v-if="hasMessageDebugSection"
        v-show="activeSectionKey === 'message-debug'"
        class="message-debug-section"
      >
        <MessageDebugPanel
          :messages="messages"
          :runs="runs"
          :thread-id="threadId"
          :active="visible && activeSectionKey === 'message-debug'"
          :active-run-id="activeRunId"
          :run-active="runActive"
        />
      </div>
    </div>

    <GlobalSearchModal
      v-model:open="fileSearchOpen"
      :modes="['file']"
      default-mode="file"
      :file-search="searchActiveTreeFiles"
      :file-placeholder="fileSearchPlaceholder"
      @select-file="handleSearchSelect"
    />
  </div>
</template>

<script setup>
import {
  computed,
  nextTick,
  onActivated,
  onDeactivated,
  onMounted,
  onUnmounted,
  ref,
  watch
} from 'vue'
import {
  Bug,
  Download,
  Folders,
  Maximize2,
  Minimize2,
  PanelRight,
  RefreshCw,
  Search,
  Trash2,
  X
} from '@lucide/vue'
import { Modal, message } from 'ant-design-vue'
import FileTreeComponent from '@/components/FileTreeComponent.vue'
import AgentFilePreview from '@/components/AgentFilePreview.vue'
import GlobalSearchModal from '@/components/GlobalSearchModal.vue'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import SubagentThreadView from '@/components/SubagentThreadView.vue'
import MessageDebugPanel from '@/components/MessageDebugPanel.vue'
import {
  createFilesystemRefreshGate,
  expandedKeysAfterFilesystemRefresh,
  reloadPreviewAfterOrderedCacheEntryInvalidation,
  replacePreviewCacheEntryIfCurrent,
  refreshExpandedTree,
  settlePreviewCacheLoad,
  shouldRefreshActivePreview,
  startAgentPanelFilesystemPolling
} from '@/utils/agentPanelFilesystemPolling'
import {
  deleteViewerFile,
  downloadViewerFile,
  getViewerFileContent,
  getViewerFileSystemTree,
  searchViewerFiles
} from '@/apis/viewer_filesystem'
import {
  downloadWorkspaceFile,
  getWorkspaceFileContent,
  getWorkspaceTree,
  searchWorkspaceFiles
} from '@/apis/workspace_api'
import { normalizePreviewResponse } from '@/utils/file_preview'
import { parseDownloadFilename } from '@/utils/file_utils'
import { threadApi } from '@/apis/agent_api'

const props = defineProps({
  runs: { type: Array, default: () => [] },
  agentState: {
    type: Object,
    default: () => ({})
  },
  threadId: {
    type: String,
    default: null
  },
  activeRunId: {
    type: String,
    default: null
  },
  runActive: {
    type: Boolean,
    default: false
  },
  visible: {
    type: Boolean,
    default: false
  },
  panelRatio: {
    type: Number,
    default: 0.35
  },
  previewTabs: {
    type: Array,
    default: () => []
  },
  previewCache: {
    type: Object,
    default: () => new Map()
  },
  activePreviewPath: {
    type: String,
    default: ''
  },
  viewMode: {
    type: String,
    default: 'tree',
    validator: (value) => ['tree', 'preview'].includes(value)
  },
  maximized: { type: Boolean, default: false },
  sections: {
    type: Array,
    default: () => [{ key: 'file-tree', type: 'file-tree', title: '文件' }]
  },
  activeSectionKey: { type: String, default: 'file-tree' },
  filesystemVisible: { type: Boolean, default: false },
  filesystemPollingActive: { type: Boolean, default: false },
  filesystemRefreshVersion: { type: Number, default: 0 },
  messages: { type: Array, default: () => [] }
})

const emit = defineEmits([
  'close',
  'refresh',
  'resize',
  'resizing',
  'open-preview',
  'activate-preview',
  'close-preview-tab',
  'close-preview-path',
  'view-mode-change',
  'toggle-maximize',
  'activate-section',
  'close-section'
])
const currentFile = ref(null)
const currentFilePath = ref('')
const loadingFiles = ref(false)
const filesystemError = ref('')

const dynamicTreeData = ref([])
const selectedKeys = ref([])
const expandedKeys = ref([])
const deletingPaths = ref(new Set())

// 个人空间（个人 workspace）树：与对话目录相互独立、切换保活。
const treeScopes = [
  { key: 'thread', label: '对话目录' },
  { key: 'workspace', label: '个人空间' }
]
const activeTreeScope = ref('thread')
const workspaceTreeData = ref([])
const workspaceSelectedKeys = ref([])
const workspaceExpandedKeys = ref([])
const workspaceLoading = ref(false)
const workspaceError = ref('')
const workspaceLoaded = ref(false)
const isResizing = ref(false)
const fileSearchOpen = ref(false)
const sectionTabsRef = ref(null)
const normalizedSections = computed(() =>
  (props.sections || []).filter((section) => section?.key && section?.type)
)
const subagentSections = computed(() =>
  normalizedSections.value.filter((section) => section.type === 'subagent' && section.threadId)
)
const hasMessageDebugSection = computed(() =>
  normalizedSections.value.some(
    (section) => section.type === 'message-debug' || section.key === 'message-debug'
  )
)
const activeSection = computed(
  () => normalizedSections.value.find((section) => section.key === props.activeSectionKey) || null
)

const ensureActiveSectionVisible = async () => {
  await nextTick()
  const activeTab = sectionTabsRef.value?.querySelector('[role="tab"][aria-selected="true"]')
  activeTab?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
}
const filesystemRefreshGate = createFilesystemRefreshGate()
let filesystemRefreshGeneration = 0

// uploads/outputs 只是 Project Workdir 下的目录约定；预取用于决定空目录是否展示。
const PREFETCH_DIRECTORY_NAMES = ['outputs', 'uploads']
const HIDE_WHEN_EMPTY_NAMES = ['outputs', 'uploads']

const fileSearchPlaceholder = computed(() =>
  activeTreeScope.value === 'workspace' ? '搜索个人空间的文件...' : '搜索当前对话的文件...'
)

const searchActiveTreeFiles = (query) =>
  activeTreeScope.value === 'workspace'
    ? searchWorkspaceFiles(query)
    : searchViewerFiles(props.threadId, query)

const handleSearchSelect = (entry) => {
  if (!entry?.path) return
  if (activeTreeScope.value === 'workspace') {
    workspaceSelectedKeys.value = [entry.path]
    emit('open-preview', { ...entry, type: 'file', workdir: false, workspace: true }, false)
    return
  }
  if (!props.threadId) return
  selectedKeys.value = [entry.path]
  emit('open-preview', { ...entry, type: 'file', workdir: true }, false)
}

const normalizedPreviewTabs = computed(() =>
  (props.previewTabs || [])
    .filter((file) => file?.path)
    .map((file) => ({
      ...file,
      path: String(file.path),
      name: file.name || getFileName(file)
    }))
)
const activePreviewTab = computed(
  () => normalizedPreviewTabs.value.find((file) => file.path === props.activePreviewPath) || null
)
const fileTreeData = computed(() => dynamicTreeData.value)

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

const createTreeNode = (entry, extraFileData = {}) => {
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
      type: isLeaf ? 'file' : 'directory',
      workdir: isLeaf,
      ...extraFileData
    },
    class: isLeaf ? 'file-node' : 'folder-node'
  }
}

// 用户目录树节点：预览与下载走 workspace 身份，而非当前对话。
const createWorkspaceTreeNode = (entry) =>
  createTreeNode(entry, { workdir: false, workspace: true })

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

const getFileName = (fileItem) => {
  if (fileItem?.name) return fileItem.name
  if (fileItem?.path) {
    return String(fileItem.path).split('/').pop() || String(fileItem.path)
  }
  return '未知文件'
}

const loadDirectoryChildren = async (directoryPath, threadId = props.threadId) => {
  const res = await getViewerFileSystemTree(threadId, directoryPath)
  return sortEntries(res?.entries || []).map((entry) => createTreeNode(entry))
}

const refreshFileSystem = async ({ silent = false, ensure = false } = {}) => {
  const requestedThreadId = props.threadId
  const requestedGeneration = filesystemRefreshGeneration
  if (!requestedThreadId) {
    dynamicTreeData.value = []
    filesystemError.value = ''
    return
  }
  if (!filesystemRefreshGate.begin(requestedThreadId, { ensure })) return

  if (!silent) loadingFiles.value = true
  filesystemError.value = ''

  try {
    const res = await getViewerFileSystemTree(requestedThreadId, '/')
    if (res?.entries) {
      let nodes = sortEntries(res.entries).map((entry) => createTreeNode(entry))

      // 预取关键目录子项：空的 outputs/uploads 不展示，workspace 默认展开
      const prefetched = await Promise.all(
        nodes.map(async (node) => {
          if (!PREFETCH_DIRECTORY_NAMES.includes(node.title)) return { node, children: null }
          let children = null
          try {
            children = await loadDirectoryChildren(node.key, requestedThreadId)
          } catch (error) {
            console.error('Failed to prefetch children for', node.key, error)
          }
          return { node, children }
        })
      )
      const prefetchedKeys = prefetched
        .filter(({ children }) => children !== null)
        .map(({ node }) => node.key)

      nodes = prefetched.reduce((visible, { node, children }) => {
        if (children === null) {
          visible.push(node)
        } else if (children.length || !HIDE_WHEN_EMPTY_NAMES.includes(node.title)) {
          visible.push({ ...node, children })
        }
        return visible
      }, [])

      if (silent && expandedKeys.value.length) {
        nodes = await refreshExpandedTree(
          nodes,
          expandedKeys.value,
          (directoryPath) => loadDirectoryChildren(directoryPath, requestedThreadId),
          prefetchedKeys
        )
      }

      if (
        requestedGeneration !== filesystemRefreshGeneration ||
        !filesystemRefreshGate.canCommit(requestedThreadId, props.threadId)
      ) {
        return
      }
      dynamicTreeData.value = nodes
      expandedKeys.value = expandedKeysAfterFilesystemRefresh(expandedKeys.value, { silent })
      selectedKeys.value = props.activePreviewPath ? [props.activePreviewPath] : []
      if (silent) await refreshActivePreviewIfChanged(nodes, requestedThreadId)
    } else if (
      requestedGeneration === filesystemRefreshGeneration &&
      filesystemRefreshGate.canCommit(requestedThreadId, props.threadId)
    ) {
      dynamicTreeData.value = []
    }
  } catch (error) {
    if (
      requestedGeneration !== filesystemRefreshGeneration ||
      !filesystemRefreshGate.canCommit(requestedThreadId, props.threadId)
    ) {
      return
    }
    dynamicTreeData.value = []
    filesystemError.value = error?.message || '加载文件系统失败'
    console.error('Failed to load root files', error)
  } finally {
    const refreshAgain = filesystemRefreshGate.finish(requestedThreadId)
    if (
      !silent &&
      requestedGeneration === filesystemRefreshGeneration &&
      filesystemRefreshGate.canCommit(requestedThreadId, props.threadId)
    ) {
      loadingFiles.value = false
    }
    if (refreshAgain) {
      void refreshFileSystem({ silent: true })
    }
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

const loadWorkspaceChildren = async (directoryPath) => {
  const res = await getWorkspaceTree(directoryPath)
  return sortEntries(res?.entries || []).map((entry) => createWorkspaceTreeNode(entry))
}

let workspaceRefreshInFlight = false
let workspaceRefreshPending = false
let workspacePreviewRefreshPending = false
let workspaceTreeGeneration = 0

const refreshWorkspaceTree = async ({ force = false, refreshPreview = false } = {}) => {
  if (workspaceLoaded.value && !force && !refreshPreview) return
  if (workspaceRefreshInFlight) {
    if (force || refreshPreview) workspaceRefreshPending = true
    if (refreshPreview) workspacePreviewRefreshPending = true
    return
  }
  workspaceRefreshInFlight = true
  workspaceLoading.value = true
  workspaceError.value = ''
  workspacePreviewRefreshPending ||= refreshPreview

  try {
    do {
      workspaceRefreshPending = false
      const shouldRefreshPreview = workspacePreviewRefreshPending
      workspacePreviewRefreshPending = false

      workspaceTreeGeneration += 1
      try {
        let nodes = await loadWorkspaceChildren('/')
        if (workspaceExpandedKeys.value.length) {
          nodes = await refreshExpandedTree(
            nodes,
            workspaceExpandedKeys.value,
            loadWorkspaceChildren
          )
        }
        workspaceTreeData.value = nodes
        workspaceLoaded.value = true

        if (shouldRefreshPreview && props.activePreviewPath && activePreviewTab.value?.workspace) {
          const cacheKey = workspacePreviewCacheKey(props.activePreviewPath)
          const entry = props.previewCache.get(cacheKey)
          if (entry?.file?.previewUrl) window.URL.revokeObjectURL(entry.file.previewUrl)
          props.previewCache.delete(cacheKey)
          await loadActivePreview()
        }
      } catch (error) {
        workspaceError.value = error?.message || '加载个人空间失败'
        console.error('Failed to load workspace tree', error)
      }
    } while (workspaceRefreshPending)
  } finally {
    workspaceRefreshInFlight = false
    workspaceLoading.value = false
  }
}

const loadWorkspaceData = async (treeNode) => {
  if (treeNode.isLeaf || treeNode.children?.length) return
  const requestedGeneration = workspaceTreeGeneration

  try {
    const children = await loadWorkspaceChildren(treeNode.key)
    if (requestedGeneration !== workspaceTreeGeneration) return
    workspaceTreeData.value = updateTreeChildren(workspaceTreeData.value, treeNode.key, children)
  } catch (error) {
    console.error('Failed to load children for', treeNode.key, error)
  }
}

const onWorkspaceFileSelect = (nextSelectedKeys, { node }) => {
  workspaceSelectedKeys.value = nextSelectedKeys
  if (!node?.isLeaf) return
  emit('open-preview', node.fileData, false)
}

const setTreeScope = (scope) => {
  if (activeTreeScope.value === scope) return
  activeTreeScope.value = scope
  syncFilesystemRefreshPolling()
  if (scope === 'workspace') {
    void refreshWorkspaceTree({ force: workspaceLoaded.value, refreshPreview: true })
  }
}

let stopFilesystemPolling = null
const componentActive = ref(true)
let previewRequestSeq = 0

const stopFilesystemRefreshPolling = () => {
  if (!stopFilesystemPolling) return
  stopFilesystemPolling()
  stopFilesystemPolling = null
}

const syncFilesystemRefreshPolling = () => {
  stopFilesystemRefreshPolling()
  if (
    !componentActive.value ||
    !props.filesystemPollingActive ||
    (activeSection.value?.type === 'file-tree' && activeTreeScope.value !== 'thread')
  ) {
    return
  }
  stopFilesystemPolling = startAgentPanelFilesystemPolling({
    refresh: () => refreshFileSystem({ silent: true })
  })
}

const revokeCurrentPreviewUrl = () => {
  const previewUrl = currentFile.value?.previewUrl
  if (previewUrl) {
    window.URL.revokeObjectURL(previewUrl)
  }
}

const previewCacheKey = (filePath, threadId = props.threadId) => `${threadId}:${filePath}`
const workspacePreviewCacheKey = (filePath) => `workspace:${filePath}`

const prunePreviewCache = (activeKey) => {
  const readyEntries = [...props.previewCache.entries()]
    .filter(([, entry]) => entry.status === 'ready')
    .sort(([, left], [, right]) => (left.lastAccessed || 0) - (right.lastAccessed || 0))

  while (readyEntries.length > 12) {
    const [key, entry] = readyEntries.shift()
    if (key === activeKey) continue
    if (entry.file?.previewUrl) window.URL.revokeObjectURL(entry.file.previewUrl)
    props.previewCache.delete(key)
  }
}

const loadActivePreview = async ({ baseFileOverride = null } = {}) => {
  const requestedThreadId = props.threadId
  const filePath = props.activePreviewPath
  const requestSeq = ++previewRequestSeq
  const requestIsCurrent = () =>
    requestSeq === previewRequestSeq &&
    requestedThreadId === props.threadId &&
    filePath === props.activePreviewPath

  const isWorkspaceFile = Boolean(activePreviewTab.value?.workspace || baseFileOverride?.workspace)

  // 用户目录文件不依赖当前对话；对话文件必须先有线程。
  if (!filePath || (!requestedThreadId && !isWorkspaceFile)) {
    revokeCurrentPreviewUrl()
    currentFile.value = null
    currentFilePath.value = ''
    return
  }

  const baseFile = {
    ...(activePreviewTab.value || {}),
    ...(baseFileOverride || {}),
    path: filePath,
    name: activePreviewTab.value?.name || getFileName({ path: filePath }),
    type: 'file'
  }

  currentFilePath.value = filePath
  currentFile.value = {
    ...baseFile,
    content: '',
    supported: true,
    previewType: 'text',
    message: '',
    previewUrl: '',
    status: 'loading',
    loading: true
  }

  const cacheKey = isWorkspaceFile
    ? workspacePreviewCacheKey(filePath)
    : previewCacheKey(filePath, requestedThreadId)
  const cachedEntry = props.previewCache.get(cacheKey)
  if (cachedEntry?.status === 'ready') {
    cachedEntry.lastAccessed = Date.now()
    if (requestIsCurrent()) currentFile.value = { ...cachedEntry.file, loading: false }
    return
  }

  if (cachedEntry?.status === 'loading') {
    try {
      const cachedFile = await cachedEntry.promise
      const currentEntry = props.previewCache.get(cacheKey)
      const cacheStillOwnsFile =
        currentEntry === cachedEntry ||
        (currentEntry?.status === 'ready' && currentEntry.file === cachedFile)
      if (requestIsCurrent() && cacheStillOwnsFile) {
        currentFile.value = { ...cachedFile, loading: false }
      }
    } catch {
      const removed = replacePreviewCacheEntryIfCurrent(
        props.previewCache,
        cacheKey,
        cachedEntry,
        null
      )
      if (requestIsCurrent() && (removed || !props.previewCache.has(cacheKey))) {
        currentFile.value = {
          ...baseFile,
          content: '',
          status: 'error',
          errorMessage: '文件预览失败',
          supported: false,
          previewType: 'unsupported',
          message: '文件预览失败',
          previewUrl: '',
          loading: false,
          error: true
        }
      }
    }
    return
  }

  const loadPromise = (async () => {
    // 路径统一为 runtime 身份：用户目录走 workspace，当前 Workdir 走 Viewer，其余走 artifact 预览。
    const res = isWorkspaceFile
      ? await getWorkspaceFileContent(filePath)
      : baseFile.workdir
        ? await getViewerFileContent(requestedThreadId, filePath)
        : await threadApi.previewThreadArtifact(requestedThreadId, filePath)
    return normalizePreviewResponse(res, baseFile)
  })()
  const loadingEntry = { status: 'loading', promise: loadPromise }
  props.previewCache.set(cacheKey, loadingEntry)

  try {
    const nextFile = await loadPromise
    const published = settlePreviewCacheLoad({
      previewCache: props.previewCache,
      cacheKey,
      loadingEntry,
      nextFile,
      lastAccessed: Date.now(),
      revokeObjectURL: window.URL.revokeObjectURL.bind(window.URL)
    })
    if (!published) return
    prunePreviewCache(cacheKey)
    if (requestIsCurrent()) {
      currentFile.value = { ...nextFile, loading: false }
    }
  } catch (error) {
    const removed = replacePreviewCacheEntryIfCurrent(
      props.previewCache,
      cacheKey,
      loadingEntry,
      null
    )
    if (!requestIsCurrent() || !removed) return

    const is403 =
      Number(error?.status || error?.statusCode || 0) === 403 ||
      String(error?.message || '').includes('403')
    const errorMessage = is403
      ? '暂无权限访问该文件（403），请确认文件是否属于当前对话或个人空间。'
      : error?.message || '文件加载失败，请重试'

    currentFile.value = {
      ...baseFile,
      content: '',
      status: 'error',
      errorMessage,
      supported: false,
      previewType: 'unsupported',
      message: errorMessage,
      previewUrl: '',
      loading: false,
      error: true
    }
  }
}

const findTreeNode = (nodes, filePath) => {
  for (const node of nodes) {
    if (node.key === filePath) return node
    if (node.children?.length) {
      const nested = findTreeNode(node.children, filePath)
      if (nested) return nested
    }
  }
  return null
}

const refreshActivePreviewIfChanged = async (nodes, requestedThreadId) => {
  const tab = activePreviewTab.value
  if (!tab || tab.workdir !== true || requestedThreadId !== props.threadId) return
  let latestFile = findTreeNode(nodes, tab.path)?.fileData || null
  if (!latestFile) {
    const parentPath = tab.path.split('/').slice(0, -1).join('/') || '/'
    try {
      latestFile = (await loadDirectoryChildren(parentPath, requestedThreadId)).find(
        (node) => node.key === tab.path
      )?.fileData
    } catch {
      return
    }
  }
  if (!shouldRefreshActivePreview(tab, latestFile)) return
  if (
    requestedThreadId !== props.threadId ||
    tab.path !== props.activePreviewPath ||
    tab !== activePreviewTab.value
  ) {
    return
  }
  const nextTab = { ...tab, ...(latestFile || {}) }
  const cacheKey = previewCacheKey(tab.path, requestedThreadId)
  await reloadPreviewAfterOrderedCacheEntryInvalidation({
    previewCache: props.previewCache,
    cacheKey,
    revokeObjectURL: window.URL.revokeObjectURL.bind(window.URL),
    notifyPreviewChanged: () => emit('open-preview', nextTab, props.viewMode === 'tree'),
    reloadPreview: () => loadActivePreview({ baseFileOverride: nextTab })
  })
}

const onFileSelect = (nextSelectedKeys, { node }) => {
  selectedKeys.value = nextSelectedKeys
  if (!node?.isLeaf || !props.threadId) return
  emit('open-preview', node.fileData, false)
}

const pruneTreeStateAfterDelete = (targetPath) => {
  selectedKeys.value = selectedKeys.value.filter((key) => !isSameOrChildPath(key, targetPath))
  expandedKeys.value = expandedKeys.value.filter((key) => !isSameOrChildPath(key, targetPath))
  emit('close-preview-path', targetPath)
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
        await deleteViewerFile(props.threadId, node.key)
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
  if (!fileItem?.path) return
  if (!fileItem.workspace && !props.threadId) return

  try {
    const response = fileItem.workspace
      ? await downloadWorkspaceFile(fileItem.path)
      : fileItem.workdir
        ? await downloadViewerFile(props.threadId, fileItem.path)
        : await threadApi.downloadThreadArtifact(props.threadId, fileItem.path)
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

const releaseWorkspacePreviewCache = () => {
  for (const [key, entry] of props.previewCache) {
    if (!key.startsWith('workspace:')) continue
    if (entry.file?.previewUrl) window.URL.revokeObjectURL(entry.file.previewUrl)
    props.previewCache.delete(key)
  }
}

const emitRefresh = async () => {
  if (activeTreeScope.value === 'workspace') {
    releaseWorkspacePreviewCache()
    await refreshWorkspaceTree({ force: true, refreshPreview: true })
    return
  }
  for (const [key, entry] of props.previewCache) {
    if (key.startsWith(`${props.threadId}:`) && entry.file?.previewUrl) {
      window.URL.revokeObjectURL(entry.file.previewUrl)
    }
    if (key.startsWith(`${props.threadId}:`)) props.previewCache.delete(key)
  }
  await refreshFileSystem()
  if (props.activePreviewPath) await loadActivePreview()
  emit('refresh', props.threadId)
}

const emitClose = () => {
  emit('close')
}

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
  if (e.button !== 0 || props.maximized) return

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
  if (props.filesystemVisible) void refreshFileSystem({ silent: true })
  syncFilesystemRefreshPolling()
})

onActivated(() => {
  const wasInactive = !componentActive.value
  componentActive.value = true
  if (wasInactive && activeTreeScope.value === 'workspace') {
    void refreshWorkspaceTree({ force: true, refreshPreview: true })
  } else if (wasInactive && props.filesystemVisible) {
    void refreshFileSystem({ silent: true })
  }
  syncFilesystemRefreshPolling()
})

onDeactivated(() => {
  componentActive.value = false
  stopFilesystemRefreshPolling()
})

onUnmounted(() => {
  stopFilesystemRefreshPolling()
  if (resizeFrameId) {
    window.cancelAnimationFrame(resizeFrameId)
    resizeFrameId = 0
  }
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', stopResize)
  window.removeEventListener('pointercancel', stopResize)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
})

watch(
  () => props.threadId,
  (threadId) => {
    filesystemRefreshGeneration += 1
    previewRequestSeq += 1
    for (const [key, entry] of props.previewCache) {
      if (!key.startsWith(`${threadId}:`)) continue
      if (entry.file?.previewUrl) window.URL.revokeObjectURL(entry.file.previewUrl)
      props.previewCache.delete(key)
    }
    loadingFiles.value = false
    dynamicTreeData.value = []
    expandedKeys.value = []
    selectedKeys.value = []
    filesystemError.value = ''
    if (threadId && props.filesystemVisible) {
      refreshFileSystem({ ensure: true })
    }
  }
)

watch(
  [
    () => props.threadId,
    () => props.activePreviewPath,
    () => activePreviewTab.value?.workdir,
    () => activePreviewTab.value?.workspace
  ],
  loadActivePreview,
  { immediate: true }
)

watch(
  () => props.filesystemVisible,
  (visible) => {
    if (!visible || !componentActive.value) return
    if (activeTreeScope.value === 'workspace') {
      void refreshWorkspaceTree({ force: true, refreshPreview: true })
    } else void refreshFileSystem({ silent: true })
  }
)

watch([() => props.filesystemPollingActive, componentActive], syncFilesystemRefreshPolling)

watch(
  () => props.filesystemRefreshVersion,
  () => {
    if (!props.filesystemVisible || !componentActive.value) return
    if (activeTreeScope.value === 'workspace') {
      void refreshWorkspaceTree({ force: true, refreshPreview: true })
    } else {
      void refreshFileSystem({ silent: true, ensure: true })
    }
  }
)

watch(
  () => props.activePreviewPath,
  (filePath) => {
    selectedKeys.value = filePath ? [filePath] : []
    workspaceSelectedKeys.value = filePath ? [filePath] : []
  }
)
watch(
  () => props.activeSectionKey,
  () => {
    void ensureActiveSectionVisible()
    if (activeSection.value?.type === 'file' && activePreviewTab.value?.workspace) {
      const cacheKey = workspacePreviewCacheKey(activePreviewTab.value.path)
      const entry = props.previewCache.get(cacheKey)
      if (entry?.file?.previewUrl) window.URL.revokeObjectURL(entry.file.previewUrl)
      props.previewCache.delete(cacheKey)
      void loadActivePreview()
    }
    if (
      props.activeSectionKey === 'file-tree' &&
      activeTreeScope.value === 'workspace' &&
      componentActive.value
    ) {
      void refreshWorkspaceTree({ force: true, refreshPreview: true })
    }
    syncFilesystemRefreshPolling()
  },
  { immediate: true }
)
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
  width: 100%;
  height: 100%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  background: var(--gray-0);
  transition: none;

  &.resizing {
    transition: none;
  }

  .panel-header {
    border-bottom: 1px solid var(--gray-100);
  }

  :deep(.side-preview-shell) {
    border: none;
  }

  :deep(.preview-header) {
    min-height: 32px;
  }
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 12px;
  min-height: var(--header-height);
  background: var(--gray-25);
  border-bottom: 1px solid var(--gray-100);
  flex-shrink: 0;
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

  &:hover,
  &.active {
    background: var(--gray-100);
    color: var(--gray-900);
  }

  &:focus-visible {
    outline: 2px solid var(--main-300);
    outline-offset: 1px;
  }

  &:disabled {
    color: var(--gray-300);
    cursor: not-allowed;
    background: transparent;
  }
}

.section-tabs {
  display: flex;
  flex: 1;
  min-width: 0;
  align-items: center;
  gap: 1px;
  overflow-x: auto;
  scrollbar-width: none;

  &::-webkit-scrollbar {
    display: none;
  }
}

.section-tab {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  min-width: 0;
  max-width: 140px;
  border-radius: 7px;
  color: var(--gray-600);

  &.active {
    background: var(--gray-100);
    color: var(--gray-900);
  }
}

.section-tab-main {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  min-width: 0;
  gap: 6px;
  padding: 5px 0 5px 7px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;

  &:only-child,
  &:last-child {
    margin-right: 7px;
  }

  > svg,
  > :deep(svg),
  > :deep(.custom-avatar) {
    flex-shrink: 0;
  }

  > span {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    font-size: 12px;
    font-weight: 600;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.section-tab-close {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  margin-right: 3px;
  padding: 0;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;

  &:hover {
    background: var(--gray-150);
    color: var(--gray-900);
  }
}

.window-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.tab-content {
  flex: 1;
  overflow: hidden;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.file-section,
.subagent-section,
.message-debug-section {
  width: 100%;
  height: 100%;
  min-height: 0;
}

.file-section {
  display: flex;
  flex-direction: column;
}

.files-display {
  flex: 1;
  min-height: 0;
  display: flex;
}

.preview-pane {
  flex: 1;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  overflow: hidden;
}

.tree-pane {
  flex: 1;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 0 6px 6px;
}

.tree-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: 0 0 36px;
  gap: 8px;
  padding: 4px 4px 4px 8px;
  border-bottom: 1px solid var(--gray-100);
  color: var(--gray-700);
  font-size: 12px;
}

.tree-scope-tabs {
  display: flex;
  align-items: center;
}

.tree-scope-separator {
  width: 1px;
  height: 10px;
  margin: 0 8px;
  background: var(--gray-200);
}

.tree-scope-tab {
  border: 0;
  background: transparent;
  color: var(--gray-500);
  font-size: 12px;
  font-weight: 500;
  line-height: 16px;
  padding: 0;
  cursor: pointer;
  white-space: nowrap;

  &:hover {
    color: var(--gray-900);
  }

  &.active {
    color: var(--main-600);
    font-weight: 700;
  }

  &:focus-visible {
    outline: 2px solid var(--main-300);
    outline-offset: 1px;
  }
}

.tree-scope-pane {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.tree-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.files-display.has-preview.with-tree .tree-pane {
  flex: 0 0 34%;
  min-width: 260px;
  max-width: 380px;
  border-left: 1px solid var(--gray-100);
}

.preview-tabs-bar {
  flex: 0 0 auto;
  width: 100%;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  overflow-x: auto;
  padding: 4px 8px;
  border-bottom: 1px solid var(--gray-100);
  scrollbar-width: none;

  &::-webkit-scrollbar {
    display: none;
  }
}

.preview-tab {
  min-width: 0;
  max-width: 220px;
  display: flex;
  align-items: center;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-25);
  color: var(--gray-700);
  overflow: hidden;
  flex-shrink: 0;

  &.active {
    border-color: var(--gray-200);
    background: var(--gray-0);
    color: var(--gray-1000);
  }
}

.preview-tab-main {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  padding: 5px 6px 5px 8px;
}

.preview-tab-icon {
  flex-shrink: 0;
  font-size: 14px;
}

.preview-tab-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 600;
}

.preview-tab-close {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-right: 3px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;
  padding: 0;
  transition:
    color 160ms ease,
    background-color 160ms ease,
    transform 160ms ease;

  &:hover {
    color: var(--gray-900);
    background: var(--gray-100);
    transform: scale(1.04);
  }

  &:active {
    background: var(--gray-150);
    transform: scale(0.96);
  }

  &:focus-visible {
    outline: 2px solid var(--main-400);
    outline-offset: 1px;
  }
}

.side-preview-shell {
  flex: 1;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  background: var(--gray-0);
  overflow: hidden;
}

.side-preview-shell :deep(.file-content),
.side-preview-shell :deep(.side-file-content) {
  flex: 1;
  height: auto;
  min-height: 0;
  max-height: none;
}

.side-preview-shell :deep(.pdf-preview),
.side-preview-shell :deep(.html-preview) {
  width: 100%;
  height: 100%;
  min-height: 0;
}

.preview-empty,
.empty {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: var(--gray-500);
  padding: 24px;
  font-size: 14px;
}

.preview-empty {
  border: 1px dashed var(--gray-200);
  border-radius: 12px;
  background: linear-gradient(180deg, var(--gray-25) 0%, var(--gray-0) 100%);
}

.preview-empty-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--gray-800);
}

.preview-empty-desc {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
}

.error-state {
  gap: 8px;
}

.file-tree-container {
  flex: 1;
  min-height: 0;
  overflow-y: auto;

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
</style>
