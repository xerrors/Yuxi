import { apiGet, apiAdminGet, apiAdminPost, apiAdminPut, apiAdminDelete } from './base'

/**
 * 系统管理API模块
 * 包含系统配置、健康检查、信息管理等功能
 */

// =============================================================================
// === 健康检查分组 ===
// =============================================================================

export const healthApi = {
  /**
   * 系统健康检查（公开接口）
   * @returns {Promise} - 健康检查结果
   */
  checkHealth: () => apiGet('/api/system/health', {}, false)
}

// =============================================================================
// === 配置管理分组 ===
// =============================================================================

export const configApi = {
  /**
   * 获取系统配置
   * @returns {Promise} - 系统配置
   */
  getConfig: async () => apiGet('/api/system/config'),

  /**
   * 更新单个配置项
   * @param {string} key - 配置键
   * @param {any} value - 配置值
   * @returns {Promise} - 更新结果
   */
  updateConfig: async (key, value) => apiAdminPost('/api/system/config', { key, value }),

  /**
   * 批量更新配置项
   * @param {Object} items - 配置项对象
   * @returns {Promise} - 更新结果
   */
  updateConfigBatch: async (items) => apiAdminPost('/api/system/config/update', items),

  /**
   * 获取系统日志
   * @param {string} levels - 可选的日志级别过滤，多个级别用逗号分隔
   * @returns {Promise} - 系统日志
   */
  getLogs: async (levels) => {
    const url = levels
      ? `/api/system/logs?levels=${encodeURIComponent(levels)}`
      : '/api/system/logs'
    return apiAdminGet(url)
  }
}

export const configOptionsApi = {
  getOptions: async () => apiAdminGet('/api/system/config/options'),

  updateOption: async (key, value) =>
    apiAdminPut(`/api/system/config/options/${encodeURIComponent(key)}`, { value })
}

// =============================================================================
// === 信息管理分组 ===
// =============================================================================

export const brandApi = {
  /**
   * 获取系统信息配置（公开接口）
   * @returns {Promise} - 系统信息配置
   */
  getInfoConfig: () => apiGet('/api/system/info', {}, false)
}

// =============================================================================
// === OCR服务分组 ===
// =============================================================================

export const ocrApi = {
  getOptions: async () => apiGet('/api/system/ocr/options'),
  getHealth: async () => apiGet('/api/system/ocr/health')
}

// =============================================================================
// === 聊天模型状态检查分组 ===
// =============================================================================

export const chatModelApi = {}

// =============================================================================
// === 独立模型供应商配置分组 ===
// =============================================================================

export const modelProviderApi = {
  getProviders: async () => {
    return apiAdminGet('/api/system/model-providers')
  },

  getV2Models: async (modelType = 'chat') => {
    return apiGet(`/api/system/model-providers/models/v2?model_type=${modelType}`)
  },

  refreshModelCache: async () => {
    return apiAdminPost('/api/system/model-providers/models/cache/refresh')
  },

  getModelStatusBySpec: async (spec) => {
    return apiAdminGet(`/api/system/model-providers/models/status?spec=${encodeURIComponent(spec)}`)
  },

  createProvider: async (payload) => {
    return apiAdminPost('/api/system/model-providers', payload)
  },

  updateProvider: async (providerId, payload) => {
    return apiAdminPut(`/api/system/model-providers/${encodeURIComponent(providerId)}`, payload)
  },

  deleteProvider: async (providerId) => {
    return apiAdminDelete(`/api/system/model-providers/${encodeURIComponent(providerId)}`)
  },

  fetchRemoteModels: async (providerId) => {
    return apiAdminGet(
      `/api/system/model-providers/${encodeURIComponent(providerId)}/remote-models`
    )
  }
}
