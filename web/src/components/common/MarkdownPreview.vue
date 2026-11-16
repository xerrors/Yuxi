<template>
  <div
    ref="previewRef"
    :class="[
      'yk-markdown-preview',
      'flat-md-preview',
      { 'is-dark': themeStore.isDark, 'is-compact': compact }
    ]"
    @click="handleMarkdownAction"
  ></div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useThemeStore } from '@/stores/theme'
import { useUserStore } from '@/stores/user'
import { renderMarkdown } from '@/utils/markdown_preview'
import { HTML_PREVIEW_MAX_HEIGHT, HTML_PREVIEW_MIN_HEIGHT } from '@/utils/htmlPreviewRenderer'
import 'katex/dist/katex.min.css'
const props = defineProps({
  content: {
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
  }
})

const themeStore = useThemeStore()
const userStore = useUserStore()
const shikiTheme = computed(() => (themeStore.isDark ? 'github-dark' : 'github-light'))
const previewRef = ref(null)
const copiedTimers = new WeakMap()
const htmlPreviewFrames = new Map()
const kbImageBlobUrls = new Set()
let pendingMarkdownHtml = null

const HTML_PREVIEW_HEIGHT_MESSAGE = 'yuxi-html-preview-height'

const KB_IMAGE_PROXY_PATH_RE = /\/api\/knowledge\/databases\/[^/]+\/images\//

const getHtmlPreviewCssNumber = (slot, property, fallback) => {
  const preview = slot.closest('.html-preview-render')
  const rawValue = preview ? getComputedStyle(preview).getPropertyValue(property) : ''
  const parsedValue = Number.parseInt(rawValue, 10)
  return Number.isFinite(parsedValue) ? parsedValue : fallback
}

const createMeasuredSrcdoc = (html, previewId) => {
  const scriptEndTag = '<' + '/script>'
  const baseStyle = `<style data-yuxi-html-preview-base>
html,
body {
  margin: 0;
  min-height: 0;
}

body {
  overflow: auto;
}

*,
*::before,
*::after {
  box-sizing: border-box;
}
</style>`
  const script = `<script>
(() => {
  const previewId = ${JSON.stringify(previewId)};
  const ignoredTags = new Set(['SCRIPT', 'STYLE', 'LINK', 'META', 'TITLE']);
  const getContentHeight = () => {
    const body = document.body;
    if (!body) return 0;

    const bodyRect = body.getBoundingClientRect();
    const bodyStyle = getComputedStyle(body);
    const paddingTop = Number.parseFloat(bodyStyle.paddingTop) || 0;
    const paddingBottom = Number.parseFloat(bodyStyle.paddingBottom) || 0;
    let bottom = paddingTop + paddingBottom;

    for (const child of body.children) {
      if (ignoredTags.has(child.tagName)) continue;

      const rect = child.getBoundingClientRect();
      const style = getComputedStyle(child);
      const marginBottom = Number.parseFloat(style.marginBottom) || 0;
      bottom = Math.max(bottom, rect.bottom - bodyRect.top + marginBottom);
    }

    return Math.ceil(Math.max(bottom, body.scrollHeight));
  };
  const sendHeight = () => {
    const height = getContentHeight();
    parent.postMessage({ type: ${JSON.stringify(HTML_PREVIEW_HEIGHT_MESSAGE)}, id: previewId, height }, '*');
  };
  document.querySelectorAll('img, video').forEach((node) => {
    node.addEventListener('load', sendHeight);
    node.addEventListener('error', sendHeight);
  });
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(sendHeight).catch(() => {});
  }
  window.addEventListener('DOMContentLoaded', sendHeight);
  window.addEventListener('load', sendHeight);
  if (typeof ResizeObserver !== 'undefined') {
    const observer = new ResizeObserver(sendHeight);
    if (document.body) observer.observe(document.body);
    document.querySelectorAll('body > *').forEach((node) => observer.observe(node));
  }
  setTimeout(sendHeight, 0);
  setTimeout(sendHeight, 100);
  setTimeout(sendHeight, 500);
})();
${scriptEndTag}`

  const withBaseStyle = /<\/head\s*>/i.test(html)
    ? html.replace(/<\/head\s*>/i, `${baseStyle}</head>`)
    : `${baseStyle}${html}`

  return /<\/body\s*>/i.test(withBaseStyle)
    ? withBaseStyle.replace(/<\/body\s*>/i, `${script}</body>`)
    : `${withBaseStyle}${script}`
}

const handleHtmlPreviewHeight = (event) => {
  const data = event.data
  if (!data || data.type !== HTML_PREVIEW_HEIGHT_MESSAGE) return

  const entry = htmlPreviewFrames.get(data.id)
  if (!entry || event.source !== entry.iframe.contentWindow) return

  const contentHeight = Number(data.height)
  if (!Number.isFinite(contentHeight) || contentHeight <= 0) return

  const minHeight = getHtmlPreviewCssNumber(
    entry.slot,
    '--html-preview-min-height',
    HTML_PREVIEW_MIN_HEIGHT
  )
  const maxHeight = getHtmlPreviewCssNumber(
    entry.slot,
    '--html-preview-max-height',
    HTML_PREVIEW_MAX_HEIGHT
  )
  const nextHeight = Math.min(Math.max(Math.ceil(contentHeight), minHeight), maxHeight)
  entry.slot.style.height = `${nextHeight}px`
  entry.slot.dataset.overflow = contentHeight > maxHeight ? 'true' : 'false'
}

const getHtmlPreviewKey = (preview) => {
  return preview.querySelector('.html-preview-srcdoc')?.textContent || ''
}

const collectExistingHtmlPreviews = (root) => {
  const previewsByKey = new Map()
  root.querySelectorAll('.html-preview-render').forEach((preview) => {
    const key = getHtmlPreviewKey(preview)
    if (!key) return

    const previews = previewsByKey.get(key) || []
    previews.push(preview)
    previewsByKey.set(key, previews)
  })
  return previewsByKey
}

const findReusableHtmlPreview = (previewsByKey, key) => {
  const previews = previewsByKey.get(key)
  if (!previews) return null

  while (previews.length) {
    const preview = previews.shift()
    if (preview.isConnected) return preview
  }

  return null
}

const replaceRangeBefore = (root, startNode, endNode, nextNodes) => {
  const marker = document.createComment('html-preview-anchor')
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

const replaceHtmlPreservingPreviews = (html) => {
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

  const existingPreviews = collectExistingHtmlPreviews(root)
  const template = document.createElement('template')
  template.innerHTML = html
  const nextNodes = [...template.content.childNodes]
  const hasReusablePreview = nextNodes.some((node) => {
    if (!(node instanceof Element) || !node.classList.contains('html-preview-render')) return false

    const key = getHtmlPreviewKey(node)
    return key && existingPreviews.get(key)?.some((preview) => preview.isConnected)
  })

  if (!hasReusablePreview) {
    root.replaceChildren(...nextNodes)
    return true
  }

  const pendingNodes = []
  let cursor = root.firstChild
  nextNodes.forEach((nextNode) => {
    if (!(nextNode instanceof Element) || !nextNode.classList.contains('html-preview-render')) {
      pendingNodes.push(nextNode)
      return
    }

    const key = getHtmlPreviewKey(nextNode)
    const reusablePreview = key ? findReusableHtmlPreview(existingPreviews, key) : null
    if (!reusablePreview) {
      pendingNodes.push(nextNode)
      return
    }

    replaceRangeBefore(root, cursor, reusablePreview, pendingNodes)
    pendingNodes.length = 0
    cursor = reusablePreview.nextSibling
  })

  replaceRangeBefore(root, cursor, null, pendingNodes)
  return true
}

const cleanupHtmlPreviewFrames = () => {
  const root = previewRef.value
  for (const [previewId, entry] of htmlPreviewFrames) {
    if (!root || !entry.slot.isConnected || !root.contains(entry.slot)) {
      htmlPreviewFrames.delete(previewId)
    }
  }
}

const enhanceCodeBlocks = () => {
  const root = previewRef.value
  if (!root) return

  root.querySelectorAll('pre:not(.fm-json):not(.html-preview-srcdoc)').forEach((pre) => {
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

const enhanceHtmlPreviews = () => {
  const root = previewRef.value
  if (!root) return

  root.querySelectorAll('.html-preview-frame-slot').forEach((slot) => {
    if (slot.querySelector('iframe')) return

    const iframe = document.createElement('iframe')
    const previewId = `html-preview-${
      globalThis.crypto?.randomUUID
        ? globalThis.crypto.randomUUID()
        : `${Date.now()}-${Math.random()}`
    }`
    iframe.className = 'html-preview-frame'
    iframe.title = 'HTML 预览'
    iframe.setAttribute('sandbox', 'allow-scripts')
    iframe.setAttribute('loading', 'lazy')
    iframe.setAttribute('referrerpolicy', 'no-referrer')
    iframe.setAttribute('scrolling', 'auto')
    iframe.srcdoc = createMeasuredSrcdoc(
      slot.parentElement?.querySelector('.html-preview-srcdoc')?.textContent || '',
      previewId
    )
    htmlPreviewFrames.set(previewId, { iframe, slot })
    slot.appendChild(iframe)
  })
}

const revokeKbImageBlobUrls = () => {
  kbImageBlobUrls.forEach((url) => URL.revokeObjectURL(url))
  kbImageBlobUrls.clear()
}

const enhanceKbImages = () => {
  const root = previewRef.value
  if (!root) return

  root.querySelectorAll('img').forEach((img) => {
    const src = img.getAttribute('src')
    if (!src || !KB_IMAGE_PROXY_PATH_RE.test(src) || img.dataset.kbImageLoaded) return

    img.dataset.kbImageLoading = 'true'
    fetch(src, { headers: userStore.getAuthHeaders() })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.blob()
      })
      .then((blob) => {
        if (!img.isConnected) return
        const objectUrl = URL.createObjectURL(blob)
        kbImageBlobUrls.add(objectUrl)
        img.src = objectUrl
        img.dataset.kbImageLoaded = 'true'
      })
      .catch((error) => {
        console.error('加载知识库图片失败:', src, error)
      })
      .finally(() => {
        delete img.dataset.kbImageLoading
      })
  })
}

onMounted(async () => {
  if (pendingMarkdownHtml === null) return

  replaceHtmlPreservingPreviews(pendingMarkdownHtml)
  await nextTick()
  enhanceHtmlPreviews()
  enhanceKbImages()
  if (props.codeCopy) enhanceCodeBlocks()
})

window.addEventListener('message', handleHtmlPreviewHeight)

onBeforeUnmount(() => {
  window.removeEventListener('message', handleHtmlPreviewHeight)
  htmlPreviewFrames.clear()
  revokeKbImageBlobUrls()
})

watch(
  [() => props.content, shikiTheme, () => props.codeCopy],
  async ([content, theme, codeCopy], _, onCleanup) => {
    let expired = false
    onCleanup(() => {
      expired = true
    })

    if (!content) {
      htmlPreviewFrames.clear()
      revokeKbImageBlobUrls()
      replaceHtmlPreservingPreviews('')
      return
    }

    const html = await renderMarkdown(content, { theme })
    if (!expired) {
      replaceHtmlPreservingPreviews(html)
      revokeKbImageBlobUrls()
      cleanupHtmlPreviewFrames()

      await nextTick()
      if (expired) return
      enhanceHtmlPreviews()
      enhanceKbImages()
      if (codeCopy) enhanceCodeBlocks()
      cleanupHtmlPreviewFrames()
    }
  },
  { immediate: true }
)

// === Markdown 内嵌操作按钮事件委托 ===
const handleMarkdownAction = async (e) => {
  const target = e.target instanceof Element ? e.target : e.target?.parentElement
  if (!target) return

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
    if (pngBlob) {
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': pngBlob })])
      showCopiedFeedback(btn)
    }
  } catch (err) {
    console.error('复制为 PNG 失败:', err)
    // fallback: 尝试复制 SVG 源码
    try {
      await writeTextToClipboard(svgContent)
      console.log('PNG 复制失败，已回退复制 SVG 源码')
    } catch (fallbackErr) {
      console.error('复制 SVG 源码失败:', fallbackErr)
    }
  } finally {
    URL.revokeObjectURL(url)
  }
}

// 反馈：按钮文字短暂变为「已复制」
const showCopiedFeedback = (btn) => {
  const originalText = btn.dataset.originalText || btn.textContent
  btn.dataset.originalText = originalText
  btn.classList.add('is-copied')
  btn.textContent = '已复制'
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
      background-color: #222;
      color: #fff;
      font-size: 13px;
      line-height: 1.5;
      text-align: center;
      white-space: normal;
      word-break: break-word;
      pointer-events: none;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
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
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
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
    border-color: rgba(255, 255, 255, 0.12);
    background: rgba(12, 13, 13, 0.92);
    color: var(--gray-500);

    &:hover,
    &:focus-visible,
    &.is-copied {
      border-color: rgba(255, 255, 255, 0.2);
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

  tr {
    background-color: var(--gray-0);
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

  .html-preview-render {
    width: var(--html-preview-width, 800px);
    max-width: 100%;
    margin: 14px 0;
    overflow: hidden;
    background: var(--gray-0);
  }

  .html-preview-frame-slot {
    display: block;
    width: 100%;
    height: var(--html-preview-min-height, 1px);
    max-height: var(--html-preview-max-height, 700px);
    background: #fff;
  }

  .html-preview-loading-slot {
    display: block;
    width: 100%;
    height: min(var(--html-preview-loading-height, 180px), 29vh);
    padding: 14px;
    background: linear-gradient(180deg, #fff 0%, var(--gray-50) 100%);
  }

  .html-preview-loading-canvas {
    box-sizing: border-box;
    width: 100%;
    height: 100%;
    padding: 14px 18px;
  }

  .html-preview-loading-text {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
  }

  .html-preview-skeleton {
    position: relative;
    overflow: hidden;
    border-radius: 50%;
    background: var(--gray-100);
  }

  .html-preview-skeleton::after {
    content: '';
    position: absolute;
    inset: 0;
    transform: translateX(-100%);
    background: linear-gradient(
      90deg,
      transparent 0%,
      rgba(255, 255, 255, 0.62) 48%,
      transparent 100%
    );
    animation: html-preview-skeleton-shimmer 1.45s ease-in-out infinite;
  }

  .html-preview-skeleton-title {
    width: min(280px, 52%);
    height: 20px;
    margin-bottom: 14px;
    border-radius: 6px;
  }

  .html-preview-skeleton-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
    margin-bottom: 12px;
  }

  .html-preview-skeleton-card {
    height: 42px;
    border-radius: 8px;
  }

  .html-preview-skeleton-line {
    width: 70%;
    height: 14px;
    margin-top: 8px;
    border-radius: 999px;
  }

  .html-preview-skeleton-line.wide {
    width: 88%;
  }

  .html-preview-skeleton-line.short {
    width: 46%;
  }

  .html-preview-srcdoc {
    display: none;
  }

  .html-preview-frame {
    display: block;
    width: 100%;
    height: 100%;
    border: 0;
    background: #fff;
  }

  &.is-dark .html-preview-render {
    border-color: rgba(255, 255, 255, 0.12);
    background: rgba(255, 255, 255, 0.03);
  }

  &.is-dark .html-preview-loading-slot {
    background: rgba(255, 255, 255, 0.04);
  }

  &.is-dark .html-preview-loading-canvas {
    background: transparent;
  }

  &.is-dark .html-preview-skeleton {
    background: rgba(255, 255, 255, 0.09);
  }

  &.is-dark .html-preview-skeleton::after {
    background: linear-gradient(
      90deg,
      transparent 0%,
      rgba(255, 255, 255, 0.14) 48%,
      transparent 100%
    );
  }

  @media (max-width: 640px) {
    .html-preview-loading-slot {
      padding: 18px;
    }

    .html-preview-loading-canvas {
      padding: 22px;
    }

    .html-preview-skeleton-grid {
      grid-template-columns: 1fr;
    }

    .html-preview-skeleton-card {
      height: 54px;
    }
  }

  @keyframes html-preview-skeleton-shimmer {
    100% {
      transform: translateX(100%);
    }
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
    background: rgba(255, 255, 255, 0.03);
    border-radius: 4px;
  }

  &.is-dark .svg-actions .svg-action-btn {
    background: rgba(255, 255, 255, 0.08);
    border-color: rgba(255, 255, 255, 0.12);
    color: var(--gray-300);

    &:hover {
      background: rgba(255, 255, 255, 0.15);
      color: var(--gray-100);
    }
  }
}
</style>
