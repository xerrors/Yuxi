<template>
  <div class="workspace-view layout-container">
    <PageHeader title="个人空间" :loading="loadingTree || loadingPreview" :show-border="true">
      <template #actions>
        <a-button
          class="lucide-icon-btn"
          :aria-label="sidebarCollapsed ? '展开个人空间侧边栏' : '收起个人空间侧边栏'"
          :aria-expanded="!sidebarCollapsed"
          aria-controls="workspace-navigation"
          @click="sidebarCollapsed = !sidebarCollapsed"
        >
          <template #icon><PanelLeft :size="16" /></template>
          {{ sidebarCollapsed ? '展开导航' : '收起导航' }}
        </a-button>
        <a-button class="lucide-icon-btn" @click="fileSearchOpen = true">
          <template #icon><Search :size="16" /></template>
          搜索
        </a-button>
        <a-button
          v-if="isAgentsWorkspacePath"
          class="lucide-icon-btn"
          @click="openAgentsGuideModal"
        >
          <template #icon><CircleHelp :size="16" /></template>
          使用说明
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
    <input
      ref="folderUploadInputRef"
      class="upload-input"
      type="file"
      webkitdirectory
      multiple
      @change="handleFolderUploadInputChange"
    />

    <GlobalSearchModal
      v-model:open="fileSearchOpen"
      :modes="['file']"
      default-mode="file"
      :file-search="searchWorkspace"
      file-placeholder="搜索个人空间文件..."
      file-scope-hint="仅搜索个人空间，不包含知识库文件。"
      @select-file="handleFileSearchSelect"
    />

    <a-alert
      v-if="uploadError && activeSourceKey === 'personal'"
      class="upload-error"
      type="error"
      :message="uploadError"
      show-icon
      closable
      @close="uploadError = ''"
    />

    <div class="workspace-shell" :class="{ 'is-sidebar-collapsed': sidebarCollapsed }">
      <div
        id="workspace-navigation"
        class="workspace-sidebar-slot"
        :inert="sidebarCollapsed"
        :aria-hidden="sidebarCollapsed"
      >
        <WorkspaceSidebar
          :active-key="activeSourceKey"
          :current-path="currentPath"
          :knowledge-bases="knowledgeBases"
          :loading-knowledge-bases="loadingKnowledgeBases"
          :current-uid="userStore.uid"
          :disabled="activeSourceKey !== 'personal' || isReadonlyWorkspacePath"
          :uploading="uploadingFile"
          @select-personal="selectPersonalWorkspace"
          @select-knowledge-base="selectKnowledgeBase"
          @select-path="selectWorkspacePath"
          @upload-file="openUploadFilePicker"
          @upload-folder="openUploadFolderPicker"
          @create-directory="openCreateDirectoryModal"
        />
      </div>
      <main
        ref="workspaceMainRef"
        class="workspace-main"
        :class="{ 'is-inline-preview': showInlinePreview }"
      >
        <template v-if="activeSourceKey === 'personal' || selectedKnowledgeBase">
          <WorkspaceFileList
            :entries="entries"
            :current-path="currentPath"
            :selected-path="selectedEntry?.path || ''"
            :selected-paths="selectedPaths"
            :deleting-paths="deletingPaths"
            :selection-mode="selectionMode"
            :loading="loadingTree"
            :readonly="isReadonlyWorkspacePath"
            :root-label="selectedKnowledgeBase?.name || '全部文件'"
            :breadcrumb-items="
              isKnowledgeSource ? knowledgeBreadcrumbItems : workspaceBreadcrumbItems
            "
            :pagination="isKnowledgeSource ? knowledgePagination : null"
            :uploadable="personalUploadEnabled"
            :uploading="uploadingFile"
            @select-entry="handleSelectEntry"
            @breadcrumb-click="handleListBreadcrumbClick"
            @update:selected-paths="selectedPaths = $event"
            @update:selection-mode="handleSelectionModeChange"
            @delete-selected="confirmDeleteEntries(selectedEntries)"
            @delete-entry="(entry) => confirmDeleteEntries([entry])"
            @download-entry="downloadEntry"
            @page-change="handleKnowledgePageChange"
            @upload-file="openUploadFilePicker"
            @create-directory="openCreateDirectoryModal"
            @upload-drop="handleDropUpload"
          />
          <Transition name="workspace-preview-slide" @after-leave="handlePreviewAfterLeave">
            <aside
              v-if="showInlinePreview"
              class="workspace-preview-panel"
              :class="{ 'is-resizing': isPreviewResizing }"
              :style="workspacePreviewStyle"
            >
              <div
                class="workspace-preview-resizer"
                role="separator"
                aria-label="调整预览宽度"
                tabindex="0"
                @pointerdown="startPreviewResize"
              ></div>
              <WorkspacePreviewPane
                :file="previewFile"
                :file-path="selectedPreviewPath"
                :editable="!isReadonlyWorkspacePath"
                :saving="savingPreviewFile"
                @close="closePreview"
                @save="handleSavePreviewFile"
              />
            </aside>
          </Transition>
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
      v-model:open="agentsGuideModalVisible"
      title="Agents 目录说明"
      okText="我知道了"
      :cancelButtonProps="{ style: { display: 'none' } }"
      @ok="closeAgentsGuideModal"
    >
      <div class="agents-guide-content">
        <p>
          这个文件夹中的说明文件会在合适的时机注入到 Agent
          的执行流程中，用来补充你的长期偏好、业务背景和协作要求。
        </p>
        <p>
          目前支持 <code>AGENTS.md</code>、<code>USER.md</code> 和
          <code>MEMORY.md</code>：其中内容会在每次会话中注入到 Agent
          Prompt，分别适合写入行为约束、用户信息和希望 Agent 记住的信息。
        </p>

        <section class="agents-guide-section">
          <h3>填写建议</h3>
          <ul>
            <li>写清常用工作背景，例如部门职责、常见任务、知识库使用方式。</li>
            <li>写清回答偏好，例如语言风格、详略程度、是否优先给结论。</li>
            <li>写清业务术语和固定称呼，帮助 Agent 保持表达一致。</li>
            <li>写清资料使用要求，例如优先引用哪些知识库、哪些内容需要谨慎确认。</li>
            <li>写清协作边界，例如不确定时先提问，涉及重要决策时先给方案再执行。</li>
            <li>优先使用明确、可执行的规则，避免“尽量做好”这类模糊描述。</li>
          </ul>
        </section>
      </div>
    </a-modal>

    <a-modal
      :open="workspaceActive && previewModalVisible && !useInlinePreview"
      width="880px"
      :style="{ maxWidth: '92vw', top: '5vh' }"
      :bodyStyle="{ height: '82vh', maxHeight: '90vh', padding: '0', overflow: 'hidden' }"
      :footer="null"
      :closable="false"
      destroy-on-close
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
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { CircleHelp, LibraryBig, PanelLeft, Search } from '@lucide/vue'
import PageHeader from '@/shared/ui/PageHeader.vue'
import AgentFilePreview from '@/modules/session/ui/workspace/AgentFilePreview.vue'
import WorkspaceFileList from '@/modules/workspace/ui/WorkspaceFileList.vue'
import WorkspacePreviewPane from '@/modules/workspace/ui/WorkspacePreviewPane.vue'
import WorkspaceSidebar from '@/modules/workspace/ui/WorkspaceSidebar.vue'
import {
  WORKSPACE_UPLOAD_MAX_FILES,
  buildFolderUploadBatches,
  collectFolderUploadEntries,
  traverseDirectoryEntry,
  validateFolderUploadEntries
} from '@/modules/workspace'
import { knowledgeBaseApi } from '@/apis/knowledge_api'
import { useUserStore } from '@/modules/identity/model/user'
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
  searchWorkspaceFiles,
  uploadWorkspaceFiles
} from '@/apis/workspace_api'
import GlobalSearchModal from '@/modules/session/ui/GlobalSearchModal.vue'
import { normalizePreviewResponse } from '@/shared/lib/file_preview'
import { parseDownloadFilename } from '@/shared/lib/file_utils'

const userStore = useUserStore()
const route = useRoute()
const router = useRouter()

const activeSourceKey = ref('personal')
const currentPath = ref('/')
const fileSearchOpen = ref(false)

const searchWorkspace = (query) => searchWorkspaceFiles(query)

// 搜索命中后切到文件所在目录并打开预览
const handleFileSearchSelect = async (entry) => {
  if (!entry?.path) return
  const parentPath = entry.path.replace(/\/[^/]+$/, '') || '/'
  await selectWorkspacePath(parentPath)
  const matched = entries.value.find((item) => item.path === entry.path)
  if (matched) await loadWorkspacePreview(matched)
}

// 侧边栏全局搜索选中工作区文件后，通过 query 跳转打开对应文件
const openFileByPath = async (path) => {
  if (!path) return
  const parentPath = String(path).replace(/\/[^/]+$/, '') || '/'
  await selectWorkspacePath(parentPath)
  const matched = entries.value.find((item) => item.path === String(path))
  if (!matched) return
  await loadWorkspacePreview(matched)
  // 消费本次打开请求，同一路径在预览收起后也可以再次打开。
  if (route.path === '/workspace' && route.query.open === path) {
    const query = { ...route.query }
    delete query.open
    await router.replace({ query })
  }
}
const knowledgeBreadcrumbItems = ref([])
const workspaceBreadcrumbItems = ref(null)
const entries = ref([])
const selectedEntry = ref(null)
const selectedPaths = ref([])
const selectionMode = ref(false)
const previewFile = ref(null)
const previewObjectUrl = ref('')
const previewModalVisible = ref(false)
const inlinePreviewVisible = ref(false)
const loadingTree = ref(false)
const loadingPreview = ref(false)
const savingPreviewFile = ref(false)
const loadingKnowledgeBases = ref(false)
const knowledgeBases = ref([])
const selectedKnowledgeBase = ref(null)
const workspaceMainRef = ref(null)
const workspaceMainWidth = ref(0)
const workspaceActive = ref(true)
const createDirectoryModalVisible = ref(false)
const agentsGuideModalVisible = ref(false)
const newDirectoryName = ref('')
const creatingDirectory = ref(false)
const uploadingFile = ref(false)
const uploadError = ref('')
const uploadInputRef = ref(null)
const folderUploadInputRef = ref(null)
const deletingPaths = ref([])
const sidebarCollapsed = ref(false)
const previewWidthPercent = ref(50)
const isPreviewResizing = ref(false)
const previewRequestId = ref(0)
const INLINE_PREVIEW_MIN_WIDTH = 960
const AGENTS_WORKSPACE_PATH = '/agents'
const knowledgeFileBrowser = reactive({
  parentId: null,
  page: 1,
  pageSize: 100,
  total: 0,
  hasMore: false
})

const useInlinePreview = computed(() => workspaceMainWidth.value >= INLINE_PREVIEW_MIN_WIDTH)
const isKnowledgeSource = computed(() => activeSourceKey.value.startsWith('knowledge-base:'))
const comparablePath = (path) => String(path || '/').replace(/\/$/, '') || '/'
const isSameOrChildPath = (path, targetPath) => {
  const normalizedPath = comparablePath(path)
  const normalizedTargetPath = comparablePath(targetPath)
  return (
    normalizedPath === normalizedTargetPath || normalizedPath.startsWith(`${normalizedTargetPath}/`)
  )
}
const isAgentsWorkspacePath = computed(
  () =>
    activeSourceKey.value === 'personal' &&
    comparablePath(currentPath.value) === AGENTS_WORKSPACE_PATH
)
const isReadonlyWorkspacePath = computed(() => isKnowledgeSource.value)
// 与侧边栏上传入口同一语义：仅个人空间非只读目录可上传，知识库只读。
const personalUploadEnabled = computed(
  () => activeSourceKey.value === 'personal' && !isReadonlyWorkspacePath.value
)
const selectedPreviewPath = computed(() =>
  selectedEntry.value?.source === 'knowledge'
    ? selectedEntry.value.name || ''
    : selectedEntry.value?.path || ''
)
const showInlinePreview = computed(
  () => useInlinePreview.value && inlinePreviewVisible.value && Boolean(previewFile.value)
)
const workspacePreviewStyle = computed(() => ({
  width: `${previewWidthPercent.value}%`
}))

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

const KNOWLEDGE_PREVIEW_LOAD_MESSAGES = {
  log: '加载知识库文件预览失败:',
  resolveUserMessage: () => '加载知识库文件预览失败'
}

const buildPreviewLoadingFile = (entry, baseFile = entry) => ({
  ...baseFile,
  ...entry,
  content: '',
  status: 'loading',
  supported: true,
  previewType: 'text',
  message: '',
  previewUrl: ''
})

const buildPreviewErrorFile = (entry, error) => ({
  ...entry,
  content: '',
  status: 'error',
  errorMessage: error?.message || '文件预览失败',
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
  inlinePreviewVisible.value = true
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
    const file = await normalizePreviewResponse(response, entry)
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
    const file = await normalizePreviewResponse(response, entry)
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
    workspaceBreadcrumbItems.value = buildWorkspaceBreadcrumbItems()
    syncSelectedPaths()
    if (!selectedPaths.value.length) {
      selectionMode.value = false
    }
  } catch (error) {
    console.warn('加载个人空间目录失败:', error)
    message.error('加载个人空间目录失败')
  } finally {
    loadingTree.value = false
  }
}

const buildWorkspaceBreadcrumbItems = () => {
  const segments = comparablePath(currentPath.value).split('/').filter(Boolean)
  if (!segments.length) return null
  return segments.reduce(
    (items, segment) => {
      const parentPath = items[items.length - 1].path
      const path = parentPath === '/' ? `/${segment}` : `${parentPath}/${segment}`
      items.push({ name: segment, path })
      return items
    },
    [{ name: '全部文件', path: '/' }]
  )
}

const loadKnowledgeEntries = async (
  knowledgeBase,
  { parentId = null, page = 1, pageSize = knowledgeFileBrowser.pageSize, breadcrumbs = null } = {}
) => {
  if (!knowledgeBase?.kb_id) return

  loadingTree.value = true
  try {
    const response = await getWorkspaceKnowledgeTree(knowledgeBase.kb_id, {
      parentId,
      page,
      pageSize
    })
    entries.value = response.entries || []
    knowledgeBreadcrumbItems.value = breadcrumbs || [
      {
        name: knowledgeBase.name || '知识库',
        path: '/',
        parentId: null
      }
    ]
    currentPath.value = knowledgeBreadcrumbItems.value.at(-1)?.path || '/'
    Object.assign(knowledgeFileBrowser, {
      parentId: response.parent_id || parentId || null,
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

const loadKnowledgeBases = async () => {
  loadingKnowledgeBases.value = true
  try {
    const response = await knowledgeBaseApi.getAccessibleKnowledgeBases()
    knowledgeBases.value = (response?.knowledge_bases || []).filter((knowledgeBase) => {
      return knowledgeBase?.supports_documents !== false
    })
  } catch (error) {
    console.warn('加载可访问知识库失败:', error)
    knowledgeBases.value = []
  } finally {
    loadingKnowledgeBases.value = false
  }
}

const selectPersonalWorkspace = async () => {
  const wasKnowledgeSource = isKnowledgeSource.value
  activeSourceKey.value = 'personal'
  selectedKnowledgeBase.value = null
  knowledgeBreadcrumbItems.value = []
  closePreview()
  clearWorkspaceSelection()
  if (wasKnowledgeSource || currentPath.value !== '/' || !entries.value.length) {
    await loadWorkspaceEntries('/')
  }
}

const selectWorkspacePath = async (path) => {
  activeSourceKey.value = 'personal'
  selectedKnowledgeBase.value = null
  knowledgeBreadcrumbItems.value = []
  closePreview()
  clearWorkspaceSelection()
  await loadWorkspaceEntries(path)
}

const selectKnowledgeBreadcrumb = async (item, index) => {
  if (!selectedKnowledgeBase.value || !item) return
  closePreview()
  clearWorkspaceSelection()
  const breadcrumbs = knowledgeBreadcrumbItems.value.slice(0, index + 1)
  await loadKnowledgeEntries(selectedKnowledgeBase.value, {
    parentId: item.parentId || null,
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

const selectKnowledgeBase = async (knowledgeBase) => {
  if (knowledgeBase?.supports_documents === false) return
  closePreview()
  clearWorkspaceSelection()
  selectedKnowledgeBase.value = knowledgeBase
  activeSourceKey.value = `knowledge-base:${knowledgeBase.kb_id}`
  await loadKnowledgeEntries(knowledgeBase)
}

const openKnowledgeDirectory = async (entry) => {
  closePreview()
  clearWorkspaceSelection()
  const parentPath = knowledgeBreadcrumbItems.value.at(-1)?.path || '/'
  const nextPath = parentPath === '/' ? `/${entry.name}` : `${parentPath}/${entry.name}`
  const nextBreadcrumb = {
    name: entry.name,
    path: nextPath,
    parentId: entry.file_id
  }
  await loadKnowledgeEntries(selectedKnowledgeBase.value, {
    parentId: nextBreadcrumb.parentId,
    page: 1,
    breadcrumbs: [...knowledgeBreadcrumbItems.value, nextBreadcrumb]
  })
}

const handleKnowledgePageChange = async ({ page, pageSize }) => {
  if (!selectedKnowledgeBase.value || !isKnowledgeSource.value) return
  closePreview()
  clearWorkspaceSelection()
  const currentBreadcrumb = knowledgeBreadcrumbItems.value.at(-1)
  await loadKnowledgeEntries(selectedKnowledgeBase.value, {
    parentId: currentBreadcrumb?.parentId || null,
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
  inlinePreviewVisible.value = false
  loadingPreview.value = false
  if (!useInlinePreview.value) {
    selectedEntry.value = null
    previewFile.value = null
    revokePreviewObjectUrl()
  }
}

const handlePreviewAfterLeave = () => {
  if (inlinePreviewVisible.value || previewModalVisible.value) return
  selectedEntry.value = null
  previewFile.value = null
  revokePreviewObjectUrl()
}

const handleSavePreviewFile = async (content) => {
  if (isReadonlyWorkspacePath.value) {
    message.warning('当前文件为只读，无法保存')
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
    console.warn('保存个人空间文件失败:', error)
    message.error(error?.message || '文件保存失败')
  } finally {
    savingPreviewFile.value = false
  }
}

const openCreateDirectoryModal = () => {
  if (activeSourceKey.value !== 'personal' || isReadonlyWorkspacePath.value) return
  newDirectoryName.value = ''
  createDirectoryModalVisible.value = true
}

const openAgentsGuideModal = () => {
  agentsGuideModalVisible.value = true
}

const closeAgentsGuideModal = () => {
  agentsGuideModalVisible.value = false
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
  if (activeSourceKey.value !== 'personal' || isReadonlyWorkspacePath.value || uploadingFile.value)
    return
  if (uploadInputRef.value) {
    uploadInputRef.value.value = ''
    uploadInputRef.value.click()
  }
}

const handleUploadInputChange = async (event) => {
  const files = Array.from(event.target?.files || [])
  if (!files.length || uploadingFile.value) return
  if (files.length > WORKSPACE_UPLOAD_MAX_FILES) {
    message.warning(`一次最多上传 ${WORKSPACE_UPLOAD_MAX_FILES} 个文件`)
    event.target.value = ''
    return
  }

  uploadingFile.value = true
  uploadError.value = ''
  try {
    await uploadWorkspaceFiles(currentPath.value, files)
    await loadWorkspaceEntries(currentPath.value)
    message.success(`${files.length} 个文件上传成功`)
  } catch (error) {
    console.warn('上传文件失败:', error)
    const hint = error?.status === 400 ? ' 请检查是否存在同名文件，重命名后再上传。' : ''
    uploadError.value = `${files.map((file) => file.name).join('、')} 上传失败：${error?.message || '请重试'}。${hint}`
    message.error(uploadError.value)
  } finally {
    uploadingFile.value = false
    event.target.value = ''
  }
}

const openUploadFolderPicker = () => {
  if (activeSourceKey.value !== 'personal' || isReadonlyWorkspacePath.value || uploadingFile.value)
    return
  if (folderUploadInputRef.value) {
    folderUploadInputRef.value.value = ''
    folderUploadInputRef.value.click()
  }
}

const formatFolderUploadFailure = (failed, batchCount, uploadedCount) => {
  const names = failed.batch
    .slice(0, 3)
    .map((entry) => entry.relativePath)
    .join('、')
  const remainder = failed.batch.length > 3 ? ` 等 ${failed.batch.length} 个文件` : ''
  const hint =
    failed.error?.status === 413
      ? ' 文件过大，请压缩或拆分后重试。'
      : failed.error?.status === 400
        ? ' 请检查目标路径是否已存在同名文件或目录；已成功批次不会回滚，重试前请删除已上传文件后重选整个根文件夹。'
        : ''
  return `已上传 ${uploadedCount} 个文件；第 ${failed.index + 1}/${batchCount} 批失败：${names}${remainder}。${failed.error?.message || '请重试'}${hint}`
}

const handleDropUpload = async (roots) => {
  if (activeSourceKey.value !== 'personal' || isReadonlyWorkspacePath.value || uploadingFile.value)
    return
  const plainFiles = roots?.files || []
  const directories = roots?.directories || []
  if (!plainFiles.length && !directories.length) return
  if (plainFiles.length > WORKSPACE_UPLOAD_MAX_FILES) {
    message.warning(`一次最多上传 ${WORKSPACE_UPLOAD_MAX_FILES} 个文件`)
    return
  }

  const parentPath = currentPath.value
  uploadingFile.value = true
  uploadError.value = ''
  let uploadedCount = 0
  const emptyDirectoryNames = []
  try {
    if (plainFiles.length) {
      await uploadWorkspaceFiles(parentPath, plainFiles)
      uploadedCount += plainFiles.length
    }
    for (const directory of directories) {
      const entries = await traverseDirectoryEntry(directory.entry)
      if (!entries.length) {
        emptyDirectoryNames.push(directory.name)
        continue
      }
      validateFolderUploadEntries(entries)
      for (const batch of buildFolderUploadBatches(entries)) {
        await uploadWorkspaceFiles(
          parentPath,
          batch.map((entry) => entry.file),
          batch.map((entry) => entry.relativePath)
        )
        uploadedCount += batch.length
      }
    }
    if (uploadedCount) {
      message.success(`${uploadedCount} 个文件上传成功`)
    } else if (emptyDirectoryNames.length) {
      message.info('拖拽内容里没有可上传的文件；空文件夹不会被上传')
    }
  } catch (error) {
    console.warn('拖拽上传失败:', error)
    const subject = plainFiles.length && !uploadedCount ? plainFiles.map((file) => file.name).join('、') : '拖拽内容'
    const hint =
      error?.status === 413
        ? ' 文件过大，请压缩或拆分后重试。'
        : error?.status === 400
          ? ' 请检查目标路径是否已存在同名文件或目录；已成功部分不会回滚。'
          : ''
    uploadError.value = `${subject} 上传失败：${error?.message || '请重试'}。${hint}`
    message.error(uploadError.value)
  } finally {
    uploadingFile.value = false
    if (activeSourceKey.value === 'personal' && currentPath.value === parentPath) {
      // 部分成功也要回读最终状态，不能依赖成功分支。
      await loadWorkspaceEntries(parentPath).catch(() => {})
    }
  }
}

const handleFolderUploadInputChange = async (event) => {
  const input = event.target
  const files = Array.from(input?.files || [])
  if (uploadingFile.value) {
    input.value = ''
    return
  }
  if (!files.length) {
    input.value = ''
    message.info('文件夹中没有可上传的文件；空文件夹不会被上传')
    return
  }

  let batches
  try {
    const entries = collectFolderUploadEntries(files)
    validateFolderUploadEntries(entries)
    batches = buildFolderUploadBatches(entries)
  } catch (error) {
    uploadError.value = error?.message || '文件夹内容无效'
    message.error(uploadError.value)
    input.value = ''
    return
  }

  const parentPath = currentPath.value
  uploadingFile.value = true
  uploadError.value = ''
  let uploadedCount = 0
  let failed = null
  try {
    for (const [index, batch] of batches.entries()) {
      try {
        await uploadWorkspaceFiles(
          parentPath,
          batch.map((entry) => entry.file),
          batch.map((entry) => entry.relativePath)
        )
        uploadedCount += batch.length
      } catch (error) {
        failed = { index, batch, error }
        break
      }
    }
    if (activeSourceKey.value === 'personal' && currentPath.value === parentPath) {
      await loadWorkspaceEntries(parentPath)
    }
    if (failed) {
      uploadError.value = formatFolderUploadFailure(failed, batches.length, uploadedCount)
      message.error(uploadError.value)
    } else {
      message.success(`${uploadedCount} 个文件上传成功`)
    }
  } finally {
    uploadingFile.value = false
    input.value = ''
  }
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
    console.warn('删除个人空间文件失败:', error)
    message.error(error?.message || '删除失败')
    await loadWorkspaceEntries(currentPath.value)
  } finally {
    deletingPaths.value = []
  }
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
  isPreviewResizing.value = false
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
  isPreviewResizing.value = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('pointermove', resizePreview)
  window.addEventListener('pointerup', stopPreviewResize)
  window.addEventListener('pointercancel', stopPreviewResize)
}

let workspaceResizeObserver = null
let workspaceMounted = false

onMounted(async () => {
  await Promise.all([loadWorkspaceEntries('/'), loadKnowledgeBases()])

  if (workspaceMainRef.value && typeof ResizeObserver !== 'undefined') {
    workspaceMainWidth.value = workspaceMainRef.value.clientWidth || 0
    workspaceResizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      workspaceMainWidth.value = entry.contentRect.width
    })
    workspaceResizeObserver.observe(workspaceMainRef.value)
  }

  // 侧边栏全局搜索跳转后打开指定文件
  await openFileByPath(route.query.open)
  workspaceMounted = true
})

onActivated(async () => {
  workspaceMainWidth.value = workspaceMainRef.value?.clientWidth || 0
  workspaceActive.value = true
  if (!workspaceMounted || activeSourceKey.value !== 'personal') return
  await loadWorkspaceEntries(currentPath.value)
  if (!selectedEntry.value?.path) return
  const refreshedEntry = entries.value.find((entry) => entry.path === selectedEntry.value.path)
  if (refreshedEntry) {
    await loadWorkspacePreview(refreshedEntry)
  } else {
    closePreview()
  }
})

onDeactivated(() => {
  workspaceActive.value = false
  stopPreviewResize()
})

watch(
  () => route.query.open,
  (path) => {
    if (path) openFileByPath(path)
  }
)

onUnmounted(() => {
  workspaceResizeObserver?.disconnect()
  workspaceResizeObserver = null
  stopPreviewResize()
  revokePreviewObjectUrl()
})

watch([useInlinePreview, workspaceActive], ([isInline, isActive]) => {
  // KeepAlive 隐藏页面后的零宽度不代表用户切换到窄屏。
  if (!isActive) return
  if (!previewFile.value) {
    previewModalVisible.value = false
    inlinePreviewVisible.value = false
    return
  }

  if (isInline) {
    previewModalVisible.value = false
    inlinePreviewVisible.value = true
    return
  }

  if (inlinePreviewVisible.value) {
    previewModalVisible.value = true
    inlinePreviewVisible.value = false
  }
})
</script>

<style scoped lang="less">
.upload-error {
  margin: 12px;
  overflow-wrap: anywhere;
}

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
  --workspace-sidebar-width: clamp(240px, 15vw, 280px);
  grid-template-columns: var(--workspace-sidebar-width) minmax(0, 1fr);
  transition: grid-template-columns 220ms ease;
  flex: 1 1 auto;
  min-height: 0;
  background: var(--bg-page);
  overflow: hidden;

  &.is-sidebar-collapsed {
    grid-template-columns: 0px minmax(0, 1fr);
  }
}

.workspace-sidebar-slot {
  position: relative;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  opacity: 1;
  visibility: visible;
  transition: opacity 180ms ease, visibility 0s;
}

.is-sidebar-collapsed .workspace-sidebar-slot {
  opacity: 0;
  visibility: hidden;
  transition: opacity 180ms ease, visibility 0s 220ms;
}

.workspace-sidebar-slot :deep(.workspace-sidebar) {
  width: var(--workspace-sidebar-width);
  height: 100%;
}

@media (max-width: 1023px) {
  .workspace-shell {
    --workspace-sidebar-width: 216px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .workspace-shell,
  .workspace-sidebar-slot,
  .is-sidebar-collapsed .workspace-sidebar-slot {
    transition: none;
  }
}

.workspace-main {
  display: flex;
  flex-direction: row;
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
  position: relative;
}

:deep(.workspace-list-shell) {
  flex: 1 1 0;
  min-width: 0;
  height: 100%;
}

.workspace-preview-panel {
  position: relative;
  display: flex;
  flex-direction: row;
  height: 100%;
  min-height: 0;
  flex-shrink: 0;
  min-width: 0;
  overflow: hidden;
  will-change: width, opacity, transform;
  transition:
    width 0.28s cubic-bezier(0.16, 1, 0.3, 1),
    opacity 0.22s ease,
    transform 0.28s cubic-bezier(0.16, 1, 0.3, 1);

  &.is-resizing {
    transition: none !important;
  }

  :deep(.workspace-preview-pane) {
    flex: 1 1 auto;
    min-width: 280px;
    height: 100%;
  }
}

.workspace-preview-slide-enter-active {
  transition:
    width 0.28s cubic-bezier(0.16, 1, 0.3, 1),
    opacity 0.22s ease,
    transform 0.28s cubic-bezier(0.16, 1, 0.3, 1);
  overflow: hidden;
  min-width: 0 !important;
}

.workspace-preview-slide-leave-active {
  transition:
    width 0.24s cubic-bezier(0.16, 1, 0.3, 1),
    opacity 0.18s ease,
    transform 0.24s cubic-bezier(0.16, 1, 0.3, 1);
  overflow: hidden;
  min-width: 0 !important;
}

.workspace-preview-slide-enter-from,
.workspace-preview-slide-leave-to {
  width: 0 !important;
  min-width: 0 !important;
  opacity: 0;
  transform: translateX(16px);
}

.workspace-preview-resizer {
  width: 1px;
  min-width: 1px;
  background: var(--gray-100);
  cursor: col-resize;
  flex-shrink: 0;
  height: 100%;
  transition: background 0.15s ease;

  &:hover,
  &:active {
    background: var(--main-400);
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

.agents-guide-content {
  color: var(--gray-700);
  font-size: 14px;
  line-height: 1.7;

  p {
    margin: 0 0 12px;
  }

  code {
    padding: 2px 5px;
    border-radius: 4px;
    background: var(--gray-50);
    color: var(--main-700);
    font-size: 13px;
  }
}

.agents-guide-section {
  margin-top: 16px;
  padding: 14px 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);

  h3 {
    margin: 0 0 10px;
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 600;
  }

  ul {
    margin: 0;
    padding-left: 18px;
  }

  li + li {
    margin-top: 6px;
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
