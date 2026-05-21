import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useChatUIStore = defineStore(
  'chatUI',
  () => {
    // ==================== 聊天界面 UI 状态 ====================
    // 加载状态
    const isLoadingMessages = ref(false)

    // ==================== AgentView UI 状态 ====================
    // 智能体选择弹窗
    const agentModalOpen = ref(false)

    // 配置侧边栏
    const isConfigSidebarOpen = ref(false)

    // 应用侧边栏折叠态
    const sidebarCollapsed = ref(false)

    // 更多菜单
    const moreMenuOpen = ref(false)
    const moreMenuPosition = ref({ x: 0, y: 0 })

    // ==================== 文件系统预览状态 ====================
    const previewFilePath = ref(null)
    const previewFileTriggerTime = ref(0)

    // ==================== 方法 ====================
    /**
     * 打开更多菜单
     * @param {number} x - X 坐标
     * @param {number} y - Y 坐标
     */
    function openMoreMenu(x, y) {
      moreMenuPosition.value = { x, y }
      moreMenuOpen.value = true
    }

    /**
     * 关闭更多菜单
     */
    function closeMoreMenu() {
      moreMenuOpen.value = false
    }

    /**
     * 触发全局文件系统预览
     * @param {string} path 文件绝对路径
     */
    function triggerFilePreview(path) {
      previewFilePath.value = path
      previewFileTriggerTime.value = Date.now()
    }

    /**
     * 重置所有 UI 状态（不包括持久化状态）
     */
    function reset() {
      isLoadingMessages.value = false
      agentModalOpen.value = false
      isConfigSidebarOpen.value = false
      moreMenuOpen.value = false
      moreMenuPosition.value = { x: 0, y: 0 }
      previewFilePath.value = null
      previewFileTriggerTime.value = 0
    }

    return {
      // 状态
      isLoadingMessages,
      agentModalOpen,
      isConfigSidebarOpen,
      sidebarCollapsed,
      moreMenuOpen,
      moreMenuPosition,
      previewFilePath,
      previewFileTriggerTime,

      // 方法
      openMoreMenu,
      closeMoreMenu,
      triggerFilePreview,
      reset
    }
  },
  {
    persist: {
      key: 'chat-ui-store',
      storage: localStorage,
      pick: ['sidebarCollapsed']
    }
  }
)
