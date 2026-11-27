<template>
  <div class="agent-panel" :class="{ resizing: isResizing, maximized }">
    <div
      v-if="!maximized"
      class="resize-divider"
      role="separator"
      tabindex="0"
      aria-label="调整侧边栏宽度"
      aria-orientation="vertical"
      :aria-valuenow="panelWidthPercentage"
      :aria-controls="tabPanelId"
      @pointerdown="startResize"
      @keydown.left.prevent="emit('resize-by', 24)"
      @keydown.right.prevent="emit('resize-by', -24)"
      @keydown.home.prevent="emit('resize-by', -Infinity)"
      @keydown.end.prevent="emit('resize-by', Infinity)"
    ></div>
    <div class="panel-header side-panel__header">
      <div ref="sectionTabsRef" class="section-tabs" role="tablist" aria-label="侧边栏内容">
        <div
          v-for="(section, index) in normalizedSections"
          :key="section.key"
          class="section-tab"
          :class="{ active: section.key === activeSectionKey }"
        >
          <button
            type="button"
            class="section-tab-main"
            role="tab"
            :id="`${tabPanelId}-tab-${index}`"
            :aria-controls="tabPanelId"
            :aria-selected="section.key === activeSectionKey"
            :tabindex="section.key === activeSectionKey ? 0 : -1"
            :aria-label="
              section.attentionLabel ? `${section.title}，${section.attentionLabel}` : section.title
            "
            :title="
              section.attentionLabel ? `${section.title}，${section.attentionLabel}` : section.title
            "
            @click="activateSection(section)"
            @keydown="handleTabKey($event, section)"
          >
            <FileTypeIcon v-if="section.type === 'file'" :name="section.path" :size="15" />
            <component v-else-if="sectionIcons[section.type]" :is="sectionIcons[section.type]" :size="15" />
            <FallbackAvatar
              v-else
              :src="section.avatar"
              :name="section.title"
              :seed="section.avatarSeed"
              kind="session"
              :size="18"
              shape="rounded"
            />
            <span>{{ section.title }}</span>
            <span v-if="section.attentionLabel" class="section-attention">{{
              section.attentionLabel
            }}</span>
          </button>
          <button
            v-if="canCloseTab(section)"
            type="button"
            class="section-tab-close"
            :tabindex="section.key === activeSectionKey ? 0 : -1"
            :aria-label="`关闭 ${section.title}`"
            @click.stop="closeTab(section)"
          >
            <X :size="13" />
          </button>
        </div>
      </div>
      <button
        v-if="!hasStartSection"
        type="button"
        class="header-action-btn add-section-btn"
        title="新建开始页"
        aria-label="新建开始页"
        @click="openStart"
      >
        <Plus :size="16" />
      </button>
      <div class="window-actions">
        <slot name="window-actions"></slot>
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

    <div class="tab-content" :id="tabPanelId" role="tabpanel" :aria-labelledby="activeTabId">
      <slot name="session-content"></slot>
      <div v-if="activeSection?.type === 'start'" class="panel-start">
        <div class="panel-start-actions">
          <Compass class="panel-start-icon" :size="36" aria-hidden="true" />
          <button
            v-for="section in startSections"
            :key="section.key"
            type="button"
            class="panel-start-action"
            @click="openStartSection(section)"
          >
            <component :is="sectionIcons[section.type]" :size="18" aria-hidden="true" />
            <span>{{ section.title }}</span>
          </button>
        </div>
      </div>
      <section v-show="activeSection?.type === 'file-tree'" class="tree-pane" aria-label="工作区文件">
        <div class="tree-toolbar">
          <span class="tree-title">项目文件夹</span>
          <div class="tree-toolbar-actions">
            <button
              type="button"
              class="header-action-btn"
              title="搜索项目文件夹"
              aria-label="搜索项目文件夹"
              :disabled="!threadId"
              @click="openFileSearch('thread')"
            >
              <Search :size="15" />
            </button>
            <button
              type="button"
              class="header-action-btn"
              title="刷新项目文件夹"
              aria-label="刷新项目文件夹"
              :disabled="!threadId"
              @click="refreshTree('thread')"
            >
              <RefreshCw :size="15" />
            </button>
          </div>
        </div>
        <div class="tree-scope-pane">
          <div v-if="!threadId" class="empty">创建对话后可查看项目文件夹</div>
          <div v-else-if="loadingFiles || (!filesystemLoaded && !filesystemError)" class="empty" role="status">正在加载文件系统...</div>
          <div v-else-if="filesystemError" class="empty error-state">
            <div>{{ filesystemError }}</div>
            <a-button type="link" size="small" @click="refreshFileSystem">重试</a-button>
          </div>
          <div v-else-if="!fileTreeData.length" class="empty">项目文件夹为空</div>
          <div v-else class="file-tree-container">
            <FileTreeComponent
              v-model:selectedKeys="selectedKeys"
              v-model:expandedKeys="expandedKeys"
              :tree-data="fileTreeData"
              :load-data="loadData"
              compact
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
                    <Download :size="13" />
                  </button>
                  <button
                    class="tree-action-btn tree-delete-btn"
                    :disabled="deletingPaths.has(node.key)"
                    @click.stop="confirmDeleteNode(node)"
                    :title="node.isLeaf ? '删除文件' : '删除文件夹'"
                    :aria-label="node.isLeaf ? '删除文件' : '删除文件夹'"
                  >
                    <Trash2 :size="13" />
                  </button>
                </div>
              </template>
            </FileTreeComponent>
          </div>
        </div>
      </section>
      <section v-show="activeSection?.type === 'workspace-tree'" class="tree-pane" aria-label="个人空间文件">
        <div class="tree-toolbar">
          <span class="tree-title">个人空间</span>
          <div class="tree-toolbar-actions">
            <button
              type="button"
              class="header-action-btn"
              title="搜索个人空间"
              aria-label="搜索个人空间"
              @click="openFileSearch('workspace')"
            >
              <Search :size="15" />
            </button>
            <button
              type="button"
              class="header-action-btn"
              title="刷新个人空间"
              aria-label="刷新个人空间"
              @click="refreshTree('workspace')"
            >
              <RefreshCw :size="15" />
            </button>
          </div>
        </div>
        <div class="tree-scope-pane">
          <div v-if="workspaceLoading && !workspaceTreeData.length" class="empty">正在加载个人空间...</div>
          <div v-else-if="workspaceError" class="empty error-state">
            <div>{{ workspaceError }}</div>
            <a-button type="link" size="small" @click="refreshWorkspaceTree()">
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
              compact
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
                    <Download :size="13" />
                  </button>
                </div>
              </template>
            </FileTreeComponent>
          </div>
        </div>
      </section>
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
          @download="downloadFile(activePreviewTab)"
        />
        <div v-else class="preview-empty">正在加载文件内容...</div>
      </div>
      <div
        v-if="hasMessageDebugSection"
        v-show="activeSectionKey === 'message-debug'"
        class="message-debug-section"
      >
        <MessageDebugPanel
          :thread-id="threadId"
          :active="visible && activeSectionKey === 'message-debug'"
          :active-run-id="activeRunId"
          :run-active="runActive"
        />
      </div>
      <div
        v-if="turnSourcesSection"
        class="turn-sources-section"
      >
        <TurnSourcesPanel
          :thread-id="turnSourcesSection.threadId || threadId"
          :turn-id="turnSourcesSection.turnId || ''"
          :sources="turnSourcesSection.sources"
          :active="visible"
        />
      </div>
    </div>

    <GlobalSearchModal
      v-model:open="fileSearchOpen"
      :modes="['file']"
      default-mode="file"
      :file-search="searchActiveTreeFiles"
      :file-placeholder="fileSearchPlaceholder"
      :file-scope-hint="fileSearchScope === 'workspace' ? '仅搜索个人空间，不包含知识库文件。' : '仅搜索当前对话或关联项目的工作目录。'"
      @select-file="handleSearchSelect"
    />
  </div>
</template>

<script setup>
import {
  filePreviewCacheKey, releaseFilePreviewCacheEntry, releaseFilePreviewCache, pruneFilePreviewCache,
  reloadPreviewAfterOrderedCacheEntryInvalidation, replacePreviewCacheEntryIfCurrent, settlePreviewCacheLoad
} from '@/modules/workspace'
import {
  computed,
  getCurrentInstance,
  nextTick,
  onActivated,
  onDeactivated,
  onMounted,
  onUnmounted,
  ref,
  toRaw,
  watch
} from 'vue'
import {
  BookOpen,
  Bug,
  Compass,
  Download,
  Folder,
  Folders,
  Maximize2,
  Minimize2,
  PanelRight,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Workflow,
  X
} from '@lucide/vue'
import { Modal, message } from 'ant-design-vue'
import FileTreeComponent from '@/modules/session/ui/workspace/FileTreeComponent.vue'
import { createAsyncPanel } from '@/shared/lib/asyncPanel'
import GlobalSearchModal from '@/modules/session/ui/GlobalSearchModal.vue'
import FileTypeIcon from '@/shared/ui/FileTypeIcon.vue'
import FallbackAvatar from '@/shared/ui/FallbackAvatar.vue'
import {
  createFilesystemRefreshGate,
  expandedKeysAfterFilesystemRefresh,
  refreshExpandedTree,
  shouldRefreshActivePreview,
  startAgentPanelFilesystemPolling
} from '@/modules/session/model/agentPanelFilesystemPolling'
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
  openWorkspaceFile,
  searchWorkspaceFiles
} from '@/apis/workspace_api'
import { normalizePreviewResponse } from '@/shared/lib/file_preview'
import { parseDownloadFilename } from '@/shared/lib/file_utils'
import { threadApi } from '@/apis/agent_api'

defineOptions({
  components: {
    AgentFilePreview: createAsyncPanel(() => import('./AgentFilePreview.vue')),
    MessageDebugPanel: createAsyncPanel(() => import('../MessageDebugPanel.vue')),
    TurnSourcesPanel: createAsyncPanel(() => import('./TurnSourcesPanel.vue'))
  }
})

const props = defineProps({
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
  panelWidthPercentage: {
    type: Number,
    default: 50
  },
  activePreview: {
    type: Object,
    default: null
  },
  previewCache: {
    type: Object,
    default: () => new Map()
  },
  maximized: { type: Boolean, default: false },
  sections: { type: Array, default: () => [] },
  startSections: { type: Array, default: () => [] },
  activeSectionKey: { type: String, default: '' },
  filesystemVisible: { type: Boolean, default: false },
  filesystemPollingActive: { type: Boolean, default: false },
  filesystemRefreshVersions: { type: Map, default: () => new Map() }
})

const emit = defineEmits([
  'close',
  'refresh',
  'resize',
  'resize-by',
  'resizing',
  'open-preview',
  'close-preview-path',
  'toggle-maximize',
  'activate-section',
  'open-start',
  'open-start-section',
  'close-section'
])
const currentFile = ref(null)
const currentFilePath = ref('')
const loadingFiles = ref(false)
const filesystemLoaded = ref(false)
const filesystemError = ref('')

const dynamicTreeData = ref([])
const selectedKeys = ref([])
const expandedKeys = ref([])
const deletingPaths = ref(new Set())

const workspaceTreeData = ref([])
const workspaceSelectedKeys = ref([])
const workspaceExpandedKeys = ref([])
const workspaceLoading = ref(false)
const workspaceError = ref('')
const isResizing = ref(false)
const fileSearchOpen = ref(false)
const fileSearchScope = ref('thread')
const sectionIcons = {
  start: Compass,
  'file-tree': Folder,
  'workspace-tree': Folders,
  'message-debug': Bug,
  cooperation: Workflow,
  'turn-sources': BookOpen
}
const sectionTabsRef = ref(null)
const tabPanelId = `agent-panel-${getCurrentInstance().uid}`
const normalizedSections = computed(() =>
  (props.sections || []).filter((section) => section?.key && section?.type)
)
const activeTabId = computed(() => {
  const index = normalizedSections.value.findIndex((section) => section.key === props.activeSectionKey)
  return index < 0 ? undefined : `${tabPanelId}-tab-${index}`
})

const hasMessageDebugSection = computed(() =>
  normalizedSections.value.some(
    (section) => section.type === 'message-debug' || section.key === 'message-debug'
  )
)
const activeSection = computed(
  () => normalizedSections.value.find((section) => section.key === props.activeSectionKey) || null
)
const hasStartSection = computed(() => normalizedSections.value.some((section) => section.type === 'start'))
const workspaceTreeVisible = computed(() => props.visible && activeSection.value?.type === 'workspace-tree')
const turnSourcesSection = computed(() => activeSection.value?.type === 'turn-sources' ? activeSection.value : null)

/** 用户切回已有的个人文件标签时也算一次访问；恢复面板由被动读取处理。 */
const activateSection = (section) => {
  const changed = section.key !== props.activeSectionKey
  emit('activate-section', section.key)
  if (changed && section.previewKey === filePreviewCacheKey(section.path, 'workspace')) {
    void loadActivePreview({ workspaceOpen: { path: section.path, name: section.title } })
  }
}

/** 方向键访问侧栏标签，删除键关闭后把焦点交给剩余标签。 */
const handleTabKey = (event, section) => {
  const sections = normalizedSections.value
  const index = sections.indexOf(section)
  const nextIndex = {
    ArrowLeft: (index + sections.length - 1) % sections.length,
    ArrowRight: (index + 1) % sections.length,
    Home: 0,
    End: sections.length - 1
  }[event.key]
  if (event.key === 'Delete' && canCloseTab(section)) {
    event.preventDefault()
    void closeTab(section)
  } else if (nextIndex !== undefined) {
    event.preventDefault()
    activateSection(sections[nextIndex])
    sectionTabsRef.value?.querySelectorAll('[role="tab"]')[nextIndex]?.focus()
  }
}

const canCloseTab = (section) => section.type !== 'main-session' && (
  section.type !== 'start' ||
  normalizedSections.value.some((item) => item.type !== 'start' && item.type !== 'main-session')
)

/** 入口和关闭操作更新标签后，将焦点交给当前标签。 */
const focusActiveTab = async () => {
  await nextTick()
  sectionTabsRef.value?.querySelector('[role="tab"][aria-selected="true"]')?.focus()
}
const closeTab = (section) => {
  emit('close-section', section.key)
  void focusActiveTab()
}
const openStart = () => {
  emit('open-start')
  void focusActiveTab()
}
const openStartSection = (section) => {
  emit('open-start-section', section)
  void focusActiveTab()
}

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
  fileSearchScope.value === 'workspace' ? '搜索个人空间的文件...' : '搜索项目文件夹的文件...'
)

const openFileSearch = (scope) => {
  fileSearchScope.value = scope
  fileSearchOpen.value = true
}

const searchActiveTreeFiles = (query) =>
  fileSearchScope.value === 'workspace'
    ? searchWorkspaceFiles(query)
    : searchViewerFiles(props.threadId, query)

/** 文件树与搜索的显式打开共享同一次预览请求。 */
const openPersonalFile = (entry) => {
  const file = { ...entry, type: 'file', workdir: false, workspace: true }
  emit('open-preview', file)
  void loadActivePreview({ workspaceOpen: file })
}

const handleSearchSelect = (entry) => {
  if (!entry?.path) return
  if (fileSearchScope.value === 'workspace') {
    workspaceSelectedKeys.value = [entry.path]
    openPersonalFile(entry)
    return
  }
  if (!props.threadId) return
  selectedKeys.value = [entry.path]
  emit('open-preview', { ...entry, type: 'file', workdir: true })
}

const activePreviewTab = computed(() => props.activePreview)
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
      filesystemLoaded.value = true
      expandedKeys.value = expandedKeysAfterFilesystemRefresh(expandedKeys.value, { silent })
      const tab = activePreviewTab.value
      selectedKeys.value = tab?.threadId === requestedThreadId && tab.workdir ? [tab.path] : []
      if (silent) await refreshActivePreviewIfChanged(nodes, requestedThreadId)
    } else if (
      requestedGeneration === filesystemRefreshGeneration &&
      filesystemRefreshGate.canCommit(requestedThreadId, props.threadId)
    ) {
      dynamicTreeData.value = []
      filesystemLoaded.value = true
    }
  } catch (error) {
    if (
      requestedGeneration !== filesystemRefreshGeneration ||
      !filesystemRefreshGate.canCommit(requestedThreadId, props.threadId)
    ) {
      return
    }
    dynamicTreeData.value = []
    filesystemLoaded.value = false
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
  const requestedThreadId = props.threadId
  const requestedGeneration = filesystemRefreshGeneration
  if (treeNode.isLeaf || treeNode.children?.length || !requestedThreadId) return

  try {
    const children = await loadDirectoryChildren(treeNode.key, requestedThreadId)
    if (requestedGeneration === filesystemRefreshGeneration) {
      dynamicTreeData.value = updateTreeChildren(dynamicTreeData.value, treeNode.key, children)
    }
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
let workspaceTreeGeneration = 0

const refreshWorkspaceTree = async () => {
  if (workspaceRefreshInFlight) {
    workspaceRefreshPending = true
    return
  }
  workspaceRefreshInFlight = true
  workspaceLoading.value = true
  workspaceError.value = ''

  try {
    do {
      workspaceRefreshPending = false

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
        workspaceError.value = ''
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
  openPersonalFile(node.fileData)
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
  if (!componentActive.value || !props.filesystemPollingActive) {
    return
  }
  stopFilesystemPolling = startAgentPanelFilesystemPolling({
    refresh: () => refreshFileSystem({ silent: true })
  })
}

const loadActivePreview = async ({ baseFileOverride = null, workspaceOpen = null } = {}) => {
  const tab = workspaceOpen
    ? { ...workspaceOpen, key: filePreviewCacheKey(workspaceOpen.path, 'workspace'), workspace: true, workdir: false, threadId: null }
    : activePreviewTab.value
  const rootThreadId = props.threadId
  const requestSeq = ++previewRequestSeq
  const requestIsCurrent = () =>
    props.visible &&
    componentActive.value &&
    requestSeq === previewRequestSeq &&
    rootThreadId === props.threadId &&
    tab?.key === activePreviewTab.value?.key

  if (!tab || !props.visible || !componentActive.value) {
    currentFile.value = null
    currentFilePath.value = ''
    return
  }

  const { path: filePath, key: cacheKey, threadId: requestedThreadId, workspace: isWorkspaceFile } = tab
  const baseFile = {
    ...tab,
    ...(baseFileOverride || {}),
    path: filePath,
    workspace: isWorkspaceFile,
    resourceBaseUrl:
      baseFileOverride?.artifact_url ||
      tab.artifact_url ||
      (requestedThreadId ? threadApi.getThreadArtifactUrl(requestedThreadId, filePath, false) : ''),
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

  // 用户打开重新校验文件并记录访问；随后响应标签变化的 watcher 共享这次读取。
  if (workspaceOpen) releaseFilePreviewCacheEntry(props.previewCache, cacheKey)
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
      // reactive Map 包装了 file，归属比较需还原实际响应对象。
      const cacheStillOwnsFile =
        currentEntry === cachedEntry ||
        (currentEntry?.status === 'ready' && toRaw(currentEntry.file) === cachedFile)
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
    const response = isWorkspaceFile
      ? await (workspaceOpen ? openWorkspaceFile : getWorkspaceFileContent)(filePath)
      : baseFile.workdir
        ? await getViewerFileContent(requestedThreadId, filePath)
        : await threadApi.previewThreadArtifact(requestedThreadId, filePath)
    return normalizePreviewResponse(response, baseFile)
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
    pruneFilePreviewCache(props.previewCache, cacheKey)
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
  if (tab?.workdir !== true || tab.threadId !== requestedThreadId) return
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
    tab !== activePreviewTab.value
  ) {
    return
  }
  const nextTab = { ...tab, ...(latestFile || {}) }
  await reloadPreviewAfterOrderedCacheEntryInvalidation({
    previewCache: props.previewCache,
    cacheKey: tab.key,
    revokeObjectURL: window.URL.revokeObjectURL.bind(window.URL),
    notifyPreviewChanged: () => emit('open-preview', nextTab, tab.threadId),
    reloadPreview: () => loadActivePreview({ baseFileOverride: nextTab })
  })
}

const onFileSelect = (nextSelectedKeys, { node }) => {
  selectedKeys.value = nextSelectedKeys
  if (!node?.isLeaf || !props.threadId) return
  emit('open-preview', node.fileData)
}

const pruneTreeStateAfterDelete = (targetPath) => {
  selectedKeys.value = selectedKeys.value.filter((key) => !isSameOrChildPath(key, targetPath))
  expandedKeys.value = expandedKeys.value.filter((key) => !isSameOrChildPath(key, targetPath))
  emit('close-preview-path', targetPath)
}

const confirmDeleteNode = (node) => {
  const threadId = props.threadId
  const generation = filesystemRefreshGeneration
  const fileName = node?.title || getFileName(node?.fileData)
  const isDirectory = !node?.isLeaf
  Modal.confirm({
    title: isDirectory ? `确认删除文件夹「${fileName}」？` : `确认删除文件「${fileName}」？`,
    content: isDirectory ? '将删除该文件夹及其所有内容，删除后不可恢复。' : '删除后不可恢复。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      if (generation !== filesystemRefreshGeneration) return
      const nextDeletingPaths = new Set(deletingPaths.value)
      nextDeletingPaths.add(node.key)
      deletingPaths.value = nextDeletingPaths

      try {
        await deleteViewerFile(threadId, node.key)
        if (generation !== filesystemRefreshGeneration) return
        dynamicTreeData.value = removeTreeNode(dynamicTreeData.value, node.key)
        pruneTreeStateAfterDelete(node.key)
        message.success(isDirectory ? '文件夹删除成功' : '文件删除成功')
      } catch (error) {
        if (generation !== filesystemRefreshGeneration) return
        console.error(isDirectory ? '删除文件夹失败:' : '删除文件失败:', error)
        message.error(error?.message || (isDirectory ? '删除文件夹失败' : '删除文件失败'))
      } finally {
        if (generation === filesystemRefreshGeneration) {
          const latestDeletingPaths = new Set(deletingPaths.value)
          latestDeletingPaths.delete(node.key)
          deletingPaths.value = latestDeletingPaths
        }
      }
    }
  })
}

const downloadFile = async (fileItem) => {
  if (!fileItem?.path) return
  const threadId = fileItem.threadId || props.threadId
  if (!fileItem.workspace && !threadId) return

  try {
    const response = fileItem.workspace
      ? await downloadWorkspaceFile(fileItem.path)
      : fileItem.workdir
        ? await downloadViewerFile(threadId, fileItem.path)
        : await threadApi.downloadThreadArtifact(threadId, fileItem.path)
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
    message.error('文件下载失败，请重试')
  }
}

const refreshActivePreview = async () => {
  const preview = activePreviewTab.value
  if (!preview) return refreshTree('thread')
  releaseFilePreviewCache(props.previewCache, preview.workspace ? 'workspace' : preview.threadId)
  await loadActivePreview()
}

const refreshTree = async (scope) => {
  if (scope === 'workspace') {
    releaseFilePreviewCache(props.previewCache, 'workspace')
    await refreshWorkspaceTree()
    return
  }
  releaseFilePreviewCache(props.previewCache, props.threadId)
  await refreshFileSystem()
  emit('refresh', props.threadId)
}

const emitClose = () => {
  emit('close')
}

let resizePointerId = null
let pendingClientX = 0
let resizeFrameId = 0
let previousBodyStyle = null

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
  previousBodyStyle = {
    cursor: document.body.style.cursor,
    userSelect: document.body.style.userSelect
  }
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
  Object.assign(document.body.style, previousBodyStyle)
  previousBodyStyle = null
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', stopResize)
  window.removeEventListener('pointercancel', stopResize)
}

onMounted(() => {
  if (props.filesystemVisible) void refreshFileSystem({ silent: true })
})

onActivated(() => {
  const wasInactive = !componentActive.value
  componentActive.value = true
  if (wasInactive && props.visible && activeSection.value?.type === 'file') {
    void refreshActivePreview()
  } else if (wasInactive) {
    if (props.filesystemVisible) void refreshFileSystem({ silent: true })
  }
})

onDeactivated(() => {
  componentActive.value = false
  fileSearchOpen.value = false
  stopResize()
  stopFilesystemRefreshPolling()
})

onUnmounted(() => {
  stopResize()
  stopFilesystemRefreshPolling()
})

watch(
  () => props.threadId,
  (threadId) => {
    if (fileSearchScope.value === 'thread') fileSearchOpen.value = false
    filesystemRefreshGeneration += 1
    previewRequestSeq += 1
    loadingFiles.value = false
    filesystemLoaded.value = false
    dynamicTreeData.value = []
    expandedKeys.value = []
    selectedKeys.value = []
    deletingPaths.value = new Set()
    filesystemError.value = ''
    if (threadId && props.filesystemVisible) {
      refreshFileSystem({ ensure: true })
    }
  }
)

watch(
  [() => props.threadId, activePreviewTab],
  () => void loadActivePreview(),
  { immediate: true }
)

watch(
  () => props.filesystemVisible,
  (visible) => {
    if (!visible || !componentActive.value) return
    void refreshFileSystem({ silent: true })
  }
)

watch(
  [workspaceTreeVisible, componentActive, () => props.filesystemRefreshVersions],
  ([visible, active]) => {
    if (visible && active) void refreshWorkspaceTree()
  },
  { immediate: true }
)

watch([() => props.visible, () => props.activeSectionKey], () => {
  fileSearchOpen.value = false
})

watch(
  () => props.visible,
  (visible) => {
    if (!visible) stopResize()
    if (visible && componentActive.value && activeSection.value?.type === 'file') {
      void loadActivePreview()
    }
  }
)

watch([() => props.filesystemPollingActive, componentActive], syncFilesystemRefreshPolling, { immediate: true })

watch(
  () => props.filesystemRefreshVersions,
  (versions, previous) => {
    const changedThreads = [...versions].filter(([id, version]) => version !== previous.get(id)).map(([id]) => id)
    for (const id of changedThreads) releaseFilePreviewCache(props.previewCache, id)
    if (!props.visible || !componentActive.value) return
    const rootChanged = changedThreads.includes(props.threadId)
    if (activeSection.value?.type === 'file') {
      const tab = activePreviewTab.value
      if (tab?.workspace && rootChanged) releaseFilePreviewCache(props.previewCache, 'workspace')
      if (tab?.workspace ? rootChanged : changedThreads.includes(tab?.threadId)) void loadActivePreview()
    } else if (rootChanged && props.filesystemVisible) {
      void refreshFileSystem({ silent: true, ensure: true })
    }
  }
)

watch(
  activePreviewTab,
  (tab) => {
    if (!tab) return
    if (tab.workspace) workspaceSelectedKeys.value = [tab.path]
    else if (tab.threadId === props.threadId && tab.workdir) selectedKeys.value = [tab.path]
  }
)
watch(
  () => props.activeSectionKey,
  () => {
    void ensureActiveSectionVisible()
  },
  { immediate: true }
)
</script>

<style scoped lang="less">
.resize-divider {
  position: absolute;
  inset: 0 auto 0 0;
  width: 8px;
  cursor: col-resize;
  z-index: 10;
  touch-action: none;

  &::before {
    content: '';
    position: absolute;
    inset: 0 auto 0 0;
    width: 1px;
    background: var(--gray-150);
    transition: width 140ms ease, background-color 140ms ease;
  }

  &:hover::before,
  &:focus-visible::before,
  .agent-panel.resizing > &::before {
    width: 3px;
    background: var(--gray-400);
  }

  &:focus-visible {
    outline: none;

    &::before {
      background: var(--gray-600);
    }
  }
}

@media (prefers-reduced-motion: reduce) {
  .resize-divider::before {
    transition: none;
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

  .panel-header {
    border-bottom: 1px solid var(--gray-100);
  }

  :deep(.side-preview-shell) {
    border: none;
  }

  :deep(.preview-header) {
    min-height: 32px;
    background: var(--gray-0);
  }
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px;
  min-height: var(--header-height);
  background: var(--gray-0);
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
  min-width: 0;
  align-items: center;
  gap: 4px;
  overflow-x: auto;
  scrollbar-width: none;

  &::-webkit-scrollbar {
    display: none;
  }
}

.section-tab {
  position: relative;
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  min-width: 0;
  max-width: 140px;
  border-radius: 7px;
  color: var(--gray-600);

  &::after {
    content: '';
    position: absolute;
    top: 50%;
    right: -3px;
    width: 1px;
    height: 14px;
    background: var(--gray-200);
    transform: translateY(-50%);
    pointer-events: none;
  }

  &:last-child::after,
  &.active::after,
  &:has(+ .section-tab.active)::after {
    display: none;
  }

  &:hover {
    background: var(--gray-50);
  }

  &.active {
    background: var(--gray-100);
    color: var(--gray-900);
  }

  &:hover .section-tab-close,
  &:focus-within .section-tab-close,
  &.active .section-tab-close {
    opacity: 1;
    pointer-events: auto;
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
  border-radius: 7px;

  &:focus-visible {
    outline: 2px solid var(--main-300);
    outline-offset: -2px;
  }

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

.section-tab:has(.section-attention) {
  max-width: 200px;
}
.section-tab-main > .section-attention {
  flex: 0 0 auto;
  padding: 2px 5px;
  border-radius: 999px;
  background: var(--color-warning-50);
  color: var(--color-warning-900);
  font-size: 11px;
  font-weight: 500;
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
  opacity: 0;
  pointer-events: none;
  transition: opacity 120ms ease;

  &:hover {
    background: var(--gray-150);
    color: var(--gray-900);
  }

  &:focus-visible {
    outline: 2px solid var(--main-300);
    outline-offset: -2px;
  }
}

@media (hover: none) {
  .section-tab-close {
    opacity: 1;
    pointer-events: auto;
  }
}

.add-section-btn {
  flex-shrink: 0;
}

.window-actions {
  margin-inline-start: auto;
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

.message-debug-section,
.turn-sources-section {
  width: 100%;
  height: 100%;
  min-height: 0;
}

.turn-sources-section {
  display: flex;
  flex-direction: column;
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

.panel-start {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  align-items: flex-start;
  overflow: auto;
  padding: clamp(16px, 15vh, 160px) 16px 16px;
}

.panel-start-actions {
  display: grid;
  gap: 8px;
  width: min(100%, 320px);
  margin: 0 auto;
}

.panel-start-icon {
  margin: 0 auto 12px;
  color: var(--gray-500);
}

.panel-start-action {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 44px;
  padding: 8px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--gray-900);
  font-size: 14px;
  text-align: left;
  cursor: pointer;

  > svg {
    flex-shrink: 0;
    color: var(--gray-600);
  }

  &:hover {
    background: var(--gray-50);
    border-color: var(--gray-200);
  }

  &:focus-visible {
    outline: 2px solid var(--main-300);
    outline-offset: 1px;
  }
}

.tree-pane {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 0 6px;
  overflow: hidden;
}

.tree-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 36px;
  flex-shrink: 0;
  gap: 8px;
  padding: 4px 4px 4px 8px;
  border-bottom: 1px solid var(--gray-100);
}

.tree-title {
  color: var(--gray-700);
  font-size: 13px;
  font-weight: 600;
}

.tree-scope-pane {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.tree-toolbar-actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 2px;
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
  font-size: 13px;
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
  color: var(--color-error-700);
}
</style>
