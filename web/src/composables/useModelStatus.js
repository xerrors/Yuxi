import { reactive } from 'vue'
import { modelProviderApi } from '@/apis/system_api'

/**
 * 模型状态检查 composable，供 Chat/Embedding/Rerank 模型选择器共用。
 */
export function useModelStatus() {
  const statusMap = reactive({})

  const getStatusIcon = (key) => {
    const status = statusMap[key]
    if (!status) return '○'
    if (status.status === 'available') return '✓'
    if (status.status === 'unavailable') return '✗'
    if (status.status === 'error') return '⚠'
    return '○'
  }

  const getStatusClass = (key) => {
    return statusMap[key]?.status || ''
  }

  const getStatusTooltip = (key) => {
    const status = statusMap[key]
    if (!status) return '状态未知'
    const text =
      { available: '可用', unavailable: '不可用', error: '错误' }[status.status] || '未知'
    return `${text}: ${status.message || '无详细信息'}`
  }

  const checkV2Status = async (spec) => {
    try {
      const response = await modelProviderApi.getModelStatusBySpec(spec)
      if (response.data) {
        statusMap[spec] = response.data
      }
    } catch {
      statusMap[spec] = { spec, status: 'error', message: '检查失败' }
    }
  }

  const checkV2Statuses = async (models) => {
    for (const model of models || []) {
      await checkV2Status(model.spec)
    }
  }

  return {
    statusMap,
    getStatusIcon,
    getStatusClass,
    getStatusTooltip,
    checkV2Status,
    checkV2Statuses
  }
}
