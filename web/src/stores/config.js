import { ref } from 'vue'
import { defineStore } from 'pinia'
import { configApi } from '@/apis/system_api'

export const useConfigStore = defineStore('config', () => {
  const config = ref({})
  function setConfig(newConfig) {
    config.value = newConfig
  }

  function setConfigValue(key, value) {
    config.value[key] = value
    configApi.updateConfigBatch({ [key]: value }).then((data) => {
      console.debug('Success:', data)
      setConfig(data)
    })
  }

  async function refreshConfig() {
    const data = await configApi.getConfig()
    console.log('config', data)
    setConfig(data)
    return data
  }

  return { config, setConfigValue, refreshConfig }
})
