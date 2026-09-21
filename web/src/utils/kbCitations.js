/** 从工具调用结果重建知识库引用注册表，并解析回答中的引用编号。 */

const KB_CITE_TOOL_NAMES = new Set(['query_kb'])

const getToolContent = (toolCall) =>
  toolCall?.tool_call_result?.content ?? toolCall?.result ?? null

const parseToolPayload = (rawContent) => {
  if (!rawContent) return null
  if (typeof rawContent === 'object') return rawContent
  if (typeof rawContent !== 'string') return null
  try {
    return JSON.parse(rawContent)
  } catch {
    return null
  }
}

const getResultChunks = (payload) => {
  if (Array.isArray(payload?.results)) return payload.results
  if (Array.isArray(payload?.chunks)) return payload.chunks
  return []
}

const normalizeCitationIndex = (chunk, fallbackIndex) => {
  const raw = chunk?.cite ?? chunk?.citation ?? chunk?.citation_index
  const parsed = Number.parseInt(String(raw ?? ''), 10)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallbackIndex
}

const isSameSource = (left, right) =>
  left.kb_id === right.kb_id && left.file_id === right.file_id && left.chunk_id === right.chunk_id

/**
 * 按工具调用顺序收集引用条目，返回「编号 -> 条目」的映射。
 *
 * 编号直接取后端写入的 cite 字段，前端不再自行推算，避免前后端规则不一致导致错位。
 * 后端未写入 cite 时退回该次检索内的顺序编号。
 *
 * 同一个编号若指向不同片段（例如并行发起的多次检索拿到相同基数），条目标记为
 * ambiguous，解析时按无引用处理——宁可不给出跳转，也不能跳到错误的来源。
 */
export function collectKbCitationRegistries(toolCalls) {
  const registry = new Map()

  for (const toolCall of toolCalls || []) {
    const toolName = toolCall?.name || toolCall?.function?.name
    if (!KB_CITE_TOOL_NAMES.has(toolName)) continue

    const payload = parseToolPayload(getToolContent(toolCall))
    const chunks = getResultChunks(payload)
    if (!chunks.length) continue

    let fallbackIndex = 0

    for (const chunk of chunks) {
      if (!chunk || typeof chunk !== 'object') continue
      const metadata = chunk.metadata && typeof chunk.metadata === 'object' ? chunk.metadata : {}
      fallbackIndex += 1

      const entry = {
        index: normalizeCitationIndex(chunk, fallbackIndex),
        kb_id: String(chunk.kb_id || ''),
        file_id: String(chunk.file_id || metadata.file_id || ''),
        chunk_id: String(chunk.id || metadata.chunk_id || ''),
        source: String(metadata.source || '未知来源'),
        ambiguous: false
      }

      const existing = registry.get(entry.index)
      if (!existing) {
        registry.set(entry.index, entry)
      } else if (!isSameSource(existing, entry)) {
        registry.set(entry.index, { ...existing, ambiguous: true })
      }
    }
  }

  return registry
}

/** 解析引用标记内的编号；不是纯编号时返回 null，由调用方按无引用处理。 */
export function parseKbCitationIndex(rawText) {
  const matched = /^\s*(\d+)\s*$/.exec(String(rawText ?? ''))
  return matched ? Number(matched[1]) : null
}

/** 按编号解析引用；编号不存在或存在歧义时返回 null。 */
export function resolveKbCitation(registry, rawText) {
  const index = parseKbCitationIndex(rawText)
  if (!index) return null

  const entry = registry instanceof Map ? registry.get(index) : null
  if (!entry || entry.ambiguous) return null
  return entry
}
