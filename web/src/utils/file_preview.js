const MARKDOWN_EXTENSIONS = new Set(['.md', '.markdown', '.mdx'])
const IMAGE_EXTENSIONS = new Set([
  '.apng',
  '.avif',
  '.png',
  '.jpg',
  '.jpeg',
  '.gif',
  '.bmp',
  '.webp',
  '.svg'
])
const PDF_EXTENSIONS = new Set(['.pdf'])
const HTML_EXTENSIONS = new Set(['.html', '.htm'])
const OFFICE_EXTENSIONS = new Set(['.docx', '.pptx'])
const TEXT_EXTENSIONS = new Set([
  '.txt',
  '.text',
  '.log',
  '.json',
  '.jsonl',
  '.yaml',
  '.yml',
  '.toml',
  '.ini',
  '.cfg',
  '.conf',
  '.csv',
  '.tsv',
  '.py',
  '.js',
  '.ts',
  '.jsx',
  '.tsx',
  '.vue',
  '.html',
  '.htm',
  '.css',
  '.less',
  '.scss',
  '.xml',
  '.sql',
  '.sh',
  '.bash',
  '.zsh',
  '.fish',
  '.env',
  '.dockerfile',
  '.gitignore'
])
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
  if (HTML_EXTENSIONS.has(extension)) return 'html'
  if (OFFICE_EXTENSIONS.has(extension)) return 'office'
  if (TEXT_EXTENSIONS.has(extension)) return 'text'
  return 'unsupported'
}

export const getCodeLanguageByPath = (path) =>
  normalizeCodeLanguage(CODE_LANGUAGE_MAP[getPreviewFileExtension(path)] || '')

export const isHtmlPreview = (path) => HTML_EXTENSIONS.has(getPreviewFileExtension(path))

export const getPreviewTypeByContentType = (contentType) => {
  const normalized = String(contentType || '').toLowerCase()
  if (normalized.includes('application/pdf')) return 'pdf'
  if (normalized.startsWith('image/')) return 'image'
  if (normalized.includes('text/markdown')) return 'markdown'
  if (normalized.includes('text/html')) return 'html'
  if (normalized.startsWith('text/')) return 'text'
  if (normalized.includes('application/json')) return 'json'
  return 'unsupported'
}

export const normalizePreviewResponse = async (response, baseFile = {}) => {
  const contentType = response?.headers?.get?.('content-type') || ''

  if (contentType.includes('application/json')) {
    const payload = await response.json()
    const previewType = payload.preview_type || payload.previewType || payload.kind || 'text'
    return {
      ...baseFile,
      ...payload,
      content: payload.content ?? '',
      previewType,
      supported: payload.supported !== false,
      message: payload.message || '',
      previewUrl: ''
    }
  }

  const previewType =
    response?.headers?.get?.('x-yuxi-preview-type') || getPreviewTypeByContentType(contentType)
  const blob = await response.blob()

  return {
    ...baseFile,
    content: null,
    previewType,
    supported: previewType !== 'unsupported',
    message: previewType === 'unsupported' ? '当前文件暂不支持预览，请下载后查看' : '',
    previewUrl: window.URL.createObjectURL(blob)
  }
}
