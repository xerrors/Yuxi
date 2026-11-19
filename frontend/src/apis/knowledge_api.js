import {
  apiGet,
  apiPost,
  apiAdminGet,
  apiAdminPost,
  apiAdminPut,
  apiAdminDelete,
  apiRequest,
  buildQuery
} from './base'

const patchMetadata = (url, data) => apiRequest(url, {
  method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data)
}, true, 'json')

/** 知识库字段定义和文件属性管理。 */
export const metadataApi = {
  getFields: (kbId) => apiAdminGet(`/api/knowledge/knowledge-bases/${kbId}/metadata-fields`),
  createField: (kbId, data) => apiAdminPost(`/api/knowledge/knowledge-bases/${kbId}/metadata-fields`, data),
  updateField: (kbId, key, data) => patchMetadata(`/api/knowledge/knowledge-bases/${kbId}/metadata-fields/${encodeURIComponent(key)}`, data),
  deleteField: (kbId, key) => apiAdminDelete(`/api/knowledge/knowledge-bases/${kbId}/metadata-fields/${encodeURIComponent(key)}`),
  patchFiles: (kbId, data) => patchMetadata(`/api/knowledge/knowledge-bases/${kbId}/documents/attributes`, data)
}

/**
 * 知识库管理API模块
 * 包含知识库管理、文档管理、查询接口等功能
 */

// =============================================================================
// === 知识库管理分组 ===
// =============================================================================

export const knowledgeBaseApi = {
  /**
   * 获取所有知识库
   * @returns {Promise} - 知识库列表
   */
  getKnowledgeBases: async () => {
    return apiAdminGet('/api/knowledge/knowledge-bases')
  },

  /**
   * 创建知识库
   * @param {Object} knowledgeBaseData - 知识库数据
   * @returns {Promise} - 创建结果
   */
  createKnowledgeBase: async (knowledgeBaseData) => {
    return apiAdminPost('/api/knowledge/knowledge-bases', knowledgeBaseData)
  },

  /**
   * 获取知识库详细信息
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 知识库信息
   */
  getKnowledgeBaseInfo: async (kbId) => {
    return apiAdminGet(`/api/knowledge/knowledge-bases/${kbId}`)
  },

  /**
   * 更新知识库信息
   * @param {string} kbId - 知识库ID
   * @param {Object} updateData - 更新数据
   * @returns {Promise} - 更新结果
   */
  updateKnowledgeBase: async (kbId, updateData) => {
    return apiAdminPut(`/api/knowledge/knowledge-bases/${kbId}`, updateData)
  },

  /**
   * 删除知识库
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 删除结果
   */
  deleteKnowledgeBase: async (kbId) => {
    return apiAdminDelete(`/api/knowledge/knowledge-bases/${kbId}`)
  },

  /**
   * 使用 AI 生成或优化知识库描述
   * @param {string} name - 知识库名称
   * @param {string} currentDescription - 当前描述（可选）
   * @param {Array} fileList - 文件列表（可选）
   * @returns {Promise} - 生成结果
   */
  generateDescription: async (name, currentDescription = '', fileList = []) => {
    return apiAdminPost('/api/knowledge/generate-description', {
      name,
      current_description: currentDescription,
      file_list: fileList
    })
  },

  /**
   * 获取当前用户有权访问的知识库列表（用于智能体配置）
   * @returns {Promise} - 可访问的知识库列表
   */
  getAccessibleKnowledgeBases: async () => {
    return apiGet('/api/knowledge/knowledge-bases/accessible')
  }
}

// =============================================================================
// === 文档管理分组 ===
// =============================================================================

export const documentApi = {
  /**
   * 分页获取知识库文档列表
   * @param {string} kbId - 知识库ID
   * @param {Object} params - 查询参数
   * @returns {Promise} - 文档列表
   */
  listDocuments: async (kbId, params = {}) => {
    const query = buildQuery(params)
    return apiAdminGet(`/api/knowledge/knowledge-bases/${kbId}/documents${query ? `?${query}` : ''}`)
  },

  searchDocuments: async (kbId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(
      `/api/knowledge/knowledge-bases/${kbId}/documents/search${query ? `?${query}` : ''}`
    )
  },

  /**
   * 创建文件夹
   * @param {string} kbId - 知识库ID
   * @param {string} folderName - 文件夹名称
   * @param {string} parentId - 父文件夹ID
   * @returns {Promise} - 创建结果
   */
  createFolder: async (kbId, folderName, parentId = null) => {
    return apiAdminPost(`/api/knowledge/knowledge-bases/${kbId}/folders`, {
      folder_name: folderName,
      parent_id: parentId
    })
  },

  renameFolder: async (kbId, folderId, folderName) => {
    return apiAdminPut(`/api/knowledge/knowledge-bases/${kbId}/folders/${folderId}/rename`, {
      folder_name: folderName
    })
  },

  moveDocument: async (kbId, documentId, newParentId) => {
    return apiAdminPut(`/api/knowledge/knowledge-bases/${kbId}/documents/${documentId}/move`, {
      new_parent_id: newParentId
    })
  },

  /**
   * 添加文档到知识库
   * @param {string} kbId - 知识库ID
   * @param {Array} items - 文档列表
   * @param {Object} params - 处理参数
   * @returns {Promise} - 添加结果
   */
  addDocuments: async (kbId, items, params = {}) => {
    return apiAdminPost(`/api/knowledge/knowledge-bases/${kbId}/documents`, {
      items,
      params
    })
  },

  /**
   * 获取文档信息
   * @param {string} kbId - 知识库ID
   * @param {string} docId - 文档ID
   * @returns {Promise} - 文档信息
   */
  getDocumentInfo: async (kbId, docId) => {
    return apiAdminGet(`/api/knowledge/knowledge-bases/${kbId}/documents/${docId}`)
  },

  /**
   * 获取文档基本信息
   * @param {string} kbId - 知识库ID
   * @param {string} docId - 文档ID
   * @returns {Promise} - 文档基本信息
   */
  getDocumentBasicInfo: async (kbId, docId) => {
    return apiGet(`/api/knowledge/knowledge-bases/${kbId}/documents/${docId}/basic`)
  },

  /**
   * 获取文档解析内容和分块
   * @param {string} kbId - 知识库ID
   * @param {string} docId - 文档ID
   * @returns {Promise} - 文档内容信息
   */
  getDocumentContent: async (kbId, docId) => {
    return apiAdminGet(`/api/knowledge/knowledge-bases/${kbId}/documents/${docId}/content`)
  },

  /**
   * 删除文档
   * @param {string} kbId - 知识库ID
   * @param {string} docId - 文档ID
   * @returns {Promise} - 删除结果
   */
  deleteDocument: async (kbId, docId) => {
    return apiAdminDelete(`/api/knowledge/knowledge-bases/${kbId}/documents/${docId}`)
  },

  /**
   * 批量删除文档
   * @param {string} kbId - 知识库ID
   * @param {Array} fileIds - 文件ID列表
   * @returns {Promise} - 批量删除结果
   */
  batchDeleteDocuments: async (kbId, fileIds) => {
    return apiRequest(
      `/api/knowledge/knowledge-bases/${kbId}/documents/batch`,
      {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(fileIds)
      },
      true,
      'json'
    )
  },

  /**
   * 下载文档
   * @param {string} kbId - 知识库ID
   * @param {string} docId - 文档ID
   * @returns {Promise} - Response对象
   */
  downloadDocument: async (kbId, docId) => {
    return apiAdminGet(`/api/knowledge/knowledge-bases/${kbId}/documents/${docId}/download`, {}, 'blob')
  },

  /**
   * 手动触发文档解析
   * @param {string} kbId - 知识库ID
   * @param {Array} fileIds - 文件ID列表
   * @param {Object} params - 处理参数（如 ocr_engine）
   * @returns {Promise} - 解析任务结果
   */
  parseDocuments: async (kbId, fileIds, params = {}) => {
    return apiAdminPost(`/api/knowledge/knowledge-bases/${kbId}/documents/parse`, {
      file_ids: fileIds,
      params
    })
  },

  /** 严格读取可编辑的完整 Markdown 和修订凭据。 */
  getDocumentMarkdown: async (kbId, fileId) => {
    return apiAdminGet(`/api/knowledge/knowledge-bases/${kbId}/documents/${encodeURIComponent(fileId)}/markdown`)
  },

  /** 以修订凭据提交 Markdown 保存任务。 */
  saveDocumentMarkdown: async (kbId, fileId, expectedRevision, content) => {
    return apiAdminPost(`/api/knowledge/knowledge-bases/${kbId}/documents/${encodeURIComponent(fileId)}/markdown`, {
      expected_revision: expectedRevision,
      content
    })
  },

  /** 从原件重新解析文件，并清除已有下游数据。 */
  reparseDocument: async (kbId, fileId, params = {}) => {
    return apiAdminPost(`/api/knowledge/knowledge-bases/${kbId}/documents/${encodeURIComponent(fileId)}/reparse`, { params })
  },

  /**
   * 手动触发全部待解析文档解析
   * @param {string} kbId - 知识库ID
   * @param {Object} params - 处理参数（如 ocr_engine）
   * @returns {Promise} - 解析任务结果
   */
  parsePendingDocuments: async (kbId, params = {}) => {
    return apiAdminPost(`/api/knowledge/knowledge-bases/${kbId}/documents/parse-pending`, {
      params
    })
  },

  /**
   * 手动触发文档入库
   * @param {string} kbId - 知识库ID
   * @param {Array} fileIds - 文件ID列表
   * @param {Object} params - 处理参数
   * @returns {Promise} - 入库任务结果
   */
  indexDocuments: async (kbId, fileIds, params = {}) => {
    return apiAdminPost(`/api/knowledge/knowledge-bases/${kbId}/documents/index`, {
      file_ids: fileIds,
      params
    })
  },

  /**
   * 手动触发全部待入库文档入库
   * @param {string} kbId - 知识库ID
   * @param {Object} params - 处理参数
   * @returns {Promise} - 入库任务结果
   */
  indexPendingDocuments: async (kbId, params = {}) => {
    return apiAdminPost(`/api/knowledge/knowledge-bases/${kbId}/documents/index-pending`, {
      params
    })
  }
}

// =============================================================================
// === 图谱构建分组 ===
// =============================================================================

function graphBuildUrl(kbId, action) {
  return `/api/knowledge/knowledge-bases/${kbId}/graph-build/${action}`
}

export const graphBuildApi = {
  getStatus: async (kbId) => {
    return apiAdminGet(graphBuildUrl(kbId, 'status'))
  },

  getFileStatus: async (kbId, fileId) => {
    return apiGet(`${graphBuildUrl(kbId, 'files')}/${encodeURIComponent(fileId)}/status`)
  },

  getFailedChunks: async (kbId, limit = 10) => {
    return apiAdminGet(`${graphBuildUrl(kbId, 'failed-chunks')}?limit=${limit}`)
  },

  configure: async (kbId, data) => {
    return apiAdminPost(graphBuildUrl(kbId, 'config'), data)
  },

  startIndex: async (kbId, fileId = null) => {
    return apiAdminPost(graphBuildUrl(kbId, 'index'), fileId ? { file_id: fileId } : {})
  },

  /** 提交单个文件的图谱清除任务。 */
  clearFile: async (kbId, fileId) => {
    return apiAdminPost(`${graphBuildUrl(kbId, 'files')}/${encodeURIComponent(fileId)}/clear`)
  },

  reset: async (kbId, data) => {
    return apiAdminPost(graphBuildUrl(kbId, 'reset'), data)
  },

  reconcile: async (kbId, mode = 'failed') => {
    return apiAdminPost(graphBuildUrl(kbId, 'reconcile'), { mode })
  }
}

// =============================================================================
// =============================================================================
// === 查询分组 ===
// =============================================================================

export const queryApi = {
  openDocument: async (kbId, fileId, offset = 0, windowSize = 200) => {
    return apiPost('/api/v1/knowledge/tools/open_kb_document', {
      kb_id: kbId,
      file_id: fileId,
      offset,
      window_size: windowSize
    })
  },

  /**
   * 测试查询知识库
   * @param {string} kbId - 知识库ID
   * @param {string} query - 查询文本
   * @param {Object} meta - 查询参数
   * @returns {Promise} - 测试结果
   */
  queryTest: async (kbId, query, meta = {}) => {
    return apiAdminPost(`/api/knowledge/knowledge-bases/${kbId}/query-test`, {
      query,
      meta
    })
  },

  /**
   * 获取知识库查询参数
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 查询参数
   */
  getKnowledgeBaseQueryParams: async (kbId) => {
    return apiAdminGet(`/api/knowledge/knowledge-bases/${kbId}/query-params`)
  },

  /**
   * 更新知识库查询参数
   * @param {string} kbId - 知识库ID
   * @param {Object} params - 查询参数
   * @returns {Promise} - 更新结果
   */
  updateKnowledgeBaseQueryParams: async (kbId, params) => {
    return apiAdminPut(`/api/knowledge/knowledge-bases/${kbId}/query-params`, params)
  },

  /**
   * 生成知识库的测试问题
   * @param {string} kbId - 知识库ID
   * @param {number} count - 生成问题数量，默认10
   * @returns {Promise} - 生成的问题列表
   */
  generateSampleQuestions: async (kbId, count = 10) => {
    return apiAdminPost(`/api/knowledge/knowledge-bases/${kbId}/sample-questions`, {
      count
    })
  },

  /**
   * 获取知识库的测试问题
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 问题列表
   */
  getSampleQuestions: async (kbId) => {
    return apiAdminGet(`/api/knowledge/knowledge-bases/${kbId}/sample-questions`)
  }
}

// =============================================================================
// === 文件管理分组 ===
// =============================================================================

export const fileApi = {
  /**
   * 构造知识库文件上传端点，供需要原生上传进度的调用方复用。
   * @param {string|null} kbId - 知识库 ID
   * @returns {string} - 上传端点
   */
  getUploadUrl: (kbId = null) => {
    if (kbId === null || kbId === undefined || kbId === '') {
      return '/api/knowledge/files/upload'
    }
    return `/api/knowledge/files/upload?kb_id=${encodeURIComponent(kbId)}`
  },

  /**
   * 抓取 URL 内容
   * @param {string} url - 目标 URL
   * @param {string} kbId - 知识库 ID
   * @returns {Promise} - 抓取结果
   */
  fetchUrl: async (url, kbId = null) => {
    return apiAdminPost('/api/knowledge/files/fetch-url', {
      url,
      kb_id: kbId
    })
  },

  /**
   * 从工作区导入文件到知识库 MinIO 暂存区
   * @param {string} kbId - 知识库 ID
   * @param {Array<string>} paths - 工作区文件路径
   * @returns {Promise} - 导入结果
   */
  importWorkspaceFiles: async (kbId, paths) => {
    return apiAdminPost('/api/knowledge/files/import-workspace', {
      kb_id: kbId,
      paths
    })
  },

  /**
   * 获取支持的文件类型
   * @returns {Promise} - 文件类型列表
   */
  getSupportedFileTypes: async () => {
    return apiAdminGet('/api/knowledge/files/supported-types')
  }
}

// =============================================================================
// === 知识库类型分组 ===
// =============================================================================

export const typeApi = {
  /**
   * 获取支持的知识库类型
   * @returns {Promise} - 知识库类型列表
   */
  getKnowledgeBaseTypes: async () => {
    return apiAdminGet('/api/knowledge/types')
  },

  /**
   * 获取支持的知识库分块策略
   * @returns {Promise} - 分块策略列表
   */
  getChunkPresets: async () => {
    return apiAdminGet('/api/knowledge/chunk-presets')
  }
}

// =============================================================================
// === RAG评估分组 ===
// =============================================================================

export const evaluationApi = {
  uploadDataset: async (kbId, file, metadata = {}) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('name', metadata.name || '')
    formData.append('description', metadata.description || '')

    return apiAdminPost(`/api/evaluation/knowledge-bases/${kbId}/datasets/upload`, formData)
  },

  listDatasets: async (kbId) => {
    return apiAdminGet(`/api/evaluation/knowledge-bases/${kbId}/datasets`)
  },

  getDataset: async (kbId, datasetId, page = 1, pageSize = 50) => {
    const params = new URLSearchParams({
      page: page.toString(),
      page_size: pageSize.toString()
    })
    return apiAdminGet(`/api/evaluation/knowledge-bases/${kbId}/datasets/${datasetId}?${params}`)
  },

  deleteDataset: async (datasetId) => {
    return apiAdminDelete(`/api/evaluation/datasets/${datasetId}`)
  },

  downloadDataset: async (datasetId) => {
    return apiAdminGet(`/api/evaluation/datasets/${datasetId}/download`, {}, 'blob')
  },

  generateDataset: async (kbId, params) => {
    return apiAdminPost(`/api/evaluation/knowledge-bases/${kbId}/datasets/generate`, params)
  },

  resumeDatasetGeneration: async (kbId, datasetId) => {
    return apiAdminPost(`/api/evaluation/knowledge-bases/${kbId}/datasets/${datasetId}/resume`, {})
  },

  runEvaluation: async (kbId, params) => {
    return apiAdminPost(`/api/evaluation/knowledge-bases/${kbId}/runs`, params)
  },

  listRuns: async (kbId) => {
    return apiAdminGet(`/api/evaluation/knowledge-bases/${kbId}/runs`)
  },

  getRunResults: async (kbId, runId, params = {}) => {
    const queryParams = new URLSearchParams()

    if (params.page) queryParams.append('page', params.page)
    if (params.pageSize) queryParams.append('page_size', params.pageSize)
    if (params.resultFilter !== undefined) queryParams.append('result_filter', params.resultFilter)

    const url = `/api/evaluation/knowledge-bases/${kbId}/runs/${runId}${queryParams.toString() ? '?' + queryParams.toString() : ''}`
    return apiAdminGet(url)
  },

  deleteRun: async (kbId, runId) => {
    return apiAdminDelete(`/api/evaluation/knowledge-bases/${kbId}/runs/${runId}`)
  }
}
