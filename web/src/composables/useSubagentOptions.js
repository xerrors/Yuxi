import { ref } from 'vue'
import { toolApi } from '@/apis/tool_api'
import { mcpApi } from '@/apis/mcp_api'
import { skillApi } from '@/apis/skill_api'

export function useSubagentOptions() {
  const availableTools = ref([])
  const availableMcps = ref([])
  const availableSkills = ref([])

  const fetchAvailableTools = async () => {
    if (availableTools.value.length > 0) return
    try {
      const result = await toolApi.getToolOptions()
      if (result.success && result.data) {
        availableTools.value = result.data
      }
    } catch (err) {
      console.error('获取工具选项失败:', err)
    }
  }

  const fetchAvailableMcps = async () => {
    if (availableMcps.value.length > 0) return
    try {
      const result = await mcpApi.getMcpServers()
      if (result.success && result.data) {
        availableMcps.value = result.data
          .filter((item) => item?.enabled !== false)
          .map((item) => ({
            label: item.name,
            value: item.name
          }))
      }
    } catch (err) {
      console.error('获取 MCP 选项失败:', err)
    }
  }

  const fetchAvailableSkills = async () => {
    if (availableSkills.value.length > 0) return
    try {
      const result = await skillApi.listSkills()
      if (result.success && result.data) {
        availableSkills.value = result.data.map((item) => ({
          label: item.slug,
          value: item.slug
        }))
      }
    } catch (err) {
      console.error('获取 Skills 选项失败:', err)
    }
  }

  return {
    availableTools,
    availableMcps,
    availableSkills,
    fetchAvailableTools,
    fetchAvailableMcps,
    fetchAvailableSkills
  }
}
