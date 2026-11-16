/**
 * 复制文本到剪贴板，优先使用 Clipboard API，
 * 在非安全上下文（如局域网 IP 访问）下降级为 execCommand。
 * @param {string} text 待复制文本
 * @returns {Promise<void>} 失败时抛错
 */
export async function copyTextToClipboard(text) {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch {
      // Clipboard API 可能因浏览器权限被拒绝，继续尝试传统复制路径。
    }
  }

  const textArea = document.createElement('textarea')
  textArea.value = text
  textArea.setAttribute('readonly', '')
  textArea.style.position = 'fixed'
  textArea.style.left = '-999999px'
  textArea.style.top = '-999999px'
  document.body.appendChild(textArea)
  textArea.focus()
  textArea.select()
  try {
    const successful = document.execCommand('copy')
    if (!successful) throw new Error('execCommand copy failed')
  } finally {
    document.body.removeChild(textArea)
  }
}
