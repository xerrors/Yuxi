<template>
  <div
    class="agent-file-preview"
    :class="[
      containerClass,
      {
        'is-full-height': fullHeight,
        'is-borderless': borderless,
        'has-preview-header': showHeader
      }
    ]"
  >
    <div v-if="showHeader" class="preview-header">
      <div class="file-title">
        <FileTypeIcon v-if="showFileIcon" :name="filePath" :size="18" />
        <span class="file-path-title">{{ filePath }}</span>
      </div>
      <div class="modal-actions">
        <div v-if="availablePreviewVariants.length > 1" class="preview-mode-switch">
          <button
            v-for="variant in availablePreviewVariants"
            :key="variant.key"
            class="preview-mode-btn text-mode-btn"
            :class="{ active: activePreviewVariant === variant.key }"
            :title="variant.label"
            @click="$emit('switchVariant', variant.key)"
          >
            {{ variant.label }}
          </button>
        </div>
        <button
          v-if="canEdit && editMode !== 'edit'"
          class="modal-action-btn"
          @click="editMode = 'edit'"
          title="编辑"
          aria-label="编辑"
        >
          <FilePen :size="18" />
        </button>

        <div v-if="isHtmlFile" class="preview-mode-switch">
          <button
            class="preview-mode-btn"
            :class="{ active: htmlPreviewMode === 'render' }"
            @click="htmlPreviewMode = 'render'"
            title="预览"
          >
            <Globe :size="16" />
          </button>
          <button
            class="preview-mode-btn"
            :class="{ active: htmlPreviewMode === 'source' }"
            @click="htmlPreviewMode = 'source'"
            title="源码"
          >
            <Code2 :size="16" />
          </button>
        </div>
        <button
          v-if="showDownload && file"
          class="modal-action-btn"
          @click="$emit('download', file)"
          title="下载"
        >
          <Download :size="18" />
        </button>
        <button
          v-if="showFullscreen && file"
          class="modal-action-btn"
          @click="openFullscreenPreview"
          title="全屏预览"
        >
          <Maximize :size="18" />
        </button>
        <button
          v-if="showClose"
          class="modal-action-btn"
          @click="$emit('close')"
          :title="closeTitle"
          :aria-label="closeTitle"
        >
          <component :is="closeIconComponent" :size="18" />
        </button>
      </div>
    </div>

    <div v-if="canEdit && editMode === 'edit'" class="edit-floating-actions">
      <span v-if="draftChanged" class="edit-status-badge">未保存</span>
      <button
        v-if="draftChanged"
        class="edit-floating-btn edit-floating-btn-primary"
        :disabled="saving"
        @click="requestSave"
        :title="saving ? '保存中' : '保存'"
        :aria-label="saving ? '保存中' : '保存'"
      >
        <Save :size="14" />
      </button>
      <button
        class="edit-floating-btn"
        :class="{ 'edit-floating-btn-danger': draftChanged }"
        :disabled="saving"
        @click="cancelEdit"
        title="取消"
        aria-label="取消"
      >
        <X :size="14" />
      </button>
    </div>

    <div
      class="file-content"
      :class="[
        contentClass,
        {
          'is-editing': canEdit && editMode === 'edit',
          'is-iframe-preview':
            file?.previewType === 'pdf' || (isHtmlFile && htmlPreviewMode === 'render')
        }
      ]"
    >
      <template v-if="canEdit && editMode === 'edit'">
        <textarea
          v-model="draftContent"
          class="file-edit-textarea"
          :disabled="saving"
          spellcheck="false"
        />
      </template>
      <template v-else-if="file?.previewType === 'image' && file?.previewUrl">
        <div class="image-preview-wrapper">
          <img :src="file.previewUrl" :alt="filePath" class="image-preview" />
        </div>
      </template>
      <template v-else-if="file?.previewType === 'pdf' && file?.previewUrl">
        <iframe :src="file.previewUrl" class="pdf-preview" :title="filePath" />
      </template>
      <template v-else-if="isHtmlFile && htmlPreviewMode === 'render'">
        <iframe
          :key="`embedded-${htmlPreviewRenderKey}`"
          class="html-preview"
          :srcdoc="htmlPreviewSrcdoc"
          :title="filePath"
          sandbox="allow-scripts"
        />
      </template>
      <template v-else-if="isMarkdown">
        <MarkdownPreview :content="formatContent(file?.content)" />
      </template>
      <template v-else-if="file?.supported === false">
        <div class="unsupported-preview">
          {{ file?.message || '当前文件暂不支持预览，请下载后查看' }}
        </div>
      </template>
      <template v-else>
        <pre v-if="Array.isArray(file?.content)" class="file-content-pre">{{
          formatContent(file.content)
        }}</pre>
        <pre
          v-else-if="isCodePreview && typeof file?.content === 'string'"
          :class="['file-content-pre', 'code-highlight', codeThemeClass]"
        ><code class="hljs" v-html="highlightedCodeContent"></code></pre>
        <pre v-else-if="typeof file?.content === 'string'" class="file-content-pre">{{
          file.content
        }}</pre>
        <pre v-else>{{ JSON.stringify(file, null, 2) }}</pre>
      </template>
    </div>

    <Teleport to="body">
      <div v-if="fullscreenPreviewVisible && file" class="fullscreen-preview-overlay">
        <div class="fullscreen-preview-actions">
          <div v-if="isHtmlFile" class="preview-mode-switch fullscreen-preview-switch">
            <button
              class="preview-mode-btn"
              :class="{ active: htmlPreviewMode === 'render' }"
              @click="htmlPreviewMode = 'render'"
              title="预览"
            >
              <Globe :size="16" />
            </button>
            <button
              class="preview-mode-btn"
              :class="{ active: htmlPreviewMode === 'source' }"
              @click="htmlPreviewMode = 'source'"
              title="源码"
            >
              <Code2 :size="16" />
            </button>
          </div>
          <button
            v-if="showDownload && file"
            class="modal-action-btn fullscreen-action-btn"
            @click="$emit('download', file)"
            title="下载"
          >
            <Download :size="18" />
          </button>
          <button
            class="modal-action-btn fullscreen-action-btn"
            @click="closeFullscreenPreview"
            title="关闭"
          >
            <X :size="18" />
          </button>
        </div>
        <div class="fullscreen-preview-content">
          <div class="file-content fullscreen-file-content">
            <template v-if="file?.previewType === 'image' && file?.previewUrl">
              <div class="image-preview-wrapper fullscreen-image-preview-wrapper">
                <img :src="file.previewUrl" :alt="filePath" class="image-preview" />
              </div>
            </template>
            <template v-else-if="file?.previewType === 'pdf' && file?.previewUrl">
              <iframe
                :src="file.previewUrl"
                class="pdf-preview fullscreen-embed-preview"
                :title="filePath"
              />
            </template>
            <template v-else-if="isHtmlFile && htmlPreviewMode === 'render'">
              <iframe
                :key="`fullscreen-${htmlPreviewRenderKey}`"
                class="html-preview fullscreen-embed-preview"
                :srcdoc="htmlPreviewFullscreenSrcdoc"
                :title="filePath"
                sandbox="allow-scripts"
              />
            </template>
            <template v-else-if="isMarkdown">
              <MarkdownPreview :content="formatContent(file?.content)" />
            </template>
            <template v-else-if="file?.supported === false">
              <div class="unsupported-preview fullscreen-unsupported-preview">
                {{ file?.message || '当前文件暂不支持预览，请下载后查看' }}
              </div>
            </template>
            <template v-else>
              <pre v-if="Array.isArray(file?.content)" class="file-content-pre">{{
                formatContent(file.content)
              }}</pre>
              <pre
                v-else-if="isCodePreview && typeof file?.content === 'string'"
                :class="['file-content-pre', 'code-highlight', codeThemeClass]"
              ><code class="hljs" v-html="highlightedCodeContent"></code></pre>
              <pre v-else-if="typeof file?.content === 'string'" class="file-content-pre">{{
                file.content
              }}</pre>
              <pre v-else>{{ JSON.stringify(file, null, 2) }}</pre>
            </template>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import {
  Code2,
  Download,
  Globe,
  Maximize,
  PanelRightClose,
  FilePen,
  Save,
  X
} from 'lucide-vue-next'
import hljs from 'highlight.js/lib/common'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'
import { useThemeStore } from '@/stores/theme'
import { escapeHtml } from '@/utils/html'
import {
  getCodeLanguageByPath,
  getPreviewFileExtension,
  isHtmlPreview,
  isMarkdownPreview
} from '@/utils/file_preview'

const EDITABLE_EXTENSIONS = new Set(['.md', '.markdown', '.mdx', '.txt'])
const HTML_PREVIEW_SCALE = 0.75
const HTML_PREVIEW_FULLSCREEN_SCALE = 1

const props = defineProps({
  file: {
    type: Object,
    default: null
  },
  filePath: {
    type: String,
    default: ''
  },
  showHeader: {
    type: Boolean,
    default: true
  },
  showDownload: {
    type: Boolean,
    default: true
  },
  showClose: {
    type: Boolean,
    default: false
  },
  showFullscreen: {
    type: Boolean,
    default: false
  },
  closeVariant: {
    type: String,
    default: 'close',
    validator: (value) => ['close', 'collapse-right'].includes(value)
  },
  fullHeight: {
    type: Boolean,
    default: false
  },
  showFileIcon: {
    type: Boolean,
    default: true
  },
  borderless: {
    type: Boolean,
    default: false
  },
  editable: {
    type: Boolean,
    default: false
  },
  editAllText: {
    type: Boolean,
    default: false
  },
  saving: {
    type: Boolean,
    default: false
  },
  containerClass: {
    type: [String, Array, Object],
    default: ''
  },
  contentClass: {
    type: [String, Array, Object],
    default: ''
  }
})

const emit = defineEmits(['close', 'download', 'save', 'switchVariant'])

const themeStore = useThemeStore()
const closeTitle = computed(() =>
  props.closeVariant === 'collapse-right' ? '收起预览面板' : '关闭预览'
)
const closeIconComponent = computed(() =>
  props.closeVariant === 'collapse-right' ? PanelRightClose : X
)
const htmlPreviewMode = ref('render')
const editMode = ref('preview')
const draftContent = ref('')
const fullscreenPreviewVisible = ref(false)
const htmlPreviewRenderKey = ref(0)

const isMarkdown = computed(() => isMarkdownPreview(props.filePath, props.file?.previewType))
const availablePreviewVariants = computed(() => {
  const variants = props.file?.availableVariants || props.file?.available_variants || []
  return variants.filter((variant) => variant?.supported !== false && variant?.key)
})
const activePreviewVariant = computed(() => props.file?.variant || props.file?.previewVariant || '')
const canEdit = computed(() => {
  const previewType = props.file?.previewType
  return (
    props.editable &&
    props.file?.supported !== false &&
    typeof props.file?.content === 'string' &&
    (previewType === 'text' || previewType === 'markdown') &&
    (props.editAllText || EDITABLE_EXTENSIONS.has(getPreviewFileExtension(props.filePath)))
  )
})
const savedContent = computed(() => formatContent(props.file?.content))
const draftChanged = computed(() => draftContent.value !== savedContent.value)
const isHtmlFile = computed(
  () =>
    props.file?.previewType === 'text' &&
    typeof props.file?.content === 'string' &&
    isHtmlPreview(props.filePath)
)
const htmlPreviewSrcdoc = computed(() =>
  buildHtmlPreviewSrcdoc(props.file?.content, HTML_PREVIEW_SCALE)
)
const htmlPreviewFullscreenSrcdoc = computed(() =>
  buildHtmlPreviewSrcdoc(props.file?.content, HTML_PREVIEW_FULLSCREEN_SCALE)
)
const codeThemeClass = computed(() => (themeStore.isDark ? 'hljs-theme-dark' : 'hljs-theme-light'))
const codeLanguage = computed(() => getCodeLanguageByPath(props.filePath))
const isCodePreview = computed(
  () =>
    props.file?.previewType === 'text' &&
    typeof props.file?.content === 'string' &&
    !isHtmlFile.value &&
    Boolean(codeLanguage.value)
)

const highlightedCodeContent = computed(() => {
  const content = props.file?.content
  if (!isCodePreview.value || typeof content !== 'string') {
    return ''
  }

  try {
    if (codeLanguage.value) {
      return hljs.highlight(content, { language: codeLanguage.value }).value
    }
    return hljs.highlightAuto(content).value
  } catch (error) {
    console.warn('代码高亮失败:', error)
    return escapeHtml(content)
  }
})

const formatContent = (content) => {
  if (Array.isArray(content)) return content.join('\n')
  if (content === undefined || content === null) return ''
  return String(content)
}

const serializeDoctype = (doctype) => {
  if (!doctype) return ''
  const publicId = doctype.publicId ? ` PUBLIC "${doctype.publicId}"` : ''
  const systemId = doctype.systemId ? ` "${doctype.systemId}"` : ''
  return `<!DOCTYPE ${doctype.name}${publicId}${systemId}>`
}

const buildHtmlPreviewSrcdoc = (content, scale = HTML_PREVIEW_SCALE) => {
  const html = formatContent(content)
  if (!html.trim() || typeof DOMParser === 'undefined') return html

  const doc = new DOMParser().parseFromString(html, 'text/html')
  if (scale !== 1) {
    const style = doc.createElement('style')
    style.setAttribute('data-yuxi-html-preview-scale', String(scale))
    style.textContent = `html { zoom: ${scale} !important; }`
    doc.head.append(style)
  }

  return `${serializeDoctype(doc.doctype)}${doc.documentElement.outerHTML}`
}

const syncDraftContent = () => {
  draftContent.value = savedContent.value
  editMode.value = 'preview'
}

const requestSave = () => {
  if (!canEdit.value || props.saving) return
  emit('save', draftContent.value)
}

const cancelEdit = () => {
  syncDraftContent()
}

const openFullscreenPreview = () => {
  if (!props.file) return
  fullscreenPreviewVisible.value = true
}

const closeFullscreenPreview = () => {
  fullscreenPreviewVisible.value = false
}

watch(
  () => props.filePath,
  () => {
    htmlPreviewMode.value = 'render'
  }
)

watch([() => props.filePath, () => props.file?.content, canEdit], syncDraftContent, {
  immediate: true
})

watch([() => props.filePath, () => props.file?.previewType, () => props.file?.content], () => {
  if (isHtmlFile.value) {
    htmlPreviewRenderKey.value += 1
  }
})

watch(fullscreenPreviewVisible, (visible) => {
  document.body.style.overflow = visible ? 'hidden' : ''
})

onUnmounted(() => {
  document.body.style.overflow = ''
})
</script>

<style scoped lang="less">
.agent-file-preview {
  position: relative;
  min-width: 0;
  border-radius: 8px;
  overflow: hidden;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.agent-file-preview.is-full-height {
  max-height: 100vh;
}

.agent-file-preview.is-borderless {
  border-radius: 0;
}

.preview-header {
  min-height: 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 12px;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-25);
}

.file-title,
.modal-actions,
.preview-mode-switch {
  display: flex;
  align-items: center;
}

.file-title {
  gap: 8px;
  min-width: 0;
}

.modal-actions,
.preview-mode-switch {
  gap: 8px;
}

.preview-mode-switch {
  padding: 2px;
  border-radius: 8px;
  background: var(--gray-100);
}

.file-path-title {
  font-weight: 400;
  color: var(--gray-700);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.modal-action-btn,
.preview-mode-btn {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-600);
  cursor: pointer;
  transition: all 0.15s ease;
  padding: 0;
}

.modal-action-btn:hover,
.preview-mode-btn:hover {
  background: var(--gray-100);
  color: var(--gray-900);
}

.modal-action-btn:disabled,
.preview-mode-btn:disabled {
  color: var(--gray-300);
  cursor: not-allowed;
}

.modal-action-btn:disabled:hover,
.preview-mode-btn:disabled:hover {
  background: transparent;
  color: var(--gray-300);
}

.preview-mode-btn.active {
  background: var(--gray-0);
  color: var(--gray-900);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
}

.text-mode-btn {
  width: auto;
  min-width: 48px;
  padding: 0 8px;
  font-size: 12px;
}

.file-content {
  min-height: 300px;
  overflow-y: auto;
  border-radius: 0px;

  &.is-editing {
    overflow: hidden;
  }

  &.is-iframe-preview {
    overflow: hidden;
  }

  &::-webkit-scrollbar {
    width: 8px;
  }

  &::-webkit-scrollbar-track {
    background: var(--gray-50);
    border-radius: 4px;
  }

  &::-webkit-scrollbar-thumb {
    background: var(--gray-300);
    border-radius: 4px;

    &:hover {
      background: var(--gray-400);
    }
  }

  .flat-md-preview {
    padding: 16px calc(var(--page-padding) - 4px);
    font-size: 0.85rem;
  }
}

.edit-floating-actions {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 8px;
  pointer-events: none;
}

.agent-file-preview.has-preview-header .edit-floating-actions {
  top: 52px;
}

.edit-status-badge {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--color-warning-100);
  border-radius: 999px;
  background: var(--color-warning-50);
  font-size: 11px;
  line-height: 1;
  color: var(--color-warning-700);
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
  pointer-events: auto;
  white-space: nowrap;
}

.edit-floating-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: 1px solid var(--gray-150);
  border-radius: 50%;
  background: var(--gray-0);
  color: var(--gray-700);
  cursor: pointer;
  box-shadow: 0 6px 18px rgba(15, 23, 42, 0.14);
  pointer-events: auto;
  transition:
    background-color 0.15s ease,
    border-color 0.15s ease,
    color 0.15s ease;

  &:hover:not(:disabled) {
    border-color: var(--gray-250);
    background: var(--gray-50);
    color: var(--gray-900);
  }

  &:disabled {
    color: var(--gray-300);
    cursor: not-allowed;
    opacity: 0.7;

    &:hover {
      border-color: var(--gray-150);
      background: var(--gray-0);
    }
  }
}

.edit-floating-btn-primary {
  background: var(--color-primary-500);
  border-color: var(--color-primary-500);
  color: #fff;

  &:hover:not(:disabled) {
    background: var(--color-primary-700);
    border-color: var(--color-primary-700);
    color: #fff;
  }

  &:disabled {
    background: var(--color-primary-500);
    border-color: var(--color-primary-500);
    color: rgba(255, 255, 255, 0.5);
    cursor: not-allowed;

    &:hover {
      background: var(--color-primary-500);
      border-color: var(--color-primary-500);
      color: rgba(255, 255, 255, 0.5);
    }
  }
}

.edit-floating-btn-danger {
  &:hover:not(:disabled) {
    border-color: var(--color-error-500);
    background: var(--color-error-50);
    color: var(--color-error-700);
  }
}

.file-edit-textarea {
  width: 100%;
  min-height: 100%;
  padding: 12px;
  border: 0;
  outline: none;
  resize: none;
  background: var(--gray-0);
  color: var(--gray-1000);
  font-family: 'JetBrains Mono', 'Fira Code', 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 13px;
  line-height: 1.5;
}

.file-edit-textarea:disabled {
  color: var(--gray-600);
  background: var(--gray-25);
}

.file-content pre,
.file-content-pre {
  font-family: 'JetBrains Mono', 'Fira Code', 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 13px;
  line-height: 1.5;
  margin: 0;
  white-space: pre-wrap;
  word-wrap: break-word;
  color: var(--gray-1000);
  background: transparent;
  padding: 12px;
}

.file-content-pre.code-highlight {
  border-radius: 8px;
  background: var(--gray-0);
  white-space: pre;
  overflow-x: auto;
}

.file-content-pre.code-highlight code {
  display: block;
  white-space: pre;
  color: inherit;
  min-height: calc(80vh - 40px);
  background: var(--gray-0);
}

.image-preview-wrapper {
  display: flex;
  justify-content: center;
  align-items: flex-start;
}

.image-preview {
  display: block;
  max-width: 100%;
  height: 100%;
  max-height: calc(80vh - 32px);
  object-fit: contain;
  border-radius: 6px;
}

.pdf-preview {
  width: 100%;
  height: 100%;
  min-height: calc(80vh - 40px);
  border: none;
  border-radius: 6px;
  background: var(--gray-25);
}

.html-preview {
  display: block; // 消除 iframe 行内基线间隙导致的底部白边
  width: 100%;
  height: 100%; // 适应父容器高度，而非固定 100vh
  min-height: 0; // 移除固定最小高度，避免短内容白边
  border: none;
  border-radius: 0px;
  background: var(--gray-0); // 跟随主题：亮色为白、暗色为近黑，避免暗色 HTML 底部露出白边
}

.unsupported-preview {
  min-height: 260px;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: var(--gray-600);
  font-size: 14px;
  line-height: 1.6;
  height: 100%;
  white-space: pre-wrap;
}

.fullscreen-preview-overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  background: var(--gray-0);
}

.fullscreen-preview-actions {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 8px;
}

.fullscreen-preview-switch {
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.14);
}

.fullscreen-action-btn {
  width: 40px;
  height: 40px;
  border-radius: 999px;
  background: var(--color-trans-light);
  border: 1px solid var(--gray-200);
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.14);
  backdrop-filter: blur(10px);
}

.fullscreen-preview-content {
  position: absolute;
  inset: 0;
  min-height: 0;
}

.fullscreen-file-content {
  height: 100vh;
  max-height: none;
  min-height: 100vh;
  padding: 0px;
  border-radius: 0;
}

.fullscreen-image-preview-wrapper {
  min-height: calc(100vh - 48px);
  align-items: center;
}

.fullscreen-embed-preview {
  height: 100vh; // 全屏时填满视口
  min-height: 100vh; // 确保全屏时不塌陷
  border-radius: 0px;
}

.fullscreen-unsupported-preview {
  min-height: calc(100vh - 48px);
}

.fullscreen-file-content .file-content-pre.code-highlight code {
  min-height: calc(100vh - 48px);
}

.fullscreen-file-content .image-preview {
  max-height: calc(100vh - 48px);
}
</style>
