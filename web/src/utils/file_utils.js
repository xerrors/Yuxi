import {
  FileTextFilled,
  FileMarkdownFilled,
  FilePdfFilled,
  FileWordFilled,
  FileExcelFilled,
  FileImageFilled,
  FileFilled,
  FilePptFilled,
  FileZipFilled,
  LinkOutlined,
  CodeFilled
} from '@ant-design/icons-vue'
import { getPreviewFileExtension, getPreviewTypeByPath } from '@/utils/file_preview'
import { formatRelative, parseToShanghai } from '@/utils/time'

const DEFAULT_FILE_ICON = { icon: FileFilled, color: 'var(--gray-600)' }
const LINK_FILE_ICON = { icon: LinkOutlined, color: 'var(--color-info-500)' }

const FILE_ICON_CONFIG = {
  txt: { icon: FileTextFilled, color: 'var(--color-info-500)' },
  text: { icon: FileTextFilled, color: 'var(--color-info-500)' },
  log: { icon: FileTextFilled, color: 'var(--color-info-500)' },
  md: { icon: FileMarkdownFilled, color: 'var(--gray-700)' },
  markdown: { icon: FileMarkdownFilled, color: 'var(--gray-700)' },
  pdf: { icon: FilePdfFilled, color: 'var(--color-error-500)' },
  doc: { icon: FileWordFilled, color: 'var(--color-info-700)' },
  docx: { icon: FileWordFilled, color: 'var(--color-info-700)' },
  xls: { icon: FileExcelFilled, color: 'var(--color-success-500)' },
  xlsx: { icon: FileExcelFilled, color: 'var(--color-success-500)' },
  csv: { icon: FileExcelFilled, color: 'var(--color-success-500)' },
  ppt: { icon: FilePptFilled, color: 'var(--color-warning-700)' },
  pptx: { icon: FilePptFilled, color: 'var(--color-warning-700)' },
  apng: { icon: FileImageFilled, color: 'var(--color-accent-700)' },
  avif: { icon: FileImageFilled, color: 'var(--color-accent-700)' },
  jpg: { icon: FileImageFilled, color: 'var(--color-accent-700)' },
  jpeg: { icon: FileImageFilled, color: 'var(--color-accent-700)' },
  png: { icon: FileImageFilled, color: 'var(--color-accent-700)' },
  gif: { icon: FileImageFilled, color: 'var(--color-accent-700)' },
  bmp: { icon: FileImageFilled, color: 'var(--color-accent-700)' },
  svg: { icon: FileImageFilled, color: 'var(--color-accent-700)' },
  webp: { icon: FileImageFilled, color: 'var(--color-accent-700)' },
  zip: { icon: FileZipFilled, color: 'var(--gray-700)' },
  rar: { icon: FileZipFilled, color: 'var(--gray-700)' },
  '7z': { icon: FileZipFilled, color: 'var(--gray-700)' },
  tar: { icon: FileZipFilled, color: 'var(--gray-700)' },
  gz: { icon: FileZipFilled, color: 'var(--gray-700)' },
  py: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  js: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  jsx: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  ts: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  tsx: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  vue: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  sh: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  go: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  rs: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  cpp: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  c: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  h: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  java: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  html: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  htm: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  css: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  less: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  scss: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  sql: { icon: CodeFilled, color: 'var(--color-primary-700)' },
  json: { icon: FileTextFilled, color: 'var(--color-primary-700)' },
  yaml: { icon: FileTextFilled, color: 'var(--color-primary-700)' },
  yml: { icon: FileTextFilled, color: 'var(--color-primary-700)' },
  toml: { icon: FileTextFilled, color: 'var(--color-primary-700)' },
  ini: { icon: FileTextFilled, color: 'var(--color-primary-700)' },
  conf: { icon: FileTextFilled, color: 'var(--color-primary-700)' },
  env: { icon: FileTextFilled, color: 'var(--color-primary-700)' }
}

const getFileIconConfig = (filename) => {
  const normalizedName = String(filename || '').trim().toLowerCase()
  if (!normalizedName) return DEFAULT_FILE_ICON
  if (normalizedName.startsWith('http://') || normalizedName.startsWith('https://')) return LINK_FILE_ICON

  const cleanName = normalizedName.split(/[?#]/)[0]
  const extension = cleanName.includes('.') ? cleanName.split('.').pop() : cleanName
  return FILE_ICON_CONFIG[extension] || DEFAULT_FILE_ICON
}

export const getFileIcon = (filename) => getFileIconConfig(filename).icon

export const getFileIconColor = (filename) => getFileIconConfig(filename).color

export const formatRelativeTime = (value) => formatRelative(value)

export const formatStandardTime = (value) => {
  const parsed = parseToShanghai(value)
  if (!parsed) return '-'
  return parsed.format('YYYY年MM月DD日 HH:mm:ss')
}

export const getStatusText = (status) => {
  const statusMap = {
    done: '处理完成',
    failed: '处理失败',
    processing: '处理中',
    waiting: '等待处理'
  }
  return statusMap[status] || status
}

export const formatFileSize = (bytes) => {
  if (bytes === 0 || bytes === '0') return '0 B'
  if (!bytes) return '-'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

export const getDisplayFileName = (pathOrName, fallback = '文件') => {
  const value = String(pathOrName || '').trim()
  if (!value) return fallback
  return value.split('/').pop() || value || fallback
}

export const getFileExtensionLabel = (pathOrName) => {
  const extension = getPreviewFileExtension(pathOrName).replace(/^\./, '')
  return extension ? extension.toUpperCase() : ''
}

export const getMimeSubtypeLabel = (mimeType) => {
  const subtype = String(mimeType || '')
    .split('/')
    .pop()
    ?.trim()
  return subtype ? subtype.toUpperCase() : ''
}

export const normalizeAttachmentPreview = (attachment) => {
  const name = getDisplayFileName(
    attachment?.file_name || attachment?.name || attachment?.path,
    '附件'
  )
  const fileId = attachment?.file_id || attachment?.path || name
  const fileType = String(attachment?.file_type || '')
  const sizeLabel = formatFileSize(attachment?.file_size)
  const typeLabel = getFileExtensionLabel(name) || getMimeSubtypeLabel(fileType) || '文件'

  return {
    raw: attachment,
    fileId,
    name,
    previewUrl: attachment?.original_artifact_url || attachment?.artifact_url || '',
    isImage: fileType.startsWith('image/') || getPreviewTypeByPath(name) === 'image',
    meta: [typeLabel, sizeLabel === '-' ? '' : sizeLabel].filter(Boolean).join(' · '),
    icon: getFileIcon(name),
    iconColor: getFileIconColor(name)
  }
}

export const normalizeAttachmentPreviews = (attachments) => {
  if (!Array.isArray(attachments)) return []
  return attachments.map(normalizeAttachmentPreview).filter((attachment) => attachment.fileId)
}
