/** 格式化 MySQL 工具结果，统一 JSON 与 primitive 的展示。 */
export function formatMysqlResult(content) {
  if (!content) return ''

  if (typeof content === 'string') {
    try {
      const parsed = JSON.parse(content)
      return JSON.stringify(parsed, null, 2)
    } catch {
      return content
    }
  }

  if (typeof content === 'object') {
    return JSON.stringify(content, null, 2)
  }

  return String(content)
}
