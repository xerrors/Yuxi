import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { brandApi } from '@/apis/system_api'

function readDebugMode() {
  try {
    return localStorage.getItem('yuxi_debug_mode') === 'true'
  } catch {
    return false
  }
}

export const useInfoStore = defineStore('info', () => {
  // 状态
  const infoConfig = ref({})
  const isLoading = ref(false)
  const isLoaded = ref(false)
  let pendingInfoRequest = null
  const debugMode = ref(readDebugMode())
  const showDebugModal = ref(false)

  // 计算属性 - 组织信息
  const organization = computed(
    () =>
      infoConfig.value.organization || {
        name: '',
        logo: '',
        avatar: ''
      }
  )

  // 计算属性 - 品牌信息
  const branding = computed(
    () =>
      infoConfig.value.branding || {
        name: '',
        title: '',
        subtitle: '',
        subtitles: []
      }
  )

  // 计算属性 - 页脚信息
  const footer = computed(() => ({
    copyright: '',
    user_agreement_url: '',
    privacy_policy_url: '',
    ...(infoConfig.value.footer || {})
  }))

  // 动作方法
  function setInfoConfig(newConfig) {
    infoConfig.value = newConfig
    isLoaded.value = true
  }

  function setDebugMode(enabled) {
    debugMode.value = Boolean(enabled)
    try {
      if (debugMode.value) {
        localStorage.setItem('yuxi_debug_mode', 'true')
      } else {
        localStorage.removeItem('yuxi_debug_mode')
      }
    } catch {
      // localStorage 不可用时仍保留当前页面内的响应式状态。
    }
  }

  function toggleDebugMode() {
    setDebugMode(!debugMode.value)
  }

  function openDebugModal() {
    showDebugModal.value = true
  }

  function closeDebugModal() {
    showDebugModal.value = false
  }

  async function loadInfoConfig(force = false) {
    if (pendingInfoRequest) return pendingInfoRequest
    // 如果已经加载过且不强制刷新，则不重新加载
    if (isLoaded.value && !force) {
      return infoConfig.value
    }

    try {
      pendingInfoRequest = fetchInfoConfig()
      return await pendingInfoRequest
    } finally {
      pendingInfoRequest = null
    }
  }

  /** 读取品牌配置，进行中的请求由 loadInfoConfig 统一合并。 */
  async function fetchInfoConfig() {
    try {
      isLoading.value = true
      const response = await brandApi.getInfoConfig()

      if (response.success && response.data) {
        setInfoConfig(response.data)
        console.debug('信息配置加载成功:', response.data)
        return response.data
      } else {
        console.warn('信息配置加载失败，使用默认配置')
        return null
      }
    } catch (error) {
      console.error('加载信息配置时发生错误:', error)
      return null
    } finally {
      isLoading.value = false
    }
  }

  return {
    // 状态
    infoConfig,
    isLoading,
    isLoaded,
    debugMode,
    showDebugModal,

    // 计算属性
    organization,
    branding,
    footer,

    // 方法
    setDebugMode,
    toggleDebugMode,
    openDebugModal,
    closeDebugModal,
    loadInfoConfig
  }
})
