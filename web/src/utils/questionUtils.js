/**
 * 问题和选项规范化工具
 */

const DEFAULT_OTHER_OPTION_VALUE = '__other__'

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
 * 判断选项是否为"其他"选项
 */
export const isOtherOption = (option) => {
  if (!option || typeof option !== 'object') return false
  const label = String(option.label || '')
    .trim()
    .toLowerCase()
  const value = String(option.value || '')
    .trim()
    .toLowerCase()

  return (
    value === DEFAULT_OTHER_OPTION_VALUE ||
    value === 'other' ||
    label.includes('其他') ||
    label.includes('other')
  )
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
      const baseOptions = normalizeOptions(optionsVal || [])
      const hasOtherOption = baseOptions.some((option) => isOtherOption(option))
      const options =
        allowOther && !hasOtherOption
          ? [...baseOptions, { label: '其他', value: DEFAULT_OTHER_OPTION_VALUE }]
          : baseOptions

      return {
        questionId,
        question,
        options,
        multiSelect,
        allowOther,
        operation
      }
    })
    .filter(Boolean)
}

export { DEFAULT_OTHER_OPTION_VALUE }
