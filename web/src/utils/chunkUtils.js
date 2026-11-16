/**
 * Chunk 工具函数
 */

export const DEFAULT_CHUNK_PRESET_ID = 'general'

export const DEFAULT_EMBEDDING_FEATURES = Object.freeze({
  bge_m3_sparse_enabled: false
})

export const DEFAULT_PARENT_CHILD_CONFIG = Object.freeze({
  enabled: false,
  parent_token_num: 1000,
  child_token_num: 200,
  child_overlap_percent: 15,
  separator: '\\n',
  text_preservation: 'parsed_markdown_raw'
})

const PARENT_CHILD_PROCESSING_KEYS = [
  'enabled',
  'parent_token_num',
  'child_token_num',
  'child_overlap_percent',
  'separator',
  'tokenizer_id',
  'text_preservation'
]

const PARENT_CHILD_KNOWLEDGE_KEYS = [
  'enabled',
  'parent_token_num',
  'child_token_num',
  'child_overlap_percent',
  'separator',
  'tokenizer_policy',
  'text_preservation'
]

export const isPlainObject = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value)

const copyDefinedFields = (source, keys) => {
  const result = {}
  if (!isPlainObject(source)) return result

  for (const key of keys) {
    if (source[key] !== undefined && source[key] !== null) {
      result[key] = source[key]
    }
  }
  return result
}

/** 按传入顺序合并索引能力配置，并返回独立副本。 */
export const createIndexingFeatureParams = (...sources) => {
  const embeddingFeatures = { ...DEFAULT_EMBEDDING_FEATURES }
  const parentChild = { ...DEFAULT_PARENT_CHILD_CONFIG }

  for (const source of sources) {
    if (!isPlainObject(source)) continue
    if (isPlainObject(source.embedding_features)) {
      Object.assign(embeddingFeatures, source.embedding_features)
    }
    if (isPlainObject(source.parent_child)) {
      Object.assign(parentChild, source.parent_child)
    }
  }

  return {
    embedding_features: embeddingFeatures,
    parent_child: parentChild
  }
}

/** 创建入库对话框参数，文件级配置覆盖知识库默认值。 */
export const createIndexingParams = (databaseParams = {}, processingParams = null) => {
  const processing = isPlainObject(processingParams) ? processingParams : {}
  const features = createIndexingFeatureParams(databaseParams, processing)

  return {
    chunk_preset_id: processing.chunk_preset_id || '',
    chunk_parser_config: isPlainObject(processing.chunk_parser_config)
      ? { ...processing.chunk_parser_config }
      : {},
    ...features
  }
}

/** 构建知识库级索引默认值载荷。 */
export const buildKnowledgeIndexingDefaultsPayload = (source) => {
  const features = createIndexingFeatureParams(source)
  return {
    chunk_preset_id: source?.chunk_preset_id || DEFAULT_CHUNK_PRESET_ID,
    embedding_features: {
      bge_m3_sparse_enabled: features.embedding_features.bge_m3_sparse_enabled === true
    },
    parent_child: copyDefinedFields(features.parent_child, PARENT_CHILD_KNOWLEDGE_KEYS)
  }
}

/** 校验 Parent-Child 表单的前端输入约束。 */
export const validateParentChildConfig = (source) => {
  const config = createIndexingFeatureParams(source).parent_child
  if (!config.enabled) return ''

  const integerRanges = [
    ['parent_token_num', 256, 4096, '父块 Token 数'],
    ['child_token_num', 64, 1024, '子块 Token 数'],
    ['child_overlap_percent', 0, 99, '子块重叠比例']
  ]

  for (const [key, minimum, maximum, label] of integerRanges) {
    const value = Number(config[key])
    if (!Number.isInteger(value) || value < minimum || value > maximum) {
      return `${label}必须是 ${minimum} 到 ${maximum} 的整数`
    }
  }
  if (Number(config.parent_token_num) <= Number(config.child_token_num)) {
    return '父块 Token 数必须大于子块 Token 数'
  }
  if (!String(config.separator || '')) {
    return 'Parent-Child 分隔符不能为空'
  }
  return ''
}

export const buildChunkParserConfigPayload = (source, { includeSizeOverlap = true } = {}) => {
  if (!isPlainObject(source)) {
    return {}
  }

  const config = {}
  if (includeSizeOverlap) {
    if (source.chunk_token_num !== undefined && source.chunk_token_num !== null) {
      config.chunk_token_num = source.chunk_token_num
    }
    if (source.overlapped_percent !== undefined && source.overlapped_percent !== null) {
      config.overlapped_percent = source.overlapped_percent
    }
  }
  if (source.delimiter) {
    config.delimiter = source.delimiter
  }

  return config
}

export const buildChunkParamsPayload = (source, options = {}) => {
  if (!isPlainObject(source)) {
    return {}
  }

  const payload = {}
  const chunkParserConfig = buildChunkParserConfigPayload(source.chunk_parser_config, options)
  if (Object.keys(chunkParserConfig).length > 0) {
    payload.chunk_parser_config = chunkParserConfig
  }
  if (source.chunk_preset_id) {
    payload.chunk_preset_id = source.chunk_preset_id
  }
  if (isPlainObject(source.embedding_features)) {
    payload.embedding_features = {
      bge_m3_sparse_enabled: source.embedding_features.bge_m3_sparse_enabled === true
    }
  }
  if (isPlainObject(source.parent_child)) {
    payload.parent_child = copyDefinedFields(
      source.parent_child,
      PARENT_CHILD_PROCESSING_KEYS
    )
  }

  return payload
}

/**
 * 查找两个字符串的重叠部分
 * @param {string} str1 - 第一个字符串
 * @param {string} str2 - 第二个字符串
 * @returns {string} - 重叠部分的内容
 */
export function findOverlap(str1, str2) {
  if (!str1 || !str2) return ''

  const maxOverlap = Math.min(str1.length, str2.length)
  let overlap = ''

  // 从最长可能的重叠开始检查
  for (let i = maxOverlap; i > 10; i--) {
    const endStr1 = str1.slice(-i)
    const startStr2 = str2.slice(0, i)

    if (endStr1 === startStr2) {
      overlap = endStr1
      break
    }
  }

  return overlap
}

/**
 * 合并chunks并处理重叠内容
 * @param {Array} chunks - chunk数组，每个chunk包含id, content, chunk_order_index
 * @returns {Object} - 合并结果，包含content和chunks数组
 */
export function mergeChunks(chunks) {
  if (!chunks || chunks.length === 0) {
    return { content: '', chunks: [] }
  }

  // 按order排序
  const sorted = [...chunks].sort((a, b) => a.chunk_order_index - b.chunk_order_index)
  const merged = []
  let currentContent = ''

  for (let i = 0; i < sorted.length; i++) {
    const chunk = sorted[i]
    const content = chunk.content

    if (i === 0) {
      // 第一个chunk直接添加
      currentContent = content
      merged.push({
        ...chunk,
        startOffset: 0,
        endOffset: content.length
      })
    } else {
      // 查找重叠部分
      const overlap = findOverlap(currentContent, content)
      const newContent = content.slice(overlap.length)

      if (newContent.length > 0) {
        const startOffset = currentContent.length
        if (overlap.length > 0) {
          currentContent += newContent
        } else {
          currentContent += `\n${newContent}`
        }
        merged.push({
          ...chunk,
          startOffset,
          endOffset: currentContent.length
        })
      }
    }
  }

  return { content: currentContent, chunks: merged }
}

/**
 * 获取chunk的预览文本
 * @param {string} content - chunk内容
 * @param {number} maxLength - 最大长度
 * @returns {string} - 预览文本
 */
export function getChunkPreview(content, maxLength = 100) {
  if (!content) return ''

  const text = content.replace(/\n+/g, ' ').trim()
  if (text.length <= maxLength) return text

  return text.slice(0, maxLength) + '...'
}
