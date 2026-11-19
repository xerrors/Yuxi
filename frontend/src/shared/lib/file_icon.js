// 文件 / 文件夹类型图标（彩色 SVG），用于在文件路径前渲染。
// 注意：仅用于「渲染文件/文件夹名称前的类型图标」，按钮图标请勿使用此处的资源。
// Vite 直接解析静态 URL，开发浏览器无需为每个图标加载独立的 JS 模块。
const archiveIcon = new URL('../../assets/icons/files/archive.svg', import.meta.url).href
const audioIcon = new URL('../../assets/icons/files/audio.svg', import.meta.url).href
const cadIcon = new URL('../../assets/icons/files/cad.svg', import.meta.url).href
const codeIcon = new URL('../../assets/icons/files/code.svg', import.meta.url).href
const fileIcon = new URL('../../assets/icons/files/file.svg', import.meta.url).href
const imageIcon = new URL('../../assets/icons/files/image.svg', import.meta.url).href
const markdownIcon = new URL('../../assets/icons/files/markdown.svg', import.meta.url).href
const pdfIcon = new URL('../../assets/icons/files/pdf.svg', import.meta.url).href
const pptIcon = new URL('../../assets/icons/files/ppt.svg', import.meta.url).href
const psdIcon = new URL('../../assets/icons/files/psd.svg', import.meta.url).href
const pythonIcon = new URL('../../assets/icons/files/python.svg', import.meta.url).href
const spreadsheetIcon = new URL('../../assets/icons/files/spreadsheet.svg', import.meta.url).href
const textIcon = new URL('../../assets/icons/files/text.svg', import.meta.url).href
const videoIcon = new URL('../../assets/icons/files/video.svg', import.meta.url).href
const webIcon = new URL('../../assets/icons/files/web.svg', import.meta.url).href
const wordIcon = new URL('../../assets/icons/files/word.svg', import.meta.url).href
const folderIcon = new URL('../../assets/icons/files/folder.svg', import.meta.url).href
const folderAgentIcon = new URL('../../assets/icons/files/folder-agent.svg', import.meta.url).href
const folderEnterpriseIcon = new URL('../../assets/icons/files/folder-enterprise.svg', import.meta.url).href
const folderFavoriteIcon = new URL('../../assets/icons/files/folder-favorite.svg', import.meta.url).href
const folderKnowledgeIcon = new URL('../../assets/icons/files/folder-knowledge.svg', import.meta.url).href
const folderPersonalIcon = new URL('../../assets/icons/files/folder-personal.svg', import.meta.url).href
const folderTrashIcon = new URL('../../assets/icons/files/folder-trash.svg', import.meta.url).href

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
