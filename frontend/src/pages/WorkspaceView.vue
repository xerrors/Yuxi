<template>
  <div class="workspace-view layout-container">
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

    <div class="workspace-shell">
      <WorkspaceSidebar
        :active-key="activeSourceKey"
        :current-path="currentPath"
        :knowledge-bases="knowledgeBases"
        :knowledge-list-status="knowledgeListStatus"
        :current-uid="userStore.uid"
        @select-overview="selectOverview"
        @select-personal="selectWorkspacePath('/')"
        @select-knowledge-base="selectKnowledgeBase"
        @reload-knowledge-bases="loadKnowledgeBases"
        @select-path="selectWorkspacePath"
      />
      <main class="workspace-main">
        <a-alert
          v-if="uploadError && !isKnowledgeSource" class="upload-error" type="error"
          :message="uploadError" show-icon closable @close="uploadError = ''"
        />
        <WorkspaceOverview
          v-if="activeSourceKey === 'overview'" v-show="!previewFile" ref="overviewRef"
          @open-file="loadWorkspacePreview" @open-artifact="loadArtifactPreview" @open-directory="selectWorkspacePath"
        >
          <template #actions>
            <WorkspaceActions :uploading="uploadingFile" writable v-on="toolbarEvents" />
          </template>
        </WorkspaceOverview>
        <WorkspaceFileList
          v-else v-show="!previewFile"
          :entries="entries"
          :current-path="currentPath"
          :selected-path="selectedEntry?.path || ''"
          :selected-paths="selectedPaths"
          :deleting-paths="deletingPaths"
          :selection-mode="selectionMode"
          :loading="loadingTree"
          :readonly="isKnowledgeSource"
          :root-label="selectedKnowledgeBase?.name || '全部文件'"
          :breadcrumb-items="isKnowledgeSource ? knowledgeBreadcrumbItems : workspaceBreadcrumbItems"
          :pagination="isKnowledgeSource ? knowledgePagination : null"
          :uploadable="personalUploadEnabled"
          @select-entry="handleSelectEntry"
          @breadcrumb-click="handleListBreadcrumbClick"
          @update:selected-paths="selectedPaths = $event"
          @update:selection-mode="handleSelectionModeChange"
          @delete-selected="confirmDeleteEntries(selectedEntries)"
          @delete-entry="(entry) => confirmDeleteEntries([entry])"
          @download-entry="downloadEntry"
          @page-change="handleKnowledgePageChange"
          @upload-drop="handleDropUpload"
        >
          <template #actions>
            <a-button v-if="isAgentsWorkspacePath" class="workspace-toolbar-icon lucide-icon-btn" title="Agents 目录说明" aria-label="Agents 目录说明" @click="openAgentsGuideModal">
              <CircleHelp :size="16" />
            </a-button>
            <WorkspaceActions :uploading="uploadingFile" :writable="personalUploadEnabled" v-on="toolbarEvents" />
          </template>
        </WorkspaceFileList>
        <WorkspacePreviewPane
          v-if="previewFile" :file="previewFile" :file-path="selectedPreviewPath"
          :editable="previewEditable" :saving="savingPreviewFile"
          @close="returnFromPreview" @download="downloadEntry(selectedEntry)" @save="handleSavePreviewFile"
        />
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

  </div>
</template>

<script setup>
import { computed, nextTick, onActivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { CircleHelp } from '@lucide/vue'
import WorkspaceActions from '@/modules/workspace/ui/WorkspaceActions.vue'
import WorkspaceOverview from '@/modules/workspace/ui/WorkspaceOverview.vue'
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
import { threadApi } from '@/apis/agent_api'
import { knowledgeBaseApi } from '@/apis/knowledge_api'
import { useUserStore } from '@/modules/identity'
import {
  createWorkspaceDirectory,
  deleteWorkspacePath,
  downloadWorkspaceFile,
  downloadWorkspaceKnowledgeFile,
  getWorkspaceFileContent,
  openWorkspaceFile,
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

const activeSourceKey = ref('overview')
const overviewRef = ref(null)
const currentPath = ref('/')
const fileSearchOpen = ref(false)

const searchWorkspace = (query) => searchWorkspaceFiles(query)

// 搜索命中后切到文件所在目录并打开预览
const handleFileSearchSelect = async (entry) => {
  if (!entry?.path) return
  if (activeSourceKey.value === 'overview') {
    await loadWorkspacePreview(entry)
  } else {
    await openFileByPath(entry.path)
  }
}

// 侧边栏全局搜索选中工作区文件后，通过 query 跳转打开对应文件
const openFileByPath = async (path) => {
  if (!path) return
  const parentPath = String(path).replace(/\/[^/]+$/, '') || '/'
  await selectWorkspacePath(parentPath)
  if (activeSourceKey.value !== 'personal' || currentPath.value !== parentPath) return
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
const loadingTree = ref(false)
const savingPreviewFile = ref(false)
const knowledgeListStatus = ref('loading')
const knowledgeBases = ref([])
const selectedKnowledgeBase = ref(null)
const createDirectoryModalVisible = ref(false)
const agentsGuideModalVisible = ref(false)
const newDirectoryName = ref('')
const creatingDirectory = ref(false)
const uploadingFile = ref(false)
const uploadError = ref('')
const uploadInputRef = ref(null)
const folderUploadInputRef = ref(null)
const deletingPaths = ref([])
const previewRequestId = ref(0)
const AGENTS_WORKSPACE_PATH = '/agents'
const knowledgeFileBrowser = reactive({
  parentId: null,
  page: 1,
  pageSize: 100,
  total: 0,
  hasMore: false
})

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
const personalUploadEnabled = computed(() => activeSourceKey.value === 'personal')
const previewEditable = computed(() => !isKnowledgeSource.value && selectedEntry.value?.source !== 'artifact' && !selectedEntry.value?.readonly)
const uploadTarget = computed(() => activeSourceKey.value === 'overview' ? '/' : currentPath.value)
const selectedPreviewPath = computed(() =>
  selectedEntry.value?.source === 'knowledge' || selectedEntry.value?.source === 'artifact'
    ? selectedEntry.value.name || ''
    : selectedEntry.value?.path || ''
)
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

const buildPreviewLoadingFile = (entry) => ({
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

let previewTrigger = null
const startPreviewRequest = (entry) => {
  if (!previewFile.value) previewTrigger = document.activeElement
  const requestId = previewRequestId.value + 1
  previewRequestId.value = requestId
  selectedEntry.value = entry
  revokePreviewObjectUrl()
  previewFile.value = buildPreviewLoadingFile(entry)
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

/** 按来源读取预览，快速切换时只发布当前请求的内容。 */
const loadPreview = async (entry, fetchResponse) => {
  const requestId = startPreviewRequest(entry)
  try {
    const response = await fetchResponse()
    if (!isCurrentPreviewEntry(requestId, entry)) return
    const file = await normalizePreviewResponse(response, entry)
    applyPreviewFile(requestId, entry, file)
  } catch (error) {
    if (!isCurrentPreviewEntry(requestId, entry)) return
    console.warn('加载文件预览失败:', error)
    previewFile.value = buildPreviewErrorFile(entry, error)
    message.error(error?.message || '加载文件预览失败')
  }
}

const loadWorkspacePreview = (entry) => loadPreview(entry, () => openWorkspaceFile(entry.path))

const loadKnowledgePreview = (entry) => loadPreview(entry, () => getWorkspaceKnowledgeFileContent(entry.kb_id, entry.file_id))

const loadArtifactPreview = (entry) => loadPreview({
  ...entry,
  source: 'artifact',
  resourceBaseUrl: threadApi.getThreadArtifactUrl(entry.thread_id, entry.path)
}, () => threadApi.previewThreadArtifact(entry.thread_id, entry.path))

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

let directoryRequestId = 0
const loadWorkspaceEntries = async (path = '/') => {
  const requestId = ++directoryRequestId
  currentPath.value = path
  workspaceBreadcrumbItems.value = buildWorkspaceBreadcrumbItems()
  loadingTree.value = true
  try {
    const response = await getWorkspaceTree(path)
    if (requestId !== directoryRequestId) return
    entries.value = response.entries || []
    knowledgeBreadcrumbItems.value = []
    syncSelectedPaths()
    if (!selectedPaths.value.length) {
      selectionMode.value = false
    }
  } catch (error) {
    if (requestId !== directoryRequestId) return
    entries.value = []
    console.warn('加载个人空间目录失败:', error)
    message.error('加载个人空间目录失败')
  } finally {
    if (requestId === directoryRequestId) loadingTree.value = false
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

  const requestId = ++directoryRequestId
  loadingTree.value = true
  try {
    const response = await getWorkspaceKnowledgeTree(knowledgeBase.kb_id, {
      parentId,
      page,
      pageSize
    })
    if (requestId !== directoryRequestId) return
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
    if (requestId !== directoryRequestId) return
    console.warn('加载知识库目录失败:', error)
    entries.value = []
    message.error(error?.message || '加载知识库目录失败')
  } finally {
    if (requestId === directoryRequestId) loadingTree.value = false
  }
}

let knowledgeListRequestId = 0
/** 返回缓存页面时重新核对当前用户可浏览的文档库。 */
const loadKnowledgeBases = async () => {
  const requestId = ++knowledgeListRequestId
  knowledgeListStatus.value = 'loading'
  try {
    const response = await knowledgeBaseApi.getAccessibleKnowledgeBases()
    if (requestId !== knowledgeListRequestId) return
    knowledgeBases.value = response.knowledge_bases.filter(kb => kb.supports_documents !== false)
    knowledgeListStatus.value = 'ready'
  } catch (error) {
    if (requestId !== knowledgeListRequestId) return
    knowledgeBases.value = []
    // Knowledge 关闭时，服务端不会挂载可访问知识库集合路由。
    knowledgeListStatus.value = error?.status === 404 ? 'unavailable' : 'error'
    if (knowledgeListStatus.value === 'error') return
  }
  if (selectedKnowledgeBase.value && !knowledgeBases.value.some(kb => kb.kb_id === selectedKnowledgeBase.value.kb_id)) {
    selectOverview()
  }
}

const selectOverview = () => {
  ++directoryRequestId
  loadingTree.value = false
  closePreview()
  activeSourceKey.value = 'overview'
  selectedKnowledgeBase.value = null
  clearWorkspaceSelection()
  overviewRef.value?.refresh()
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
  selectedEntry.value = null
  previewFile.value = null
  revokePreviewObjectUrl()
}

const returnFromPreview = async () => {
  closePreview()
  if (activeSourceKey.value === 'overview') await overviewRef.value?.refresh()
  await nextTick()
  if (!previewFile.value && previewTrigger?.isConnected) previewTrigger.focus()
  previewTrigger = null
}

const handleSavePreviewFile = async (content) => {
  if (!previewEditable.value) {
    message.warning('当前文件为只读，无法保存')
    return
  }
  if (!selectedEntry.value?.path || savingPreviewFile.value) return

  savingPreviewFile.value = true
  try {
    const entry = selectedEntry.value
    const requestId = previewRequestId.value
    const response = await saveWorkspaceFileContent(entry.path, content)
    if (!isCurrentPreviewEntry(requestId, entry)) return
    if (response.entry) selectedEntry.value = response.entry
    previewFile.value = { ...previewFile.value, content }
    if (activeSourceKey.value === 'personal') await loadWorkspaceEntries(currentPath.value)
    message.success('文件保存成功')
  } catch (error) {
    console.warn('保存个人空间文件失败:', error)
    message.error(error?.message || '文件保存失败')
  } finally {
    savingPreviewFile.value = false
  }
}

const openCreateDirectoryModal = () => {
  if (isKnowledgeSource.value) return
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
    const target = uploadTarget.value
    await createWorkspaceDirectory(target, directoryName)
    if (activeSourceKey.value === 'personal' && currentPath.value === target) await loadWorkspaceEntries(target)
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
  if (isKnowledgeSource.value || uploadingFile.value)
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
    const target = uploadTarget.value
    await uploadWorkspaceFiles(target, files)
    if (activeSourceKey.value === 'personal' && currentPath.value === target) await loadWorkspaceEntries(target)
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
  if (isKnowledgeSource.value || uploadingFile.value)
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
  if (!personalUploadEnabled.value || uploadingFile.value) return
  const plainFiles = roots?.files || []
  const directories = roots?.directories || []
  if (!plainFiles.length && !directories.length) return
  if (plainFiles.length > WORKSPACE_UPLOAD_MAX_FILES) {
    message.warning(`一次最多上传 ${WORKSPACE_UPLOAD_MAX_FILES} 个文件`)
    return
  }

  const parentPath = uploadTarget.value
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

  const parentPath = uploadTarget.value
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
        : entry.source === 'artifact'
          ? await threadApi.downloadThreadArtifact(entry.thread_id, entry.path)
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

const toolbarEvents = {
  search: () => { fileSearchOpen.value = true },
  'upload-file': openUploadFilePicker,
  'upload-folder': openUploadFolderPicker,
  'create-directory': openCreateDirectoryModal
}

let workspaceMounted = false
onMounted(() => {
  loadKnowledgeBases()
  nextTick(() => { workspaceMounted = true })
})

onActivated(async () => {
  if (!workspaceMounted) return
  void loadKnowledgeBases()
  // 路由携带打开请求时，目录加载和预览由下方的 watcher 独占。
  if (route.query.open) return
  const entry = selectedEntry.value
  if (activeSourceKey.value === 'overview') {
    if (!entry) await overviewRef.value?.refresh()
    else if (entry.source === 'artifact') await loadArtifactPreview(entry)
    else await loadPreview(entry, () => getWorkspaceFileContent(entry.path))
    return
  }
  if (activeSourceKey.value !== 'personal') return
  await loadWorkspaceEntries(currentPath.value)
  if (!entry || selectedEntry.value !== entry) return
  if (entries.value.some(item => item.path === entry.path)) {
    await loadPreview(entry, () => getWorkspaceFileContent(entry.path))
  } else {
    closePreview()
  }
})

watch(() => route.path === '/workspace' && route.query.open, (path) => {
  if (path) openFileByPath(path)
}, { immediate: true })

onUnmounted(closePreview)
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
  --workspace-header-content-height: 44px;
  --workspace-sidebar-width: clamp(188px, 15vw, 240px);
  grid-template-columns: var(--workspace-sidebar-width) minmax(0, 1fr);
  flex: 1 1 auto;
  min-height: 0;
  background: var(--gray-0);
  overflow: hidden;
}

.workspace-main {
  --workspace-content-inset: var(--page-padding);
  --workspace-action-size: 32px;
  container-type: inline-size;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.workspace-main > :deep(.workspace-list-shell),
.workspace-main > :deep(.workspace-overview),
.workspace-main > :deep(.workspace-preview-pane) { flex: 1; min-height: 0; }

.workspace-main :deep(.workspace-overview .page-header),
.workspace-main :deep(.workspace-file-list .file-browser-header) {
  min-height: calc(var(--workspace-header-content-height) + 1px);
  padding: 6px var(--workspace-content-inset);
  flex-direction: row;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;
}

.workspace-main :deep(.workspace-overview .page-header-left) { width: auto; }
.workspace-main :deep(.workspace-file-list .file-browser-breadcrumb-label) {
  color: var(--gray-600);
}
.workspace-main :deep(.workspace-file-list .file-browser-breadcrumb-item.active .file-browser-breadcrumb-label) {
  color: var(--gray-2000);
  font-size: 16px;
  font-weight: 600;
}

.workspace-main :deep(.workspace-overview .page-header-right),
.workspace-main :deep(.workspace-file-list .file-browser-actions) {
  width: auto;
  max-width: 100%;
  margin-left: auto;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.workspace-main :deep(.workspace-overview .page-header-right .ant-btn),
.workspace-main :deep(.workspace-file-list .file-browser-actions .ant-btn) {
  height: var(--workspace-action-size);
}

.workspace-main :deep(.workspace-toolbar-icon) {
  width: var(--workspace-action-size);
  padding: 0;
}

@media (max-width: 1023px) {
  .workspace-shell { --workspace-sidebar-width: 164px; }
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
