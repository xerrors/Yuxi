import { apiGet, apiAdminGet, apiAdminPost, apiAdminPut, apiAdminDelete, apiRequest } from './base'

/**
 * 知识库管理API模块
 * 包含数据库管理、文档管理、查询接口等功能
 */

// =============================================================================
// === 数据库管理分组 ===
// =============================================================================

export const databaseApi = {
  /**
   * 获取所有知识库
   * @returns {Promise} - 知识库列表
   */
  getDatabases: async () => {
    return apiAdminGet('/api/knowledge/databases')
  },

  /**
   * 创建知识库
   * @param {Object} databaseData - 知识库数据
   * @returns {Promise} - 创建结果
   */
  createDatabase: async (databaseData) => {
    return apiAdminPost('/api/knowledge/databases', databaseData)
  },

  /**
   * 获取知识库详细信息
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 知识库信息
   */
  getDatabaseInfo: async (kbId) => {
    return apiAdminGet(`/api/knowledge/databases/${kbId}`)
  },

  /**
   * 修复知识库文件统计
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 修复结果
   */
  repairDatabaseStats: async (kbId) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/stats/repair`, {})
  },

  /**
   * 更新知识库信息
   * @param {string} kbId - 知识库ID
   * @param {Object} updateData - 更新数据
   * @returns {Promise} - 更新结果
   */
  updateDatabase: async (kbId, updateData) => {
    return apiAdminPut(`/api/knowledge/databases/${kbId}`, updateData)
  },

  /**
   * 删除知识库
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 删除结果
   */
  deleteDatabase: async (kbId) => {
    return apiAdminDelete(`/api/knowledge/databases/${kbId}`)
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
  getAccessibleDatabases: async () => {
    return apiGet('/api/knowledge/databases/accessible')
  }
}

// =============================================================================
// === 文档管理分组 ===
// =============================================================================

export const documentApi = {
  /**
   * 创建文件夹
   * @param {string} kbId - 知识库ID
   * @param {string} folderName - 文件夹名称
   * @param {string} parentId - 父文件夹ID
   * @returns {Promise} - 创建结果
   */
  createFolder: async (kbId, folderName, parentId = null) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/folders`, {
      folder_name: folderName,
      parent_id: parentId
    })
  },

  /**
   * 移动文档/文件夹
   * @param {string} kbId - 知识库ID
   * @param {string} docId - 文档/文件夹ID
   * @param {string} newParentId - 新的父文件夹ID
   * @returns {Promise} - 移动结果
   */
  moveDocument: async (kbId, docId, newParentId) => {
    return apiAdminPut(`/api/knowledge/databases/${kbId}/documents/${docId}/move`, {
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
    return apiAdminPost(`/api/knowledge/databases/${kbId}/documents`, {
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
    return apiAdminGet(`/api/knowledge/databases/${kbId}/documents/${docId}`)
  },

  /**
   * 删除文档
   * @param {string} kbId - 知识库ID
   * @param {string} docId - 文档ID
   * @returns {Promise} - 删除结果
   */
  deleteDocument: async (kbId, docId) => {
    return apiAdminDelete(`/api/knowledge/databases/${kbId}/documents/${docId}`)
  },

  /**
   * 批量删除文档
   * @param {string} kbId - 知识库ID
   * @param {Array} fileIds - 文件ID列表
   * @returns {Promise} - 批量删除结果
   */
  batchDeleteDocuments: async (kbId, fileIds) => {
    return apiRequest(
      `/api/knowledge/databases/${kbId}/documents/batch`,
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
    return apiAdminGet(`/api/knowledge/databases/${kbId}/documents/${docId}/download`, {}, 'blob')
  },

  /**
   * 手动触发文档解析
   * @param {string} kbId - 知识库ID
   * @param {Array} fileIds - 文件ID列表
   * @returns {Promise} - 解析任务结果
   */
  parseDocuments: async (kbId, fileIds) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/documents/parse`, fileIds)
  },

  /**
   * 手动触发文档入库
   * @param {string} kbId - 知识库ID
   * @param {Array} fileIds - 文件ID列表
   * @param {Object} params - 处理参数
   * @returns {Promise} - 入库任务结果
   */
  indexDocuments: async (kbId, fileIds, params = {}) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/documents/index`, {
      file_ids: fileIds,
      params
    })
  }
}

// =============================================================================
// === 图谱构建分组 ===
// =============================================================================

function graphBuildUrl(kbId, action) {
  return `/api/knowledge/databases/${kbId}/graph-build/${action}`
}

export const graphBuildApi = {
  getStatus: async (kbId) => {
    return apiAdminGet(graphBuildUrl(kbId, 'status'))
  },

  configure: async (kbId, data) => {
    return apiAdminPost(graphBuildUrl(kbId, 'config'), data)
  },

  startIndex: async (kbId, batchSize = 20) => {
    return apiAdminPost(graphBuildUrl(kbId, 'index'), {
      batch_size: batchSize
    })
  },

  reset: async (kbId, data) => {
    return apiAdminPost(graphBuildUrl(kbId, 'reset'), data)
  }
}

// =============================================================================
// === 思维导图分组 ===
// =============================================================================

export const mindmapApi = {
  getDatabases: async () => {
    return apiAdminGet('/api/knowledge/mindmap/databases')
  },

  getDatabaseFiles: async (kbId) => {
    return apiAdminGet(`/api/knowledge/databases/${kbId}/mindmap/files`)
  },

  generateMindmap: async (kbId, fileIds = [], userPrompt = '', incremental = false) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/mindmap/generate`, {
      file_ids: fileIds,
      user_prompt: userPrompt,
      incremental
    })
  },

  getByDatabase: async (kbId) => {
    return apiAdminGet(`/api/knowledge/databases/${kbId}/mindmap`)
  },

  getDiff: async (kbId) => {
    return apiAdminGet(`/api/knowledge/databases/${kbId}/mindmap/diff`)
  }
}

// =============================================================================
// === 查询分组 ===
// =============================================================================

export const queryApi = {
  /**
   * 查询知识库
   * @param {string} kbId - 知识库ID
   * @param {string} query - 查询文本
   * @param {Object} meta - 查询参数
   * @returns {Promise} - 查询结果
   */
  queryKnowledgeBase: async (kbId, query, meta = {}) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/query`, {
      query,
      meta
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
    return apiAdminPost(`/api/knowledge/databases/${kbId}/query-test`, {
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
    return apiAdminGet(`/api/knowledge/databases/${kbId}/query-params`)
  },

  /**
   * 更新知识库查询参数
   * @param {string} kbId - 知识库ID
   * @param {Object} params - 查询参数
   * @returns {Promise} - 更新结果
   */
  updateKnowledgeBaseQueryParams: async (kbId, params) => {
    return apiAdminPut(`/api/knowledge/databases/${kbId}/query-params`, params)
  },

  /**
   * 生成知识库的测试问题
   * @param {string} kbId - 知识库ID
   * @param {number} count - 生成问题数量，默认10
   * @returns {Promise} - 生成的问题列表
   */
  generateSampleQuestions: async (kbId, count = 10) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/sample-questions`, {
      count
    })
  },

  /**
   * 获取知识库的测试问题
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 问题列表
   */
  getSampleQuestions: async (kbId) => {
    return apiAdminGet(`/api/knowledge/databases/${kbId}/sample-questions`)
  }
}

// =============================================================================
// === 文件管理分组 ===
// =============================================================================

export const fileApi = {
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
   * 上传文件
   * @param {File} file - 文件对象
   * @param {string} kbId - 知识库ID（可选）
   * @returns {Promise} - 上传结果
   */
  uploadFile: async (file, kbId = null) => {
    const formData = new FormData()
    formData.append('file', file)

    const url = kbId ? `/api/knowledge/files/upload?kb_id=${kbId}` : '/api/knowledge/files/upload'

    return apiAdminPost(url, formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
  },

  /**
   * 获取支持的文件类型
   * @returns {Promise} - 文件类型列表
   */
  getSupportedFileTypes: async () => {
    return apiAdminGet('/api/knowledge/files/supported-types')
  },

  /**
   * 上传文件夹（zip格式）
   * @param {File} file - zip文件
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 上传结果
   */
  uploadFolder: async (file, kbId) => {
    const formData = new FormData()
    formData.append('file', file)

    // 使用 apiRequest 直接发送 FormData，但使用统一的错误处理
    return apiRequest(
      `/api/knowledge/files/upload-folder?kb_id=${kbId}`,
      {
        method: 'POST',
        body: formData
        // 不设置 Content-Type，让浏览器自动设置 boundary
      },
      true,
      'json'
    ) // 需要认证，期望JSON响应
  },

  /**
   * 处理文件夹（异步处理zip文件）
   * @param {Object} data - 处理参数
   * @param {string} data.file_path - 已上传的zip文件路径
   * @param {string} data.kb_id - 知识库ID
   * @param {string} data.content_hash - 文件内容哈希
   * @returns {Promise} - 处理任务结果
   */
  processFolder: async ({ file_path, kb_id, content_hash }) => {
    return apiAdminPost('/api/knowledge/files/process-folder', {
      file_path,
      kb_id,
      content_hash
    })
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
   * 获取知识库统计信息
   * @returns {Promise} - 统计信息
   */
  getStatistics: async () => {
    return apiAdminGet('/api/knowledge/stats')
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

    return apiAdminPost(`/api/evaluation/databases/${kbId}/datasets/upload`, formData)
  },

  listDatasets: async (kbId) => {
    return apiAdminGet(`/api/evaluation/databases/${kbId}/datasets`)
  },

  getDataset: async (kbId, datasetId, page = 1, pageSize = 50) => {
    const params = new URLSearchParams({
      page: page.toString(),
      page_size: pageSize.toString()
    })
    return apiAdminGet(`/api/evaluation/databases/${kbId}/datasets/${datasetId}?${params}`)
  },

  deleteDataset: async (datasetId) => {
    return apiAdminDelete(`/api/evaluation/datasets/${datasetId}`)
  },

  downloadDataset: async (datasetId) => {
    return apiAdminGet(`/api/evaluation/datasets/${datasetId}/download`, {}, 'blob')
  },

  generateDataset: async (kbId, params) => {
    return apiAdminPost(`/api/evaluation/databases/${kbId}/datasets/generate`, params)
  },

  runEvaluation: async (kbId, params) => {
    return apiAdminPost(`/api/evaluation/databases/${kbId}/runs`, params)
  },

  listRuns: async (kbId) => {
    return apiAdminGet(`/api/evaluation/databases/${kbId}/runs`)
  },

  getRunResults: async (kbId, runId, params = {}) => {
    const queryParams = new URLSearchParams()

    if (params.page) queryParams.append('page', params.page)
    if (params.pageSize) queryParams.append('page_size', params.pageSize)
    if (params.errorOnly !== undefined) queryParams.append('error_only', params.errorOnly)

    const url = `/api/evaluation/databases/${kbId}/runs/${runId}${queryParams.toString() ? '?' + queryParams.toString() : ''}`
    return apiAdminGet(url)
  },

  deleteRun: async (kbId, runId) => {
    return apiAdminDelete(`/api/evaluation/databases/${kbId}/runs/${runId}`)
  }
}
