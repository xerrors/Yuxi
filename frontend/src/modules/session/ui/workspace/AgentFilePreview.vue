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
        <FileTypeIcon v-if="showFileIcon" :name="filePath" :size="16" />
        <span class="file-path-title">{{ displayFilePath }}</span>
      </div>
      <div class="modal-actions">
        <button
          v-if="canEdit && editMode !== 'edit'"
          class="modal-action-btn"
          @click="startEditing"
          title="编辑"
          aria-label="编辑"
        >
          <FilePen :size="16" />
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
        <div
          v-if="isHtmlFile && htmlPreviewMode === 'render' && !(canEdit && editMode === 'edit')"
          class="html-preview-zoom-control"
        >
          <button
            class="preview-mode-btn"
            :disabled="zoomOutDisabled"
            @click="zoomHtmlPreview(-HTML_PREVIEW_SCALE_STEP)"
            title="缩小"
            aria-label="缩小"
          >
            <ZoomOut :size="14" />
          </button>
          <span class="html-preview-zoom-value" title="缩放比例"
            >{{ htmlPreviewScalePercent }}%</span
          >
          <button
            class="preview-mode-btn"
            :disabled="zoomInDisabled"
            @click="zoomHtmlPreview(HTML_PREVIEW_SCALE_STEP)"
            title="放大"
            aria-label="放大"
          >
            <ZoomIn :size="14" />
          </button>
        </div>
        <button
          v-if="file && (showDownload || officePreviewActive)"
          class="modal-action-btn"
          @click="downloadPreviewFile"
          title="下载"
        >
          <Download :size="16" />
        </button>
        <button
          v-if="showClose"
          class="modal-action-btn"
          @click="$emit('close')"
          :title="closeTitle"
          :aria-label="closeTitle"
        >
          <component :is="closeIconComponent" :size="16" />
        </button>
      </div>
    </div>

    <div
      v-if="
        showInlineHtmlControls && !showHeader && isHtmlFile && !(canEdit && editMode === 'edit')
      "
      class="preview-mode-switch inline-html-preview-switch"
    >
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
      <template v-if="htmlPreviewMode === 'render' && !(canEdit && editMode === 'edit')">
        <span class="preview-mode-divider"></span>
        <button
          class="preview-mode-btn"
          :disabled="zoomOutDisabled"
          @click="zoomHtmlPreview(-HTML_PREVIEW_SCALE_STEP)"
          title="缩小"
          aria-label="缩小"
        >
          <ZoomOut :size="14" />
        </button>
        <span class="html-preview-zoom-value" title="缩放比例">{{ htmlPreviewScalePercent }}%</span>
        <button
          class="preview-mode-btn"
          :disabled="zoomInDisabled"
          @click="zoomHtmlPreview(HTML_PREVIEW_SCALE_STEP)"
          title="放大"
          aria-label="放大"
        >
          <ZoomIn :size="14" />
        </button>
      </template>
    </div>

    <div v-if="showEditActions && canEdit && editMode === 'edit'" class="edit-floating-actions">
      <span v-if="draftChanged" class="edit-status-badge">未保存</span>
      <button
        class="edit-floating-btn edit-floating-wrap-btn"
        :class="{ active: lineWrapEnabled }"
        :aria-pressed="lineWrapEnabled"
        :title="lineWrapEnabled ? '关闭自动换行' : '开启自动换行'"
        :aria-label="lineWrapEnabled ? '关闭自动换行' : '开启自动换行'"
        @click="lineWrapEnabled = !lineWrapEnabled"
      >
        <WrapText :size="14" />
      </button>
      <button
        v-if="draftChanged && showSaveAction"
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
          'is-iframe-preview': isHtmlFile && htmlPreviewMode === 'render',
          'is-office-preview': officePreviewActive
        }
      ]"
    >
      <template v-if="canEdit && editMode === 'edit'">
        <textarea
          ref="editTextarea"
          v-model="draftContent"
          @input="emit('draft-change', draftContent)"
          class="file-edit-textarea"
          aria-label="编辑文件内容"
          :class="{ 'no-wrap': !lineWrapEnabled }"
          :disabled="saving"
          :wrap="lineWrapEnabled ? 'soft' : 'off'"
          spellcheck="false"
        />
      </template>
      <template v-else-if="currentStatus === 'loading'">
        <div class="unsupported-preview" role="status" aria-label="正在加载预览">
          <LoaderCircle class="preview-spinner" :size="20" aria-hidden="true" />
        </div>
      </template>
      <template v-else-if="currentStatus === 'error'">
        <div class="unsupported-preview error-preview">
          <CircleAlert :size="22" class="preview-error-icon" />
          <span class="preview-error-text">{{ currentErrorMessage }}</span>
        </div>
      </template>
      <template v-else-if="currentStatus === 'unsupported' || file?.supported === false">
        <div class="unsupported-preview">
          {{ file?.message || '当前文件暂不支持预览，请下载后查看' }}
        </div>
      </template>
      <template v-else-if="file?.previewType === 'image' && file?.previewUrl">
        <div class="image-preview-wrapper">
          <img :src="file.previewUrl" :alt="filePath" class="image-preview" />
        </div>
      </template>
      <template v-else-if="file?.previewType === 'pdf' && file?.previewUrl">
        <PdfPreview :url="file.previewUrl" class="pdf-preview" />
      </template>
      <template v-else-if="file?.previewType === 'office'">
        <div v-if="!officePreviewActive" class="unsupported-preview">
          当前文件暂不支持预览，请下载后查看
        </div>
        <OfficePreview v-else :file="file.previewFile" class="office-preview" @download="downloadPreviewFile" />
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
        <MarkdownPreview
          :content="formatContent(file?.content)"
          :resource-base-url="file?.resourceBaseUrl"
        />
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
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import {
  CircleAlert,
  Code2,
  Download,
  Globe,
  LoaderCircle,
  PanelRight,
  FilePen,
  Save,
  WrapText,
  X,
  ZoomIn,
  ZoomOut
} from '@lucide/vue'
import hljs from 'highlight.js/lib/common'
import MarkdownPreview from '@/modules/workspace/ui/MarkdownPreview.vue'
import PdfPreview from '@/shared/ui/PdfPreview.vue'
import OfficePreview from '@/shared/ui/OfficePreview.vue'
import FileTypeIcon from '@/shared/ui/FileTypeIcon.vue'
import { useThemeStore } from '@/shared/model/theme'
import { escapeHtml } from '@/shared/lib/html'
import {
  getCodeLanguageByPath,
  getPreviewFileExtension,
  isHtmlPreview,
  isMarkdownPreview
} from '@/shared/lib/file_preview'

const EDITABLE_EXTENSIONS = new Set(['.md', '.markdown', '.mdx', '.txt'])
const HTML_PREVIEW_DEFAULT_SCALE = 0.9
const HTML_PREVIEW_SCALE_STEP = 0.1
const HTML_PREVIEW_SCALE_MIN = 0.6
const HTML_PREVIEW_SCALE_MAX = 1.5

const props = defineProps({
  showSaveAction: { type: Boolean, default: true },
  showEditActions: { type: Boolean, default: true },
  draft: { type: String, default: null },
  file: {
    type: Object,
    default: null
  },
  filePath: {
    type: String,
    default: ''
  },
  errorMessage: {
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
  showInlineHtmlControls: {
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

const emit = defineEmits(['close', 'download', 'save', 'draft-change', 'cancel-edit'])

const themeStore = useThemeStore()
const closeTitle = computed(() =>
  props.closeVariant === 'collapse-right' ? '收起预览面板' : '关闭预览'
)
const closeIconComponent = computed(() =>
  props.closeVariant === 'collapse-right' ? PanelRight : X
)
const currentStatus = computed(() => {
  if (props.file?.status) return props.file.status
  if (props.file?.loading === true) return 'loading'
  if (props.file?.error || props.file?.isError) return 'error'
  if (props.file?.supported === false) return 'unsupported'
  return 'ready'
})
const currentErrorMessage = computed(() => {
  return (
    props.errorMessage ||
    props.file?.errorMessage ||
    (typeof props.file?.error === 'string' ? props.file.error : '') ||
    props.file?.message ||
    '加载文件预览失败'
  )
})
const htmlPreviewMode = ref('render')
const editMode = ref('preview')
const draftContent = ref('')
const lineWrapEnabled = ref(true)
const htmlPreviewRenderKey = ref(0)
const htmlPreviewScale = ref(HTML_PREVIEW_DEFAULT_SCALE)
const zoomInDisabled = computed(() => htmlPreviewScale.value >= HTML_PREVIEW_SCALE_MAX)
const zoomOutDisabled = computed(() => htmlPreviewScale.value <= HTML_PREVIEW_SCALE_MIN)
const htmlPreviewScalePercent = computed(() => Math.round(htmlPreviewScale.value * 100))
const displayFilePath = computed(() =>
  String(props.filePath || '').replace(/^\/home\/user\/workspace(?=\/|$)/, '~').replace(/^\//, '')
)

const officePreviewActive = computed(
  () => props.file?.previewType === 'office' && props.file?.previewFile instanceof File
)

const downloadPreviewFile = () => {
  if (!officePreviewActive.value) {
    emit('download', props.file)
    return
  }
  const original = props.file.previewFile
  const url = URL.createObjectURL(original)
  const link = document.createElement('a')
  link.href = url
  link.download = original.name
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

const isMarkdown = computed(() => isMarkdownPreview(props.filePath, props.file?.previewType))
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
    ['text', 'html'].includes(props.file?.previewType) &&
    typeof props.file?.content === 'string' &&
    isHtmlPreview(props.filePath)
)
const htmlPreviewSrcdoc = computed(() =>
  buildHtmlPreviewSrcdoc(props.file?.content, htmlPreviewScale.value)
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

/** 让 srcdoc 同框架锚点留在预览内，并保留文档对默认导航的取消。 */
function installHtmlPreviewAnchors() {
  const pendingClicks = new WeakMap()
  const afterWindowCapture = (event) => {
    if (event.cancelBubble) pendingClicks.get(event)?.()
  }
  const refreshWindowCapture = () => {
    // 用户激活前把观察器放到作者已经注册的 Window 捕获监听器之后。
    window.removeEventListener('click', afterWindowCapture, true)
    window.addEventListener('click', afterWindowCapture, true)
  }
  window.addEventListener('load', refreshWindowCapture, { once: true })
  window.addEventListener('pointerdown', refreshWindowCapture, true)
  window.addEventListener('keydown', refreshWindowCapture, true)

  const getFragment = (link) => {
    if (link.hasAttribute('download') || document.querySelector('base[href]')) return null
    const target = link.getAttribute('target') || document.querySelector('base[target]')?.getAttribute('target')
    if (target && target.toLowerCase() !== '_self') return null

    // URL 解析只裁掉首尾 C0 控制字符和 ASCII 空格。
    // eslint-disable-next-line no-control-regex
    const href = link.getAttribute('href')?.replace(/^[\x00-\x20]+|[\x00-\x20]+$/g, '')
    return href?.startsWith('#') ? href : null
  }
  window.addEventListener('click', (event) => {
    if (
      !event.cancelable ||
      event.button !== 0 ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey
    ) return

    const path = event.composedPath()
    const link = path.find((node) => node instanceof Element && node.matches('a[href], area[href]'))
    if (!link) return

    const listeners = new AbortController()
    const followFragment = () => {
      if (listeners.signal.aborted) return
      listeners.abort()
      pendingClicks.delete(event)
      const href = getFragment(link)
      if (!href || event.defaultPrevented) return
      event.preventDefault()
      window.location.assign(`about:srcdoc${href}`)
    }

    const afterDocument = (click) => {
      const reachedWindow = click.currentTarget === window && click.eventPhase === Event.BUBBLING_PHASE
      if (click === event && (click.cancelBubble || reachedWindow)) followFragment()
    }
    // 让文档监听器先运行。单独停止传播不会取消锚点的默认动作。
    pendingClicks.set(event, followFragment)
    for (const node of path) {
      if (node !== window) node.addEventListener('click', afterDocument, { capture: true, signal: listeners.signal })
      node.addEventListener('click', afterDocument, { signal: listeners.signal })
    }

    // 立即停止传播会跳过后续监听器。真实点击的微任务能在默认导航前读取取消结果。
    const stop = event.stopImmediatePropagation
    const stopImmediately = function () {
      stop.call(this)
      queueMicrotask(followFragment)
    }
    if (event.isTrusted) event.stopImmediatePropagation = stopImmediately
    setTimeout(() => {
      listeners.abort()
      pendingClicks.delete(event)
      if (event.stopImmediatePropagation === stopImmediately) delete event.stopImmediatePropagation
    }, 0)
  }, true)
  document.currentScript.remove()
}

const buildHtmlPreviewSrcdoc = (content, scale = HTML_PREVIEW_DEFAULT_SCALE) => {
  const html = formatContent(content)
  if (!html.trim() || typeof DOMParser === 'undefined') return html

  const doc = new DOMParser().parseFromString(html, 'text/html')
  // 预览脚本先于文档自己的 CSP 执行，文档原有脚本仍受其策略约束。
  const script = doc.createElement('script')
  script.textContent = `(${installHtmlPreviewAnchors.toString()})()`
  doc.head.prepend(script)
  if (scale !== 1) {
    const style = doc.createElement('style')
    style.setAttribute('data-yuxi-html-preview-scale', String(scale))
    style.textContent = `html { zoom: ${scale} !important; }`
    doc.head.append(style)
  }

  return `${serializeDoctype(doc.doctype)}${doc.documentElement.outerHTML}`
}

const syncDraftContent = () => {
  draftContent.value = props.draft ?? savedContent.value
  editMode.value = 'preview'
}

const zoomHtmlPreview = (delta) => {
  const next = Math.round((htmlPreviewScale.value + delta) * 10) / 10
  htmlPreviewScale.value = Math.min(Math.max(next, HTML_PREVIEW_SCALE_MIN), HTML_PREVIEW_SCALE_MAX)
  htmlPreviewRenderKey.value += 1
}

const resetHtmlPreviewZoom = () => {
  htmlPreviewScale.value = HTML_PREVIEW_DEFAULT_SCALE
}

const editTextarea = ref(null)
/** 进入编辑后定位源文件行，焦点交接让浏览器滚动到光标。 */
const startEditing = async (startLine = 1) => {
  if (!canEdit.value || props.saving) return
  editMode.value = 'edit'
  await nextTick()
  const textarea = editTextarea.value
  if (!textarea) return
  const line = Number.isInteger(startLine) && startLine > 0 ? startLine : 1
  const lines = textarea.value.split('\n')
  const offset = lines.slice(0, Math.min(line - 1, lines.length - 1)).reduce((total, text) => total + text.length + 1, 0)
  textarea.setSelectionRange(offset, offset)
  textarea.focus()
}

defineExpose({
  startEditing,
  discardChanges: syncDraftContent,
  hasUnsavedChanges: () => editMode.value === 'edit' && draftChanged.value
})

const requestSave = () => {
  if (!canEdit.value || props.saving) return
  emit('save', draftContent.value)
}

const cancelEdit = () => {
  draftContent.value = savedContent.value
  emit('draft-change', savedContent.value)
  editMode.value = 'preview'
  emit('cancel-edit')
}

watch(
  () => props.filePath,
  () => {
    htmlPreviewMode.value = 'render'
    resetHtmlPreviewZoom()
  }
)

watch([() => props.filePath, () => props.file?.content, canEdit], syncDraftContent, {
  immediate: true
})

watch(
  () => props.draft,
  (value) => {
    if (value != null) draftContent.value = value
  }
)

watch([() => props.filePath, () => props.file?.previewType, () => props.file?.content], () => {
  if (isHtmlFile.value) {
    htmlPreviewRenderKey.value += 1
  }
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
  width: 100%;
  height: 100%;
  max-height: none;
}

.agent-file-preview.is-full-height .file-content {
  flex: 1 1 0;
  height: auto;
  min-height: 0;
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
  padding: 4px calc(var(--page-padding) - 4px);
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

.inline-html-preview-switch {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 5;
  box-shadow: 0 8px 24px var(--shadow-3);
}

.file-path-title {
  font-weight: 400;
  font-size: 12px;
  color: var(--gray-600);
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
  box-shadow: 0 1px 2px var(--shadow-2);
}

.text-mode-btn {
  width: auto;
  min-width: 48px;
  padding: 0 8px;
  font-size: 12px;
}

.preview-mode-divider {
  width: 1px;
  height: 14px;
  margin: 0 2px;
  background: var(--gray-300);
}

.html-preview-zoom-control {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: 8px;
  background: var(--gray-100);
}

.html-preview-zoom-value {
  min-width: 34px;
  color: var(--gray-700);
  font-size: 12px;
  line-height: 1;
  text-align: center;
  user-select: none;
}

.file-content {
  flex: 1 1 auto;
  min-height: 300px;
  min-width: 0;
  overflow-y: auto;
  border-radius: 0px;

  &.is-editing {
    overflow: hidden;
  }

  &.is-iframe-preview {
    overflow: hidden;
  }

  /* 标准滚动条着色，宽度沿用全局 thin；WebKit 前缀规则仅在标准属性不可用时兜底。 */
  scrollbar-color: var(--gray-300) var(--gray-50);

  @supports not (scrollbar-color: auto) {
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
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--gray-150);
  border-radius: 999px;
  background: var(--gray-0);
  box-shadow: 0 6px 18px var(--shadow-3);
  pointer-events: none;
}

.agent-file-preview.has-preview-header .edit-floating-actions {
  top: 52px;
}

.edit-status-badge {
  display: inline-flex;
  align-items: center;
  height: 24px;
  margin: 0 4px;
  padding: 0 6px;
  border: 0;
  border-radius: 999px;
  background: var(--gray-50);
  font-size: 11px;
  line-height: 1;
  color: var(--gray-700);
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
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--gray-700);
  cursor: pointer;
  pointer-events: auto;
  transition:
    background-color 0.15s ease,
    border-color 0.15s ease,
    color 0.15s ease;

  &:hover:not(:disabled) {
    background: var(--gray-50);
    color: var(--gray-900);
  }

  &:disabled {
    color: var(--gray-300);
    cursor: not-allowed;
    opacity: 0.7;

    &:hover {
      background: transparent;
    }
  }
}

.edit-floating-btn-primary {
  color: var(--main-600);

  &:hover:not(:disabled) {
    background: var(--color-primary-50);
    color: var(--color-primary-700);
  }

  &:disabled {
    background: transparent;
    color: var(--gray-400);
    cursor: not-allowed;

    &:hover {
      background: transparent;
      color: var(--gray-400);
    }
  }
}

.edit-floating-wrap-btn {
  &.active {
    background: var(--main-10);
    color: var(--main-color);
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
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  resize: none;
  background: var(--gray-0);
  color: var(--gray-1000);
  font-family: 'JetBrains Mono', 'Fira Code', 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 13px;
  line-height: 1.5;

  &.no-wrap {
    white-space: pre;
    overflow-x: auto;
  }
}

.file-edit-textarea:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: -2px;
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

.pdf-preview,
.office-preview {
  display: block;
  width: 100%;
  min-height: 100%;
  border: none;
  border-radius: 6px;
  background: var(--gray-25);
}

.file-content.is-office-preview {
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.agent-file-preview.is-full-height .office-preview {
  flex: 1 1 auto;
  height: 100%;
  min-height: 0;
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
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: var(--gray-600);
  font-size: 14px;
  line-height: 1.6;
  height: 100%;
  padding: 24px;
  gap: 10px;
  white-space: pre-wrap;

  &.error-preview {
    color: var(--color-error-700);
  }
}

.preview-spinner {
  animation: spin 1s linear infinite;
  color: var(--color-primary-500);
}

.preview-error-icon {
  color: var(--color-error-500);
}

.preview-error-text {
  max-width: 480px;
  word-break: break-word;
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
