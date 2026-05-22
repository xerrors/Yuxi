/**
 * @file mention.js
 * @description Yuxi 平台 @提及药丸（Mention Pills）通用图标生成、富文本渲染与复制粘贴反序列化核心工具库
 */

export const mentionTypePrefixMap = {
  file: 'file',
  knowledge: 'knowledge',
  mcp: 'mcp',
  skill: 'skill',
  subagent: 'subagent'
}

/**
 * 格式化提及 Token
 * @param {string} type 提及类型
 * @param {string} value 提及值
 * @returns {string} 格式化后的 Token
 */
export const formatMentionToken = (type, value) => {
  const prefix = mentionTypePrefixMap[type] || type
  return `@${prefix}:${value}`
}

/**
 * 格式化文件路径，去除冗余家目录并提取父级目录
 * @param {string} path 文件路径
 * @returns {string} 格式化后的父级目录路径
 */
export const formatMentionPath = (path) => {
  if (!path) return ''
  let cleanPath = path.replace(/^\/?home\/gem\/user-data\/?/, '')
  if (cleanPath.startsWith('/')) {
    cleanPath = cleanPath.substring(1)
  }
  let isDir = cleanPath.endsWith('/')
  let pathForParent = isDir ? cleanPath.substring(0, cleanPath.length - 1) : cleanPath

  const lastSlashIndex = pathForParent.lastIndexOf('/')
  if (lastSlashIndex === -1) {
    return ''
  }
  return pathForParent.substring(0, lastSlashIndex + 1)
}

/**
 * 高性能且安全的关键字切片高亮解析函数 (100% 防御 XSS，避开危险的 v-html)
 * @param {string} text 原始文本
 * @param {string} query 搜索关键字
 * @returns {Array<{text: string, isMatch: boolean}>} 切片后的文本数组
 */
export const splitTextByQuery = (text, query) => {
  if (!text) return []
  if (!query) return [{ text, isMatch: false }]

  const escapedQuery = query.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&')
  const regex = new RegExp(`(${escapedQuery})`, 'gi')
  const parts = text.split(regex)

  return parts.map((part) => ({
    text: part,
    isMatch: part.toLowerCase() === query.toLowerCase()
  }))
}

/**
 * 根据提及类型与特征，返回极细 1.8 线宽 SVG 矢量或极客迷你语法高亮三色代码图标
 * @param {string|Object} typeOrItem 提及类型 ('file' | 'knowledge' | 'mcp' | 'skill' | 'subagent') 或包含该特征的实体对象
 * @param {string} [label] 提及标签展示文本 (当第一个参数为 String 时使用)
 * @param {boolean} [isDir=false] 是否为文件夹 (针对 file 类型，当第一个参数为 String 时使用)
 * @returns {string} 图标 HTML / SVG 字符串
 */
export const getMentionIconSvg = (typeOrItem, label, isDir = false) => {
  let type = typeOrItem
  let itemLabel = label
  let itemIsDir = isDir

  // 智能兼容对象重载 (如 MessageInputComponent.vue 中传入的完整 item 结构)
  if (typeof typeOrItem === 'object' && typeOrItem !== null) {
    type = typeOrItem.type
    itemLabel = typeOrItem.label
    itemIsDir = !!typeOrItem.is_dir
  }

  if (type === 'file') {
    if (itemIsDir) {
      // 文件夹极细线框
      return `<svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>`
    }

    // 智能提取文件扩展名并检测是否为代码文件类型
    const name = (itemLabel || '').toLowerCase()
    const codeExtensions = [
      '.py', '.js', '.ts', '.jsx', '.tsx', '.json', '.vue', '.css', '.less', '.html', 
      '.cpp', '.c', '.h', '.cc', '.java', '.go', '.sh', '.yaml', '.yml', '.md', 
      '.rs', '.sql', '.toml', '.xml', '.ini', '.bat', '.ps1'
    ]
    const isCode = codeExtensions.some(ext => name.endsWith(ext))

    if (isCode) {
      // 极致科技感的极客 CSS 迷你语法高亮代码行 (三色线条发光渲染)
      return `<div class="mini-code-icon">
        <span class="mini-code-line mini-code-line-1"></span>
        <span class="mini-code-line mini-code-line-2"></span>
        <span class="mini-code-line mini-code-line-3"></span>
      </div>`
    }

    // 普通文件的 1.8 极细描边纸张线框
    return `<svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>`
  }
  if (type === 'knowledge') {
    // 知识库/书本的 1.8 极细描边线框
    return `<svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></svg>`
  }
  if (type === 'mcp') {
    // MCP 插头的 1.8 极细描边线框
    return `<svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><path d="M18 8v3a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8h12z"></path><path d="M9 8V2h6v6M12 22v-5"></path></svg>`
  }
  if (type === 'skill') {
    // 技能闪电的 1.8 极细描边线框
    return `<svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>`
  }
  if (type === 'subagent') {
    // 智能体机器人的 1.8 极细描边线框
    return `<svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><rect x="3" y="11" width="18" height="10" rx="2"></rect><circle cx="12" cy="5" r="2"></circle><path d="M12 7v4M8 16h.01M16 16h.01"></path></svg>`
  }
  // 兜底极细矢量链接图标
  return `<svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>`
}

/**
 * 工厂函数：统一创建带有 contenteditable="false" 的药丸 DOM 元素
 * @param {string|Object} typeOrItem 提及类型或提及实体对象
 * @param {string} [value] 提及的值 (当第一个参数为 String 时使用)
 * @param {string} [label] 提及展示标签 (当第一个参数为 String 时使用)
 * @param {boolean} [isDir=false] 是否为文件夹 (针对 file 类型)
 * @returns {HTMLSpanElement} 构建好的药丸 DOM 节点
 */
export const createPillElement = (typeOrItem, value, label, isDir = false) => {
  let type = typeOrItem
  let itemValue = value
  let itemLabel = label
  let itemIsDir = isDir

  if (typeof typeOrItem === 'object' && typeOrItem !== null) {
    type = typeOrItem.type
    itemValue = typeOrItem.value
    itemLabel = typeOrItem.label
    itemIsDir = !!typeOrItem.is_dir
  }

  const pill = document.createElement('span')
  pill.className = `mention-pill ${type}-pill`
  pill.setAttribute('contenteditable', 'false')
  pill.setAttribute('data-type', type)
  pill.setAttribute('data-value', itemValue)
  pill.setAttribute('data-label', itemLabel)

  const iconContainer = document.createElement('span')
  iconContainer.className = 'pill-icon'
  iconContainer.innerHTML = getMentionIconSvg(type, itemLabel, itemIsDir)
  pill.appendChild(iconContainer)

  const textContainer = document.createElement('span')
  textContainer.className = 'pill-text'
  textContainer.textContent = itemLabel
  pill.appendChild(textContainer)

  const deleteBtn = document.createElement('span')
  deleteBtn.className = 'pill-close'
  deleteBtn.innerHTML = `<svg viewBox="0 0 24 24" width="10" height="10" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round" style="display: block;"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`
  pill.appendChild(deleteBtn)

  return pill
}

/**
 * HTML 转义方法，安全过滤 HTML 字符防御 XSS
 * @param {string} text 原始文本
 * @returns {string} 安全的转义后文本
 */
export const escapeHtml = (text) => {
  if (!text) return ''
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

/**
 * 渲染用户聊天气泡的文本内容，将 @ 提及文本高级解析替换为药丸 HTML
 * @param {string} content 原始文本内容
 * @returns {string} 替换后的富文本 HTML
 */
export const renderUserMessage = (content) => {
  if (!content) return ''

  // 1. 进行基础 HTML 安全转义，防范 XSS
  const escaped = escapeHtml(content)

  // 2. 正则表达式精准匹配并替换提及项
  const mentionRegex = /@(file|knowledge|mcp|skill|subagent):([^\s\n]+)/g

  return escaped.replace(mentionRegex, (match, type, value) => {
    let label = value
    if (type === 'file') {
      const parts = value.split('/')
      label = parts[parts.length - 1] || value
      try {
        label = decodeURIComponent(label)
      } catch (e) {
        // 忽略解码异常
      }
    }

    const iconHtml = getMentionIconSvg(type, label)

    // XSS 深度物理防御：对拼入 HTML 属性和文本的所有字段做 escapeHtml 处理
    const escapedType = escapeHtml(type)
    const escapedValue = escapeHtml(value)
    const escapedLabel = escapeHtml(label)

    // 生成精美药丸 HTML 字符串（在历史流中免除 Close 按钮，以防多余交互）
    return `<span class="mention-pill ${escapedType}-pill" data-type="${escapedType}" data-value="${escapedValue}" data-label="${escapedLabel}">` +
      `<span class="pill-icon">${iconHtml}</span>` +
      `<span class="pill-text">${escapedLabel}</span>` +
      `</span>`
  })
}

/**
 * 智能深度优先遍历选中的 DOM 树，将提及药丸高保真还原为标准的 @type:value 纯文本格式
 * @param {Node} node DOM 节点
 * @returns {string} 拼装后的纯文本
 */
export const getSelectionText = (node) => {
  let text = ''
  if (node.nodeType === Node.TEXT_NODE) {
    text += node.textContent
  } else if (node.nodeType === Node.ELEMENT_NODE) {
    // 智能识别：如果是提及药丸
    const isPill = node.classList.contains('mention-pill') || node.getAttribute('data-type')
    if (isPill) {
      const type = node.getAttribute('data-type')
      const value = node.getAttribute('data-value')
      if (type && value) {
        text += `@${type}:${value}`
      } else {
        text += node.getAttribute('data-label') || node.textContent.trim()
      }
    } else if (node.tagName === 'BR') {
      text += '\n'
    } else {
      // 深度遍历子节点
      for (let i = 0; i < node.childNodes.length; i++) {
        text += getSelectionText(node.childNodes[i])
      }
    }
  }
  return text
}

/**
 * 从 HTML 富文本中高精度提取药丸和普通文本，物理过滤外部富文本样式污染，返回 DocumentFragment
 * @param {string} pastedHtml 粘贴的 HTML 富文本
 * @returns {DocumentFragment} 洗涤洁净后的 DocumentFragment
 */
export const parseMentionHtml = (pastedHtml) => {
  const parser = new DOMParser()
  const doc = parser.parseFromString(pastedHtml, 'text/html')
  const fragment = document.createDocumentFragment()

  const cleanAndExtract = (node, frag) => {
    if (node.nodeType === Node.TEXT_NODE) {
      let text = node.textContent
      // 智能防重双空格：如果前一个节点已是药丸尾随空格（\u00A0），且当前文本节点以空格开头，则去除重复的首空格
      if (text.startsWith(' ') || text.startsWith('\u00A0')) {
        const last = frag.lastChild
        if (last && last.nodeType === Node.TEXT_NODE && (last.textContent === '\u00A0' || last.textContent === ' ')) {
          text = text.substring(1)
        }
      }
      if (text) {
        frag.appendChild(document.createTextNode(text))
      }
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      // 智能捕获：是否是我们的 mention-pill 药丸
      const isPill = node.classList.contains('mention-pill') || 
                      node.getAttribute('data-type') || 
                      node.closest?.('.mention-pill')

      if (isPill) {
        // 如果是子节点，向上寻找真正的药丸容器
        const pillEl = node.classList.contains('mention-pill') ? node : (node.closest?.('.mention-pill') || node)
        const type = pillEl.getAttribute('data-type')
        const value = pillEl.getAttribute('data-value')
        const label = pillEl.getAttribute('data-label') || pillEl.textContent.trim()

        if (type && value) {
          const pill = createPillElement(type, value, label)
          frag.appendChild(pill)

          // 物理尾随不折行空格
          const spaceNode = document.createTextNode('\u00A0')
          frag.appendChild(spaceNode)
        }
        // 药丸已经提取完毕，跳过对其子节点的遍历，防止重复输出文字
        return
      } else if (node.tagName === 'BR') {
        frag.appendChild(document.createElement('br'))
      } else {
        // 递归深度提取子节点，保持语义结构
        for (let i = 0; i < node.childNodes.length; i++) {
          cleanAndExtract(node.childNodes[i], frag)
        }
      }
    }
  }

  // 提取 body 下所有有效元素及文本
  for (let i = 0; i < doc.body.childNodes.length; i++) {
    cleanAndExtract(doc.body.childNodes[i], fragment)
  }

  return fragment
}

/**
 * 从纯文本中识别 @type:value 极客明文，并高精度翻译为带有 contenteditable="false" 的药丸 DOM，返回 DocumentFragment
 * 智能防范连续药丸、开头、中间与最末尾普通文本中夹带的所有多余双空格，提供完美的单空格高级排版。
 * @param {string} pastedText 粘贴的纯文本
 * @returns {DocumentFragment} 洗涤洁净后的 DocumentFragment
 */
export const parseMentionText = (pastedText) => {
  const fragment = document.createDocumentFragment()
  if (!pastedText) return fragment

  const mentionRegex = /@(file|knowledge|mcp|skill|subagent):([^\s\n\u00A0]+)/g
  let lastIndex = 0
  let match

  mentionRegex.lastIndex = 0

  while ((match = mentionRegex.exec(pastedText)) !== null) {
    const matchIndex = match.index
    const type = match[1]
    const value = match[2]

    // 插入前面的普通文本
    if (matchIndex > lastIndex) {
      let beforeText = pastedText.slice(lastIndex, matchIndex)
      // 智能防重双空格：扣除原本夹在两个正则匹配药丸之间的冗余前导空格
      if (beforeText.startsWith(' ') || beforeText.startsWith('\u00A0')) {
        beforeText = beforeText.substring(1)
      }
      if (beforeText) {
        const textNode = document.createTextNode(beforeText)
        fragment.appendChild(textNode)
      }
    }

    // 精细格式化 label
    let label = value
    if (type === 'file') {
      const parts = value.split('/')
      label = parts[parts.length - 1] || value
      try {
        label = decodeURIComponent(label)
      } catch (err) {
        // 忽略解码异常
      }
    }

    // 创建 mention-pill DOM 节点
    const pill = createPillElement(type, value, label)
    fragment.appendChild(pill)

    // 添加尾随的空格，避免连续输入与药丸粘连
    const spaceNode = document.createTextNode('\u00A0')
    fragment.appendChild(spaceNode)

    lastIndex = mentionRegex.lastIndex
  }

  // 插入最后剩下的普通文本
  if (lastIndex < pastedText.length) {
    let afterText = pastedText.slice(lastIndex)
    // 智能去重：若紧跟在药丸后面的文本以空格开头，剥离此冗余空格，完美消除历史消息气泡点击复制粘贴产生的双空格排版缺陷
    if (afterText.startsWith(' ') || afterText.startsWith('\u00A0')) {
      afterText = afterText.substring(1)
    }
    if (afterText) {
      const textNode = document.createTextNode(afterText)
      fragment.appendChild(textNode)
    }
  }

  return fragment
}
