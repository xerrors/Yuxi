// 文件 / 文件夹类型图标（彩色 SVG），用于在文件路径前渲染。
// 注意：仅用于「渲染文件/文件夹名称前的类型图标」，按钮图标请勿使用此处的资源。
import archiveIcon from '@/assets/icons/files/archive.svg?url'
import audioIcon from '@/assets/icons/files/audio.svg?url'
import cadIcon from '@/assets/icons/files/cad.svg?url'
import codeIcon from '@/assets/icons/files/code.svg?url'
import fileIcon from '@/assets/icons/files/file.svg?url'
import imageIcon from '@/assets/icons/files/image.svg?url'
import markdownIcon from '@/assets/icons/files/markdown.svg?url'
import pdfIcon from '@/assets/icons/files/pdf.svg?url'
import pptIcon from '@/assets/icons/files/ppt.svg?url'
import psdIcon from '@/assets/icons/files/psd.svg?url'
import pythonIcon from '@/assets/icons/files/python.svg?url'
import spreadsheetIcon from '@/assets/icons/files/spreadsheet.svg?url'
import textIcon from '@/assets/icons/files/text.svg?url'
import videoIcon from '@/assets/icons/files/video.svg?url'
import webIcon from '@/assets/icons/files/web.svg?url'
import wordIcon from '@/assets/icons/files/word.svg?url'
import folderIcon from '@/assets/icons/files/folder.svg?url'
import folderAgentIcon from '@/assets/icons/files/folder-agent.svg?url'
import folderEnterpriseIcon from '@/assets/icons/files/folder-enterprise.svg?url'
import folderFavoriteIcon from '@/assets/icons/files/folder-favorite.svg?url'
import folderKnowledgeIcon from '@/assets/icons/files/folder-knowledge.svg?url'
import folderPersonalIcon from '@/assets/icons/files/folder-personal.svg?url'
import folderTrashIcon from '@/assets/icons/files/folder-trash.svg?url'

export const FOLDER_ICONS = {
  default: folderIcon,
  agent: folderAgentIcon,
  enterprise: folderEnterpriseIcon,
  favorite: folderFavoriteIcon,
  knowledge: folderKnowledgeIcon,
  personal: folderPersonalIcon,
  trash: folderTrashIcon
}

const EXTENSION_ICONS = {
  // 文档
  pdf: pdfIcon,
  doc: wordIcon,
  docx: wordIcon,
  rtf: wordIcon,
  ppt: pptIcon,
  pptx: pptIcon,
  xls: spreadsheetIcon,
  xlsx: spreadsheetIcon,
  csv: spreadsheetIcon,
  // 文本 / Markdown
  txt: textIcon,
  text: textIcon,
  log: textIcon,
  md: markdownIcon,
  markdown: markdownIcon,
  mdx: markdownIcon,
  // 代码
  py: pythonIcon,
  js: codeIcon,
  mjs: codeIcon,
  cjs: codeIcon,
  jsx: codeIcon,
  ts: codeIcon,
  tsx: codeIcon,
  vue: codeIcon,
  json: codeIcon,
  yaml: codeIcon,
  yml: codeIcon,
  toml: codeIcon,
  ini: codeIcon,
  conf: codeIcon,
  env: codeIcon,
  sh: codeIcon,
  bash: codeIcon,
  bat: codeIcon,
  go: codeIcon,
  rs: codeIcon,
  c: codeIcon,
  h: codeIcon,
  cpp: codeIcon,
  java: codeIcon,
  css: codeIcon,
  less: codeIcon,
  scss: codeIcon,
  sql: codeIcon,
  xml: codeIcon,
  // 网页
  html: webIcon,
  htm: webIcon,
  // 图片
  png: imageIcon,
  jpg: imageIcon,
  jpeg: imageIcon,
  gif: imageIcon,
  bmp: imageIcon,
  webp: imageIcon,
  svg: imageIcon,
  apng: imageIcon,
  avif: imageIcon,
  ico: imageIcon,
  // 设计 / 工程
  psd: psdIcon,
  dwg: cadIcon,
  dxf: cadIcon,
  // 音视频
  mp3: audioIcon,
  wav: audioIcon,
  flac: audioIcon,
  aac: audioIcon,
  ogg: audioIcon,
  m4a: audioIcon,
  mp4: videoIcon,
  mov: videoIcon,
  avi: videoIcon,
  mkv: videoIcon,
  webm: videoIcon,
  flv: videoIcon,
  // 压缩包
  zip: archiveIcon,
  rar: archiveIcon,
  '7z': archiveIcon,
  tar: archiveIcon,
  gz: archiveIcon
}

const getExtension = (name) => {
  const cleanName = String(name || '')
    .trim()
    .toLowerCase()
    .split(/[?#]/)[0]
  const fileName = cleanName.split('/').pop() || ''
  const dotIndex = fileName.lastIndexOf('.')
  if (dotIndex <= 0) return ''
  return fileName.slice(dotIndex + 1)
}

/**
 * 解析文件 / 文件夹对应的彩色图标 URL。
 * @param {string} name 文件名或路径（目录可以 `/` 结尾）
 * @param {object} [options]
 * @param {boolean} [options.isDir] 是否为目录
 * @param {string} [options.folderVariant] 文件夹图标变体：default | agent | enterprise | favorite | knowledge | personal | trash
 */
export const resolveFileIconUrl = (name, { isDir = false, folderVariant = 'default' } = {}) => {
  const isDirectory = isDir || String(name || '').endsWith('/')
  if (isDirectory) return FOLDER_ICONS[folderVariant] || FOLDER_ICONS.default
  return EXTENSION_ICONS[getExtension(name)] || fileIcon
}
