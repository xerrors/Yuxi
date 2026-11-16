/**
 * SVG 渲染工具函数
 * 将 Markdown 中的 ```svg 围栏代码块转换为内联 SVG HTML
 */

/**
 * 将 Markdown 字符串中的 ```svg 围栏代码块转换为内联 SVG HTML
 *
 * 使用逐行扫描算法而非单一大正则，以避免嵌套围栏和内容中反引号的误匹配。
 * SVG 内容会被压缩为单行，防止 markdown-it HTML 块解析因空白行截断内容。
 *
 * @param {string} markdown - 原始 Markdown 字符串
 * @returns {string} 转换后的字符串，SVG 围栏被替换为 <div class="svg-inline-render"><svg>...</svg></div>
 */
export function renderSvgBlocks(markdown) {
  const lines = markdown.split('\n')
  const output = []
  let i = 0

  while (i < lines.length) {
    const openMatch = lines[i].match(/^( {0,3})(`{3,}|~{3,})\s*(\S*)/)

    if (openMatch && openMatch[3].toLowerCase() === 'svg') {
      const indent = openMatch[1]
      const fenceChar = openMatch[2]
      const openLine = lines[i]
      const svgLines = []
      i++

      // 扫描闭合围栏
      let closed = false
      while (i < lines.length) {
        const closeMatch = lines[i].match(/^( {0,3})(`{3,}|~{3,})\s*$/)
        if (
          closeMatch &&
          closeMatch[1].length <= indent.length &&
          closeMatch[2][0] === fenceChar[0] &&
          closeMatch[2].length >= fenceChar.length
        ) {
          closed = true
          // 压缩 SVG 为单行，防止 markdown-it HTML 块解析截断
          const singleLine = svgLines
            .join('')
            .replace(/>\s+</g, '><')
            .replace(/\s{2,}/g, ' ')
            .trim()
          const actionsHtml = [
            `<div class="svg-actions">`,
            `<button class="svg-action-btn svg-copy-btn" type="button">复制 SVG</button>`,
            `<button class="svg-action-btn svg-png-btn" type="button">复制为 PNG</button>`,
            `</div>`
          ].join('')
          output.push(`<div class="svg-inline-render">${actionsHtml}${singleLine}</div>`)
          i++
          break
        }
        svgLines.push(lines[i])
        i++
      }

      if (!closed) {
        // 未闭合的围栏 — 保持原样（流式安全）
        output.push(openLine)
        output.push(...svgLines)
      }
    } else {
      output.push(lines[i])
      i++
    }
  }

  return output.join('\n')
}
