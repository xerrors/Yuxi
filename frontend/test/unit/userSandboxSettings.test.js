import assert from 'node:assert/strict'
import test from 'node:test'
import { setImmediate } from 'node:timers'
import { readFileSync } from 'node:fs'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, h, nextTick } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

async function renderSettings(component, api) {
  globalThis.__sandboxSettingsTest = api
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
    ssr: { noExternal: ['ant-design-vue'] },
    plugins: [
      {
        name: 'sandbox-settings-client',
        enforce: 'pre',
        resolveId(id) {
          if (id === 'ant-design-vue') return '\0virtual:sandbox-ant'
          if (id.endsWith('/ActionDropdown.vue')) return '\0virtual:action-dropdown'
          if (id === '../model/sandboxTiming')
            return new URL('../../src/modules/settings/model/sandboxTiming.js', import.meta.url)
              .pathname
          if (id.endsWith('/AgentEnvEditor.vue')) return '\0virtual:env-editor'
          if (id.endsWith('UserSandboxSettingsCard.vue')) return '\0virtual:sandbox-child'
          if (id.endsWith('SandboxBrowserSettings.vue')) return '\0virtual:sandbox-browser'
          if (
            [
              'virtual:sandbox-settings',
              'virtual:sandbox-ant',
              'virtual:env-editor',
              'virtual:sandbox-child',
              'virtual:action-dropdown',
              '@/apis/sandbox_api',
              '@/apis/agent_env_api',
              '@/apis/devices_api'
            ].includes(id)
          )
            return '\0' + id
        },
        load(id) {
          if (id === '\0virtual:sandbox-ant')
            return `const t = globalThis.__sandboxSettingsTest;
          export const Modal = { confirm: t.confirm }; export const message = t;`
          if (id === '\0@/apis/sandbox_api' || id.endsWith('/src/apis/sandbox_api.js'))
            return 'export const sandboxApi = globalThis.__sandboxSettingsTest'
          if (id === '\0@/apis/agent_env_api' || id.endsWith('/src/apis/agent_env_api.js'))
            return 'export const agentEnvApi = globalThis.__sandboxSettingsTest'
          if (id === '\0@/apis/devices_api' || id.endsWith('/src/apis/devices_api.js')) {
            return `export const devicesApi = {
              getSandboxBrowser: async () => globalThis.__sandboxSettingsTest.getSandboxBrowser
                ? globalThis.__sandboxSettingsTest.getSandboxBrowser()
                : { status: 'unavailable' },
              connectSandboxBrowser: () => globalThis.__sandboxSettingsTest.connectSandboxBrowser()
            }`
          }
          if (id === '\0virtual:env-editor')
            return `import { h } from 'vue'; export default { setup: (_, { attrs, slots }) => () => h('env-editor', attrs, slots.actions?.()) }`
          if (id === '\0virtual:sandbox-child')
            return 'export default { setup: (_, { expose }) => { expose({ refresh: async () => {} }); return () => null } }'
          if (id === '\0virtual:action-dropdown')
            return `import { h } from 'vue'; export default { setup: (_, { slots }) => () => h('dropdown', [slots.trigger?.(), slots.default?.()]) }`
          if (!['\0virtual:sandbox-settings', '\0virtual:sandbox-browser'].includes(id)) return
          const name = id === '\0virtual:sandbox-browser' ? 'SandboxBrowserSettings' : component
          const source = readFileSync(
            new URL(`../../src/modules/settings/ui/${name}.vue`, import.meta.url),
            'utf8'
          )
          return compileScript(parse(source).descriptor, {
            id,
            inlineTemplate: true
          }).content
        }
      }
    ]
  })
  const node = (type) => ({ type, children: [], props: {}, parent: null })
  const renderer = createRenderer({
    createElement: node,
    createText: (text) => ({ ...node('text'), text }),
    createComment: (text) => ({ ...node('comment'), text }),
    insert(child, parent, anchor) {
      if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1)
      child.parent = parent
      const index = anchor ? parent.children.indexOf(anchor) : -1
      if (index < 0) parent.children.push(child)
      else parent.children.splice(index, 0, child)
    },
    remove(child) {
      child.parent.children.splice(child.parent.children.indexOf(child), 1)
    },
    setText: (node, text) => {
      node.text = text
    },
    setElementText: (node, text) => {
      node.text = text
      node.children = []
    },
    parentNode: (node) => node.parent,
    nextSibling: (node) => node.parent?.children[node.parent.children.indexOf(node) + 1],
    patchProp: (node, key, old, value) => {
      node.props[key] = value
    }
  })
  const find = (node, predicate) =>
    predicate(node) ? node : node.children.map((child) => find(child, predicate)).find(Boolean)
  const settle = async () => {
    await new Promise((resolve) => setImmediate(resolve))
    await nextTick()
  }
  let app
  try {
    const { default: Settings } = await server.ssrLoadModule('virtual:sandbox-settings')
    app = renderer.createApp(Settings)
    for (const name of ['a-button', 'a-tag', 'a-alert', 'a-spin', 'a-tooltip', 'RouterLink']) {
      app.component(name, {
        setup:
          (_, { attrs, slots }) =>
          () =>
            h(
              name === 'RouterLink' ? 'a' : name === 'a-tooltip' ? 'span' : 'button',
              attrs,
              slots.default?.()
            )
      })
    }
    const host = node('root')
    app.mount(host)
    await settle()
    return {
      find: (predicate) => find(host, predicate),
      settle,
      close: async () => {
        app.unmount()
        await server.close()
        delete globalThis.__sandboxSettingsTest
      }
    }
  } catch (error) {
    app?.unmount()
    await server.close()
    delete globalThis.__sandboxSettingsTest
    throw error
  }
}

test('环境重建读取最新占用，确认后 409 回读状态且不会显示成功', async () => {
  const idle = { state: 'paused', busy: false, active_runs: [], active_operations: 0 }
  const busy = {
    state: 'running',
    busy: true,
    active_operations: 1,
    active_runs: [{ id: 'run-a', thread_id: 'thread-a', status: 'cancel_requested' }]
  }
  let current = busy
  let confirm
  let calls = 0
  const messages = []
  const api = {
    get: async () => current,
    rebuild: async () => {
      calls++
      current = busy
      throw new Error('仍有 Run 正在使用环境')
    },
    confirm: (options) => {
      confirm = options
    },
    success: (message) => messages.push(['success', message]),
    error: (message) => messages.push(['error', message])
  }
  const view = await renderSettings('UserSandboxSettingsCard', api)
  try {
    assert.equal(
      view.find(
        (element) =>
          element.type === 'button' &&
          element.children.some((child) => child.text?.trim() === '重启环境')
      ),
      undefined
    )
    const rebuild = () => view.find((node) => node.props.role === 'menuitem')
    assert.equal(rebuild().props.disabled, true)
    assert.ok(view.find((node) => node.props['aria-label'] === '环境操作'))
    assert.equal(rebuild().text.trim(), '重建环境')
    const link = view.find((node) => node.type === 'a')
    assert.equal(link.props.to.params.thread_id, 'thread-a')
    assert.equal(link.props.target, '_blank')
    current = idle
    await view.find((node) => node.props['aria-label'] === '刷新环境状态').props.onClick()
    await view.settle()
    assert.equal(rebuild().props.disabled, false)
    current = busy
    await rebuild().props.onClick()
    await view.settle()
    assert.equal(confirm, undefined)
    assert.equal(calls, 0)
    current = idle
    await view.find((node) => node.props['aria-label'] === '刷新环境状态').props.onClick()
    await rebuild().props.onClick()
    assert.match(confirm.content, /工作区文件与技能将保留/)
    await confirm.onOk()
    await view.settle()
    assert.equal(calls, 1)
    assert.equal(rebuild().props.disabled, true)
    assert.deepEqual(messages, [['error', '仍有 Run 正在使用环境']])
  } finally {
    await view.close()
  }
})

test('环境变量验证显示匹配结果，编辑或其他页面更新后不能复用旧成功', async () => {
  let saved = { env: { LAB_ENV: 'secret-value' }, updated_at: 'version-1' }
  let verify = async () => ({
    updated_at: saved.updated_at,
    verified_at: '2026-10-14T14:00:00Z',
    variables: [{ key: 'LAB_ENV', matches: false }]
  })
  const api = {
    get: async () => saved,
    update: async (env) => {
      saved = { env, updated_at: 'version-2' }
      return saved
    },
    verify: () => verify(),
    success: () => {},
    info: () => {},
    error: () => {}
  }
  const view = await renderSettings('AgentEnvSettingsCard', api)
  const button = (text) =>
    view.find(
      (element) =>
        element.type === 'button' && element.children.some((child) => child.text?.trim() === text)
    )
  const editor = () => view.find((element) => element.type === 'env-editor')
  try {
    assert.equal(button('验证配置').props.disabled, false)
    await button('验证配置').props.onClick()
    await view.settle()
    assert.equal(editor().props['key-verification'].get('LAB_ENV'), false)
    editor().props['onUpdate:modelValue']({ LAB_ENV: 'new-secret' })
    await view.settle()
    assert.equal(editor().props['key-verification'].size, 0)
    assert.equal(button('验证配置').props.disabled, true)
    assert.equal(editor().props['key-verification'].size, 0)
    await button('保存更改').props.onClick()
    await view.settle()
    let complete
    verify = () =>
      new Promise((resolve) => {
        complete = resolve
      })
    const pending = button('验证配置').props.onClick()
    editor().props['onUpdate:modelValue']({ LAB_ENV: 'another-change' })
    complete({
      updated_at: 'version-2',
      verified_at: '2026-10-14T14:00:00Z',
      variables: [{ key: 'LAB_ENV', matches: true }]
    })
    await pending
    await view.settle()
    assert.equal(editor().props['key-verification'].size, 0)
    assert.equal(button('验证配置').props.disabled, true)
    await button('保存更改').props.onClick()
    verify = async () => ({ updated_at: 'other-page-version', variables: [] })
    await button('验证配置').props.onClick()
    await view.settle()
    assert.ok(view.find((node) => node.props.message?.includes('配置已更新')))
    verify = async () => {
      throw new Error('环境连接失败')
    }
    await button('验证配置').props.onClick()
    await view.settle()
    assert.ok(view.find((node) => node.props.message === '环境连接失败'))
    let finishVerify, finishRefresh
    verify = () =>
      new Promise((resolve) => {
        finishVerify = resolve
      })
    api.get = () =>
      new Promise((resolve) => {
        finishRefresh = resolve
      })
    const verifying = button('验证配置').props.onClick()
    const refreshing = view
      .find((node) => node.props['aria-label'] === '刷新环境变量')
      .props.onClick()
    finishVerify({
      updated_at: 'version-2',
      verified_at: '2026-10-14T14:00:00Z',
      variables: [{ key: 'LAB_ENV', matches: true }]
    })
    await verifying
    finishRefresh({ env: { LAB_ENV: 'another-page-value' }, updated_at: 'version-3' })
    await refreshing
    await view.settle()
    assert.equal(editor().props['key-verification'].size, 0)
    assert.equal(editor().props.modelValue.LAB_ENV, 'another-page-value')
  } finally {
    await view.close()
  }
})

test('休眠时间与空闲倒计时按服务端快照展示，忙碌和未知时间不猜测', async () => {
  let current = {
    state: 'running',
    busy: false,
    error: null,
    server_time: '2026-10-15T02:03:00Z',
    last_active_at: '2026-10-15T02:00:00Z',
    idle_deadline_at: '2026-10-15T02:05:00Z',
    idle_timeout_seconds: 300
  }
  const view = await renderSettings('UserSandboxSettingsCard', { get: async () => current })
  const timing = () => view.find((node) => node.props.class === 'sandbox-timing')?.text?.trim()
  const refresh = async () => {
    await view.find((node) => node.props['aria-label'] === '刷新环境状态').props.onClick()
    await view.settle()
  }
  try {
    // 本机时钟与固定服务端快照相差数小时，仍应显示两分钟。
    assert.equal(timing(), '预计 2 分钟 后休眠')
    current = { ...current, busy: true, idle_deadline_at: null }
    await refresh()
    assert.equal(timing(), '任务或操作进行中，暂不休眠')
    current = {
      ...current,
      busy: false,
      server_time: '2026-10-15T02:05:01Z',
      idle_deadline_at: '2026-10-15T02:05:00Z'
    }
    await refresh()
    assert.equal(timing(), '已达到空闲时限，等待休眠')
    current = {
      ...current,
      state: 'paused',
      paused_at: '2026-10-15T02:00:00Z',
      server_time: '2026-10-15T02:05:00Z'
    }
    await refresh()
    assert.equal(timing(), '5 分钟')
    current = { ...current, paused_at: null }
    await refresh()
    assert.equal(timing(), '时间未记录')
    current = { ...current, state: 'failed' }
    await refresh()
    assert.equal(timing(), undefined)
  } finally {
    await view.close()
  }
})

test('container 模式显示任务级临时环境并隐藏重建和磁盘操作', async () => {
  const current = {
    backend: 'container',
    state: 'not_created',
    busy: false,
    active_runs: [],
    active_operations: 0
  }
  const view = await renderSettings('UserSandboxSettingsCard', { get: async () => current })
  try {
    assert.equal(view.find((node) => node.props['aria-label'] === '沙盒浏览器'), undefined)
    assert.ok(
      view.find(
        (node) =>
          node.text?.trim() === '任务独立' &&
          node.props.title === '每个任务独立临时环境，任务结束后销毁'
      )
    )
    assert.equal(
      view.find((node) => node.props.role === 'menuitem'),
      undefined
    )
    assert.equal(
      view.find((node) => node.props['aria-label'] === '环境操作'),
      undefined
    )
    assert.equal(
      view.find((node) => node.props['aria-label']?.includes('磁盘用量')),
      undefined
    )
    assert.equal(
      view.find((node) => node.props.class === 'sandbox-timing'),
      undefined
    )
    assert.ok(view.find((node) => node.text?.trim() === '未创建'))
  } finally {
    await view.close()
  }
})

test('较旧的环境查询晚返回不能覆盖最新休眠时间，错误后不保留倒计时', async () => {
  let get = async () => ({ state: 'not_created', idle_timeout_seconds: 300 })
  const view = await renderSettings('UserSandboxSettingsCard', { get: () => get() })
  try {
    const refresh = () =>
      view.find((node) => node.props['aria-label'] === '刷新环境状态').props.onClick()
    let finishOld
    get = () =>
      new Promise((resolve) => {
        finishOld = resolve
      })
    const old = refresh()
    get = async () => ({ state: 'paused', paused_at: null, idle_timeout_seconds: 300 })
    await refresh()
    finishOld({ state: 'running', busy: true, idle_timeout_seconds: 300 })
    await old
    await view.settle()
    assert.equal(
      view.find((node) => node.props.class === 'sandbox-timing').text.trim(),
      '时间未记录'
    )
    get = async () => {
      throw new Error('无法查询执行节点')
    }
    await refresh()
    await view.settle()
    assert.equal(
      view.find((node) => node.props.class === 'sandbox-timing'),
      undefined
    )
    assert.ok(view.find((node) => node.props.message === '无法查询执行节点'))
  } finally {
    await view.close()
  }
})

test('磁盘回读取代重建预检时，较旧的空闲状态不能打开确认框', async () => {
  const idle = { backend: 'cube', state: 'running', generation: 1, busy: false }
  const preflight = Promise.withResolvers()
  const afterDisk = Promise.withResolvers()
  const diskUsage = Promise.withResolvers()
  const reads = [Promise.resolve(idle), preflight.promise, afterDisk.promise]
  let confirmation
  const view = await renderSettings('UserSandboxSettingsCard', {
    get: () => reads.shift(),
    diskUsage: () => diskUsage.promise,
    confirm: (options) => { confirmation = options }
  })
  const menuItem = () => view.find((node) => node.props.role === 'menuitem')
  try {
    const disk = view.find((node) => node.props['aria-label'] === '查看磁盘用量').props.onClick()
    const checking = menuItem().props.onClick()
    diskUsage.resolve({ used_bytes: 1, available_bytes: 1 })
    await view.settle()
    preflight.resolve(idle)
    await checking
    assert.equal(confirmation, undefined)

    afterDisk.resolve({ ...idle, busy: true })
    await disk
    await view.settle()
    assert.equal(menuItem().props.disabled, true)
    assert.equal(confirmation, undefined)
  } finally {
    await view.close()
  }
})

test('重建成功后迟到刷新不能留下 loading 或覆盖新环境', async () => {
  const idle = { state: 'paused', busy: false, idle_timeout_seconds: 300 }
  let get = async () => idle
  let confirm, finishRebuild, finishRefresh
  const api = {
    get: () => get(),
    rebuild: () =>
      new Promise((resolve) => {
        finishRebuild = resolve
      }),
    confirm: (options) => {
      confirm = options
    },
    success: () => {},
    error: () => {}
  }
  const view = await renderSettings('UserSandboxSettingsCard', api)
  const rebuildButton = () => view.find((node) => node.props.role === 'menuitem')
  const refreshButton = () => view.find((node) => node.props['aria-label'] === '刷新环境状态')
  try {
    await rebuildButton().props.onClick()
    const rebuilding = confirm.onOk()
    get = () =>
      new Promise((resolve) => {
        finishRefresh = resolve
      })
    const refreshing = refreshButton().props.onClick()
    finishRebuild({ state: 'running', busy: false, idle_timeout_seconds: 300 })
    await rebuilding
    finishRefresh(idle)
    await refreshing
    await view.settle()
    assert.equal(refreshButton().props.loading, false)
    assert.equal(rebuildButton().props.disabled, false)
    assert.ok(view.find((node) => node.text?.trim() === '运行中'))
  } finally {
    await view.close()
  }
})

test('磁盘只在手动查询时读取，保留零值并清除失败或新代次的旧用量', async () => {
  let current = { state: 'paused', busy: false, generation: 1, idle_timeout_seconds: 300 }
  let reads = 0
  let read = async () => ({ used_bytes: 0, available_bytes: 2147483648 })
  const view = await renderSettings('UserSandboxSettingsCard', {
    get: async () => current,
    diskUsage: () => {
      reads++
      current = { ...current, state: 'running' }
      return read()
    }
  })
  const button = () =>
    view.find(
      (node) =>
        node.type === 'button' &&
        /^(查看|刷新|唤醒并查询|唤醒并刷新)磁盘用量$/.test(node.props['aria-label'] || '')
    )
  const refresh = async () => {
    await view.find((node) => node.props['aria-label'] === '刷新环境状态').props.onClick()
    await view.settle()
  }
  /** 新环境清除旧缓存后，才重新提供查询入口。 */
  const queryNewEnvironment = async () => {
    current = { ...current, generation: current.generation + 1 }
    await refresh()
    await button().props.onClick()
    await view.settle()
  }
  try {
    assert.equal(reads, 0)
    assert.equal(
      view.find((node) => node.props.role === 'meter'),
      undefined
    )
    assert.ok(view.find((node) => node.props['aria-label'] === '唤醒并查询磁盘用量'))
    await refresh()
    assert.equal(reads, 0)
    await button().props.onClick()
    await view.settle()
    assert.ok(view.find((node) => node.text?.includes('已用 0 B · 可用 2 GB')))
    assert.equal(view.find((node) => node.props.role === 'meter').props['aria-valuenow'], 0)
    read = async () => ({ used_bytes: 1073741824, available_bytes: 3221225472 })
    await queryNewEnvironment()
    assert.equal(view.find((node) => node.props.role === 'meter').props['aria-valuenow'], 25)
    assert.equal(button(), undefined, '查询成功后不显示额外刷新入口')
    const fill = view.find((node) => node.props.class === 'sandbox-disk-fill')
    assert.equal(fill.props.style.width, '25%')
    read = async () => ({ used_bytes: 1073741824, available_bytes: 0 })
    await queryNewEnvironment()
    assert.equal(view.find((node) => node.props.role === 'meter').props['aria-valuenow'], 100)
    read = async () => ({ used_bytes: 0, available_bytes: 0 })
    await queryNewEnvironment()
    assert.equal(
      view.find((node) => node.props.role === 'meter'),
      undefined
    )

    read = async () => {
      throw new Error('磁盘用量读取失败')
    }
    await queryNewEnvironment()
    assert.ok(view.find((node) => node.props.role === 'alert' && node.text === '磁盘用量读取失败'))
    assert.equal(
      view.find((node) => node.props.role === 'meter'),
      undefined
    )
    assert.equal(
      view.find((node) => node.text?.includes('已用 0 B')),
      undefined
    )
    read = async () => ({ used_bytes: 1073741824, available_bytes: 2147483648 })
    await queryNewEnvironment()
    current = { ...current, generation: current.generation + 1 }
    await refresh()
    assert.equal(
      view.find((node) => node.props.role === 'meter'),
      undefined
    )
    assert.equal(
      view.find((node) => node.text?.includes('已用 1 GB')),
      undefined
    )
    let finish
    read = () =>
      new Promise((resolve) => {
        finish = resolve
      })
    const pending = button().props.onClick()
    current = { ...current, generation: current.generation + 1 }
    await refresh()
    finish({ used_bytes: 1073741824, available_bytes: 2147483648 })
    await pending
    await view.settle()
    assert.equal(
      view.find((node) => node.text?.includes('已用 1 GB')),
      undefined
    )
    assert.equal(button().props.loading, false)
  } finally {
    await view.close()
  }
})

test('状态断线作废磁盘结果，恢复到新代次后不接收旧请求', async () => {
  let current = { state: 'running', busy: false, generation: 1, idle_timeout_seconds: 300 }
  let get = async () => current
  let read = async () => ({ used_bytes: 1073741824, available_bytes: 2147483648 })
  const view = await renderSettings('UserSandboxSettingsCard', {
    get: () => get(),
    diskUsage: () => read()
  })
  const button = () =>
    view.find(
      (node) =>
        node.type === 'button' &&
        /^(查看|刷新|唤醒并查询|唤醒并刷新)磁盘用量$/.test(node.props['aria-label'] || '')
    )
  const refresh = async () => {
    await view.find((node) => node.props['aria-label'] === '刷新环境状态').props.onClick()
    await view.settle()
  }
  try {
    await button().props.onClick()
    get = async () => {
      throw new Error('状态读取失败')
    }
    await refresh()
    current = { ...current, generation: 2 }
    get = async () => current
    await refresh()
    assert.equal(
      view.find((node) => node.text?.includes('已用 1 GB')),
      undefined
    )
    let finish
    read = () =>
      new Promise((resolve) => {
        finish = resolve
      })
    const pending = button().props.onClick()
    get = async () => {
      throw new Error('状态读取失败')
    }
    await refresh()
    current = { ...current, generation: 3 }
    get = async () => current
    await refresh()
    finish({ used_bytes: 1073741824, available_bytes: 2147483648 })
    await pending
    await view.settle()
    assert.equal(
      view.find((node) => node.text?.includes('已用 1 GB')),
      undefined
    )
    assert.equal(button().props.loading, false)
  } finally {
    await view.close()
  }
})

test('未创建环境可连接浏览器，旧客户端的失败有明确说明且可以重试', async () => {
  let connections = 0
  let sandboxReads = 0
  let generation = 0
  let browserStatus = 'not_connected'
  const view = await renderSettings('UserSandboxSettingsCard', {
    get: async () => {
      sandboxReads++
      return { backend: 'cube', state: generation ? 'running' : 'not_created', generation, active_runs: [] }
    },
    getSandboxBrowser: async () => ({ status: browserStatus }),
    connectSandboxBrowser: async () => {
      if (++connections === 1) {
        generation = 1
        throw Object.assign(new Error('不支持'), {
          response: { data: { detail: { code: 'sandbox_service_unsupported' } } }
        })
      }
      browserStatus = 'ready'
      return { status: browserStatus }
    }
  })
  const connectButton = () =>
    view.find((element) =>
      element.type === 'button' && element.children.some((child) => child.text?.trim() === '连接沙盒浏览器')
    )
  try {
    assert.equal(connections, 0)
    assert.ok(view.find((element) => element.text === '首次连接会创建运行环境'))
    await connectButton().props.onClick()
    await view.settle()
    assert.equal(view.find((element) => element.props.role === 'alert').text, '此环境需要升级浏览器客户端，请联系管理员')
    await connectButton().props.onClick()
    await view.settle()
    assert.equal(connections, 2)
    assert.equal(connectButton(), undefined)
    assert.ok(view.find((element) => element.text === '已连接到 aBridge'))
    assert.equal(sandboxReads, 3)
    browserStatus = 'offline'
    await view.find((element) => element.props['aria-label'] === '刷新环境状态').props.onClick()
    await view.settle()
    assert.ok(view.find((element) => element.text === '已离线'))
  } finally {
    await view.close()
  }
})

test('浏览器首次连接失败后刷新环境，禁止重复连接并复用显式重建', async () => {
  let state = 'not_created'
  let connections = 0
  let confirmation
  let rebuiltGeneration
  const view = await renderSettings('UserSandboxSettingsCard', {
    get: async () => ({ backend: 'cube', state, generation: 2, rebuild_required: state === 'failed', busy: false, active_runs: [] }),
    rebuild: async (generation) => {
      rebuiltGeneration = generation
      return { backend: 'cube', state: 'running', generation: 3, rebuild_required: false }
    },
    success: () => {},
    getSandboxBrowser: async () => ({ status: 'not_connected' }),
    connectSandboxBrowser: async () => {
      connections++
      state = 'failed'
      throw new Error('运行环境创建失败')
    },
    confirm: (options) => { confirmation = options }
  })
  const button = (text) =>
    view.find((element) => element.type === 'button' && element.children.some((child) => child.text?.trim() === text))
  try {
    await button('连接沙盒浏览器').props.onClick()
    await view.settle()
    assert.equal(button('连接沙盒浏览器'), undefined)
    assert.equal(connections, 1)
    assert.ok(view.find((element) => element.text === '恢复运行环境后可连接浏览器'))
    await view.find((element) => element.props.role === 'menuitem').props.onClick()
    assert.equal(confirmation.title, '确认重建运行环境')
    await confirmation.onOk()
    assert.equal(rebuiltGeneration, 2)
  } finally {
    await view.close()
  }
})

test('故障重建只确认点选的代次，刷新发现替换或恢复时不重建', async () => {
  let current = { backend: 'cube', state: 'failed', generation: 1, rebuild_required: true, busy: false }
  let confirmation
  const rebuiltGenerations = []
  const view = await renderSettings('UserSandboxSettingsCard', {
    get: async () => current,
    confirm: (options) => { confirmation = options },
    rebuild: async (generation) => {
      rebuiltGenerations.push(generation)
      return { ...current, state: 'running', generation: 3, rebuild_required: false }
    },
    success: () => {}
  })
  const rebuild = () => view.find((element) => element.props.role === 'menuitem').props.onClick()
  try {
    assert.ok(view.find((element) => element.props.description?.includes('「环境操作」')))
    current = { ...current, generation: 2 }
    await rebuild()
    await view.settle()
    assert.equal(confirmation, undefined)

    current = { ...current, rebuild_required: false, error: '暂时无法读取 Cube 实例状态' }
    await rebuild()
    await view.settle()
    assert.equal(confirmation, undefined)
    const alert = view.find((element) => element.props.message === current.error)
    assert.equal(alert.props.description, undefined)
    await rebuild()
    await confirmation.onOk()
    assert.deepEqual(rebuiltGenerations, [undefined])

    // 点击前显示运行中，确认框打开前的刷新首次发现故障。
    current = { ...current, rebuild_required: true, error: null }
    await rebuild()
    current = { ...current, generation: 3 }
    await confirmation.onOk()
    await view.settle()
    assert.deepEqual(rebuiltGenerations, [undefined, 2])
    assert.ok(view.find((element) => element.text === '运行中'))
    assert.equal(view.find((element) => element.props.description?.includes('「环境操作」')), undefined)
  } finally {
    await view.close()
  }
})

test('新环境的浏览器状态不受迟到的上一代请求影响', async () => {
  let generation = 1
  let finishOldRead
  const view = await renderSettings('UserSandboxSettingsCard', {
    get: async () => ({ backend: 'cube', state: 'running', generation }),
    getSandboxBrowser: () =>
      generation === 1
        ? new Promise((resolve) => { finishOldRead = resolve })
        : Promise.resolve({ status: 'not_connected' })
  })
  try {
    generation = 2
    await view.find((element) => element.props['aria-label'] === '刷新环境状态').props.onClick()
    await view.settle()
    assert.ok(view.find((element) => element.text === '未连接'))
    finishOldRead({ status: 'ready' })
    await view.settle()
    assert.ok(view.find((element) => element.text === '未连接'))
    assert.equal(view.find((element) => element.text === '已连接到 aBridge'), undefined)
  } finally {
    await view.close()
  }
})

test('首次连接待确认时可立即检查，父组件刷新发现离线后可重新连接', async (t) => {
  const initialEnvironment = { backend: 'cube', state: 'not_created', generation: 0 }
  let status = 'not_connected'
  let reads = 0
  let connections = 0
  let finishEnvironmentRefresh
  const view = await renderSettings('UserSandboxSettingsCard', {
    get: () =>
      connections
        ? new Promise((resolve) => {
            finishEnvironmentRefresh = resolve
          })
        : Promise.resolve(initialEnvironment),
    getSandboxBrowser: async () => {
      reads++
      return { status }
    },
    connectSandboxBrowser: async () => {
      connections++
      status = 'connecting'
      return { status }
    }
  })
  const button = (text) =>
    view.find(
      (element) =>
        element.type === 'button' && element.children.some((child) => child.text?.trim() === text)
    )
  try {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const connect = button('连接沙盒浏览器').props.onClick
    const connecting = connect()
    await connect()
    await view.settle()
    assert.ok(view.find((element) => element.text === '正在确认连接'))
    assert.equal(button('连接沙盒浏览器'), undefined)
    assert.equal(button('重新连接'), undefined)
    assert.equal(button('检查连接').props.loading, false)
    assert.equal(typeof finishEnvironmentRefresh, 'function')
    await connecting

    await button('检查连接').props.onClick()
    await view.settle()
    assert.ok(view.find((element) => element.text === '正在确认连接'))
    t.mock.timers.tick(10000)
    await view.settle()
    assert.equal(reads, 2)
    assert.equal(connections, 1)
    assert.equal(view.find((element) => element.props.role === 'alert'), undefined)

    status = 'offline'
    finishEnvironmentRefresh({ ...initialEnvironment, state: 'running', generation: 1 })
    await view.settle()
    assert.ok(view.find((element) => element.text === '已离线'))
    assert.ok(button('重新连接'))
    assert.equal(connections, 1)
  } finally {
    t.mock.timers.reset()
    await view.close()
  }
})
