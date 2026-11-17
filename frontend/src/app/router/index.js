import { createRouter, createWebHistory } from 'vue-router'
import BlankLayout from '@/app/layouts/BlankLayout.vue'
import { useUserStore } from '@/modules/identity/model/user'
import { sanitizeRedirect } from '@/modules/identity/model/oidcAutoStart'
import { message } from 'ant-design-vue'

const AppLayout = () => import('@/app/layouts/AppLayout.vue')

/** 将会话身份绑定到路由实例，保活页面不读取其他会话的路由参数。 */
const sessionRouteProps = (route) => ({
  memberSessionId: typeof route.query.member_session_id === 'string' ? route.query.member_session_id : '',
  threadId: typeof route.params.thread_id === 'string' ? route.params.thread_id : '',
  agentId: typeof route.query.agent_id === 'string' ? route.query.agent_id : '',
  projectId: typeof route.query.project_id === 'string' ? route.query.project_id : ''
})

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'main',
      component: BlankLayout,
      children: [
        {
          path: '',
          name: 'Home',
          component: () => import('../../pages/HomeView.vue'),
          meta: { keepAlive: true, requiresAuth: false }
        }
      ]
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('../../pages/LoginView.vue'),
      meta: { requiresAuth: false }
    },
    {
      path: '/auth/oidc/callback', // oidc登录回调页面
      name: 'OIDCCallback',
      component: () => import('@/pages/OIDCCallbackView.vue'),
      meta: { requiresAuth: false }
    },
    {
      path: '/auth/provider',
      name: 'OIDCProvider',
      component: () => import('@/pages/OIDCProviderView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/auth/cli/authorize',
      name: 'CLIAuthAuthorize',
      component: () => import('@/pages/CLIAuthAuthorizeView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/agent',
      name: 'AgentMain',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'AgentComp',
          component: () => import('../../pages/AgentView.vue'),
          props: sessionRouteProps,
          meta: { keepAlive: true, requiresAuth: true }
        },
        {
          path: ':thread_id',
          name: 'AgentCompWithThreadId',
          component: () => import('../../pages/AgentView.vue'),
          props: sessionRouteProps,
          meta: { keepAlive: true, requiresAuth: true }
        }
      ]
    },
    {
      path: '/workspace',
      name: 'workspace',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'WorkspaceComp',
          component: () => import('../../pages/WorkspaceView.vue'),
          meta: { keepAlive: true, requiresAuth: true }
        }
      ]
    },
    {
      path: '/dashboard',
      name: 'dashboard',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'DashboardComp',
          component: () => import('../../pages/DashboardView.vue'),
          meta: { keepAlive: false, requiresAuth: true, requiresSuperAdmin: true }
        }
      ]
    },
    {
      path: '/agent-manage',
      name: 'agent-manage',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'AgentManageComp',
          component: () => import('../../pages/AgentManageView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        }
      ]
    },
    {
      path: '/extensions',
      name: 'extensions',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'ExtensionsComp',
          component: () => import('../../pages/ExtensionsView.vue'),
          meta: {
            keepAlive: false,
            requiresAuth: true
          },
          children: [
            {
              path: 'knowledge-bases/:kbId',
              name: 'ExtensionKnowledgeBaseDetail',
              component: () => import('../../pages/KnowledgeBaseInfoView.vue'),
              meta: {
                keepAlive: false,
                requiresAuth: true,
                requiresAdmin: true
              }
            },
            {
              path: 'knowledge-bases/:kbId/evaluation/:datasetId',
              name: 'ExtensionEvaluationBenchmarkDetail',
              component: () => import('../../pages/EvaluationBenchmarkDetailView.vue'),
              meta: {
                keepAlive: false,
                requiresAuth: true,
                requiresAdmin: true
              }
            },
            {
              path: 'mcp/:slug',
              name: 'ExtensionMcpDetail',
              component: () => import('../../modules/extensions/ui/McpDetailView.vue'),
              meta: {
                keepAlive: false,
                requiresAuth: true,
                requiresSuperAdmin: true
              }
            },
            {
              path: 'skill/:slug',
              name: 'ExtensionSkillDetail',
              component: () => import('../../modules/extensions/ui/SkillDetailView.vue'),
              meta: {
                keepAlive: false,
                requiresAuth: true
              }
            }
          ]
        }
      ]
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'NotFound',
      component: () => import('../../pages/EmptyView.vue'),
      meta: { requiresAuth: false }
    }
  ]
})

// 页面挂载前校验身份；导航跟踪先运行，失败沿用 App 的重试界面。
router.beforeResolve(async (to) => {
  // 检查路由是否需要认证
  const requiresAuth = to.matched.some((record) => record.meta.requiresAuth === true)
  const requiresAdmin = to.matched.some((record) => record.meta.requiresAdmin)
  const requiresSuperAdmin = to.matched.some((record) => record.meta.requiresSuperAdmin)

  const userStore = useUserStore()

  // 如果有 token 但用户信息未加载，先获取用户信息
  if (userStore.token && !userStore.userId) {
    try {
      await userStore.getCurrentUser()
    } catch (error) {
      // 只有认证失效才清除凭据；服务异常拒绝导航并保留重试能力。
      if (error.status !== 401) throw error
      userStore.logout()
    }
  }

  const isLoggedIn = userStore.isLoggedIn
  const isAdmin = userStore.isAdmin
  const isSuperAdmin = userStore.isSuperAdmin

  // 如果路由需要认证但用户未登录
  if (requiresAuth && !isLoggedIn) {
    // 保存尝试访问的路径，登录后跳转
    sessionStorage.setItem('redirect', to.fullPath)
    return '/login'
  }

  if ((requiresAdmin && !isAdmin) || (requiresSuperAdmin && !isSuperAdmin)) {
    message.warning(to.path === '/dashboard' ? '无权访问数据总览' : '无权访问此页面')
    return '/agent'
  }

  // 如果用户已登录但访问登录页，按 redirect 参数跳转
  if (to.path === '/login' && isLoggedIn) {
    return sanitizeRedirect(to.query.redirect)
  }

  // 其他情况正常导航
  return true
})

export default router
