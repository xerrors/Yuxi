import { getDisplayFileName } from '@/shared/lib/file_utils'
import { mentionTypePrefixMap } from './mention_token.js'

export { formatMentionToken, mentionTypePrefixMap } from './mention_token.js'

const mentionTypePattern = Object.values(mentionTypePrefixMap).join('|')
const mentionTokenRegex = new RegExp(
  `@(${mentionTypePattern}):(?:"((?:\\\\.|[^"\\\\])*)"|(\\S+))`,
  'g'
)

const unquoteMentionValue = (value) => String(value ?? '').replace(/\\(["\\])/g, '$1')

export const parseMentionText = (text = '') => {
  const value = String(text || '')
  const segments = []
  let lastIndex = 0

  mentionTokenRegex.lastIndex = 0
  for (const match of value.matchAll(mentionTokenRegex)) {
    const raw = match[0]
    const start = match.index ?? 0
    const end = start + raw.length

    if (start > lastIndex) {
      segments.push({
        kind: 'text',
        text: value.slice(lastIndex, start),
        start: lastIndex,
        end: start
      })
    }

    const type = match[1]
    const quotedValue = match[2]
    const rawValue = match[3]
    segments.push({
      kind: 'mention',
      raw,
      type,
      value: quotedValue !== undefined ? unquoteMentionValue(quotedValue) : rawValue,
      start,
      end
    })

    lastIndex = end
  }

  if (lastIndex < value.length) {
    segments.push({
      kind: 'text',
      text: value.slice(lastIndex),
      start: lastIndex,
      end: value.length
    })
  }

  return segments
}

const setMentionLabel = (labels, type, value, label) => {
  const rawValue = String(value ?? '').trim()
  const displayLabel = String(label || '').trim()
  if (!rawValue || !displayLabel) return
  labels[`${type}:${rawValue}`] = displayLabel
}

export const buildMentionDisplayLabels = (mention = {}) => {
  const labels = {}

  ;(mention.knowledgeBases || []).forEach((kb) => {
    const label = kb?.name || kb?.label || kb?.kb_id || kb?.value || ''
    setMentionLabel(labels, 'knowledge', kb?.name, label)
    setMentionLabel(labels, 'knowledge', kb?.label, label)
    setMentionLabel(labels, 'knowledge', kb?.kb_id, label)
    setMentionLabel(labels, 'knowledge', kb?.value, label)
  })
  ;(mention.mcps || []).forEach((mcp) => {
    const label = mcp?.name || mcp?.label || mcp?.slug || mcp?.id || mcp?.value || ''
    setMentionLabel(labels, 'mcp', mcp?.slug, label)
    setMentionLabel(labels, 'mcp', mcp?.id, label)
    setMentionLabel(labels, 'mcp', mcp?.value, label)
    setMentionLabel(labels, 'mcp', mcp?.name, label)
  })
  ;(mention.plugins || []).forEach((plugin) => {
    setMentionLabel(labels, 'plugin', plugin?.id, plugin?.name || plugin?.id)
  })
  ;(mention.skills || []).forEach((skill) => {
    const label = skill?.name || ''
    setMentionLabel(labels, 'skill', skill?.slug, label)
    setMentionLabel(labels, 'skill', skill?.name, label)
  })

  ;(mention.agents || []).forEach((agent) => {
    setMentionLabel(labels, 'agent', agent?.agent_id, agent?.name || agent?.agent_id)
  })

  return labels
}

export const getMentionDisplayLabel = (type, value, displayLabels = {}) => {
  const mappedLabel = displayLabels[`${type}:${value}`]
  if (mappedLabel) return mappedLabel

  if (type === 'session' || type === 'device') {
    const [id, ...name] = String(value ?? '').split(' ')
    return name.join(' ').trim() || (type === 'session' ? '未命名对话' : id)
  }

  if (type === 'file') {
    const normalizedPath = String(value ?? '').replace(/\/+$/, '')
    return getDisplayFileName(normalizedPath || value, '文件')
  }
  return String(value ?? '').trim() || type
}

export const findActiveMentionQuery = (text = '', rawCaretOffset = 0) => {
  const value = String(text || '')
  const offset = Math.max(0, Math.min(rawCaretOffset, value.length))
  const segments = parseMentionText(value)
  const mentionAtCursor = segments.find(
    (segment) => segment.kind === 'mention' && offset > segment.start && offset <= segment.end
  )

  if (mentionAtCursor) return null

  const textBeforeCursor = value.slice(0, offset)
  const atIndex = textBeforeCursor.lastIndexOf('@')
  if (atIndex === -1) return null

  const query = textBeforeCursor.slice(atIndex + 1)
  if (query.includes('@') || query.includes(':') || /\s/.test(query)) return null

  return { start: atIndex, end: offset, query }
}

export const replaceRawRange = (text = '', start = 0, end = start, replacement = '') => {
  const value = String(text || '')
  const safeStart = Math.max(0, Math.min(start, value.length))
  const safeEnd = Math.max(safeStart, Math.min(end, value.length))
  return value.slice(0, safeStart) + replacement + value.slice(safeEnd)
}

export const expandMentionDeletionRange = (
  text = '',
  start = 0,
  end = start,
  direction = 'backward',
  knownMentionRanges = []
) => {
  const value = String(text || '')
  const safeStart = Math.max(0, Math.min(start, value.length))
  const safeEnd = Math.max(safeStart, Math.min(end, value.length))
  const cursor = safeStart
  if (safeStart === safeEnd && direction === 'backward') {
    const knownMention = knownMentionRanges.find(
      (mention) => mention && cursor === mention.end && mention.start < mention.end
    )
    if (knownMention) return { start: knownMention.start, end: knownMention.end }
  }

  const mentions = parseMentionText(value).filter((segment) => segment.kind === 'mention')
  if (safeStart !== safeEnd) {
    const touchedMentions = mentions.filter(
      (segment) => segment.start < safeEnd && segment.end > safeStart
    )
    if (!touchedMentions.length) return null

    return {
      start: Math.min(safeStart, ...touchedMentions.map((segment) => segment.start)),
      end: Math.max(safeEnd, ...touchedMentions.map((segment) => segment.end))
    }
  }

  if (direction === 'forward') {
    const mention = mentions.find((segment) => cursor >= segment.start && cursor < segment.end)
    if (!mention) return null

    const includeTrailingSpace = value[mention.end] === ' '
    return {
      start: mention.start,
      end: includeTrailingSpace ? mention.end + 1 : mention.end
    }
  }

  const mention = mentions.find((segment) => cursor > segment.start && cursor <= segment.end)
  if (mention) {
    return { start: mention.start, end: mention.end }
  }

  return null
}
