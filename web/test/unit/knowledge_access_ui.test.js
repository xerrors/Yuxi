import assert from 'node:assert/strict'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createServer } from 'vite'

globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {}
}

const componentStubSource = `
import { h } from 'vue'
export default {
  props: ['readonly', 'ready', 'tabs', 'description', 'open', 'title'],
  setup(props, { slots, attrs }) {
    return () => h('div', {
      'data-component': COMPONENT_NAME,
      'data-readonly': String(props.readonly),
      'data-tabs': props.tabs?.map(tab => tab.key).join(','),
      ...attrs
    }, [props.description, ...Object.values(slots).map(slot => slot())])
  }
}`

async function withComponents(run) {
  const server = await createServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] },
    plugins: [
      {
        name: 'knowledge-ui-leaf-stubs',
        enforce: 'pre',
        resolveId(id) {
          if (id.replaceAll('\\', '/').endsWith('/components/FileUploadModal.vue')) return null
          if (
            (id.startsWith('@/components/') ||
              id.replaceAll('\\', '/').includes('/src/components/')) &&
            id.endsWith('.vue')
          )
            return `\0knowledge-ui:${id}`
        },
        load(id) {
          if (!id.startsWith('\0knowledge-ui:')) return null
          const name = id.split('/').at(-1).replace('.vue', '')
          return componentStubSource.replaceAll('COMPONENT_NAME', JSON.stringify(name))
        }
      }
    ]
  })
  try {
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const { useDatabaseStore } = await server.ssrLoadModule('/src/stores/database.js')
    const { databaseApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
    databaseApi.detectVirtualFolders = async () => ({ has_virtual_folders: false })
    async function render(
      path,
      { role = 'user', database, route = '/extensions?tab=knowledge', props = {} } = {}
    ) {
      const { default: component } = await server.ssrLoadModule(path)
      const app = createSSRApp(component, props)
      const pinia = createPinia()
      app.use(pinia)
      setActivePinia(pinia)
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [
          { path: '/extensions', component: {} },
          { path: '/extensions/knowledgebase/:kbId', component: {} }
        ]
      })
      app.use(router)
      await router.push(route)
      const user = useUserStore()
      user.token = 'test-session'
      user.userRole = role
      const store = app.runWithContext(() => useDatabaseStore())
      store.databases = database ? [database] : []
      // 提供后端读回的权限投影；真实详情组件仍拥有 Tab、操作和 readonly 的决定。
      store.getDatabaseInfo = (id) => {
        store.kbId = id
        store.database = {
          ...database,
          kb_id: id,
          kb_type: 'milvus',
          files: {},
          stats: { file_count: 1 }
        }
      }
      store.loadDatabases = async () => {}
      const passSlots = {
        setup(_props, { slots }) {
          return () => Object.values(slots).flatMap((slot) => slot())
        }
      }
      const closedModal = {
        props: ['open'],
        setup:
          (props, { slots }) =>
          () =>
            props.open ? slots.default?.() : null
      }
      for (const name of [
        'a-space',
        'a-select',
        'a-select-option',
        'a-menu',
        'a-menu-item',
        'a-menu-divider',
        'a-tag',
        'a-tooltip',
        'a-form',
        'a-form-item',
        'a-form-item-rest',
        'a-tabs',
        'a-tab-pane',
        'a-input',
        'a-input-password'
      ])
        app.component(name, passSlots)
      app.component('a-button', {
        setup(_props, { slots, attrs }) {
          return () => h('button', attrs, slots.default?.())
        }
      })
      app.component('a-modal', closedModal)
      app.component('a-segmented', {
        props: ['options'],
        setup: (props) => () => props.options.map((option) => option.label)
      })
      app.config.warnHandler = () => {}
      try {
        return await renderToString(app)
      } finally {
        store.$dispose()
      }
    }
    await run(render)
  } finally {
    await server.close()
  }
}

test('普通用户可打开知识库 Tab，创建整库操作仍只给管理员', async () => {
  await withComponents(async (render) => {
    const ordinary = await render('/src/views/ExtensionsView.vue')
    assert.match(ordinary, /data-tabs="knowledge,skills"/)
    assert.match(ordinary, /请联系管理员为你授权知识库/)
    assert.doesNotMatch(ordinary, /新建知识库|创建知识库|DatabaseCreateFlowModal/)
    const admin = await render('/src/views/ExtensionsView.vue', { role: 'admin' })
    assert.match(admin, /data-tabs="knowledge,skills,tools,mcp"/)
    assert.match(admin, /新建知识库/)
    assert.match(admin, /DatabaseCreateFlowModal/)
  })
})

test('普通文档编辑者能上传维护，但配置与评估仅管理员可见', async () => {
  await withComponents(async (render) => {
    for (const [role, canEdit, canManage] of [
      ['user', true, false],
      ['user', false, false],
      ['admin', true, true]
    ]) {
      const html = await render('/src/views/DataBaseInfoView.vue', {
        role,
        route: '/extensions/knowledgebase/shared',
        database: { name: '共享资料', can_edit_documents: canEdit, can_manage: canManage }
      })
      assert.match(html, new RegExp(`data-component="FileTable" data-readonly="${!canEdit}"`))
      assert.equal(/<span[^>]*>上传<\/span>/.test(html), canEdit)
      assert.equal(html.includes('aria-label="配置知识库"'), canManage)
      assert.match(
        html,
        new RegExp(`data-tabs="filetable,query,graph${canManage ? ',evaluation' : ''}"`)
      )
    }
  })
})

test('普通用户上传选项保留文件、文件夹和个人空间，URL仅管理员可用', async () => {
  await withComponents(async (render) => {
    const ordinary = await render('/src/components/FileUploadModal.vue', {
      props: { visible: true }
    })
    assert.match(ordinary, /上传文件夹/)
    assert.match(ordinary, /个人空间/)
    assert.doesNotMatch(ordinary, /解析 URL/)
    const admin = await render('/src/components/FileUploadModal.vue', {
      role: 'admin',
      props: { visible: true }
    })
    assert.match(admin, /解析 URL/)
  })
})
