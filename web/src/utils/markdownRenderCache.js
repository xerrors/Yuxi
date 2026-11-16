/** 按字符预算与条数限制保存 Markdown 渲染结果，命中时更新淘汰顺序。 */
export const createMarkdownRenderCache = ({
  maxChars = 2 * 1024 * 1024,
  maxEntries = 100
} = {}) => {
  const entries = new Map()
  let totalChars = 0

  const remove = (key) => {
    totalChars -= key.length + entries.get(key).length
    entries.delete(key)
  }

  return {
    get(key) {
      if (!entries.has(key)) return undefined
      const html = entries.get(key)
      entries.delete(key)
      entries.set(key, html)
      return html
    },
    set(key, html) {
      if (entries.has(key)) remove(key)
      const chars = key.length + html.length
      if (chars > maxChars) return
      while (entries.size && (totalChars + chars > maxChars || entries.size >= maxEntries)) {
        remove(entries.keys().next().value)
      }
      entries.set(key, html)
      totalChars += chars
    }
  }
}
