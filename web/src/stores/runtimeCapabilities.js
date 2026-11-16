import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { discoveryApi } from '@/apis/system_api'

const DISABLED_FEATURES = Object.freeze({ knowledge: false })

function readFeatures(payload) {
  return {
    knowledge: payload?.capabilities?.features?.knowledge === true
  }
}

export const useRuntimeCapabilitiesStore = defineStore('runtime-capabilities', () => {
  const features = ref({ ...DISABLED_FEATURES })
  const status = ref('idle')
  const error = ref(null)
  let loadingPromise = null

  const knowledgeEnabled = computed(() => features.value.knowledge)

  async function ensureLoaded() {
    if (status.value === 'ready') return features.value
    if (loadingPromise) return loadingPromise

    status.value = 'loading'
    error.value = null
    loadingPromise = discoveryApi
      .getCapabilities()
      .then((payload) => {
        features.value = readFeatures(payload)
        status.value = 'ready'
        return features.value
      })
      .catch((cause) => {
        features.value = { ...DISABLED_FEATURES }
        error.value = cause
        status.value = 'error'
        console.warn('加载运行时能力失败，已关闭可选能力:', cause)
        return features.value
      })
      .finally(() => {
        loadingPromise = null
      })

    return loadingPromise
  }

  return { features, status, error, knowledgeEnabled, ensureLoaded }
})
