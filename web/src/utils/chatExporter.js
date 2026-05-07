import { escapeHtml } from '@/utils/html'
import { renderMarkdown as renderMarkdownPreview } from '@/utils/markdown_preview'
import dayjs, { parseToShanghai } from '@/utils/time'
import chatExportTemplate from './templates/chat-export-template.html?raw'

export class ChatExporter {
  /**
   * 导出聊天对话为 HTML 文件
   * @param {Object} options 导出选项
   */
  static async exportToHTML(options = {}) {
    const {
      chatTitle = '新对话',
      agentName = '智能助手',
      agentDescription = '',
      messages = []
    } = options || {}

    try {
      const htmlContent = await this.generateHTML({
        chatTitle,
        agentName,
        agentDescription,
        messages
      })

      const blob = new Blob([htmlContent], { type: 'text/html;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      const timestamp = dayjs().tz('Asia/Shanghai').format('YYYYMMDD-HHmmss')
      const safeTitle = chatTitle.replace(/[\\/:*?"<>|]/g, '_')
      const filename = `${safeTitle}-${timestamp}.html`

      link.href = url
      link.download = filename
      link.style.display = 'none'

      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(url)

      return { success: true, filename }
    } catch (error) {
      console.error('导出对话失败:', error)
      throw new Error(`导出失败: ${error.message}`, { cause: error })
    }
  }

  /**
   * 生成完整 HTML 内容
   */
  static async generateHTML(options) {
    const { chatTitle, agentName, agentDescription, messages } = options

    const flattenedMessages = this.flattenMessages(messages)
    if (flattenedMessages.length === 0) {
      throw new Error('没有可导出的对话内容')
    }

    const messagesHTML = await this.generateMessagesHTML(flattenedMessages, agentName)

    return this.generateHTMLTemplate({
      chatTitle,
      agentName,
      agentDescription,
      exportTime: dayjs().tz('Asia/Shanghai').format('YYYY年MM月DD日 HH:mm:ss'),
      messagesHTML
    })
  }

  /**
   * 扁平化消息列表
   */
  static flattenMessages(messages = []) {
    const result = []

    console.log('[ChatExporter] flattenMessages input:', {
      messagesLength: messages?.length || 0,
      messagesType: Array.isArray(messages) ? 'array' : typeof messages,
      firstMessage: messages?.[0]
        ? {
            hasMessages: Array.isArray(messages[0].messages),
            hasType: !!messages[0].type,
            hasRole: !!messages[0].role,
            hasContent: !!messages[0].content,
            keys: Object.keys(messages[0])
          }
        : null
    })
    ;(messages || []).forEach((item) => {
      if (!item) return

      if (Array.isArray(item.messages)) {
        item.messages.forEach((msg) => {
          if (msg) result.push(msg)
        })
        return
      }

      // 支持直接传入消息扁平数组
      if (item.type || item.role || item.content) {
        result.push(item)
      }
    })

    return result
  }

  /**
   * 生成对话消息的 HTML 片段
   */
  static async generateMessagesHTML(messages, agentName) {
    const messageSegments = await Promise.all(
      messages.map(async (msg) => {
        const isUserMessage = ['human', 'user'].includes(msg?.type) || msg?.role === 'user'
        const avatar = isUserMessage ? '👤' : '🤖'
        const senderLabel = isUserMessage ? '用户' : agentName || '智能助手'
        const messageClass = isUserMessage ? 'user-message' : 'ai-message'
        const timestampRaw = this.getMessageTimestamp(msg)
        const timestamp = this.escapeHtml(this.formatTimestamp(timestampRaw))

        const { content, reasoning } = this.extractMessageContent(msg)
        const [contentHTML, reasoningHTML] = await Promise.all([
          content ? this.renderMarkdown(content) : '',
          !isUserMessage ? this.generateReasoningHTML(reasoning) : ''
        ])
        const toolCallsHTML = !isUserMessage ? this.generateToolCallsHTML(msg) : ''

        const bodySegments = [
          reasoningHTML,
          contentHTML ? `<div class="markdown-body">${contentHTML}</div>` : '',
          toolCallsHTML
        ].filter(Boolean)

        return `
        <div class="message ${messageClass}">
          <div class="message-header">
            <span class="avatar">${avatar}</span>
            <span class="sender">${this.escapeHtml(senderLabel)}</span>
            <span class="time">${timestamp}</span>
          </div>
          <div class="message-content">
            ${bodySegments.length > 0 ? bodySegments.join('') : '<div class="empty-message">（此消息暂无可展示内容）</div>'}
          </div>
        </div>
      `
      })
    )

    return messageSegments.join('')
  }

  /**
   * 拆分消息内容与推理文本
   */
  static extractMessageContent(msg = {}) {
    const content = this.normalizeContent(msg?.content)
    let reasoning = msg?.additional_kwargs?.reasoning_content || msg?.reasoning_content || ''
    let visibleContent = content

    if (!reasoning && content.includes('<think')) {
      const thinkRegex = /<think>([\s\S]*?)<\/think>|<think>([\s\S]*)$/i
      const match = content.match(thinkRegex)
      if (match) {
        reasoning = (match[1] || match[2] || '').trim()
        visibleContent = content.replace(match[0], '').trim()
      }
    }

    return {
      content: visibleContent,
      reasoning
    }
  }

  /**
   * 标准化消息内容
   */
  static normalizeContent(raw) {
    if (raw == null) return ''
    if (typeof raw === 'string') return raw

    if (Array.isArray(raw)) {
      return raw
        .map((item) => {
          if (!item) return ''
          if (typeof item === 'string') return item
          if (typeof item === 'object') {
            return item.text || item.content || item.value || ''
          }
          return String(item)
        })
        .filter(Boolean)
        .join('\n')
        .trim()
    }

    if (typeof raw === 'object') {
      if (typeof raw.text === 'string') return raw.text
      if (typeof raw.content === 'string') return raw.content
      if (Array.isArray(raw.content)) return this.normalizeContent(raw.content)
      try {
        return JSON.stringify(raw, null, 2)
      } catch {
        return String(raw)
      }
    }

    return String(raw)
  }

  /**
   * 生成推理过程 HTML
   */
  static async generateReasoningHTML(reasoning) {
    if (!reasoning) return ''

    const reasoningHTML = await this.renderMarkdown(reasoning)
    if (!reasoningHTML) return ''

    return `
      <details class="reasoning-section">
        <summary class="reasoning-summary">💭 思考过程</summary>
        <div class="reasoning-content markdown-body">
          ${reasoningHTML}
        </div>
      </details>
    `
  }

  /**
   * 生成工具调用 HTML
   */
  static generateToolCallsHTML(msg = {}) {
    const toolCalls = this.normalizeToolCalls(msg)
    if (toolCalls.length === 0) return ''

    const sections = toolCalls
      .map((toolCall) => {
        const toolName = this.escapeHtml(toolCall?.function?.name || toolCall?.name || '工具调用')
        const argsSource = toolCall?.args ?? toolCall?.function?.arguments
        const args = this.stringifyToolArgs(argsSource)
        const result = this.normalizeToolResult(toolCall?.tool_call_result?.content)
        const isFinished = toolCall?.status === 'success'
        const stateClass = isFinished ? 'done' : 'pending'
        const stateLabel = isFinished ? '已完成' : '执行中'

        return `
        <details class="tool-call" ${isFinished ? '' : 'open'}>
          <summary>
            <span class="tool-call-title">🔧 ${toolName}</span>
            <span class="tool-call-state ${stateClass}">${stateLabel}</span>
          </summary>
          <div class="tool-call-body">
            ${
              args
                ? `
              <div class="tool-call-args">
                <strong>参数</strong>
                <pre>${this.escapeHtml(args)}</pre>
              </div>
            `
                : ''
            }
            ${
              isFinished && result
                ? `
              <div class="tool-call-result">
                <strong>结果</strong>
                <pre>${this.escapeHtml(result)}</pre>
              </div>
            `
                : ''
            }
          </div>
        </details>
      `
      })
      .join('')

    return `<div class="tool-calls">${sections}</div>`
  }

  static normalizeToolCalls(msg = {}) {
    const rawCalls = msg.tool_calls || msg.additional_kwargs?.tool_calls
    if (!rawCalls) return []
    if (Array.isArray(rawCalls)) return rawCalls.filter(Boolean)
    if (typeof rawCalls === 'object') {
      return Object.values(rawCalls).filter(Boolean)
    }
    return []
  }

  static stringifyToolArgs(rawArgs) {
    if (rawArgs == null || rawArgs === '') return ''

    if (typeof rawArgs === 'string') {
      const trimmed = rawArgs.trim()
      if (!trimmed) return ''
      try {
        return JSON.stringify(JSON.parse(trimmed), null, 2)
      } catch {
        return trimmed
      }
    }

    if (typeof rawArgs === 'object') {
      try {
        return JSON.stringify(rawArgs, null, 2)
      } catch {
        return String(rawArgs)
      }
    }

    return String(rawArgs)
  }

  static normalizeToolResult(result) {
    if (!result) return ''
    if (typeof result === 'string') return result.trim()

    if (Array.isArray(result)) {
      return result
        .map((item) => {
          if (!item) return ''
          if (typeof item === 'string') return item
          if (typeof item === 'object') {
            return item.text || item.content || JSON.stringify(item, null, 2)
          }
          return String(item)
        })
        .filter(Boolean)
        .join('\n\n')
        .trim()
    }

    if (typeof result === 'object') {
      if (typeof result.content !== 'undefined') {
        return this.normalizeToolResult(result.content)
      }
      try {
        return JSON.stringify(result, null, 2)
      } catch {
        return String(result)
      }
    }

    return String(result)
  }

  /**
   * 统一的 Markdown 渲染，失败时回退到简单换行
   */
  static async renderMarkdown(content) {
    if (!content) return ''
    try {
      return (await renderMarkdownPreview(content)).trim()
    } catch (error) {
      console.warn('Markdown 渲染失败，回退为纯文本:', error)
      return this.escapeHtml(content).replace(/\n/g, '<br>')
    }
  }

  static escapeHtml(value) {
    return escapeHtml(value)
  }

  /**
   * 提取消息时间戳
   */
  static getMessageTimestamp(msg = {}) {
    const candidates = [
      msg.timestamp,
      msg.created_at,
      msg.createdAt,
      msg.createdTime,
      msg.time,
      msg.datetime,
      msg.date,
      msg.additional_kwargs?.timestamp,
      msg.additional_kwargs?.created_at
    ]

    return candidates.find((value) => value !== undefined && value !== null)
  }

  /**
   * 格式化时间戳
   */
  static formatTimestamp(raw) {
    const fallback = dayjs().tz('Asia/Shanghai')

    if (raw instanceof Date) {
      return dayjs(raw).tz('Asia/Shanghai').format('YYYY年MM月DD日 HH:mm:ss')
    }

    if (raw || raw === 0) {
      if (typeof raw === 'number') {
        const value = raw < 1e12 ? raw * 1000 : raw
        return dayjs(value).tz('Asia/Shanghai').format('YYYY年MM月DD日 HH:mm:ss')
      }

      const parsed = parseToShanghai(raw)
      if (parsed) {
        return parsed.format('YYYY年MM月DD日 HH:mm:ss')
      }
    }

    return fallback.format('YYYY年MM月DD日 HH:mm:ss')
  }

  /**
   * 生成完整 HTML 文档骨架
   */
  static generateHTMLTemplate(options) {
    const { chatTitle, agentName, agentDescription, exportTime, messagesHTML } = options

    const safeTitle = this.escapeHtml(chatTitle)
    const safeAgentName = this.escapeHtml(agentName)
    const safeDescription = this.escapeHtml(agentDescription).replace(/\n/g, '<br>')
    const safeExportTime = this.escapeHtml(exportTime)

    const descriptionBlock = agentDescription ? `<br><strong>描述:</strong> ${safeDescription}` : ''

    return chatExportTemplate
      .replace(/{{TITLE}}/g, safeTitle)
      .replace('{{AGENT_NAME}}', safeAgentName)
      .replace('{{DESCRIPTION_BLOCK}}', descriptionBlock)
      .replace('{{EXPORT_TIME}}', safeExportTime)
      .replace('{{MESSAGES}}', messagesHTML)
  }
}

export default ChatExporter
