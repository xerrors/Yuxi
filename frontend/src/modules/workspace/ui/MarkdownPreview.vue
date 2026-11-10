<template>
  <div
    v-bind="$attrs"
    :class="[
      'yk-markdown-preview',
      'flat-md-preview',
      { 'is-dark': themeStore.isDark, 'is-compact': compact }
    ]"
    @click="handleMarkdownAction"
    @pointerover="handleReferenceEnter"
    @pointerout="scheduleReferenceClose"
    @focusin="handleReferenceEnter"
    @focusout="scheduleReferenceClose"
    @keydown="handleReferenceKeydown"
  >
    <!-- 单根承接调用方 scoped 样式；正文替换只操作独立容器，保留 Vue 浮层。 -->
    <div ref="previewRef"></div>
    <ReferenceSourcePopover
      v-if="referencePreview"
      ref="referencePopover"
      :anchor="referencePreview.anchor"
      :citations="referencePreview.citations"
      :preview-id="referencePreviewId"
      @keep-open="cancelReferenceClose"
      @leave="scheduleReferenceClose"
      @close="closeReferencePreview"
      @open="openReference"
    />
  </div>
</template>

<script setup>
import { h, render, computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { useThemeStore } from '@/shared/model/theme'
import { apiGet } from '@/apis/base'
import { renderMarkdown, resolveMarkdownImageUrl } from '@/shared/lib/markdown_preview'
import { decorateMarkdownReferences } from '@/shared/lib/markdownReferences'
import ReferenceSourcePopover from '@/modules/workspace/ui/ReferenceSourcePopover.vue'
import WidgetRenderer from '@/shared/ui/WidgetRenderer.vue'
import 'katex/dist/katex.min.css'
defineOptions({ inheritAttrs: false })
const props = defineProps({
  widgets: { type: Boolean, default: false },
  widgetsDisabled: { type: Boolean, default: false },
  widgetAction: { type: Function, default: null },
  widgetImageLoader: { type: Function, default: null },
  references: { type: Array, default: () => [] },
  content: {
    type: String,
    default: ''
  },
  resourceBaseUrl: {
    type: String,
    default: ''
  },
  compact: {
    type: Boolean,
    default: false
  },
  codeCopy: {
    type: Boolean,
    default: false
  },
  startLine: {
    type: Number,
    default: null
  },
  endLine: {
    type: Number,
    default: null
  },
  sourceLines: {
    type: Boolean,
    default: false
  }
})
const emit = defineEmits(['open-reference'])

const themeStore = useThemeStore()
/** 为组件与预览提供同源受保护图片读取，外源不携带凭据。 */
const widgetSourceImageLoader = computed(() => {
  const resourceBaseUrl = props.resourceBaseUrl
  return (source) => {
    const imageUrl = resolveMarkdownImageUrl(source, resourceBaseUrl, window.location.origin)
    return imageUrl ? apiGet(imageUrl, {}, true, 'blob') : null
  }
})
const shikiTheme = computed(() => (themeStore.isDark ? 'github-dark' : 'github-light'))
const previewRef = ref(null)
const referencePreview = ref(null)
const referencePopover = ref(null)
const referencePreviewId = `reference-preview-${useId()}`
let referenceOpenTimer
let referenceCloseTimer

/** 仅在查看来源时强调命中的正文块。 */
function highlightReferences(citations) {
  previewRef.value?.querySelectorAll('.reference-answer-highlight').forEach((block) => {
    const active = citations.some(
      (citation) =>
        Number(block.dataset.sourceStart) <= citation.answer_end_line &&
        Number(block.dataset.sourceEnd) >= citation.answer_start_line
    )
    block.classList.toggle('reference-answer-active', active)
  })
}

/** 进入胶囊或卡片时取消延迟关闭。 */
function cancelReferenceClose() {
  clearTimeout(referenceCloseTimer)
}

/** 关闭预览并清除胶囊状态与临时强调。 */
function closeReferencePreview() {
  clearTimeout(referenceOpenTimer)
  cancelReferenceClose()
  const anchor = referencePreview.value?.anchor
  anchor?.setAttribute('aria-expanded', 'false')
  anchor?.removeAttribute('aria-controls')
  referencePreview.value = null
  highlightReferences([])
}

/** 留出跨越卡片间隙的时间，再确认焦点和指针均已离开。 */
function scheduleReferenceClose(event) {
  const anchor =
    event?.target instanceof Element ? event.target.closest('.reference-citation') : null
  if (anchor?.contains(event.relatedTarget)) return
  clearTimeout(referenceOpenTimer)
  cancelReferenceClose()
  referenceCloseTimer = setTimeout(() => {
    const anchor = referencePreview.value?.anchor
    if (
      anchor?.matches(':hover') ||
      anchor === document.activeElement ||
      referencePopover.value?.isActive()
    )
      return
    closeReferencePreview()
  }, 180)
}

/** 从胶囊索引取得当前回答的引用。 */
function referenceCitations(anchor) {
  return anchor.dataset.referenceIndices
    .split(',')
    .map((index) => props.references[Number(index)])
    .filter(Boolean)
}

/** 展示来源组，需要时将键盘焦点移入卡片。 */
async function showReferencePreview(anchor, focus = false) {
  clearTimeout(referenceOpenTimer)
  cancelReferenceClose()
  if (referencePreview.value?.anchor !== anchor) closeReferencePreview()
  const citations = referenceCitations(anchor)
  if (!citations.length) return
  referencePreview.value = { anchor, citations }
  anchor.setAttribute('aria-expanded', 'true')
  anchor.setAttribute('aria-controls', referencePreviewId)
  highlightReferences(citations)
  if (focus) {
    await nextTick()
    referencePopover.value?.focus()
  }
}

/** 悬停后展示来源；键盘聚焦直接展示，避免操作等待。 */
function handleReferenceEnter(event) {
  const anchor =
    event.target instanceof Element ? event.target.closest('.reference-citation') : null
  if (!anchor || anchor.contains(event.relatedTarget)) return
  clearTimeout(referenceOpenTimer)
  cancelReferenceClose()
  if (referencePreview.value?.anchor === anchor) return
  if (event.type === 'focusin') {
    void showReferencePreview(anchor)
    return
  }
  referenceOpenTimer = setTimeout(() => {
    if (anchor.isConnected && anchor.matches(':hover')) void showReferencePreview(anchor)
  }, 400)
}

/** 向下键进入来源卡片。 */
function handleReferenceKeydown(event) {
  const anchor =
    event.target instanceof Element ? event.target.closest('.reference-citation') : null
  if (anchor && event.key === 'ArrowDown') {
    event.preventDefault()
    void showReferencePreview(anchor, true)
  }
}

/** 交给回答组件打开原文或网页。 */
function openReference(citation) {
  closeReferencePreview()
  emit('open-reference', citation)
}

/** 对命中的 Markdown 块高亮，并滚动到最接近起始行的块。 */
const highlightSourceLines = async () => {
  await nextTick()
  if (!previewRef.value) return
  const blocks = [...previewRef.value.querySelectorAll('[data-source-start]')]
  const start = props.startLine
  const end = props.endLine || start
  const matches = new Set(
    blocks.filter(
      (block) =>
        start > 0 &&
        Number(block.dataset.sourceStart) <= end &&
        Number(block.dataset.sourceEnd) >= start
    )
  )
  // 只标记最内层的命中块，避免给整张表或整个列表的其他内容染色。
  for (const block of matches) {
    let parent = block.parentElement
    while (parent && parent !== previewRef.value) {
      matches.delete(parent)
      parent = parent.parentElement
    }
  }
  for (const block of blocks) block.classList.toggle('source-line-highlight', matches.has(block))
  const target =
    [...matches].find(
      (block) =>
        Number(block.dataset.sourceStart) <= start && Number(block.dataset.sourceEnd) >= start
    ) || [...matches][0]
  target?.scrollIntoView({ block: 'center', behavior: 'auto' })
}

watch([() => props.startLine, () => props.endLine], highlightSourceLines)
const copiedTimers = new WeakMap()
const widgetMounts = new Set()
const imageBlobUrls = new Map()
let pendingMarkdownHtml = null

const getWidgetPreviewKey = (preview) => {
  return `${preview.querySelector('.widget-mount')?.dataset.widgetClosed || ''}:${preview.querySelector('.widget-source')?.textContent || ''}`
}

const collectExistingWidgets = (root) => {
  const previewsByKey = new Map()
  root.querySelectorAll('.widget-preview-render').forEach((preview) => {
    if (preview.parentElement !== root) return
    const key = getWidgetPreviewKey(preview)
    const previews = previewsByKey.get(key) || []
    previews.push(preview)
    previewsByKey.set(key, previews)
  })
  return previewsByKey
}

const findReusableWidget = (previewsByKey, key) => {
  const previews = previewsByKey.get(key)
  if (!previews) return null

  while (previews.length) {
    const preview = previews.shift()
    if (preview.isConnected) return preview
  }

  return null
}

const replaceRangeBefore = (root, startNode, endNode, nextNodes) => {
  const marker = document.createComment('widget-preview-anchor')
  root.insertBefore(marker, endNode)

  let node = startNode
  while (node && node !== marker && node !== endNode) {
    const nextNode = node.nextSibling
    root.removeChild(node)
    node = nextNode
  }

  nextNodes.forEach((nextNode) => {
    root.insertBefore(nextNode, marker)
  })
  root.removeChild(marker)
}

const replaceHtmlPreservingWidgets = (html) => {
  const root = previewRef.value
  if (!root) {
    pendingMarkdownHtml = html
    return false
  }

  pendingMarkdownHtml = null
  if (!html) {
    root.replaceChildren()
    return true
  }

  const existingPreviews = collectExistingWidgets(root)
  const template = document.createElement('template')
  template.innerHTML = html
  const nextNodes = [...template.content.childNodes]
  const hasReusablePreview = nextNodes.some((node) => {
    if (!(node instanceof Element) || !node.matches('.widget-preview-render')) return false

    const key = getWidgetPreviewKey(node)
    return existingPreviews.get(key)?.some((preview) => preview.isConnected)
  })

  if (!hasReusablePreview) {
    root.replaceChildren(...nextNodes)
    return true
  }

  const pendingNodes = []
  let cursor = root.firstChild
  nextNodes.forEach((nextNode) => {
    if (!(nextNode instanceof Element) || !nextNode.matches('.widget-preview-render')) {
      pendingNodes.push(nextNode)
      return
    }

    const key = getWidgetPreviewKey(nextNode)
    const reusablePreview = findReusableWidget(existingPreviews, key)
    if (!reusablePreview) {
      pendingNodes.push(nextNode)
      return
    }

    for (const name of ['data-source-start', 'data-source-end', 'data-reference-excluded']) {
      if (nextNode.hasAttribute(name))
        reusablePreview.setAttribute(name, nextNode.getAttribute(name))
      else reusablePreview.removeAttribute(name)
    }

    replaceRangeBefore(root, cursor, reusablePreview, pendingNodes)
    pendingNodes.length = 0
    cursor = reusablePreview.nextSibling
  })

  replaceRangeBefore(root, cursor, null, pendingNodes)
  return true
}

/** 保留未变组件的交互状态，并卸载已经移除的 Vue 实例。 */
const enhanceWidgets = () => {
  const root = previewRef.value
  for (const slot of widgetMounts) {
    if (!root?.contains(slot)) {
      render(null, slot)
      widgetMounts.delete(slot)
    }
  }
  if (!props.widgets || !root) return
  root.querySelectorAll('.widget-mount').forEach((slot) => {
    render(
      h(WidgetRenderer, {
        source: slot.parentElement.querySelector('.widget-source')?.textContent || '',
        closed: slot.dataset.widgetClosed === 'true',
        disabled: props.widgetsDisabled,
        actionHandler: props.widgetAction,
        imageLoader: props.widgetImageLoader,
        sourceImageLoader: widgetSourceImageLoader.value
      }),
      slot
    )
    widgetMounts.add(slot)
  })
}

watch(
  [() => props.widgetsDisabled, () => props.widgetImageLoader, widgetSourceImageLoader],
  enhanceWidgets
)

const enhanceCodeBlocks = () => {
  const root = previewRef.value
  if (!root) return

  root.querySelectorAll('pre:not(.fm-json):not(.widget-source)').forEach((pre) => {
    if (pre.closest('.markdown-code-block')) return

    const parent = pre.parentNode
    if (!parent) return

    const wrapper = document.createElement('div')
    wrapper.className = 'markdown-code-block'
    parent.insertBefore(wrapper, pre)
    wrapper.appendChild(pre)

    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'markdown-code-copy-btn'
    button.textContent = '复制'
    button.setAttribute('aria-label', '复制代码')
    button.setAttribute('title', '复制代码')
    wrapper.appendChild(button)
  })
}

/** 正文更新保留仍挂载的图片 URL，移除或卸载时释放。 */
const revokeImageBlobUrls = (onlyDetached = false) => {
  imageBlobUrls.forEach((img, url) => {
    if (onlyDetached && img.isConnected) return
    URL.revokeObjectURL(url)
    imageBlobUrls.delete(url)
  })
}

const enhanceDocumentImages = () => {
  const root = previewRef.value
  if (!root) return

  root.querySelectorAll('img').forEach((img) => {
    // Widget 图片自己拥有鉴权请求与 Blob，Markdown 只增强正文图片。
    if (img.closest('.yuxi-widget')) return
    const src = img.getAttribute('src')
    const imageUrl = resolveMarkdownImageUrl(src, props.resourceBaseUrl, window.location.origin)
    if (!imageUrl || img.dataset.documentImageLoaded || img.dataset.documentImageLoading) return

    img.dataset.documentImageLoading = 'true'
    apiGet(imageUrl, {}, true, 'blob')
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.blob()
      })
      .then((blob) => {
        if (!img.isConnected) return
        const objectUrl = URL.createObjectURL(blob)
        imageBlobUrls.set(objectUrl, img)
        img.src = objectUrl
        img.dataset.documentImageLoaded = 'true'
      })
      .catch((error) => {
        console.error('加载文档图片失败:', imageUrl, error)
      })
      .finally(() => {
        delete img.dataset.documentImageLoading
      })
  })
}

onMounted(async () => {
  if (pendingMarkdownHtml === null) return

  replaceHtmlPreservingWidgets(pendingMarkdownHtml)
  await nextTick()
  enhanceWidgets()
  enhanceDocumentImages()
  if (props.codeCopy) enhanceCodeBlocks()
  decorateMarkdownReferences(previewRef.value, props.references)
})

onBeforeUnmount(() => {
  closeReferencePreview()
  for (const slot of widgetMounts) render(null, slot)
  widgetMounts.clear()
  revokeImageBlobUrls()
})

watch(
  [
    () => props.content,
    () => props.widgets,
    shikiTheme,
    () => props.codeCopy,
    () => props.resourceBaseUrl,
    () => props.sourceLines,
    () => props.references
  ],
  async ([content, widgets, theme, codeCopy], _, onCleanup) => {
    closeReferencePreview()
    let expired = false
    onCleanup(() => {
      expired = true
    })

    if (!content) {
      revokeImageBlobUrls()
      replaceHtmlPreservingWidgets('')
      enhanceWidgets()
      return
    }

    const html = await renderMarkdown(content, {
      theme,
      widgets,
      sourceLines: props.sourceLines || props.references.length > 0
    })
    if (!expired) {
      replaceHtmlPreservingWidgets(html)
      revokeImageBlobUrls(true)
      await highlightSourceLines()

      await nextTick()
      if (expired) return
      enhanceWidgets()
      enhanceDocumentImages()
      if (codeCopy) enhanceCodeBlocks()
      decorateMarkdownReferences(previewRef.value, props.references)
    }
  },
  { immediate: true }
)

// === Markdown 内嵌操作按钮事件委托 ===
const handleMarkdownAction = async (e) => {
  const target = e.target instanceof Element ? e.target : e.target?.parentElement
  if (!target) return

  const reference = target.closest('.reference-citation')
  if (reference) {
    const citations = referenceCitations(reference)
    if (citations.length === 1) openReference(citations[0])
    else await showReferencePreview(reference, true)
    return
  }

  const codeCopyBtn = target.closest('.markdown-code-copy-btn')
  if (codeCopyBtn) {
    await copyCodeBlock(codeCopyBtn)
    return
  }

  const btn = target.closest('.svg-copy-btn, .svg-png-btn')
  if (!btn) return

  const container = btn.closest('.svg-inline-render')
  const svgEl = container?.querySelector('svg')
  if (!svgEl) return

  if (btn.classList.contains('svg-copy-btn')) {
    await copySvgText(svgEl, btn)
  } else if (btn.classList.contains('svg-png-btn')) {
    await copySvgAsPng(svgEl, btn)
  }
}

const writeTextToClipboard = async (text) => {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }

  const textArea = document.createElement('textarea')
  textArea.value = text
  textArea.style.position = 'fixed'
  textArea.style.left = '-999999px'
  textArea.style.top = '-999999px'
  document.body.appendChild(textArea)
  textArea.focus()
  textArea.select()
  const successful = document.execCommand('copy')
  document.body.removeChild(textArea)
  if (!successful) throw new Error('execCommand failed')
}

const copyCodeBlock = async (btn) => {
  const block = btn.closest('.markdown-code-block')
  const codeEl = block?.querySelector('pre code') || block?.querySelector('pre')
  const codeText = codeEl?.textContent || ''
  if (!codeText) return

  try {
    await writeTextToClipboard(codeText)
    showCopiedFeedback(btn)
  } catch (err) {
    console.error('复制代码失败:', err)
  }
}

// 复制 SVG 源代码
const copySvgText = async (svgEl, btn) => {
  try {
    await writeTextToClipboard(svgEl.outerHTML)
    showCopiedFeedback(btn)
  } catch (err) {
    console.error('复制 SVG 失败:', err)
  }
}

// 复制为 PNG 图片
const copySvgAsPng = async (svgEl, btn) => {
  const svgContent = svgEl.outerHTML
  const blob = new Blob([svgContent], { type: 'image/svg+xml' })
  const url = URL.createObjectURL(blob)

  try {
    // 三级递进尺寸策略：
    // 1) viewBox 固有坐标尺寸（最佳品质，不受 CSS 缩放影响）
    let width, height
    const vb = svgEl.viewBox
    if (vb && vb.baseVal && vb.baseVal.width && vb.baseVal.height) {
      width = vb.baseVal.width
      height = vb.baseVal.height
    }

    // 2) 客户端渲染尺寸（SVG 在 DOM 中一定可获取）
    if (!width || !height) {
      const rect = svgEl.getBoundingClientRect()
      width = rect.width
      height = rect.height
    }

    // 3) 回退
    if (!width || !height) {
      width = 800
      height = 600
    }

    const img = await new Promise((resolve, reject) => {
      const image = new Image()
      image.onload = () => resolve(image)
      image.onerror = reject
      image.src = url
    })

    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')
    // 不填充背景色 — Canvas 默认为全透明
    // 背景色由 SVG 自身决定
    ctx.drawImage(img, 0, 0, width, height)

    const pngBlob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'))
    if (!pngBlob) throw new Error('PNG 编码失败')
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': pngBlob })])
    showCopiedFeedback(btn)
  } catch (err) {
    console.error('复制为 PNG 失败:', err)
    try {
      await writeTextToClipboard(svgContent)
      showCopiedFeedback(btn, '已复制 SVG 源码')
    } catch (fallbackErr) {
      console.error('复制 SVG 源码失败:', fallbackErr)
    }
  } finally {
    URL.revokeObjectURL(url)
  }
}

// 反馈：按钮文字短暂变为成功文案，默认表示 PNG 或纯文本已复制。
const showCopiedFeedback = (btn, label = '已复制') => {
  const originalText = btn.dataset.originalText || btn.textContent
  btn.dataset.originalText = originalText
  btn.classList.add('is-copied')
  btn.textContent = label
  const existingTimer = copiedTimers.get(btn)
  if (existingTimer) window.clearTimeout(existingTimer)

  const timer = window.setTimeout(() => {
    btn.textContent = btn.dataset.originalText || originalText
    btn.classList.remove('is-copied')
    copiedTimers.delete(btn)
  }, 1500)
  copiedTimers.set(btn, timer)
}
</script>

<style lang="less">
.yk-markdown-preview .reference-answer-active {
  text-decoration-line: underline;
  text-decoration-color: var(--gray-200);
  text-decoration-thickness: 0.3em;
  text-underline-offset: -0.15em;
  text-decoration-skip-ink: none;
}
.yk-markdown-preview .reference-citation {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  max-width: min(95px, 100%);
  height: 21px;
  padding: 0 8px;
  margin-left: 7px;
  border: 0;
  border-radius: 999px;
  color: var(--gray-700);
  background: var(--gray-100);
  font-family: inherit;
  font-size: 11px;
  line-height: 1.5;
  vertical-align: middle;
  cursor: pointer;
  transition:
    background-color 150ms ease,
    color 150ms ease;
  &:hover,
  &[aria-expanded='true'] {
    background: var(--gray-200);
    color: var(--gray-1000);
  }
  &:focus-visible {
    outline: 2px solid var(--main-500);
    outline-offset: 2px;
  }
  .reference-citation-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .reference-citation-count {
    flex-shrink: 0;
    color: var(--gray-600);
  }
}
@media (prefers-reduced-motion: reduce) {
  .yk-markdown-preview .reference-citation {
    transition: none;
  }
}
.yk-markdown-preview .source-line-highlight {
  background-color: var(--color-warning-25);
  outline: 1px solid var(--color-warning-50);
  border-radius: 2px;
}

.yk-markdown-preview,
.flat-md-preview.yk-markdown-preview {
  max-width: 100%;
  color: var(--gray-1000);
  font-family:
    -apple-system, BlinkMacSystemFont, 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei',
    'Hiragino Sans GB', 'Source Han Sans CN', sans-serif;
  font-size: 0.9rem;
  line-height: 1.75;
  word-break: break-word;
  padding: 0;

  &.is-compact {
    font-size: 14px;
    line-height: 1.65;
  }

  h1,
  h2 {
    font-size: 1rem;
  }

  h3,
  h4 {
    font-size: 0.95rem;
  }

  h5,
  h6 {
    font-size: 0.9rem;
  }

  strong {
    font-weight: 500;
  }

  p:last-child {
    margin-bottom: 0;
  }

  li > p,
  ol > p,
  ul > p {
    margin: 0.25rem 0;
  }

  ul,
  ol {
    padding-left: 1.625rem;
  }

  ul li::marker,
  ol li::marker {
    color: var(--main-bright);
  }

  .contains-task-list {
    padding-left: 0;
    list-style: none;
  }

  .task-list-item {
    list-style: none;
  }

  .task-list-item-checkbox {
    margin-right: 8px;
    transform: translateY(1px);
  }

  a {
    color: var(--main-700);
  }

  hr {
    height: 1px;
    margin: 1.25rem 0;
    border: 0;
    background: linear-gradient(90deg, transparent, var(--gray-200), transparent);
  }

  blockquote {
    margin: 1rem 0;
    padding: 0.25rem 0 0.25rem 1rem;
    border-left: 3px solid var(--gray-200);
    color: var(--gray-700);
  }

  cite {
    position: relative;
    margin: 0 4px;
    padding: 0 0.25rem;
    border-radius: 4px;
    outline: 2px solid var(--gray-100);
    background-color: var(--gray-100);
    color: var(--gray-800);
    font-size: 12px;
    font-style: normal;
    cursor: pointer;
    user-select: none;

    &:hover::after {
      content: attr(source);
      position: absolute;
      bottom: calc(100% + 6px);
      left: 50%;
      z-index: 1000;
      width: max-content;
      min-width: 100px;
      max-width: 400px;
      padding: 8px 12px;
      border-radius: 6px;
      transform: translateX(-50%);
      background-color: var(--gray-1000);
      color: var(--gray-0);
      font-size: 13px;
      line-height: 1.5;
      text-align: center;
      white-space: normal;
      word-break: break-word;
      pointer-events: none;
      box-shadow: 0 2px 8px var(--shadow-3);
    }

    &:hover::before {
      content: '';
      position: absolute;
      bottom: 100%;
      left: 50%;
      z-index: 1000;
      transform: translateX(-50%);
      border: 5px solid transparent;
      border-top-color: var(--gray-900);
    }
  }

  code {
    font-family:
      'Menlo', 'Monaco', 'Consolas', 'PingFang SC', 'Noto Sans SC', 'Microsoft YaHei',
      'Hiragino Sans GB', 'Source Han Sans CN', 'Courier New', monospace;
    font-size: 13px;
    line-height: 1.5;
    letter-spacing: 0.025em;
    tab-size: 4;
    -moz-tab-size: 4;
  }

  :not(pre) > code {
    padding: 1px 5px;
    border-radius: 4px;
    background-color: var(--gray-25);
  }

  pre.shiki {
    margin: 12px 0;
    padding: 12px 14px;
    border: 1px solid var(--gray-100);
    border-radius: 8px;
    overflow: auto;
    font-size: 13px;
    line-height: 1.5;
  }

  &:not(.is-dark) pre.shiki {
    background: var(--gray-25) !important;
  }

  &.is-dark pre.shiki {
    border-color: var(--gray-200);
  }

  .markdown-code-block {
    position: relative;
    max-width: 100%;
    margin: 12px 0;

    > pre {
      margin: 0;
      padding-right: 64px;
    }

    > pre:not(.shiki) {
      padding: 12px 64px 12px 14px;
      border: 1px solid var(--gray-100);
      border-radius: 8px;
      overflow: auto;
      background: var(--gray-25);
      font-size: 13px;
      line-height: 1.5;
    }
  }

  .markdown-code-copy-btn {
    position: absolute;
    top: 8px;
    right: 8px;
    z-index: 2;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 24px;
    padding: 0 8px;
    border: 1px solid var(--gray-200);
    border-radius: 5px;
    background: var(--gray-0);
    color: var(--gray-600);
    box-shadow: 0 1px 4px var(--shadow-2);
    font-size: 12px;
    line-height: 1;
    cursor: pointer;
    opacity: 0.72;
    transition:
      background-color 0.15s ease,
      border-color 0.15s ease,
      color 0.15s ease,
      opacity 0.15s ease;
    user-select: none;
    white-space: nowrap;

    &:hover,
    &:focus-visible,
    &.is-copied {
      border-color: var(--gray-300);
      color: var(--gray-900);
      opacity: 1;
    }

    &:focus-visible {
      outline: 2px solid var(--main-300);
      outline-offset: 2px;
    }
  }

  &.is-dark .markdown-code-copy-btn {
    border-color: var(--gray-200);
    background: var(--gray-0);
    color: var(--gray-500);

    &:hover,
    &:focus-visible,
    &.is-copied {
      border-color: var(--gray-300);
      color: var(--gray-900);
    }
  }

  table {
    width: 100%;
    border-collapse: collapse;
    margin: 1rem 0;
    font-size: 0.8rem;
    display: table;
  }

  th,
  td {
    padding: 0.5rem 0;
    text-align: left;
    border: none;
  }

  td {
    border-bottom: 1px solid var(--gray-100);
    color: var(--gray-800);
  }

  tbody tr:last-child td {
    border-bottom: none;
  }

  th {
    border-bottom: 1px solid var(--gray-200);
    color: var(--gray-800);
    font-weight: 600;
  }

  img {
    max-width: 100%;
    height: auto;
  }

  .katex {
    font-size: 1.05em;
  }

  .katex-display {
    margin: 1rem 0;
    overflow-x: auto;
    overflow-y: hidden;
  }

  .frontmatter-card {
    margin: 0 0 20px;
    padding: 12px 14px;
    border-radius: 8px;
    background: var(--gray-25);
  }

  .frontmatter-card .fm-body {
    display: grid;
    gap: 6px;
  }

  .frontmatter-card .fm-row {
    display: grid;
    grid-template-columns: 96px minmax(0, 1fr);
    gap: 14px;
    align-items: baseline;
  }

  .frontmatter-card .fm-key {
    color: var(--gray-500);
    font-family: 'JetBrains Mono', 'Fira Code', 'Monaco', 'Menlo', monospace;
    font-size: 12px;
    line-height: 1.5;
  }

  .frontmatter-card .fm-value {
    color: var(--gray-800);
    font-size: 13px;
    line-height: 1.5;
    min-width: 0;
  }

  .frontmatter-card .fm-doc-title {
    color: var(--gray-1000);
    font-weight: 600;
  }

  .frontmatter-card .fm-tag {
    display: inline-flex;
    align-items: center;
    margin: 0 4px 4px 0;
    padding: 1px 6px;
    border-radius: 4px;
    background: var(--gray-100);
    color: var(--gray-700);
    font-size: 12px;
    line-height: 1.5;
  }

  .frontmatter-card .fm-json {
    margin: 2px 0 0;
    padding: 8px 10px;
    border-radius: 6px;
    overflow: auto;
    background: var(--gray-50);
    color: var(--gray-800);
    font-size: 12px;
    line-height: 1.5;
  }

  .svg-inline-render {
    position: relative;
    max-width: 100%;
    height: auto;
    overflow: auto;
    margin: 12px 0;

    svg {
      max-width: 100%;
      height: auto;
    }

    .svg-actions {
      position: absolute;
      top: 8px;
      right: 8px;
      z-index: 10;
      display: none;
      gap: 4px;

      .svg-action-btn {
        display: inline-flex;
        align-items: center;
        padding: 3px 8px;
        border: 1px solid var(--gray-200);
        border-radius: 4px;
        background: var(--gray-0);
        color: var(--gray-700);
        font-size: 12px;
        line-height: 1.5;
        cursor: pointer;
        transition: all 0.15s ease;
        white-space: nowrap;
        user-select: none;

        &:hover {
          background: var(--gray-100);
          color: var(--gray-900);
        }
      }
    }

    &:hover .svg-actions {
      display: inline-flex;
    }
  }

  &.is-dark .svg-inline-render {
    background: var(--gray-10);
    border-radius: 4px;
  }

  &.is-dark .svg-actions .svg-action-btn {
    background: var(--gray-30);
    border-color: var(--border-surface);
    color: var(--gray-600);

    &:hover {
      background: var(--gray-50);
      color: var(--gray-900);
    }
  }
}
</style>
