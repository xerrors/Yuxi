/** 解析工具返回的 JSON 内容。 */
const parseToolResultContent = (content) => {
  if (Array.isArray(content)) return content
  if (content && typeof content === 'object') return content
  if (typeof content === 'string') {
    try {
      return JSON.parse(content)
    } catch {
      return null
    }
  }
  return null
}

/**
 * 消息处理工具类
 */
export class MessageProcessor {
  /**
   * 将工具结果与消息合并
   * @param {Array} msgs - 消息数组
   * @returns {Array} 合并后的消息数组
   */
  static convertToolResultToMessages(msgs) {
    const toolResponseMap = new Map()

    // 构建工具响应映射
    for (const item of msgs) {
      if (item.type === 'tool') {
        const toolCallId = item.tool_call_id
        if (toolCallId) {
          toolResponseMap.set(JSON.stringify([item.run_id || '', toolCallId]), item)
        }
      }
    }

    // 合并工具调用和响应
    const convertedMsgs = msgs.map((item) => {
      if (item.type === 'ai' && item.tool_calls && item.tool_calls.length > 0) {
        return {
          ...item,
          tool_calls: item.tool_calls.map((toolCall) => {
            const toolResponse = toolResponseMap.get(JSON.stringify([item.run_id || '', toolCall.id]))
            return {
              ...toolCall,
              tool_call_result: toolResponse || null
            }
          })
        }
      }
      return item
    })

    return convertedMsgs
  }

  /**
   * 将服务器历史记录转换为对话格式
   * @param {Array} serverHistory - 服务器历史记录
   * @returns {Array} 对话数组
   */
  static convertServerHistoryToMessages(serverHistory, runs = []) {
    const terminalStatuses = new Set(['completed', 'failed', 'cancelled', 'interrupted'])
    const conversations = runs.map((run) => ({
      run,
      messages: [],
      status: terminalStatuses.has(run.status) ? 'finished' : 'loading'
    }))
    const byRunId = new Map(conversations.map((conv) => [conv.run.run_id, conv]))
    let legacyConv = null

    for (const item of serverHistory) {
      if (item.type === 'tool' ||
          (item.type === 'human' && item.extra_metadata?.source === 'ask_user_question_resume')) {
        continue
      }
      const runId = item.run_id || item.extra_metadata?.run_id
      let conv = runId ? byRunId.get(runId) : legacyConv
      if (!conv || (!runId && item.type === 'human')) {
        conv = { messages: [], status: 'loading' }
        conversations.push(conv)
        if (runId) byRunId.set(runId, conv)
      }
      conv.messages.push({ ...item })
      // 没有 Run 关联的旧历史仍按用户轮次展示，不能挂到相邻 Run 上。
      legacyConv = runId ? null : conv
    }

    conversations.sort((left, right) => {
      const leftTime = left.run?.timing?.created_at || left.messages[0]?.created_at || ''
      const rightTime = right.run?.timing?.created_at || right.messages[0]?.created_at || ''
      return leftTime.localeCompare(rightTime)
    })
    for (const conv of conversations) {
      const lastAi = conv.messages.findLast((message) => message.type === 'ai')
      if (lastAi) {
        lastAi.isLast = true
        if (!conv.run) conv.status = 'finished'
      }
    }
    return conversations
  }

  /**
   * 提取一轮对话中已成功登记的交付物路径。
   * @param {Object} conv - 单轮对话
   * @returns {Array<string>} 去重后的交付物路径
   */
  static extractArtifactsFromConversation(conv) {
    if (!conv || !Array.isArray(conv.messages)) return []

    const artifacts = []
    const seenPaths = new Set()
    for (const message of conv.messages) {
      if (message?.type !== 'ai' || !Array.isArray(message.tool_calls)) continue

      for (const toolCall of message.tool_calls) {
        const toolName = toolCall?.name || toolCall?.function?.name
        if (toolName !== 'present_artifacts') continue
        if (!toolCall.tool_call_result && toolCall.status !== 'success') continue

        let args = toolCall.args ?? toolCall.function?.arguments
        if (typeof args === 'string') {
          try {
            args = JSON.parse(args)
          } catch {
            continue
          }
        }

        const filepaths = Array.isArray(args?.filepaths) ? args.filepaths : []
        for (const filepath of filepaths) {
          const normalizedPath = typeof filepath === 'string' ? filepath.trim() : ''
          if (!normalizedPath || seenPaths.has(normalizedPath)) continue
          seenPaths.add(normalizedPath)
          artifacts.push(normalizedPath)
        }
      }
    }
    return artifacts
  }

  /**
   * 提取一轮对话中所有知识库检索块
   * @param {Object} conv - 单轮对话
   * @param {Array} databases - 知识库列表
   * @returns {Array} 归一化后的检索块
   */
  static extractKnowledgeChunksFromConversation(conv, databases = []) {
    if (!conv || !Array.isArray(conv.messages) || conv.messages.length === 0) return []

    const databaseNames = new Set(
      (databases || [])
        .map((db) => db?.name)
        .filter((name) => typeof name === 'string' && name.trim())
    )
    if (databaseNames.size === 0) return []

    const normalizedChunks = []
    const dedupSet = new Set()

    const appendChunk = (chunk, kbName) => {
      if (!chunk || typeof chunk !== 'object') return
      const content = typeof chunk.content === 'string' ? chunk.content.trim() : ''
      if (!content) return

      const metadata = chunk.metadata && typeof chunk.metadata === 'object' ? chunk.metadata : {}
      const dedupKey =
        metadata.chunk_id && typeof metadata.chunk_id === 'string'
          ? `${kbName}::${metadata.chunk_id}`
          : `${kbName}::${content}`
      if (dedupSet.has(dedupKey)) return
      dedupSet.add(dedupKey)

      const score = typeof chunk.score === 'number' ? chunk.score : null
      normalizedChunks.push({
        kb_name: kbName,
        content,
        score,
        metadata: {
          source: metadata.source || '',
          file_id: metadata.file_id || '',
          chunk_id: metadata.chunk_id || '',
          chunk_index: metadata.chunk_index
        }
      })
    }

    for (const msg of conv.messages) {
      if (!msg || msg.type !== 'ai' || !Array.isArray(msg.tool_calls)) continue

      for (const toolCall of msg.tool_calls) {
        const kbName = toolCall?.name || toolCall?.function?.name
        if (!databaseNames.has(kbName)) continue

        const content = toolCall?.tool_call_result?.content
        const parsed = parseToolResultContent(content)
        if (!parsed) continue

        // Milvus / Dify: 直接是 chunks 数组
        if (Array.isArray(parsed)) {
          for (const chunk of parsed) appendChunk(chunk, kbName)
          continue
        }

        const wrappedChunks = parsed?.data?.chunks
        if (Array.isArray(wrappedChunks)) {
          for (const chunk of wrappedChunks) appendChunk(chunk, kbName)
        }
      }
    }

    normalizedChunks.sort((a, b) => {
      const scoreA = typeof a.score === 'number' ? a.score : Number.NEGATIVE_INFINITY
      const scoreB = typeof b.score === 'number' ? b.score : Number.NEGATIVE_INFINITY
      return scoreB - scoreA
    })

    return normalizedChunks
  }

  /**
   * 提取一轮对话中的网络搜索来源
   * @param {Object} conv - 单轮对话
   * @returns {Array} 归一化后的网络来源
   */
  static extractWebSourcesFromConversation(conv) {
    if (!conv || !Array.isArray(conv.messages) || conv.messages.length === 0) return []

    const webSources = []
    const dedupSet = new Set()

    for (const msg of conv.messages) {
      if (!msg || msg.type !== 'ai' || !Array.isArray(msg.tool_calls)) continue

      for (const toolCall of msg.tool_calls) {
        const toolName = (toolCall?.name || toolCall?.function?.name || '').toLowerCase()
        if (
          !toolName.includes('web_search') &&
          !toolName.includes('tavily_search') &&
          !toolName.includes('doubao_search')
        )
          continue

        const content = toolCall?.tool_call_result?.content
        const parsed = parseToolResultContent(content)
        const results = Array.isArray(parsed?.results) ? parsed.results : []
        if (results.length === 0) continue

        for (const item of results) {
          const title = typeof item?.title === 'string' ? item.title.trim() : ''
          const url = typeof item?.url === 'string' ? item.url.trim() : ''
          if (!title || !url) continue
          if (dedupSet.has(url)) continue
          dedupSet.add(url)

          webSources.push({
            tool_name: toolCall?.name || toolCall?.function?.name || '网络搜索',
            title,
            url,
            score: typeof item?.score === 'number' ? item.score : null,
            content: typeof item?.content === 'string' ? item.content : '',
            published_date: typeof item?.published_date === 'string' ? item.published_date : ''
          })
        }
      }
    }

    webSources.sort((a, b) => {
      const scoreA = typeof a.score === 'number' ? a.score : Number.NEGATIVE_INFINITY
      const scoreB = typeof b.score === 'number' ? b.score : Number.NEGATIVE_INFINITY
      return scoreB - scoreA
    })

    return webSources
  }

  /**
   * 提取单个消息中的来源
   * @param {Object} message - 消息对象
   * @param {Array} databases - 知识库列表
   * @returns {{knowledgeChunks: Array, webSources: Array}}
   */
  static extractSourcesFromMessage(message, databases = []) {
    if (!message || message.type !== 'ai') return { knowledgeChunks: [], webSources: [] }

    // 复用提取逻辑，通过构建临时对话对象
    const mockConv = { messages: [message] }
    return {
      knowledgeChunks: MessageProcessor.extractKnowledgeChunksFromConversation(mockConv, databases),
      webSources: MessageProcessor.extractWebSourcesFromConversation(mockConv)
    }
  }

  /**
   * 提取一轮对话中的全部来源（知识库+网络搜索）
   * @param {Object} conv - 单轮对话
   * @param {Array} databases - 知识库列表
   * @returns {{knowledgeChunks: Array, webSources: Array}}
   */
  static extractSourcesFromConversation(conv, databases = []) {
    return {
      knowledgeChunks: MessageProcessor.extractKnowledgeChunksFromConversation(conv, databases),
      webSources: MessageProcessor.extractWebSourcesFromConversation(conv)
    }
  }

  /**
   * 解析助手消息正文与推理内容，保持渲染和列表拆分使用同一套规则。
   * @param {Object} message - AI 消息对象
   * @returns {{content: string, reasoningContent: string}}
   */
  static parseAssistantMessageBody(message) {
    return {
      content: typeof message?.content === 'string' ? message.content.trim() : '',
      reasoningContent: message?.reasoning_content || ''
    }
  }

  /**
   * 合并消息块
   * @param {Array} chunks - 消息块数组
   * @returns {Object|null} 合并后的消息
   */
  static mergeMessageChunk(chunks) {
    if (chunks.length === 0) return null

    // 深拷贝第一个chunk作为结果
    const result = JSON.parse(JSON.stringify(chunks[0]))
    MessageProcessor._mergeToolCalls(result, chunks[0])

    // 处理用户消息的内容格式 - 确保显示纯文本
    if (result.type === 'human' || result.role === 'user') {
      // 如果content是数组格式（LangChain多模态消息），提取文本部分
      if (Array.isArray(result.content)) {
        const textPart = result.content.find((item) => item.type === 'text')
        result.content = textPart ? textPart.text : ''
      } else {
        result.content = result.content || ''
      }
    } else {
      result.content = result.content || ''
    }

    // 合并后续chunks
    for (let i = 1; i < chunks.length; i++) {
      const chunk = chunks[i]

      // 合并内容
      if (chunk.content) {
        result.content += chunk.content
      }

      // 合并reasoning_content
      if (chunk.reasoning_content) {
        if (!result.reasoning_content) {
          result.reasoning_content = ''
        }
        result.reasoning_content += chunk.reasoning_content
      }

      // 合并tool_calls (处理新的数据结构)
      MessageProcessor._mergeToolCalls(result, chunk)
    }

    // 处理AIMessageChunk类型
    if (result.type === 'AIMessageChunk') {
      result.type = 'ai'
    }

    return result
  }

  /**
   * 合并工具调用
   * @private
   * @param {Object} result - 结果对象
   * @param {Object} chunk - 当前块
   */
  static _mergeToolCalls(result, chunk) {
    if (chunk.tool_call_chunks && chunk.tool_call_chunks.length > 0) {
      // 确保 result 有 tool_calls 数组
      if (!result.tool_calls) result.tool_calls = []

      for (const toolCallChunk of chunk.tool_call_chunks) {
        // 使用 index 来标识工具调用（因为可能有多个工具调用）
        const existingToolCallIndex = result.tool_calls.findIndex(
          (t) => t.index === toolCallChunk.index
        )

        if (existingToolCallIndex !== -1) {
          // 合并相同index的tool call
          const existingToolCall = result.tool_calls[existingToolCallIndex]

          // 更新名称和ID（如果存在）
          if (toolCallChunk.name) {
            existingToolCall.name = toolCallChunk.name
          }

          if (toolCallChunk.id && !existingToolCall.id) {
            existingToolCall.id = toolCallChunk.id
          }

          // 合并参数
          existingToolCall.args = toolCallChunk.complete
            ? toolCallChunk.args || ''
            : (existingToolCall.args || '') + (toolCallChunk.args || '')
        } else {
          // 添加新的tool call
          const newToolCall = {
            index: toolCallChunk.index,
            id: toolCallChunk.id,
            name: toolCallChunk.name || null,
            args: toolCallChunk.args || ''
          }
          result.tool_calls.push(newToolCall)
        }
      }
    }
  }
}

export default MessageProcessor
