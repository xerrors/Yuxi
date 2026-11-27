import { createRenderer, h, reactive, ssrContextKey, toRaw } from 'vue'
import { routerKey, routeLocationKey } from 'vue-router'
import { createTestServer } from './createTestServer.js'

/** 装配工作区页面，并在路由和文件服务边界提供可控数据。 */
export async function mountWorkspaceView(t, { path = '/agents/MEMORY.md' } = {}) {
  const globals = Object.fromEntries(['window', 'document', '__workspaceViewBoundary'].map((name) =>
    [name, Object.getOwnPropertyDescriptor(globalThis, name)]
  ))
  let server, app, mounted = false
  t.after(async () => {
    if (mounted) app.unmount()
    try {
      await server?.close()
    } finally {
      for (const [name, descriptor] of Object.entries(globals)) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor)
        else delete globalThis[name]
      }
    }
  })
  const route = reactive({ path: '/workspace', query: {} })
  const replacements = []
  const boundary = {
    route,
    router: { replace: async (location) => { replacements.push(location); route.query = location.query } },
    getWorkspaceTree: async () => ({ entries: [{ path, name: 'MEMORY.md', is_dir: false }] }),
    getAccessibleKnowledgeBases: async () => ({ knowledge_bases: [] }),
    getWorkspaceFileContent: async () => ({}),
    openWorkspaceFile: async () => ({}),
    previewThreadArtifact: async () => ({}),
    normalizePreviewResponse: async () => ({ path, content: '# MEMORY', previewType: 'text' })
  }
  globalThis.__workspaceViewBoundary = boundary
  const exports = {
    router: 'export const useRoute = () => globalThis.__workspaceViewBoundary.route; export const useRouter = () => globalThis.__workspaceViewBoundary.router',
    user: 'export const useUserStore = () => ({})',
    workspace: `export const getWorkspaceTree = (...args) => globalThis.__workspaceViewBoundary.getWorkspaceTree(...args);
      export const openWorkspaceFile = (...args) => globalThis.__workspaceViewBoundary.openWorkspaceFile(...args);
      export const getWorkspaceFileContent = (...args) => globalThis.__workspaceViewBoundary.getWorkspaceFileContent(...args);
      export const createWorkspaceDirectory = () => {}; export const deleteWorkspacePath = () => {};
      export const downloadWorkspaceFile = () => {}; export const downloadWorkspaceKnowledgeFile = () => {};
      export const getWorkspaceKnowledgeFileContent = () => {}; export const getWorkspaceKnowledgeTree = () => {};
      export const saveWorkspaceFileContent = () => {}; export const searchWorkspaceFiles = () => {};
      export const uploadWorkspaceFiles = () => {}`,
    preview: 'export const normalizePreviewResponse = (...args) => globalThis.__workspaceViewBoundary.normalizePreviewResponse(...args)'
  }
  server = await createTestServer({
    server: { middlewareMode: true, hmr: false }, appType: 'custom',
    plugins: [{
      name: 'workspace-view-boundaries', enforce: 'pre',
      resolveId(id, importer) {
        if (!importer?.includes('/pages/WorkspaceView.vue')) return
        if (id.endsWith('.vue')) return '\0workspace-view-child/' + id.split('/').at(-1).replace('.vue', '')
        if (id === 'vue-router') return '\0workspace-view-router'
        if (/\/modules\/identity(?:\/index\.js)?$/.test(id)) return '\0workspace-view-user'
        if (id.endsWith('/apis/workspace_api') || id.endsWith('/apis/workspace_api.js')) return '\0workspace-view-workspace'
        if (id.endsWith('/shared/lib/file_preview') || id.endsWith('/shared/lib/file_preview.js')) return '\0workspace-view-preview'
      },
      load(id) {
        if (id.startsWith('\0workspace-view-child/')) {
          const name = id.split('/').at(-1)
          return `import { h } from 'vue'; export default { render() { return h('section', { 'data-child': '${name}' }) } }`
        }
        return exports[id.replace('\0workspace-view-', '')]
      }
    }]
  })
  const { threadApi } = await server.ssrLoadModule('/src/apis/agent_api.js')
  t.mock.method(threadApi, 'previewThreadArtifact', (...args) => boundary.previewThreadArtifact(...args))
  const { knowledgeBaseApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
  t.mock.method(knowledgeBaseApi, 'getAccessibleKnowledgeBases', () => boundary.getAccessibleKnowledgeBases())
  const { default: component } = await server.ssrLoadModule('/src/pages/WorkspaceView.vue')
  globalThis.window = { URL: { revokeObjectURL() {} }, removeEventListener() {} }
  globalThis.document = { body: { style: {} } }

  const renderer = createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
    parentNode: () => null, nextSibling: () => null
  })
  app = renderer.createApp({ ...component, render: () => h('div') })
  app.provide(ssrContextKey, { modules: new Set() })
  app.provide(routerKey, boundary.router)
  app.provide(routeLocationKey, route)
  app.mount({})
  mounted = true
  const state = toRaw(app._instance.setupState)
  return { state, route, replacements, instance: app._instance, component, boundary }
}
