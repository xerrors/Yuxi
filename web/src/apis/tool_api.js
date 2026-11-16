import { apiGet } from './base'

/**
 * 工具管理 API 模块
 * 包含系统内置工具的查询功能
 */

const BASE_URL = '/api/system/tools'

/**
 * 获取工具列表
 * @param {string} category - 可选，按分类筛选
 * @returns {Promise} - 工具列表
 */
export const getTools = async (category = null) => {
  const query = category ? `?${new URLSearchParams({ category }).toString()}` : ''
  return apiGet(`${BASE_URL}${query}`)
}

/**
 * 获取工具选项列表（用于下拉选择）
 * @returns {Promise} - 工具选项
 */
export const getToolOptions = async () => {
  return apiGet(`${BASE_URL}/options`)
}

export const toolApi = {
  getTools,
  getToolOptions
}

export default toolApi
