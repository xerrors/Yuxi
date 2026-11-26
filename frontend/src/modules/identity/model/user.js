import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/apis/auth_api'
import { configureApiAuth } from '@/apis/auth_context'
import { useAgentStore } from '@/modules/agents'
import { useProjectsStore } from '@/modules/projects'
import { useSessionFeedbackStore } from '@/modules/session'

export const useUserStore = defineStore('user', () => {
  // 状态
  const token = ref(localStorage.getItem('user_token') || '')
  const userId = ref(null)
  const username = ref('')
  const uid = ref('')
  const phoneNumber = ref('')
  const avatar = ref('')
  const userRole = ref('')
  const departmentId = ref(null)
  const departmentName = ref('')

  // 计算属性
  const isLoggedIn = computed(() => !!token.value)
  const isEnterpriseAccount = computed(() => uid.value.startsWith('oidc:'))
  // 开户随机后缀用于唯一性；资料与菜单共享同一可读名称。
  const displayName = computed(() =>
    isEnterpriseAccount.value ? username.value.replace(/-[0-9a-f]{12}$/, '') : username.value
  )
  const isAdmin = computed(() => userRole.value === 'admin' || userRole.value === 'superadmin')
  const isSuperAdmin = computed(() => userRole.value === 'superadmin')

  // 动作
  /** 统一应用认证响应，保持响应式会话与持久 token 同步。 */
  function applySession(data) {
    useProjectsStore().reset()
    useSessionFeedbackStore().reset()
    token.value = data.access_token
    userId.value = data.user_id ?? data.uid ?? null
    username.value = data.username
    uid.value = data.uid ?? ''
    phoneNumber.value = data.phone_number || ''
    avatar.value = data.avatar || ''
    userRole.value = data.role
    departmentId.value = data.department_id || null
    departmentName.value = data.department_name || ''
    localStorage.setItem('user_token', data.access_token)
  }

  async function login(credentials) {
    try {
      const data = await authApi.login(credentials)
      applySession(data)
      return true
    } catch (error) {
      console.error('登录错误:', error)
      throw error
    }
  }

  function logout() {
    const previousToken = token.value
    // 清除状态
    token.value = ''
    userId.value = null
    username.value = ''
    uid.value = ''
    phoneNumber.value = ''
    avatar.value = ''
    userRole.value = ''
    departmentId.value = null
    departmentName.value = ''

    // 清除 agentStore 状态，确保重新登录时能正确加载数据
    const agentStore = useAgentStore()
    agentStore.reset()
    useProjectsStore().reset()
    useSessionFeedbackStore().reset()

    // 另一个标签页可能已经写入替代会话。
    if (localStorage.getItem('user_token') === previousToken) localStorage.removeItem('user_token')
  }

  async function initialize(admin) {
    try {
      const data = await authApi.initialize(admin)
      applySession(data)
      return true
    } catch (error) {
      console.error('初始化管理员错误:', error)
      throw error
    }
  }

  async function checkFirstRun() {
    try {
      const data = await authApi.checkFirstRun()
      return data.first_run
    } catch (error) {
      console.error('检查首次运行状态错误:', error)
      return false
    }
  }

  // 用于API请求的授权头
  function getAuthHeaders() {
    return {
      Authorization: `Bearer ${token.value}`
    }
  }

  async function createUser(userData) {
    try {
      return await authApi.createUser(userData)
    } catch (error) {
      console.error('创建用户错误:', error)
      throw error
    }
  }

  async function updateUser(userId, userData) {
    try {
      return await authApi.updateUser(userId, userData)
    } catch (error) {
      console.error('更新用户错误:', error)
      throw error
    }
  }

  async function deleteUser(userId) {
    try {
      return await authApi.deleteUser(userId)
    } catch (error) {
      console.error('删除用户错误:', error)
      throw error
    }
  }

  // 验证用户名并生成uid
  async function validateUsernameAndGenerateUid(username) {
    try {
      return await authApi.validateUsername(username)
    } catch (error) {
      console.error('用户名验证错误:', error)
      throw error
    }
  }

  // 上传头像
  async function uploadAvatar(file) {
    const requestToken = token.value
    try {
      const data = await authApi.uploadAvatar(file)
      if (token.value === requestToken) avatar.value = data.avatar_url
      return data
    } catch (error) {
      console.error('头像上传错误:', error)
      throw error
    }
  }

  // 获取当前用户信息
  async function getCurrentUser() {
    const requestToken = token.value
    try {
      const userData = await authApi.getCurrentUser()
      if (token.value !== requestToken) return

      // 更新本地状态
      userId.value = userData.id
      username.value = userData.username
      uid.value = userData.uid
      phoneNumber.value = userData.phone_number || ''
      avatar.value = userData.avatar || ''
      userRole.value = userData.role
      departmentId.value = userData.department_id || null
      departmentName.value = userData.department_name || ''

      return userData
    } catch (error) {
      console.error('获取用户信息错误:', error)
      throw error
    }
  }

  // 更新个人资料
  async function updateProfile(profileData) {
    const requestToken = token.value
    try {
      const userData = await authApi.updateProfile(profileData)
      if (token.value !== requestToken) return userData
      if (typeof userData.username === 'string') {
        username.value = userData.username
      }
      if (typeof userData.phone_number !== 'undefined') {
        phoneNumber.value = userData.phone_number || ''
      }

      return userData
    } catch (error) {
      console.error('更新个人资料错误:', error)
      throw error
    }
  }

  return {
    // 状态
    token,
    userId,
    username,
    uid,
    phoneNumber,
    avatar,
    userRole,
    departmentId,
    departmentName,

    // 计算属性
    isLoggedIn,
    isEnterpriseAccount,
    displayName,
    isAdmin,
    isSuperAdmin,

    // 方法
    applySession,
    login,
    logout,
    initialize,
    checkFirstRun,
    getAuthHeaders,
    createUser,
    updateUser,
    deleteUser,
    validateUsernameAndGenerateUid,
    uploadAvatar,
    getCurrentUser,
    updateProfile
  }
})

configureApiAuth({
  getToken: () => useUserStore().token,
  isAdmin: () => useUserStore().isAdmin,
  isSuperAdmin: () => useUserStore().isSuperAdmin,
  onUnauthorized: () => {
    const userStore = useUserStore()
    if (userStore.isLoggedIn) userStore.logout()
  }
})

// 检查当前用户是否有超级管理员权限
export const checkSuperAdminPermission = () => {
  const userStore = useUserStore()
  return userStore.isSuperAdmin
}
