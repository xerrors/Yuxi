// 文件相关工具函数
import {
  FileTextFilled,
  FileMarkdownFilled,
  FilePdfFilled,
  FileWordFilled,
  FileExcelFilled,
  FileImageFilled,
  FileUnknownFilled,
  FilePptFilled,
  LinkOutlined,
  CodeFilled,
  FileFilled
} from '@ant-design/icons-vue'
import { formatRelative, parseToShanghai } from '@/utils/time'

// 根据文件扩展名获取文件图标
export const getFileIcon = (filename) => {
  if (!filename) return FileFilled

  // Check if it's a URL
  if (filename.startsWith('http://') || filename.startsWith('https://')) {
    return LinkOutlined
  }

  const extension = filename.toLowerCase().split('.').pop()

  const iconMap = {
    // 文本文件与常规文档
    txt: FileTextFilled,
    text: FileTextFilled,
    log: FileTextFilled,
    pdf: FilePdfFilled,
    doc: FileWordFilled,
    docx: FileWordFilled,
    xls: FileExcelFilled,
    xlsx: FileExcelFilled,
    csv: FileExcelFilled,
    ppt: FilePptFilled,
    pptx: FilePptFilled,

    // Markdown文件
    md: FileMarkdownFilled,
    markdown: FileMarkdownFilled,

    // 图片文件
    jpg: FileImageFilled,
    jpeg: FileImageFilled,
    png: FileImageFilled,
    gif: FileImageFilled,
    bmp: FileImageFilled,
    svg: FileImageFilled,
    webp: FileImageFilled,

    // 代码文件
    py: CodeFilled,
    js: CodeFilled,
    ts: CodeFilled,
    vue: CodeFilled,
    sh: CodeFilled,
    go: CodeFilled,
    cpp: CodeFilled,
    c: CodeFilled,
    h: CodeFilled,
    java: CodeFilled,
    html: CodeFilled,
    htm: CodeFilled,
    css: CodeFilled,
    less: CodeFilled,
    scss: CodeFilled,
    sql: CodeFilled,

    // 配置文件与数据结构
    json: FileTextFilled,
    yaml: FileTextFilled,
    yml: FileTextFilled,
    toml: FileTextFilled,
    ini: FileTextFilled,
    conf: FileTextFilled,
    env: FileTextFilled
  }

  return iconMap[extension] || FileFilled
}

// 根据文件扩展名获取文件图标颜色
export const getFileIconColor = (filename) => {
  if (!filename) return '#8c8c8c'

  // Check if it's a URL
  if (filename.startsWith('http://') || filename.startsWith('https://')) {
    return '#1890ff' // Blue for links
  }

  const extension = filename.toLowerCase().split('.').pop()

  const colorMap = {
    // 文本文件 - 蓝色
    txt: '#1890ff',
    text: '#1890ff',
    log: '#1890ff',

    // Markdown文件 - 深灰色
    md: '#595959',
    markdown: '#595959',

    // PDF文件 - 红色
    pdf: '#ff4d4f',

    // Word文档 - 深蓝色
    doc: '#2f54eb',
    docx: '#2f54eb',

    // Excel文档 - 绿色
    xls: '#52c41a',
    xlsx: '#52c41a',
    csv: '#52c41a',

    // PPT文档 - 橙色
    ppt: '#f6720d',
    pptx: '#f6720d',

    // 图片文件 - 紫色
    jpg: '#722ed1',
    jpeg: '#722ed1',
    png: '#722ed1',
    gif: '#722ed1',
    bmp: '#722ed1',
    svg: '#722ed1',
    webp: '#722ed1',

    // 前端与样式文件 - 橙黄色
    js: '#fa8c16',
    ts: '#fa8c16',
    vue: '#fa8c16',
    html: '#fa8c16',
    htm: '#fa8c16',
    css: '#fa8c16',
    less: '#fa8c16',
    scss: '#fa8c16',

    // 后端核心代码文件 - 亮天蓝
    py: '#1890ff',
    go: '#1890ff',
    java: '#1890ff',
    cpp: '#1890ff',
    c: '#1890ff',
    h: '#1890ff',

    // 配置文件 - 青色
    json: '#13c2c2',
    yaml: '#13c2c2',
    yml: '#13c2c2',
    toml: '#13c2c2',
    ini: '#13c2c2',
    conf: '#13c2c2',
    env: '#13c2c2',

    // 脚本文件 - 中灰
    sh: '#595959',
    sql: '#595959'
  }

  return colorMap[extension] || '#8c8c8c'
}

// Format relative time with CST baseline
export const formatRelativeTime = (value) => formatRelative(value)

// 格式化标准时间
export const formatStandardTime = (value) => {
  const parsed = parseToShanghai(value)
  if (!parsed) return '-'
  return parsed.format('YYYY年MM月DD日 HH:mm:ss')
}

// 获取状态文本
export const getStatusText = (status) => {
  const statusMap = {
    done: '处理完成',
    failed: '处理失败',
    processing: '处理中',
    waiting: '等待处理'
  }
  return statusMap[status] || status
}

// 格式化文件大小
export const formatFileSize = (bytes) => {
  if (bytes === 0 || bytes === '0') return '0 B'
  if (!bytes) return '-'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

/**
 * 解析 HTTP 响应头 Content-Disposition 中的文件名
 * @param {string} contentDisposition 
 * @returns {string} 文件名
 */
export const parseDownloadFilename = (contentDisposition) => {
  if (!contentDisposition) return ''
  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match && utf8Match[1]) {
    try {
      return decodeURIComponent(utf8Match[1])
    } catch (error) {
      console.warn('解析 UTF-8 文件名失败:', error)
    }
  }
  const asciiMatch = contentDisposition.match(/filename="?([^";]+)"?/i)
  return asciiMatch?.[1] || ''
}

