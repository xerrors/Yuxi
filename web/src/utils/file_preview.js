const MARKDOWN_EXTENSIONS = new Set(['.md', '.markdown', '.mdx'])
const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg'])
const PDF_EXTENSIONS = new Set(['.pdf'])
const HTML_EXTENSIONS = new Set(['.html', '.htm'])
const CODE_LANGUAGE_ALIASES = {
  js: 'javascript',
  ts: 'typescript',
  py: 'python',
  sh: 'bash',
  shell: 'bash',
  yml: 'yaml',
  docker: 'dockerfile'
}

export const normalizeCodeLanguage = (lang) => {
  const language = String(lang || '')
    .trim()
    .split(/[\s:,]/)[0]
    .toLowerCase()

  return CODE_LANGUAGE_ALIASES[language] || language
}

const CODE_LANGUAGE_MAP = {
  '.py': 'python',
  '.js': 'javascript',
  '.mjs': 'javascript',
  '.cjs': 'javascript',
  '.ts': 'typescript',
  '.tsx': 'tsx',
  '.jsx': 'jsx',
  '.vue': 'xml',
  '.html': 'xml',
  '.htm': 'xml',
  '.xml': 'xml',
  '.css': 'css',
  '.less': 'less',
  '.scss': 'scss',
  '.json': 'json',
  '.yaml': 'yaml',
  '.yml': 'yaml',
  '.toml': 'ini',
  '.ini': 'ini',
  '.cfg': 'ini',
  '.conf': 'ini',
  '.sh': 'bash',
  '.bash': 'bash',
  '.zsh': 'bash',
  '.fish': 'bash',
  '.sql': 'sql',
  '.java': 'java',
  '.kt': 'kotlin',
  '.go': 'go',
  '.rs': 'rust',
  '.php': 'php',
  '.rb': 'ruby',
  '.c': 'c',
  '.h': 'c',
  '.cpp': 'cpp',
  '.cc': 'cpp',
  '.cxx': 'cpp',
  '.hpp': 'cpp',
  '.cs': 'csharp',
  '.swift': 'swift',
  '.dockerfile': 'dockerfile'
}

export const getPreviewFileExtension = (path) => {
  const normalizedPath = String(path || '')
    .trim()
    .toLowerCase()
  if (!normalizedPath) return ''

  const fileName = normalizedPath.split('/').pop() || ''
  const dotIndex = fileName.lastIndexOf('.')
  if (dotIndex <= 0) return ''
  return fileName.slice(dotIndex)
}

export const isMarkdownPreview = (path, previewType) => {
  if (previewType === 'markdown') return true
  return MARKDOWN_EXTENSIONS.has(getPreviewFileExtension(path))
}

export const getPreviewTypeByPath = (path) => {
  const extension = getPreviewFileExtension(path)
  if (IMAGE_EXTENSIONS.has(extension)) return 'image'
  if (PDF_EXTENSIONS.has(extension)) return 'pdf'
  if (MARKDOWN_EXTENSIONS.has(extension)) return 'markdown'
  return 'text'
}

export const getCodeLanguageByPath = (path) =>
  normalizeCodeLanguage(CODE_LANGUAGE_MAP[getPreviewFileExtension(path)] || '')

export const isHtmlPreview = (path) => HTML_EXTENSIONS.has(getPreviewFileExtension(path))
