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
  return {
    invalidate() { revision++ },
    async navigate(target, { restore = false } = {}) {
      const request = ++revision
      const route = router.currentRoute.value
      const current = () => request === revision && isActive() && router.currentRoute.value === route
      if (!current()) return
      try {
        const destination = typeof target === 'string' ? await resolveTarget(target) : target
        if (!current()) return
        openTarget(destination, { restore })
        if (!restore) await router.push(sessionLinkLocation(destination))
      } catch (error) {
        if (!current()) return
        if (restore) throw error
        onError(error)
      }
    }
  }
}
