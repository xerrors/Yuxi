import { ref } from 'vue'
import { defineStore } from 'pinia'
import { configApi } from '@/apis/system_api'

export const useConfigStore = defineStore('config', () => {
  const config = ref({})
  function setConfig(newConfig) {
    config.value = newConfig
  }

  async function setConfigValue(key, value) {
    const data = await configApi.updateConfigBatch({ [key]: value })
    setConfig(data)
    return data
  }

  async function refreshConfig() {
    const data = await configApi.getConfig()
    setConfig(data)
    return data
  }

  return { config, setConfigValue, refreshConfig }
})
