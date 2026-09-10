const PDF_ERROR_MESSAGES_BY_NAME = {
  InvalidPDFException: '无法加载 PDF 文件或文件格式受损',
  MissingPDFException: '未找到 PDF 文件，请刷新后重试',
  UnexpectedResponseException: 'PDF 文件加载失败，请稍后重试',
  PasswordException: 'PDF 文件已加密，暂不支持在线预览'
}

const NETWORK_FAILURE_PATTERN = /network|failed to fetch|load failed/i

/**
 * 把 pdf.js 加载异常映射为用户可理解的提示，区分资源失败与文件损坏。
 */
export const resolvePdfLoadErrorMessage = (error) => {
  const messageByName = PDF_ERROR_MESSAGES_BY_NAME[error?.name]
  if (messageByName) return messageByName

  const message = String(error?.message || error || '')
  if (message.includes('Setting up fake worker')) {
    return 'PDF 渲染组件加载失败，请刷新页面重试'
  }
  if (error instanceof TypeError || NETWORK_FAILURE_PATTERN.test(message)) {
    return 'PDF 文件加载失败，请检查网络后重试'
  }
  return '无法加载 PDF 文件或文件格式受损'
}
