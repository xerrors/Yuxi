import { widgetFencePlugin } from './chatWidgets.js'
import MarkdownIt from 'markdown-it'
import markdownItKatex from '@vscode/markdown-it-katex'
import taskLists from 'markdown-it-task-lists'
import DOMPurify from 'dompurify'
import { load as yamlLoad } from 'js-yaml'
import { escapeHtml } from './html.js'
import { normalizeCodeLanguage } from './file_preview.js'
import { renderSvgBlocks } from './svgRenderer.js'
import { createMarkdownRenderCache } from './markdownRenderCache.js'

const markdownKatexPlugin = markdownItKatex.default || markdownItKatex
const FRONTMATTER_MARKER = '---'

let highlighterPromise
const getHighlighter = () => {
  if (!highlighterPromise) {
    highlighterPromise = import('shiki')
      .then(({ createHighlighter }) => createHighlighter({
        themes: ['github-light', 'github-dark'],
        langs: ['plaintext']
      }))
      .catch((error) => {
        highlighterPromise = undefined
        throw error
      })
  }
  return highlighterPromise
}

/** 修复展示 HTML 的弯引号，保留 widget 围栏内的原始 JSON 字节。 */
const normalizeHtmlTagQuotes = (content, widgets = false) => {
  const source = String(content || '')
  if (!/[“”]/.test(source)) return source
  const normalize = (text) =>
    text.replace(/<[^>]+>/g, (tag) => tag.replaceAll('“', '"').replaceAll('”', '"'))
  if (!widgets) return normalize(source)

  const lines = source.split('\n')
  const ranges = new MarkdownIt({ html: true })
    .parse(source, {})
    .filter((token) => token.type === 'fence' && token.info.trim() === 'widget')
  let cursor = 0
  const result = []
  for (const token of ranges) {
    if (cursor < token.map[0])
      result.push(...normalize(lines.slice(cursor, token.map[0]).join('\n')).split('\n'))
    result.push(...lines.slice(token.map[0], token.map[1]))
    cursor = token.map[1]
  }
  if (cursor < lines.length) result.push(...normalize(lines.slice(cursor).join('\n')).split('\n'))
  return result.join('\n')
}

const renderFrontmatterValue = (value) => {
  if (Array.isArray(value)) {
    return value.map((item) => `<span class="fm-tag">${escapeHtml(item)}</span>`).join('')
  }

  if (value instanceof Date) {
    return escapeHtml(value.toISOString().slice(0, 10))
  }

  if (typeof value === 'object' && value !== null) {
    return `<pre class="fm-json">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`
  }

  return escapeHtml(value)
}

const renderFrontmatterField = (key, value) => {
  if (key === 'title') {
    return `<strong class="fm-doc-title">${escapeHtml(value)}</strong>`
  }

  if (key === 'date') {
    return `<time>${renderFrontmatterValue(value)}</time>`
  }

  if (key === 'tags' && Array.isArray(value)) {
    return value.map((tag) => `<span class="fm-tag">#${escapeHtml(tag)}</span>`).join('')
  }

  return renderFrontmatterValue(value)
}

const getMarkdownLine = (state, line) => {
  const start = state.bMarks[line]
  const end = state.eMarks[line]
  return state.src.slice(start, end)
}

const renderFrontmatterCard = (data) => {
  const rows = Object.entries(data)
    .map(
      ([key, value]) => `
        <div class="fm-row">
          <div class="fm-key">${escapeHtml(key)}</div>
          <div class="fm-value">${renderFrontmatterField(key, value)}</div>
        </div>`
    )
    .join('')

  return `<section class="frontmatter-card">
    <div class="fm-body">${rows || '<div class="fm-empty">无 frontmatter 信息</div>'}</div>
  </section>`
}

const markdownItFrontmatterCard = (md) => {
  md.block.ruler.before('table', 'frontmatter_card', (state, startLine, endLine, silent) => {
    if (startLine !== 0 || getMarkdownLine(state, startLine).trim() !== FRONTMATTER_MARKER) {
      return false
    }

    let nextLine = startLine + 1
    while (nextLine < endLine && getMarkdownLine(state, nextLine).trim() !== FRONTMATTER_MARKER) {
      nextLine += 1
    }

    if (nextLine >= endLine) return false

    const rawYaml = Array.from({ length: nextLine - startLine - 1 }, (_, index) =>
      getMarkdownLine(state, startLine + index + 1)
    ).join('\n')
    let data

    try {
      data = yamlLoad(rawYaml) || {}
    } catch {
      const foldedDescription = rawYaml.replace(
        /^description:[ \t]*\n(?=[ \t]+\S)/m,
        'description: >-\n'
      )
      if (foldedDescription === rawYaml) return false

      try {
        data = yamlLoad(foldedDescription) || {}
      } catch {
        return false
      }
    }

    if (!data || typeof data !== 'object' || Array.isArray(data)) return false
    if (silent) return true

    const token = state.push('frontmatter_card', 'section', 0)
    token.block = true
    token.map = [startLine, nextLine + 1]
    token.meta = { data }
    state.line = nextLine + 1
    return true
  })

  md.renderer.rules.frontmatter_card = (tokens, idx) => renderFrontmatterCard(tokens[idx].meta.data)
}

const rendererCache = new Map()
const renderedHtmlCache = createMarkdownRenderCache()
const CODE_FENCE_RE = /(^|\n) {0,3}(```|~~~)/
const CODE_FENCE_LANGUAGE_RE = /(^|\n) {0,3}(```+|~~~+)[ \t]*([^\s:,`]*)/g

const normalizeTheme = (theme) => (theme === 'github-dark' ? 'github-dark' : 'github-light')
const hasCodeFence = (content) => CODE_FENCE_RE.test(content)

const collectCodeFenceLanguages = (content) => {
  const languages = new Set()
  for (const match of String(content || '').matchAll(CODE_FENCE_LANGUAGE_RE)) {
    const language = normalizeCodeLanguage(match[3])
    if (language) languages.add(language)
  }
  return [...languages]
}

const ensureLanguages = async (highlighter, languages) => {
  const loaded = new Set(highlighter.getLoadedLanguages())
  await Promise.all(
    languages
      .filter((language) => !loaded.has(language))
      .map((language) => {
        try {
          return highlighter.loadLanguage(language).catch(() => null)
        } catch {
          return null
        }
      })
  )
}

/** 把 Markdown block 的原始行范围交给预览定位，不改写源文本。 */
const markdownSourceLines = (md) => {
  // 在 taskLists 注入 checkbox HTML 前判断原始正文类型。
  md.core.ruler.after('inline', 'source_lines', (state) => {
    if (!state.env.sourceLines) return
    for (const [index, token] of state.tokens.entries()) {
      if (!token.map || token.nesting === -1 || token.type === 'inline') continue
      token.attrSet('data-source-start', String(token.map[0] + 1))
      token.attrSet('data-source-end', String(token.map[1]))
      if (token.type === 'heading_open') token.attrSet('data-reference-excluded', '')
      if (!['paragraph_open', 'tr_open'].includes(token.type)) continue
      const target = token.hidden ? state.tokens[index - 1] : token
      if (token.type === 'paragraph_open') {
        const children = state.tokens[index + 1].children || []
        if (children.some((child) => child.type === 'html_inline') ||
          !children.some((child) => child.type === 'image' || (child.type === 'text' && child.content.trim()))) {
          target.attrSet('data-reference-excluded', '')
          continue
        }
      }
      // 紧凑列表隐藏 paragraph 标签，标注范围仍由正文拥有，不能扩大到同项代码或组件。
      target.attrSet('data-reference-start', String(token.map[0] + 1))
      target.attrSet('data-reference-end', String(token.map[1]))
    }
  })
  for (const type of ['fence', 'code_block', 'html_block', 'frontmatter_card', 'math_block']) {
    const render = md.renderer.rules[type]
    if (!render) continue
    md.renderer.rules[type] = (tokens, index, options, env, renderer) => {
      const token = tokens[index]
      let html = render(tokens, index, options, env, renderer)
      // 在 token 层渲染预览，避免预处理压缩围栏后改变后续原文行号。
      if (env.sourceLines && type === 'fence') {
        const fenced = `${token.markup}${token.info}\n${token.content}${token.markup}`
        const language = token.info.trim().toLowerCase()
        if (language === 'svg') html = renderSvgBlocks(fenced)
      }
      const map = token.map
      return env.sourceLines && map
        ? `<div data-source-start="${map[0] + 1}" data-source-end="${map[1]}" data-reference-excluded>${html}</div>`
        : html
    }
  }
}

export const createMarkdownRenderer = ({ themeName, highlighter }) =>
  new MarkdownIt({
    html: true,
    breaks: true,
    linkify: true,
    typographer: true,
    highlight: highlighter
      ? (code, lang) => {
          const language = normalizeCodeLanguage(lang)
          const loadedLanguages = highlighter.getLoadedLanguages()
          const targetLanguage = loadedLanguages.includes(language) ? language : 'plaintext'
          return highlighter.codeToHtml(code, { lang: targetLanguage, theme: themeName })
        }
      : undefined
  })
    .use(markdownKatexPlugin, { throwOnError: false, errorColor: '#cc0000', trust: false })
    .use(taskLists, { enabled: false, label: false, labelAfter: false })
    .use(markdownItFrontmatterCard)
    .use(markdownSourceLines)
    .use(widgetFencePlugin)

const getRenderer = async (theme, needsHighlight) => {
  const themeName = normalizeTheme(theme)
  const cacheKey = needsHighlight ? themeName : 'plain'
  const cached = rendererCache.get(cacheKey)
  if (cached) return cached

  const rendererPromise = needsHighlight
    ? getHighlighter()
        .then((highlighter) => createMarkdownRenderer({ themeName, highlighter }))
        .catch((error) => {
          console.warn('Markdown code highlighting unavailable, rendering without Shiki:', error)
          return createMarkdownRenderer({ themeName })
        })
    : Promise.resolve(createMarkdownRenderer({ themeName }))
  rendererCache.set(cacheKey, rendererPromise)
  return rendererPromise
}

export const renderMarkdown = async (
  content,
  { theme = 'github-light', sourceLines = false, widgets = false } = {}
) => {
  try {
    const normalizedContent = normalizeHtmlTagQuotes(content, widgets)
    const svgContent = sourceLines ? normalizedContent : renderSvgBlocks(normalizedContent)
    const themeName = normalizeTheme(theme)
    const needsHighlight = hasCodeFence(svgContent)
    const cacheKey = `${widgets ? 'widgets' : 'text'}\u0000${sourceLines ? 'lines' : 'plain'}\u0000${needsHighlight ? themeName : 'plain'}\u0000${svgContent}`
    const cachedHtml = renderedHtmlCache.get(cacheKey)
    if (cachedHtml !== undefined) return cachedHtml

    if (needsHighlight) {
      try {
        const highlighter = await getHighlighter()
        await ensureLanguages(highlighter, collectCodeFenceLanguages(svgContent))
      } catch (error) {
        console.warn('Markdown languages unavailable, continuing without code highlighting:', error)
      }
    }

    const md = await getRenderer(themeName, needsHighlight)
    const html = DOMPurify.sanitize(
      md.render(svgContent, { sourceLines, widgets, widgetLines: svgContent.split('\n') }),
      {
        ADD_TAGS: ['input'],
        ADD_ATTR: [
          'class',
          'style',
          'target',
          'rel',
          'type',
          'checked',
          'disabled',
          'source',
          'colspan',
          'rowspan'
        ]
      }
    )
    renderedHtmlCache.set(cacheKey, html)
    return html
  } catch (error) {
    console.error('Failed to render markdown:', error)
    return `<pre>${escapeHtml(content)}</pre>`
  }
}
