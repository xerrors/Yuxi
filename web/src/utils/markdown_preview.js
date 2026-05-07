import MarkdownIt from 'markdown-it'
import markdownItKatex from '@vscode/markdown-it-katex'
import taskLists from 'markdown-it-task-lists'
import DOMPurify from 'dompurify'
import { createHighlighter } from 'shiki'
import yaml from 'js-yaml'
import { escapeHtml } from '@/utils/html'
import { normalizeCodeLanguage } from '@/utils/file_preview'

const markdownKatexPlugin = markdownItKatex.default || markdownItKatex
const FRONTMATTER_MARKER = '---'

let highlighterPromise
const getHighlighter = () => {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighter({
      themes: ['github-light', 'github-dark'],
      langs: ['plaintext']
    }).catch((error) => {
      highlighterPromise = undefined
      throw error
    })
  }
  return highlighterPromise
}

const normalizeHtmlTagQuotes = (content) => {
  const source = String(content || '')
  if (!/[“”]/.test(source)) return source
  return source.replace(/<[^>]+>/g, (tag) => tag.replaceAll('“', '"').replaceAll('”', '"'))
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
  const start = state.bMarks[line] + state.tShift[line]
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
      data = yaml.load(rawYaml) || {}
    } catch {
      return false
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
const renderedHtmlCache = new Map()
const MAX_RENDER_CACHE_SIZE = 100
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
      .map((language) => highlighter.loadLanguage(language).catch(() => null))
  )
}

const createRenderer = ({ themeName, highlighter }) =>
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

const getRenderer = async (theme, needsHighlight) => {
  const themeName = normalizeTheme(theme)
  const cacheKey = needsHighlight ? themeName : 'plain'
  const cached = rendererCache.get(cacheKey)
  if (cached) return cached

  const rendererPromise = needsHighlight
    ? getHighlighter().then((highlighter) => createRenderer({ themeName, highlighter }))
    : Promise.resolve(createRenderer({ themeName }))
  rendererCache.set(cacheKey, rendererPromise)
  return rendererPromise
}

const getCachedHtml = (cacheKey) => renderedHtmlCache.get(cacheKey)
const setCachedHtml = (cacheKey, html) => {
  if (renderedHtmlCache.size >= MAX_RENDER_CACHE_SIZE) {
    renderedHtmlCache.delete(renderedHtmlCache.keys().next().value)
  }
  renderedHtmlCache.set(cacheKey, html)
}

export const renderMarkdown = async (content, { theme = 'github-light' } = {}) => {
  try {
    const normalizedContent = normalizeHtmlTagQuotes(content)
    const themeName = normalizeTheme(theme)
    const needsHighlight = hasCodeFence(normalizedContent)
    const cacheKey = `${needsHighlight ? themeName : 'plain'}\u0000${normalizedContent}`
    const cachedHtml = getCachedHtml(cacheKey)
    if (cachedHtml !== undefined) return cachedHtml

    if (needsHighlight) {
      const highlighter = await getHighlighter()
      await ensureLanguages(highlighter, collectCodeFenceLanguages(normalizedContent))
    }

    const md = await getRenderer(themeName, needsHighlight)
    const html = DOMPurify.sanitize(md.render(normalizedContent), {
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
    })
    setCachedHtml(cacheKey, html)
    return html
  } catch (error) {
    console.error('Failed to render markdown:', error)
    return `<pre>${escapeHtml(content)}</pre>`
  }
}
