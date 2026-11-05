/** 根会话打开主页面，成员通过可刷新的查询参数打开所属根页面侧栏。 */
export function sessionLinkLocation(target) {
  return {
    name: 'AgentCompWithThreadId',
    params: { thread_id: target.tree_root_session_id },
    query: target.parent_session_id ? { member_session_id: target.session_id } : {}
  }
}

/** 仅让当前页面最新的来源点击完成导航，失效请求不再改变页面或显示错误。 */
export function createSessionLinkNavigator({ router, resolveTarget, isActive, openTarget, onError }) {
  let revision = 0
  let pendingClick = null
  return {
    invalidate() { revision++; pendingClick = null },
    async navigate(target, { restore = false } = {}) {
      const route = router.currentRoute.value
      // 同一路由的恢复可能晚于用户点击启动，不能抢占已在读取的新目标。
      if (restore && pendingClick?.route === route) return
      const request = ++revision
      const current = () => request === revision && isActive() && router.currentRoute.value === route
      if (!current()) return
      if (!restore) pendingClick = { request, route }
      try {
        const destination = typeof target === 'string' ? await resolveTarget(target) : target
        if (!current()) return
        openTarget(destination, { restore })
        if (!restore) await router.push(sessionLinkLocation(destination))
      } catch (error) {
        if (!current()) return
        if (restore) throw error
        onError(error)
      } finally {
        if (pendingClick?.request === request) pendingClick = null
      }
    }
  }
}
