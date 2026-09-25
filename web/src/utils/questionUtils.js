/**
 * 问题和选项规范化工具
 */

const WRAPPER_OPTION_KEYS = ['item', 'items', 'options', 'list', 'choices', 'data']
const WRAPPER_QUESTION_KEYS = ['questions', 'items', 'item', 'list', 'data']

const normalizeCollection = (value, wrapperKeys, isItem, mappingAsOptions = false) => {
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      if (typeof parsed !== 'string') value = parsed
    } catch {
      return []
    }
  }

  if (Array.isArray(value)) return value
  if (!value || typeof value !== 'object') return []

  for (const key of wrapperKeys) {
    if (!(key in value)) continue
    const wrapped = value[key]
    if (Array.isArray(wrapped)) return wrapped
    if (wrapped && typeof wrapped === 'object' && isItem(wrapped)) return [wrapped]
  }

  if (isItem(value)) return [value]
  return mappingAsOptions
    ? Object.entries(value).map(([option, label]) => ({
        label: String(label || ''),
        value: String(option || '')
      }))
    : []
}

/**
 * 安全解析布尔值
 */
export const parseBool = (val, defaultVal = false) => {
  if (val === undefined || val === null) return defaultVal
  if (typeof val === 'boolean') return val
  if (typeof val === 'number') return val !== 0
  if (typeof val === 'string') {
    const s = val.trim().toLowerCase()
    if (['true', '1', 'yes', 'y', 't'].includes(s)) return true
    if (['false', '0', 'no', 'n', 'f', ''].includes(s)) return false
  }
  return defaultVal
}

/**
 * 规范化选项列表
 */
export const normalizeOptions = (rawOptions) => {
  const target = normalizeCollection(
    rawOptions,
    WRAPPER_OPTION_KEYS,
    (item) => Boolean(item.label || item.value),
    true
  )

  return target
    .map((item) => {
      if (item && typeof item === 'object') {
        const label = String(item.label || item.value || item.title || item.text || '').trim()
        const value = String(item.value || item.label || item.id || item.key || '').trim()
        const description = String(item.description || item.desc || '').trim()
        if (label && value) {
          const res = { label, value }
          if (description) {
            res.description = description
          }
          return res
        }
        return null
      }

      const text = String(item || '').trim()
      return text ? { label: text, value: text } : null
    })
    .filter(Boolean)
}

/**
 * 规范化问题列表
 */
export const normalizeQuestions = (rawQuestions) => {
  const target = normalizeCollection(rawQuestions, WRAPPER_QUESTION_KEYS, (item) =>
    Boolean(item.question || item.title || item.text)
  )

  return target
    .map((item, index) => {
      if (!item || typeof item !== 'object') return null

      const question = String(item.question || item.title || item.text || '').trim()
      if (!question) return null

      const questionId =
        String(item.questionId || item.question_id || item.id || '').trim() || `q-${index + 1}`
      const operation = String(item.operation || '').trim()
      const allowOther = parseBool(item.allowOther ?? item.allow_other, true)
      const multiSelect = parseBool(item.multiSelect ?? item.multi_select, false)
      const optionsVal = item.options !== undefined ? item.options : item.choices
      const options = normalizeOptions(optionsVal || [])
      let answerMode = 'text'
      if (options.length > 0) {
        answerMode = multiSelect ? 'multiple' : 'single'
      }

      return {
        questionId,
        question,
        options,
        multiSelect,
        allowOther,
        operation,
        answerMode
      }
    })
    .filter(Boolean)
}

/**
 * 判断问题是否已有可提交答案
 */
export const isQuestionAnswered = (
  question,
  selectedValues = [],
  text = '',
  customAnswer = false
) => {
  const normalizedText = String(text || '').trim()
  if (question?.answerMode === 'text') return Boolean(normalizedText)
  if (question?.allowOther && customAnswer) return Boolean(normalizedText)
  return Array.isArray(selectedValues) && selectedValues.length > 0
}

/**
 * 构建单题的 resume 答案
 */
export const buildQuestionAnswer = (
  question,
  selectedValues = [],
  text = '',
  customAnswer = false
) => {
  const normalizedText = String(text || '').trim()
  if (question?.answerMode === 'text') return normalizedText

  const selected = Array.isArray(selectedValues) ? selectedValues : []
  if (question?.allowOther && customAnswer) {
    return {
      type: 'other',
      text: normalizedText,
      selected
    }
  }
  return question?.multiSelect ? selected : selected[0]
}
