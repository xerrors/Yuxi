/** 仅按服务端明确标记分组，不从创建人或管理员可见性推断企业归属。 */
export const isEnterpriseDatabase = (database) => database?.is_enterprise_shared === true

/** 浏览器偏好按登录 uid 隔离，不提供部署级或跨设备配置。 */
export function readWorkspaceDefault(uid) {
  if (!uid) return 'personal'
  try {
    return localStorage.getItem(`workspace-default:${uid}`) === 'enterprise' ? 'enterprise' : 'personal'
  } catch {
    return 'personal'
  }
}

export function saveWorkspaceDefault(uid, source) {
  if (!uid || !['personal', 'enterprise'].includes(source)) return false
  try {
    localStorage.setItem(`workspace-default:${uid}`, source)
    return true
  } catch {
    return false
  }
}
