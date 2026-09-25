<template>
  <a-modal
    v-model:open="visible"
    width="800px"
    :footer="null"
    :closable="false"
    wrap-class-name="file-detail"
    @after-open-change="afterOpenChange"
    :bodyStyle="{ height: '80vh', padding: '0' }"
  >
    <template #title>
      <div class="modal-title-wrapper">
        <!-- 左侧：文件名和图标 -->
        <div class="file-title">
          <FileTypeIcon :name="file?.filename" :size="18" />
          <span class="file-name">{{ file?.filename || '文件详情' }}</span>
        </div>

        <div class="header-controls">
          <!-- 字符数/片段数显示在 segment 左边 -->
          <span v-if="viewInfoText" class="view-info">{{ viewInfoText }}</span>

          <!-- 视图模式切换：编辑期间禁用，否则切视图会静默丢弃草稿（见 isEditingMarkdown） -->
          <div class="view-controls" v-if="file && viewModeOptions.length > 1">
            <a-segmented v-model:value="viewMode" :options="viewModeOptions" :disabled="isEditingMarkdown" />
          </div>

          <!-- 编辑解析产物：与视图切换并列（不放进下载菜单，语义不同） -->
          <a-button
            v-if="canEditMarkdown && !isEditingMarkdown"
            type="text"
            title="编辑 Markdown"
            aria-label="编辑 Markdown"
            @click="startEditing"
          >
            <Pencil :size="16" />
          </a-button>

          <!-- 下载按钮下拉菜单 -->
          <a-dropdown trigger="click" v-if="file">
            <a-button type="default" class="download-btn" title="下载" aria-label="下载">
              <Download :size="16" />
              <ChevronDown :size="14" />
            </a-button>
            <template #overlay>
              <a-menu @click="handleDownloadMenuClick">
                <a-menu-item key="original" :disabled="!file.file_id">
                  <template #icon><Download :size="16" /></template>
                  下载原文
                </a-menu-item>
                <a-menu-item key="markdown" :disabled="contentState.loading || !mergedContent">
                  <template #icon><FileText :size="16" /></template>
                  下载 Markdown
                </a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>

          <!-- 自定义关闭按钮 -->
          <button class="custom-close-btn" @click="handleClose">
            <X :size="16" />
          </button>
        </div>
      </div>
    </template>
    <div v-if="basicLoading" class="loading-container">
      <a-spin tip="正在加载文档内容..." />
    </div>
    <div v-else-if="detailError" class="empty-content">
      <p>{{ detailError }}</p>
    </div>
    <div v-else-if="file && hasAvailableView" class="file-detail-content">
      <div v-if="viewMode === 'source'" class="content-panel source-panel">
        <AgentFilePreview
          :file="sourcePreviewFile"
          :file-path="file?.filename || ''"
          :status="sourcePreview.loading ? 'loading' : ''"
          loading-message="正在加载文件内容..."
          :show-header="false"
          :show-download="false"
          :show-inline-html-controls="true"
          :full-height="true"
          :borderless="true"
          container-class="source-preview-container"
          content-class="source-preview-content"
        />
      </div>

      <!-- Markdown 模式：预览与编辑都交给 AgentFilePreview（与 source 视图、工作区预览同一套）。
           编辑态由它内部管理，保存期间它会禁用 textarea，因此不存在"回包覆盖新输入"的窗口。 -->
      <div v-else-if="viewMode === 'markdown'" class="content-panel flat-md-preview">
        <AgentFilePreview
          ref="parsedPreviewRef"
          :file="parsedPreviewFile"
          :file-path="file?.filename || ''"
          :editable="canEditMarkdown"
          :edit-all-text="true"
          :saving="savingMarkdown"
          :show-header="false"
          :show-download="false"
          :full-height="true"
          :borderless="true"
          container-class="parsed-preview-container"
          content-class="parsed-preview-content"
          @save="saveMarkdown"
        />
      </div>

      <!-- Chunks 模式：使用 Grid 布局 -->
      <div v-else-if="viewMode === 'chunks'" class="chunks-panel">
        <div v-if="contentState.loading" class="loading-container">
          <a-spin tip="正在加载分块内容..." />
        </div>
        <div v-else class="chunk-grid">
          <div v-for="chunk in mappedChunks" :key="chunk.id" class="chunk-card">
            <div class="chunk-card-header">
              <span class="chunk-order">#{{ chunk.chunk_order_index }}</span>
            </div>
            <div class="chunk-card-content">
              {{ chunk.content.replace(/\n+/g, ' ') }}
            </div>
          </div>
        </div>
        <div v-if="!contentState.loading && mappedChunks.length === 0" class="empty-content">
          <p>{{ contentState.error || '暂无分块信息' }}</p>
        </div>
      </div>
    </div>

    <div v-else-if="file" class="empty-content">
      <p>暂无文件内容</p>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, h, onBeforeUnmount, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { documentApi } from '@/apis/knowledge_api'
import { getWorkspaceKnowledgeFileContent } from '@/apis/workspace_api'
import { mergeChunks } from '@/utils/chunkUtils'
import { getPreviewTypeByPath, normalizePreviewResponse } from '@/utils/file_preview'
import { parseDownloadFilename } from '@/utils/file_utils'
import {
  canEditParsedContent,
  canPreviewChunks,
  canPreviewOriginal,
  canPreviewParsed,
  getDefaultDetailView
} from '@/utils/knowledge_file_policy'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'
import AgentFilePreview from '@/components/AgentFilePreview.vue'
import { Download, ChevronDown, FileSearch, FileText, Pencil, Rows3, X } from '@lucide/vue'

const props = defineProps({
  open: {
    type: Boolean,
    default: false
  },
  kbId: {
    type: [String, Number],
    default: ''
  },
  fileId: {
    type: [String, Number],
    default: ''
  },
  // 是否允许编辑解析产物。默认关闭：检索结果面板等场景不传，避免
  // 「在检索结果里编辑却没有列表可刷新」的死角。
  editable: {
    type: Boolean,
    default: false
  },
  // 打开时是否直接进入编辑态（文件行菜单的「编辑文件」会置位）
  startInEdit: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:open', 'closed', 'saved'])

const visible = computed({
  get: () => props.open,
  // 关闭请求统一走脏检查：a-modal 的 ESC 与点击遮罩都会写这个 model，
  // 若直接透传就会绕过下面的确认逻辑，静默丢弃未保存的草稿。
  set: (value) => (value ? emit('update:open', true) : requestClose())
})

const file = ref(null)
const basicLoading = ref(false)
const detailError = ref('')
const downloadingOriginal = ref(false)
const downloadingMarkdown = ref(false)
const contentState = ref({
  loading: false,
  loaded: false,
  lines: [],
  content: '',
  // 读取内容时服务端返回的内容修订；保存时必须原样回传，用于检出并发修改
  revision: '',
  error: ''
})
const sourcePreview = ref({
  loading: false,
  url: '',
  content: '',
  type: '',
  message: '',
  supported: true
})

let basicRequestSeq = 0
let contentRequestSeq = 0
let sourceRequestSeq = 0
// 保存的上下文序号：关闭弹窗或切换到另一份文档时自增，让在飞的保存响应作废
let markdownSaveSeq = 0

const revokeSourcePreviewUrl = () => {
  if (sourcePreview.value.url) {
    window.URL.revokeObjectURL(sourcePreview.value.url)
    sourcePreview.value.url = ''
  }
}

const resetContentState = () => {
  contentState.value = {
    loading: false,
    loaded: false,
    lines: [],
    content: '',
    revision: '',
    error: ''
  }
}

const resetSourcePreview = () => {
  sourceRequestSeq += 1
  revokeSourcePreviewUrl()
  sourcePreview.value = {
    loading: false,
    url: '',
    content: '',
    type: '',
    message: '',
    supported: true
  }
}

// ── 解析产物编辑（入库前复核；只对待入库文件开放）──────────────
// 编辑态与草稿由 AgentFilePreview 自己管理（复用工作区/source 视图同一套编辑器，
// 它在保存期间禁用 textarea，因此不存在"回包覆盖等待期新输入"的窗口）。
// 这里只保留本弹窗需要的两个状态：保存中、以及「打开即编辑」的一次性意图。
const savingMarkdown = ref(false)
// 「打开即编辑」是一次性意图：消费后即失效。否则保存时 file.value 更新会再次触发
// 内容 watch，把刚落地的保存又拉回编辑态（保存按钮还是灰的，用户会以为没存上）。
const pendingStartEdit = ref(false)

const parsedPreviewRef = ref(null)

/** 解析产物视图的数据源：预览与编辑共用（内容为合并后的 Markdown） */
const parsedPreviewFile = computed(() => {
  if (!file.value) return null
  return {
    ...file.value,
    content: mergedContent.value,
    previewType: 'markdown',
    supported: true,
    status: contentState.value.loading ? 'loading' : contentState.value.error ? 'error' : 'ready',
    errorMessage: contentState.value.error,
    loadingMessage: '正在加载解析内容...',
    message: contentState.value.error
  }
})

const canEditMarkdown = computed(
  () =>
    props.editable &&
    viewMode.value === 'markdown' &&
    canEditParsedContent(file.value) &&
    !contentState.value.loading &&
    // 保存时服务端要求回传修订，没有修订必然失败，入口本身就不该出现。
    // 修订来自文件行的 updated_at（随内容同一次读取返回），行在读到时就有值。
    Boolean(contentState.value.revision)
)

// 编辑是独占态：草稿存在 AgentFilePreview 的局部状态里，而 markdown 分支是 v-if 渲染的，
// 切到 source/chunks 会让它连同草稿一起被卸载且没有任何确认步骤。因此编辑期间禁用视图切换
// ——与「保存期间禁用输入」同一种收敛方式。
const isEditingMarkdown = computed(() => parsedPreviewRef.value?.editMode === 'edit')

const startEditing = () => {
  if (!canEditMarkdown.value) return
  parsedPreviewRef.value?.startEditing()
}

/** 关闭请求的唯一出口：自定义关闭按钮、ESC 与点击遮罩都经此，未保存的草稿都要先确认 */
const requestClose = () => {
  // 保存中不弹「放弃未保存的修改」：服务端已经在写，此时说「关闭后将丢失」与事实相反
  if (savingMarkdown.value) {
    message.info('正在保存，请稍候')
    return
  }
  // 草稿由 AgentFilePreview 持有，它把 draftChanged 暴露出来供宿主判断
  if (!parsedPreviewRef.value?.draftChanged) {
    emit('update:open', false)
    return
  }
  Modal.confirm({
    title: '放弃未保存的修改？',
    content: '当前编辑内容尚未保存，关闭后将丢失。',
    okText: '放弃并关闭',
    okButtonProps: { danger: true },
    cancelText: '继续编辑',
    onOk: () => {
      emit('update:open', false)
    }
  })
}

const handleClose = () => requestClose()

/** 保存解析产物。content 是 AgentFilePreview 提交那一刻的全文快照——
 *  它在保存期间禁用 textarea，所以不存在"等待期继续输入"的窗口。 */
const saveMarkdown = async (content) => {
  if (savingMarkdown.value) return

  // 与 basic/content 同一套序号守卫：保存是弹窗里最慢的一次往返，若返回时弹窗已关闭
  // 或已换文档，旧响应不得把新上下文的 meta、修订和提示改掉
  const requestId = ++markdownSaveSeq
  savingMarkdown.value = true
  try {
    const data = await documentApi.updateDocumentContent(
      props.kbId,
      props.fileId,
      content,
      contentState.value.revision
    )
    if (requestId !== markdownSaveSeq) return
    ensureApiSuccess(data, '保存解析内容失败')
    const nextMeta = normalizeFileMeta(data?.meta || {})
    file.value = nextMeta
    // 保存后产物换成新内容，修订也要跟着换：服务端返回的是刚写入内容的修订，
    // 不回填的话用户连续第二次保存会拿旧修订去比对，必然 409
    contentState.value = {
      loading: false,
      loaded: true,
      lines: contentState.value.lines,
      content,
      revision: data?.content_revision || '',
      error: ''
    }
    message.success('已保存')
    emit('saved', { meta: nextMeta })
  } catch (error) {
    if (requestId !== markdownSaveSeq) return
    // apis/base.js 会把非 422 的响应 detail 统一替换成公共文案（409 →「请求冲突」），
    // 所以冲突要在这里补一条可操作提示：几乎总是「产物已被他人改动」
    message.error(
      error?.status === 409
        ? '解析产物已被其他人修改，请关闭后重新打开编辑再保存'
        : `保存失败，请重新打开编辑后再试${error?.message ? `（${error.message}）` : ''}`
    )
  } finally {
    if (requestId === markdownSaveSeq) savingMarkdown.value = false
  }
}

const resetLocalState = () => {
  basicRequestSeq += 1
  contentRequestSeq += 1
  file.value = null
  basicLoading.value = false
  detailError.value = ''
  downloadingOriginal.value = false
  downloadingMarkdown.value = false
  resetContentState()
  resetSourcePreview()
  viewMode.value = 'markdown'
  savingMarkdown.value = false
}

const normalizeFileMeta = (meta = {}) => ({
  ...meta,
  file_id: meta.file_id || String(props.fileId || ''),
  kb_id: meta.kb_id || String(props.kbId || ''),
  filename: meta.filename || meta.original_filename || String(props.fileId || ''),
  file_size: meta.file_size ?? meta.size ?? 0,
  has_original_file:
    'has_original_file' in meta
      ? Boolean(meta.has_original_file)
      : Boolean(meta.minio_url || meta.path),
  has_parsed_markdown:
    'has_parsed_markdown' in meta ? Boolean(meta.has_parsed_markdown) : Boolean(meta.markdown_file)
})

const ensureApiSuccess = (data, fallbackMessage) => {
  if (data?.status === 'failed') {
    throw new Error(data.message || fallbackMessage)
  }
}

// 视图模式
const viewMode = ref('markdown')
const hasContent = computed(
  () =>
    (contentState.value.lines && contentState.value.lines.length > 0) || contentState.value.content
)
const sourcePreviewCandidateType = computed(() => getPreviewTypeByPath(file.value?.filename || ''))
const sourcePreviewDisplayType = computed(
  () => sourcePreview.value.type || sourcePreviewCandidateType.value
)
const sourceContentLength = computed(() =>
  typeof sourcePreview.value.content === 'string' ? sourcePreview.value.content.length : 0
)
const sourcePreviewFile = computed(() => {
  if (!file.value) return null
  const isError = Boolean(
    sourcePreview.value.message &&
    !sourcePreview.value.supported &&
    !sourcePreview.value.content &&
    !sourcePreview.value.url
  )
  return {
    ...file.value,
    content: sourcePreview.value.content,
    previewType: sourcePreviewDisplayType.value,
    previewUrl: sourcePreview.value.url,
    supported: sourcePreview.value.supported,
    status: sourcePreview.value.loading
      ? 'loading'
      : isError
        ? 'error'
        : sourcePreview.value.supported === false
          ? 'unsupported'
          : 'ready',
    errorMessage: sourcePreview.value.message,
    loadingMessage: '正在加载文件内容...',
    message: sourcePreview.value.message
  }
})
const hasSourcePreview = computed(() => canPreviewOriginal(file.value))
const hasMarkdownPreview = computed(() => canPreviewParsed(file.value) || hasContent.value)
const hasChunkPreview = computed(() => canPreviewChunks(file.value))
const availableViewModes = computed(() => {
  const modes = []
  if (hasSourcePreview.value) modes.push('source')
  if (hasMarkdownPreview.value) modes.push('markdown')
  if (hasChunkPreview.value) modes.push('chunks')
  return modes
})
const hasAvailableView = computed(() => availableViewModes.value.length > 0)

const makeViewModeOption = (label, value, icon) => ({
  label: h(
    'span',
    {
      class: 'view-option-icon',
      title: label,
      'aria-label': label
    },
    [h(icon, { size: 15 })]
  ),
  value
})

const viewModeOptions = computed(() => {
  const optionMap = {
    source: makeViewModeOption('源文件', 'source', FileSearch),
    markdown: makeViewModeOption('Markdown', 'markdown', FileText),
    chunks: makeViewModeOption('Chunks', 'chunks', Rows3)
  }
  return availableViewModes.value.map((mode) => optionMap[mode])
})

const loadBasicInfo = async () => {
  const kbId = String(props.kbId || '')
  const fileId = String(props.fileId || '')
  if (!kbId || !fileId) return

  const requestId = ++basicRequestSeq
  contentRequestSeq += 1
  file.value = null
  detailError.value = ''
  resetContentState()
  resetSourcePreview()
  viewMode.value = 'markdown'
  basicLoading.value = true

  try {
    const data = await documentApi.getDocumentBasicInfo(kbId, fileId)
    if (requestId !== basicRequestSeq) return
    ensureApiSuccess(data, '加载文件信息失败')

    const nextFile = normalizeFileMeta(data?.meta || data)
    if (nextFile.is_folder) {
      detailError.value = '文件夹不支持详情预览'
      return
    }

    file.value = nextFile
    viewMode.value = getDefaultDetailView(nextFile)
  } catch (error) {
    if (requestId !== basicRequestSeq) return
    console.error('加载文件基本信息失败:', error)
    detailError.value = error.message || '加载文件信息失败'
    message.error(detailError.value)
  } finally {
    if (requestId === basicRequestSeq) {
      basicLoading.value = false
    }
  }
}

const loadParsedContent = async () => {
  if (!props.kbId || !props.fileId || contentState.value.loading || contentState.value.loaded)
    return

  const requestId = ++contentRequestSeq
  contentState.value = {
    ...contentState.value,
    loading: true,
    error: ''
  }

  try {
    const data = await documentApi.getDocumentContent(props.kbId, props.fileId)
    if (requestId !== contentRequestSeq) return
    ensureApiSuccess(data, '加载解析内容失败')
    contentState.value = {
      loading: false,
      loaded: true,
      lines: data?.lines || [],
      content: data?.content || '',
      revision: data?.content_revision || '',
      error: ''
    }
  } catch (error) {
    if (requestId !== contentRequestSeq) return
    console.error('加载解析内容失败:', error)
    const errorMessage = error.message || '加载解析内容失败'
    contentState.value = {
      loading: false,
      loaded: false,
      lines: [],
      content: '',
      revision: '',
      error: errorMessage
    }
    message.error(errorMessage)
  }
}

watch(
  () => [props.open, props.kbId, props.fileId],
  ([open]) => {
    // 换文档也走这里（open 不变、fileId 变），此时在飞的保存响应同样属于旧上下文。
    // 序号自增让旧响应作废，同时解除「保存中」——否则那次保存之后 textarea 会一直禁用
    markdownSaveSeq += 1
    savingMarkdown.value = false
    if (!open) {
      resetLocalState()
      pendingStartEdit.value = false
      return
    }
    // 每次打开时取一次父层意图，之后由内容 watch 消费
    pendingStartEdit.value = props.startInEdit
    loadBasicInfo()
  },
  { immediate: true }
)

watch(
  availableViewModes,
  (modes) => {
    if (modes.length > 0 && !modes.includes(viewMode.value)) {
      viewMode.value = modes[0]
    }
  },
  { immediate: true }
)

watch(
  [visible, file, viewMode],
  async ([open, currentFile, currentViewMode]) => {
    if (!open || !currentFile) return
    if (
      (currentViewMode === 'markdown' && canPreviewParsed(currentFile)) ||
      (currentViewMode === 'chunks' && canPreviewChunks(currentFile))
    ) {
      await loadParsedContent()
      // 从文件行菜单点「编辑文件」进来时，等内容就绪后直接进编辑态（仅消费一次）
      if (currentViewMode === 'markdown' && pendingStartEdit.value && canEditMarkdown.value) {
        pendingStartEdit.value = false
        startEditing()
      }
    }
  },
  { immediate: true }
)

watch(
  [visible, file, viewMode],
  async ([open, currentFile, currentViewMode]) => {
    if (!open || !currentFile || !hasSourcePreview.value || currentViewMode !== 'source') {
      if (!open || !hasSourcePreview.value) {
        resetSourcePreview()
      }
      return
    }

    await loadSourcePreview()
  },
  { immediate: true }
)

// 统计信息
const mergeResult = computed(() => mergeChunks(contentState.value.lines || []))
const mappedChunks = computed(() => mergeResult.value.chunks)
const mergedContent = computed(() => contentState.value.content || mergeResult.value.content || '')
const charCount = computed(() => mergedContent.value.length)
const chunkCount = computed(
  () => mappedChunks.value.length || contentState.value.lines?.length || 0
)
const viewInfoText = computed(() => {
  if (viewMode.value === 'chunks') {
    if (contentState.value.loading) return ''
    return `${chunkCount.value} 个片段`
  }
  if (viewMode.value === 'source') {
    if (sourcePreview.value.loading) return ''
    if (sourceContentLength.value > 0) return `${formatTextLength(sourceContentLength.value)} 字符`
    if (sourcePreview.value.url) return '源文件预览'
    return ''
  }
  if (contentState.value.loading) return ''
  return `${formatTextLength(charCount.value)} 字符`
})

// 格式化文本长度
function formatTextLength(length) {
  if (!length && length !== 0) return '0 字符'

  if (length < 1000) {
    return `${length}`
  } else {
    return `${(length / 1000).toFixed(1)}k`
  }
}

const afterOpenChange = (open) => {
  if (!open) {
    resetLocalState()
    emit('closed')
  }
}

const loadSourcePreview = async () => {
  if (!file.value?.file_id || !props.kbId || !props.fileId || !hasSourcePreview.value) return
  if (sourcePreview.value.url || sourcePreview.value.content || sourcePreview.value.message) return

  const requestId = ++sourceRequestSeq
  sourcePreview.value.loading = true
  try {
    const response = await getWorkspaceKnowledgeFileContent(props.kbId, props.fileId)
    const preview = await normalizePreviewResponse(response)
    if (requestId !== sourceRequestSeq) {
      if (preview.previewUrl) {
        window.URL.revokeObjectURL(preview.previewUrl)
      }
      return
    }
    revokeSourcePreviewUrl()
    sourcePreview.value.type = preview.previewType || sourcePreviewCandidateType.value
    sourcePreview.value.message = preview.message || ''
    sourcePreview.value.supported = preview.supported !== false
    sourcePreview.value.url = preview.previewUrl || ''
    sourcePreview.value.content = preview.content || ''
  } catch (error) {
    if (requestId !== sourceRequestSeq) return
    console.error('加载源文件预览失败:', error)
    sourcePreview.value.message = error.message || '加载源文件预览失败'
    sourcePreview.value.supported = false
    message.error(sourcePreview.value.message)
  } finally {
    if (requestId === sourceRequestSeq) {
      sourcePreview.value.loading = false
    }
  }
}

// 下载菜单点击处理
const handleDownloadMenuClick = ({ key }) => {
  if (key === 'original') {
    handleDownloadOriginal()
  } else if (key === 'markdown') {
    handleDownloadMarkdown()
  }
}

// 下载原文
const handleDownloadOriginal = async () => {
  if (!file.value || !props.kbId || !props.fileId) {
    message.error('文件信息不完整')
    return
  }

  downloadingOriginal.value = true
  try {
    const response = await documentApi.downloadDocument(props.kbId, props.fileId)

    const contentDisposition = response.headers.get('content-disposition')
    const filename = parseDownloadFilename(contentDisposition) || file.value.filename
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
    message.success('下载成功')
  } catch (error) {
    console.error('下载文件时出错:', error)
    message.error(error.message || '下载文件失败')
  } finally {
    downloadingOriginal.value = false
  }
}

// 下载 Markdown
const handleDownloadMarkdown = () => {
  const content = mergedContent.value

  if (!content) {
    message.error('没有可下载的 Markdown 内容')
    return
  }

  downloadingMarkdown.value = true
  try {
    // 生成文件名（如果原文件没有 .md 扩展名，则添加）
    let filename = file.value.filename || 'document.md'
    if (!filename.toLowerCase().endsWith('.md')) {
      // 移除原扩展名，添加 .md
      const lastDotIndex = filename.lastIndexOf('.')
      if (lastDotIndex > 0) {
        filename = filename.substring(0, lastDotIndex) + '.md'
      } else {
        filename = filename + '.md'
      }
    }

    // 创建 blob 并下载
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.style.display = 'none'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
    message.success('下载成功')
  } catch (error) {
    console.error('下载 Markdown 时出错:', error)
    message.error(error.message || '下载 Markdown 失败')
  } finally {
    downloadingMarkdown.value = false
  }
}

onBeforeUnmount(resetLocalState)
</script>

<style scoped>
.file-detail-content {
  height: 100%;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.content-panel,
.chunks-panel {
  flex: 1;
  overflow-y: auto;
  padding: 0;
  min-height: 0;
}

.source-panel {
  overflow: hidden;
}

:deep(.parsed-preview-container) {
  height: 100%;
  max-height: none;
}

:deep(.parsed-preview-content) {
  flex: 1 1 auto;
  max-height: none;
  min-height: 0;
}

:deep(.source-preview-container) {
  height: 100%;
  max-height: none;
}

:deep(.source-preview-content) {
  flex: 1 1 auto;
  max-height: none;
  min-height: 0;
}

:deep(.source-preview-content .html-preview),
:deep(.source-preview-content .pdf-preview) {
  display: block;
  height: 100%;
  min-height: 100%;
}

.markdown-content {
  min-height: 100%;
}

.loading-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 200px;
}

.empty-content {
  text-align: center;
  padding: 40px 0;
  color: var(--gray-400);
  width: 100%;
}

/* Chunks 面板样式 */
.chunk-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
}

.chunk-card {
  background: var(--gray-0);
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  padding: 12px;
  transition: all 0.2s ease;
}

.chunk-card:hover {
  border-color: var(--main-color);
  box-shadow: 0 2px 8px rgba(1, 97, 121, 0.1);
}

.chunk-card-header {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
}

.chunk-order {
  font-weight: 600;
  color: var(--main-color);
  font-size: 12px;
}

.chunk-card-content {
  font-size: 12px;
  color: var(--gray-600);
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
}
</style>

<style lang="less">
.file-detail {
  .ant-modal {
    top: 20px;
  }

  .ant-modal-header {
    .ant-modal-title {
      width: 100%;
    }
  }
}

.modal-title-wrapper {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-width: 0;
}

/* 文件标题样式 */
.file-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1 1 auto;
  min-width: 0;

  svg {
    flex: 0 0 auto;
  }
}

.file-name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  font-size: 15px;
  color: var(--gray-900);
}

.title-info {
  font-size: 13px;
  color: var(--gray-600);
  font-weight: 500;
}

.header-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
  margin-left: auto;
  min-width: 0;
}

/* 下载按钮样式 */
.download-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: auto;
  min-width: 48px;
  padding: 0 10px;
  height: 28px;
  line-height: 1;
  border-radius: 6px;
  gap: 4px;

  svg {
    flex: 0 0 auto;
    vertical-align: middle;
  }
}

/* 自定义关闭按钮 */
.custom-close-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
  color: var(--gray-500);
  transition: all 0.2s;

  &:hover {
    background: var(--gray-100);
    color: var(--gray-700);
  }
}

/* 视图切换控件 */
.view-controls {
  display: flex;
  align-items: center;
  flex: 0 0 auto;

  .ant-segmented {
    padding: 2px;
  }

  .ant-segmented-item {
    min-width: 30px;
  }

  .ant-segmented-item-label {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 24px;
    min-height: 24px;
    padding: 0 7px;
    line-height: 24px;
  }
}

.view-option-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
}

.view-info {
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--gray-500);
  white-space: nowrap;
}

/* 下拉菜单样式 */
.ant-dropdown-menu {
  border-radius: 8px;
  padding: 4px;
}

.ant-dropdown-menu-item {
  border-radius: 6px;
  display: flex;
  align-items: center;
  padding: 8px 12px;

  // svg {
  //   margin-right: 8px;
  // }
}
</style>
