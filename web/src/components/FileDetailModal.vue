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

          <!-- 视图模式切换 -->
          <div class="view-controls" v-if="file && viewModeOptions.length > 1">
            <a-segmented v-model:value="viewMode" :options="viewModeOptions" />
          </div>

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
                <a-menu-item
                  key="markdown"
                  :disabled="!((file.lines && file.lines.length > 0) || file.content)"
                >
                  <template #icon><FileText :size="16" /></template>
                  下载 Markdown
                </a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>

          <!-- 自定义关闭按钮 -->
          <button class="custom-close-btn" @click="visible = false">
            <X :size="16" />
          </button>
        </div>
      </div>
    </template>
    <div v-if="loading" class="loading-container">
      <a-spin tip="正在加载文档内容..." />
    </div>
    <div v-else-if="file && hasAvailableView" class="file-detail-content">
      <div v-if="viewMode === 'source'" class="content-panel source-panel">
        <div v-if="sourcePreview.loading" class="loading-container">
          <a-spin tip="正在加载源文件预览..." />
        </div>
        <AgentFilePreview
          v-else
          :file="sourcePreviewFile"
          :file-path="file?.filename || ''"
          :show-header="false"
          :show-download="false"
          :show-inline-html-controls="true"
          :full-height="true"
          :borderless="true"
          container-class="source-preview-container"
          content-class="source-preview-content"
        />
      </div>

      <!-- Markdown 模式 -->
      <div v-else-if="viewMode === 'markdown'" class="content-panel flat-md-preview">
        <MarkdownPreview v-if="mergedContent" :content="mergedContent" class="markdown-content" />
        <div v-else class="empty-content">
          <p>暂无文件内容</p>
        </div>
      </div>

      <!-- Chunks 模式：使用 Grid 布局 -->
      <div v-else-if="viewMode === 'chunks'" class="chunks-panel">
        <div class="chunk-grid">
          <div v-for="chunk in mappedChunks" :key="chunk.id" class="chunk-card">
            <div class="chunk-card-header">
              <span class="chunk-order">#{{ chunk.chunk_order_index }}</span>
            </div>
            <div class="chunk-card-content">
              {{ chunk.content.replace(/\n+/g, ' ') }}
            </div>
          </div>
        </div>
        <div v-if="mappedChunks.length === 0" class="empty-content">
          <p>暂无分块信息</p>
        </div>
      </div>
    </div>

    <div v-else-if="file" class="empty-content">
      <p>暂无文件内容</p>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, h, ref, watch } from 'vue'
import { useDatabaseStore } from '@/stores/database'
import { message } from 'ant-design-vue'
import { documentApi } from '@/apis/knowledge_api'
import { getWorkspaceKnowledgeFileContent } from '@/apis/workspace_api'
import { mergeChunks } from '@/utils/chunkUtils'
import { getPreviewTypeByPath, normalizePreviewResponse } from '@/utils/file_preview'
import {
  canPreviewChunks,
  canPreviewOriginal,
  canPreviewParsed,
  getDefaultDetailView
} from '@/utils/knowledge_file_policy'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'
import AgentFilePreview from '@/components/AgentFilePreview.vue'
import { Download, ChevronDown, FileSearch, FileText, Rows3, X } from 'lucide-vue-next'

const store = useDatabaseStore()

const visible = computed({
  get: () => store.state.fileDetailModalVisible,
  set: (value) => (store.state.fileDetailModalVisible = value)
})

const file = computed(() => store.selectedFile)
const loading = computed(() => store.state.fileDetailLoading)

const downloadingOriginal = ref(false)
const downloadingMarkdown = ref(false)
const sourcePreview = ref({
  loading: false,
  url: '',
  content: '',
  type: '',
  message: '',
  supported: true
})

const revokeSourcePreviewUrl = () => {
  if (sourcePreview.value.url) {
    window.URL.revokeObjectURL(sourcePreview.value.url)
    sourcePreview.value.url = ''
  }
}

const resetSourcePreview = () => {
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

// 视图模式
const viewMode = ref('markdown')
const hasContent = computed(
  () => (file.value?.lines && file.value?.lines.length > 0) || file.value?.content
)
const sourcePreviewCandidateType = computed(() => getPreviewTypeByPath(file.value?.filename || ''))
const sourcePreviewDisplayType = computed(() => sourcePreview.value.type || sourcePreviewCandidateType.value)
const sourceContentLength = computed(() =>
  typeof sourcePreview.value.content === 'string' ? sourcePreview.value.content.length : 0
)
const sourcePreviewFile = computed(() => {
  if (!file.value) return null
  return {
    ...file.value,
    content: sourcePreview.value.content,
    previewType: sourcePreviewDisplayType.value,
    previewUrl: sourcePreview.value.url,
    supported: sourcePreview.value.supported,
    message: sourcePreview.value.message
  }
})
const hasSourcePreview = computed(() => canPreviewOriginal(file.value))
const hasMarkdownPreview = computed(() => canPreviewParsed(file.value) || hasContent.value)
// 是否有实际的分块数据
const hasChunks = computed(() => mappedChunks.value && mappedChunks.value.length > 0)
const hasChunkPreview = computed(() => canPreviewChunks(file.value) && hasChunks.value)
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

// 切换文件时重置预览状态和默认视图；同一文件内容补齐时不打断当前视图。
watch(file, (newFile, oldFile) => {
  if (newFile?.file_id !== oldFile?.file_id) {
    resetSourcePreview()
    viewMode.value = getDefaultDetailView(newFile)
  }

  if (!newFile) {
    resetSourcePreview()
    return
  }
})

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
const mergeResult = computed(() => mergeChunks(file.value?.lines || []))
const mappedChunks = computed(() => mergeResult.value.chunks)
const mergedContent = computed(() => file.value?.content || mergeResult.value.content || '')
const charCount = computed(() => mergedContent.value.length)
const chunkCount = computed(() => mappedChunks.value.length || file.value?.lines?.length || 0)
const viewInfoText = computed(() => {
  if (viewMode.value === 'chunks') {
    return `${chunkCount.value} 个片段`
  }
  if (viewMode.value === 'source') {
    if (sourcePreview.value.loading) return ''
    if (sourceContentLength.value > 0) return `${formatTextLength(sourceContentLength.value)} 字符`
    if (sourcePreview.value.url) return '源文件预览'
    return ''
  }
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
    resetSourcePreview()
    store.selectedFile = null
    viewMode.value = 'markdown'
  }
}

const loadSourcePreview = async () => {
  if (!file.value?.file_id || !store.kbId || !hasSourcePreview.value) return
  if (sourcePreview.value.url || sourcePreview.value.content || sourcePreview.value.message) return

  sourcePreview.value.loading = true
  try {
    const response = await getWorkspaceKnowledgeFileContent(store.kbId, file.value.file_id)
    const preview = await normalizePreviewResponse(response)
    revokeSourcePreviewUrl()
    sourcePreview.value.type = preview.previewType || sourcePreviewCandidateType.value
    sourcePreview.value.message = preview.message || ''
    sourcePreview.value.supported = preview.supported !== false
    sourcePreview.value.url = preview.previewUrl || ''
    sourcePreview.value.content = preview.content || ''
  } catch (error) {
    console.error('加载源文件预览失败:', error)
    sourcePreview.value.message = error.message || '加载源文件预览失败'
    sourcePreview.value.supported = false
    message.error(sourcePreview.value.message)
  } finally {
    sourcePreview.value.loading = false
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
  if (!file.value || !file.value.file_id) {
    message.error('文件信息不完整')
    return
  }

  const kbId = store.kbId
  if (!kbId) {
    message.error('无法获取数据库ID，请刷新页面后重试')
    return
  }

  downloadingOriginal.value = true
  try {
    const response = await documentApi.downloadDocument(kbId, file.value.file_id)

    // 获取文件名
    const contentDisposition = response.headers.get('content-disposition')
    let filename = file.value.filename
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

  svg {
    margin-right: 8px;
  }
}
</style>
