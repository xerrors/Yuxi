import { escapeHtml } from './html.js'

export const HTML_PREVIEW_WIDTH = 800
export const HTML_PREVIEW_HEIGHT = 360
export const HTML_PREVIEW_LOADING_HEIGHT = 180
export const HTML_PREVIEW_MIN_HEIGHT = 1
export const HTML_PREVIEW_MAX_HEIGHT = 700

const HTML_PREVIEW_LANGUAGE = 'html:preview'
const isHtmlPreviewLanguage = (language) => language === HTML_PREVIEW_LANGUAGE
const isStreamingHtmlPreviewLanguage = (language) =>
  language.startsWith('html:') && HTML_PREVIEW_LANGUAGE.startsWith(language)

const renderHtmlPreviewContainer = (content) =>
  [
    `<div class="html-preview-render" style="--html-preview-width: ${HTML_PREVIEW_WIDTH}px; --html-preview-height: ${HTML_PREVIEW_HEIGHT}px; --html-preview-loading-height: ${HTML_PREVIEW_LOADING_HEIGHT}px; --html-preview-min-height: ${HTML_PREVIEW_MIN_HEIGHT}px; --html-preview-max-height: ${HTML_PREVIEW_MAX_HEIGHT}px;">`,
    content,
    `</div>`
  ].join('')

const escapeSrcdoc = (value) => escapeHtml(value).replaceAll('\r', '').replaceAll('\n', '&#10;')

const renderHtmlPreviewLoading = () =>
  renderHtmlPreviewContainer(
    [
      `<div class="html-preview-loading-slot" aria-live="polite" aria-label="HTML 预览加载中">`,
      `<div class="html-preview-loading-canvas">`,
      `<div class="html-preview-loading-text">HTML 预览加载中...</div>`,
      `<div class="html-preview-skeleton html-preview-skeleton-title"></div>`,
      `<div class="html-preview-skeleton-grid">`,
      `<div class="html-preview-skeleton html-preview-skeleton-card"></div>`,
      `<div class="html-preview-skeleton html-preview-skeleton-card"></div>`,
      `<div class="html-preview-skeleton html-preview-skeleton-card"></div>`,
      `</div>`,
      `<div class="html-preview-skeleton html-preview-skeleton-line wide"></div>`,
      `<div class="html-preview-skeleton html-preview-skeleton-line"></div>`,
      `<div class="html-preview-skeleton html-preview-skeleton-line short"></div>`,
      `</div>`,
      `</div>`
    ].join('')
  )

const renderHtmlPreview = (html, sanitizeHtml) => {
  const safeHtml = sanitizeHtml(html)
  const srcdoc = escapeSrcdoc(safeHtml)

  return renderHtmlPreviewContainer(
    [
      `<pre class="html-preview-srcdoc">${srcdoc}</pre>`,
      `<div class="html-preview-frame-slot"></div>`
    ].join('')
  )
}

/**
 * 将 Markdown 中的 ```html:preview 围栏代码块转换为 sandboxed iframe 预览。
 *
 * 普通 ```html 代码块保持不变，避免误伤需要展示源码的回答。
 * 未闭合的 html:preview 围栏渲染为加载占位块，等闭合后再替换成 iframe。
 */
export function renderHtmlPreviewBlocks(markdown, options = {}) {
  const sanitizeHtml = options.sanitizeHtml || ((html) => html)
  const lines = String(markdown || '').split('\n')
  const output = []
  let i = 0

  while (i < lines.length) {
    const openMatch = lines[i].match(/^( {0,3})(`{3,}|~{3,})\s*(\S*)/)
    const language = openMatch?.[3].toLowerCase()

    if (
      openMatch &&
      (isHtmlPreviewLanguage(language) || isStreamingHtmlPreviewLanguage(language))
    ) {
      const indent = openMatch[1]
      const fenceChar = openMatch[2]
      const openLine = lines[i]
      const htmlLines = []
      i++

      let closed = false
      while (i < lines.length) {
        const closeMatch = lines[i].match(/^( {0,3})(`{3,}|~{3,})\s*$/)
        if (
          closeMatch &&
          closeMatch[1].length <= indent.length &&
          closeMatch[2][0] === fenceChar[0] &&
          closeMatch[2].length >= fenceChar.length
        ) {
          closed = true
          if (isHtmlPreviewLanguage(language)) {
            output.push(renderHtmlPreview(htmlLines.join('\n'), sanitizeHtml))
          } else {
            output.push([openLine, ...htmlLines, lines[i]].join('\n'))
          }
          i++
          break
        }

        htmlLines.push(lines[i])
        i++
      }

      if (!closed) {
        output.push(renderHtmlPreviewLoading())
      }
    } else {
      output.push(lines[i])
      i++
    }
  }

  return output.join('\n')
}
