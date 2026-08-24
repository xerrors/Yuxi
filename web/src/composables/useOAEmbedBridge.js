import { onActivated, onDeactivated, onMounted, onUnmounted, ref, unref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useChatThreadsStore } from '@/stores/chatThreads'
import { useUserStore } from '@/stores/user'
import { authApi } from '@/apis/auth_api'
import {
  confirmEmbedDisplayMode,
  resolveAppNavigationPath,
  resetEmbedDisplayMode
} from '@/composables/useEmbedMode'
import { canAccessRoute, getAuthenticatedHomePath } from '@/utils/authNavigation'
import {
  createOAEmbedBridge,
  getOAEmbedRenewalDelay,
  parseOAEmbedAllowedOrigins
} from '@/utils/oaEmbedBridge'
import { setOAEmbedAuthRequiredHandler } from '@/utils/oaEmbedSession'

/** 在嵌入路由中将父项目下发的 OA 账号交换为 Yuxi 登录态。 */
export function useOAEmbedBridge(enabled) {
  const userStore = useUserStore()
  const chatThreadsStore = useChatThreadsStore()
  const route = useRoute()
  const router = useRouter()
  const isAuthorized = ref(false)
  const statusMessage = ref('等待 OA 授权')
  let bridge = null
  let clearAuthRequiredHandler = null
  let renewalTimer = null

  const clearRenewalTimer = () => {
    if (renewalTimer !== null) window.clearTimeout(renewalTimer)
    renewalTimer = null
  }

  const scheduleRenewal = (accessToken) => {
    clearRenewalTimer()
    const delay = getOAEmbedRenewalDelay(accessToken)
    if (delay === null) return
    renewalTimer = window.setTimeout(requestAuthRequired, delay)
  }

  const clearAuthorization = (message) => {
    isAuthorized.value = false
    statusMessage.value = message
    userStore.logout()
    chatThreadsStore.reset()
  }

  const requestAuthRequired = () => {
    clearRenewalTimer()
    clearAuthorization('等待 OA 重新授权')
    bridge?.requestAuthRequired()
  }

  const startBridge = () => {
    if (bridge) return
    if (!unref(enabled)) {
      isAuthorized.value = true
      return
    }
    resetEmbedDisplayMode()

    const allowedOrigins = parseOAEmbedAllowedOrigins(
      import.meta.env.VITE_YUXI_EMBED_ALLOWED_ORIGINS
    )
    if (!allowedOrigins.length) {
      statusMessage.value = 'OA 嵌入未配置'
      return
    }

    bridge = createOAEmbedBridge({
      allowedOrigins,
      onAccount: async (account) => {
        clearAuthorization('正在验证 OA 账号')
        try {
          const loginData = await authApi.exchangeOAAccount(account)
          await userStore.acceptEmbedToken(loginData.access_token)
          scheduleRenewal(loginData.access_token)
          if (!canAccessRoute(route.matched, userStore.hasPermission)) {
            await router.replace(
              resolveAppNavigationPath(true, getAuthenticatedHomePath(userStore.hasPermission))
            )
          }
          isAuthorized.value = true
          statusMessage.value = ''
        } catch (error) {
          isAuthorized.value = false
          statusMessage.value = '等待 OA 重新授权'
          throw error
        }
      },
      onModeChanged: confirmEmbedDisplayMode
    })
    clearAuthRequiredHandler = setOAEmbedAuthRequiredHandler(requestAuthRequired)
    bridge.start()
  }

  const stopBridge = () => {
    clearRenewalTimer()
    bridge?.stop()
    bridge = null
    clearAuthRequiredHandler?.()
    clearAuthRequiredHandler = null
    if (unref(enabled)) {
      clearAuthorization('等待 OA 授权')
    }
  }

  onMounted(startBridge)
  onActivated(startBridge)
  onDeactivated(stopBridge)
  onUnmounted(stopBridge)

  return {
    isAuthorized,
    statusMessage,
    requestDisplayMode(mode, threadId) {
      // 正式父插件没有模式确认回执，消息发出后由当前嵌入页面立即完成状态同步。
      if (bridge?.requestMode(mode, threadId)) confirmEmbedDisplayMode(mode)
    },
    requestClose(threadId) {
      bridge?.requestClose(threadId)
    }
  }
}
