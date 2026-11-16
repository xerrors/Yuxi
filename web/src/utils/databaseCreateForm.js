export const createDefaultShareConfig = () => ({
  version: 2,
  read_scope: { access_level: 'global', department_ids: [], user_uids: [] },
  manage_scope: null
})

export const createEmptyDatabaseForm = (embeddingModel = '') => ({
  name: '',
  description: '',
  embedding_model_spec: embeddingModel,
  kb_type: '',
  chunk_preset_id: DEFAULT_CHUNK_PRESET_ID,
  additional_params: {
    chunk_parser_config: {
      chunk_token_num: 512,
      overlapped_percent: 0,
      delimiter: '\\n'
    },
    embedding_features: { bge_m3_sparse_enabled: false },
    parent_child: {
      enabled: false,
      parent_token_num: 1000,
      child_token_num: 200,
      child_overlap_percent: 15,
      separator: '\\n',
      text_preservation: 'parsed_markdown_raw'
    }
  }
})

export const isBgeM3EmbeddingModelSpec = (spec = '') => {
  const modelId = String(spec).split(':').pop()?.trim().toLowerCase()
  return modelId === 'baai/bge-m3' || modelId === 'pro/baai/bge-m3'
}

export const createParamValues = (fields = []) =>
  Object.fromEntries(
    fields.map((field) => [
      field.key,
      'default' in field ? field.default : field.type === 'boolean' ? false : ''
    ])
  )

export const selectDatabaseType = (form, type, typeInfo) => {
  const indexingDefaults = createEmptyDatabaseForm(form.embedding_model_spec).additional_params
  return {
    ...form,
    kb_type: type,
    additional_params: typeInfo?.requires_embedding_model
      ? {
          ...indexingDefaults,
          ...form.additional_params,
          ...createParamValues(typeInfo?.create_params?.options)
        }
      : createParamValues(typeInfo?.create_params?.options)
  }
}

export const validateDatabaseConfig = (form, typeInfo) => {
  if (!String(form?.name || '').trim()) return '请输入知识库名称'
  if (typeInfo?.requires_embedding_model && !form?.embedding_model_spec) {
    return '请选择嵌入模型'
  }
  if (
    form?.additional_params?.embedding_features?.bge_m3_sparse_enabled &&
    !isBgeM3EmbeddingModelSpec(form.embedding_model_spec)
  ) {
    return '只有 BGE-M3 嵌入模型可以启用稀疏向量'
  }
  const parentChild = form?.additional_params?.parent_child
  if (parentChild?.enabled) {
    const ranges = [
      ['parent_token_num', 256, 4096, '父块 Token 数'],
      ['child_token_num', 64, 1024, '子块 Token 数'],
      ['child_overlap_percent', 0, 99, '子块重叠比例']
    ]
    for (const [key, minimum, maximum, label] of ranges) {
      const value = Number(parentChild[key])
      if (!Number.isInteger(value) || value < minimum || value > maximum) {
        return `${label}必须是 ${minimum} 到 ${maximum} 的整数`
      }
    }
    if (Number(parentChild.parent_token_num) <= Number(parentChild.child_token_num)) {
      return '父块 Token 数必须大于子块 Token 数'
    }
    if (!String(parentChild.separator || '')) return 'Parent-Child 分隔符不能为空'
  }

  for (const field of typeInfo?.create_params?.options || []) {
    const value = form?.additional_params?.[field.key]
    if (
      field.required &&
      (value === undefined || value === null || (typeof value === 'string' && !value.trim()))
    ) {
      return `请填写${field.label || field.key}`
    }
    if (field.type === 'number' && typeof value === 'number') {
      if (field.min !== undefined && value < field.min)
        return `${field.label || field.key}不能小于${field.min}`
      if (field.max !== undefined && value > field.max)
        return `${field.label || field.key}不能大于${field.max}`
    }
  }
  return ''
}

export const buildDatabaseRequest = (form, typeInfo, shareConfig, defaultEmbeddingModel) => {
  const additionalParams = {}
  for (const field of typeInfo?.create_params?.options || []) {
    const value = form.additional_params[field.key]
    additionalParams[field.key] = typeof value === 'string' ? value.trim() : value
  }

  const request = {
    database_name: form.name.trim(),
    description: form.description?.trim() || '',
    kb_type: form.kb_type,
    additional_params: additionalParams,
    share_config: shareConfig
  }
  if (typeInfo?.requires_embedding_model) {
    request.embedding_model_spec = form.embedding_model_spec || defaultEmbeddingModel
    request.additional_params.chunk_preset_id = form.chunk_preset_id || DEFAULT_CHUNK_PRESET_ID
    request.additional_params.embedding_features = {
      bge_m3_sparse_enabled:
        isBgeM3EmbeddingModelSpec(request.embedding_model_spec) &&
        form.additional_params.embedding_features?.bge_m3_sparse_enabled === true
    }
    if (form.additional_params.parent_child?.enabled === true) {
      request.additional_params.parent_child = { ...form.additional_params.parent_child }
    } else {
      request.additional_params.chunk_parser_config = {
        ...form.additional_params.chunk_parser_config
      }
      request.additional_params.parent_child = { enabled: false }
    }
  }
  return request
}
import { DEFAULT_CHUNK_PRESET_ID } from './chunkUtils.js'
