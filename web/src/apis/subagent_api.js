import { apiAdminGet, apiAdminPost, apiAdminPut, apiAdminDelete } from './base'

/**
 * SubAgent 管理 API 模块
 * 包含 SubAgent 的增删改查功能
 */

const BASE_URL = '/api/system/subagents'

// =============================================================================
// === SubAgent CRUD ===
// =============================================================================

/**
 * 获取所有 SubAgent 配置
 * @returns {Promise} - SubAgent 列表
 */
export const getSubAgents = async () => {
  return apiAdminGet(BASE_URL)
}

/**
 * 获取单个 SubAgent 配置
 * @param {string} name - SubAgent 名称
 * @returns {Promise} - SubAgent 配置
 */
export const getSubAgent = async (name) => {
  return apiAdminGet(`${BASE_URL}/${encodeURIComponent(name)}`)
}

/**
 * 创建新的 SubAgent
 * @param {Object} data - SubAgent 配置数据
 * @returns {Promise} - 创建结果
 */
export const createSubAgent = async (data) => {
  return apiAdminPost(BASE_URL, data)
}

/**
 * 更新 SubAgent 配置
 * @param {string} name - SubAgent 名称
 * @param {Object} data - 更新数据
 * @returns {Promise} - 更新结果
 */
export const updateSubAgent = async (name, data) => {
  return apiAdminPut(`${BASE_URL}/${encodeURIComponent(name)}`, data)
}

/**
 * 删除 SubAgent
 * @param {string} name - SubAgent 名称
 * @returns {Promise} - 删除结果
 */
export const deleteSubAgent = async (name) => {
  return apiAdminDelete(`${BASE_URL}/${encodeURIComponent(name)}`)
}

// =============================================================================
// === 导出为对象形式（兼容现有代码风格）===
// =============================================================================

export const subagentApi = {
  getSubAgents,
  getSubAgent,
  createSubAgent,
  updateSubAgent,
  deleteSubAgent
}

export default subagentApi
