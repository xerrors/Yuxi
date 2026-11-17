import assert from 'node:assert/strict'
import test from 'node:test'
import { setImmediate } from 'node:timers'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, h, nextTick } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

/** 挂载真实组件，仅替换网络与基础控件。 */
async function render(api) {
  globalThis.__devicesTest = api
  const server = await createTestServer({
    resolve: {
      alias: {
        '@/assets': fileURLToPath(new URL('../../src/assets', import.meta.url)),
        '@/shared': fileURLToPath(new URL('../../src/shared', import.meta.url))
      }
    },
    ssr: { noExternal: ['ant-design-vue', 'vue-router'] },
    configFile: false,
    server: { middlewareMode: true },
    appType: 'custom',
    plugins: [
      {
        name: 'device-settings-test',
        enforce: 'pre',
        resolveId(id) {
          if (
            [
              'virtual:device-view',
              '@/apis/devices_api',
              '@/apis/system_api',
              '@/modules/identity',
              'ant-design-vue'
            ].includes(id)
          )
            return '\0' + id
        },
        load(id) {
          if (id === '\0@/apis/devices_api')
            return 'export const devicesApi = globalThis.__devicesTest'
          if (id === '\0@/apis/system_api')
            return 'export const configOptionsApi = globalThis.__devicesTest'
          if (id === '\0@/modules/identity')
            return 'export const useUserStore = () => globalThis.__devicesTest.user'
          if (id === '\0ant-design-vue') return 'export const message = globalThis.__devicesTest'
          if (id !== '\0virtual:device-view') return
          const path = '../../src/modules/settings/ui/DeviceSettingsSection.vue'
          return compileScript(
            parse(readFileSync(new URL(path, import.meta.url), 'utf8')).descriptor,
            { id: 'devices', inlineTemplate: true }
          ).content
        }
      }
    ]
  })
  const node = (type) => ({ type, children: [], props: {}, parent: null })
  const renderer = createRenderer({
    createElement: node,
    createText: (text) => ({ ...node('text'), text }),
    createComment: () => node('comment'),
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
  let app
  const settle = async () => {
    await new Promise((resolve) => setImmediate(resolve))
    await nextTick()
  }
  try {
    const { default: View } = await server.ssrLoadModule('virtual:device-view')
    app = renderer.createApp(View)
    for (const name of [
      'a-button',
      'a-spin',
      'a-skeleton',
      'a-alert',
      'a-input',
      'a-input-password',
      'a-checkbox'
    ]) {
      app.component(name, {
        setup:
          (_, { attrs, slots }) =>
          () =>
            h(name, attrs, slots.default?.())
      })
    }
    const host = node('root')
    app.mount(host)
    await settle()
    const find = (node, predicate) =>
      predicate(node) ? node : node.children.map((child) => find(child, predicate)).find(Boolean)
    const view = {
      find: (predicate) => find(host, predicate),
      settle,
      close: async () => {
        app.unmount()
        await server.close()
        delete globalThis.__devicesTest
      }
    }
    view.button = (text) =>
      view.find(
        (node) =>
          node.type === 'a-button' && node.children.some((child) => child.text?.trim() === text)
      )
    return view
  } catch (error) {
    app?.unmount()
    await server.close()
    delete globalThis.__devicesTest
    throw error
  }
}

const ready = {
  state: 'ready',
  configured: true,
  management_url: 'https://bridge.example/api/auth/oidc/login',
  devices: [{ id: 'one', name: '我的电脑', kind: 'computer', status: 'online', paused: false }]
}

test('设备图标依据平台和设备类型，未知平台不从名称猜测', async () => {
  const cases = [
    ['computer', 'macOS', 'macos.svg', 'macOS · 电脑'],
    ['computer', 'darwin', 'macos.svg', 'macOS · 电脑'],
    ['computer', 'Windows 11', 'windows.svg', 'Windows · 电脑'],
    ['computer', 'win32', 'windows.svg', 'Windows · 电脑'],
    ['computer', 'Android 15', 'android.svg', 'Android · 设备'],
    ['browser', 'Windows', 'chrome.svg', 'Chrome · 浏览器'],
    ['browser', 'Browser', 'chrome.svg', 'Chrome · 浏览器'],
    ['computer', 'Linux', null, 'Linux · 电脑'],
    ['computer', null, null, '电脑']
  ]
  for (const [kind, platform, icon, label] of cases) {
    const view = await render({
      user: { isSuperAdmin: false },
      list: async () => ({
        ...ready,
        devices: [{ ...ready.devices[0], name: 'Chrome Windows Android macOS', kind, platform }]
      })
    })
    try {
      assert.ok(
        view.find((node) => node.text === label),
        `${kind}/${platform}: 平台文字不正确`
      )
      const image = view.find((node) => node.type === 'img')
      if (icon) {
        assert.ok(image?.props.src.startsWith('data:image/svg+xml,'))
        const expected = readFileSync(
          new URL(`../../src/assets/icons/devices/${icon}`, import.meta.url),
          'utf8'
        )
        const normalize = (svg) => svg.replace(/\s/g, '').replaceAll('"', "'")
        assert.equal(
          normalize(decodeURIComponent(image.props.src.split(',')[1])),
          normalize(expected)
        )
        assert.equal(image.props.alt, '')
      } else {
        assert.equal(image, undefined)
        assert.ok(view.find((node) => node.type === 'svg' && node.props['aria-hidden'] === 'true'))
      }
    } finally {
      await view.close()
    }
  }
})

test('普通用户仅查本人设备，失败清除旧列表并允许重试，不读取管理员数据', async () => {
  let list = async () => ready
  const view = await render({
    user: { isSuperAdmin: false },
    list: () => list(),
    getOptions: () => {
      throw new Error('不应读取配置')
    },
    overview: () => {
      throw new Error('不应读取全局数据')
    }
  })
  try {
    assert.ok(view.find((node) => node.text === '我的电脑'))
    assert.ok(view.find((node) => node.text?.includes('默认授权本人全部设备与能力')))
    assert.ok(view.find((node) => node.text?.includes('在 aBridge 中调整范围')))
    assert.equal(view.button('查看全局概况'), undefined)
    assert.equal(view.button('进入 aBridge').props.rel, 'noopener noreferrer')
    list = async () => {
      throw new Error('设备服务不可用')
    }
    await view.button('刷新设备').props.onClick()
    await view.settle()
    assert.equal(
      view.find((node) => node.text === '我的电脑'),
      undefined
    )
    assert.ok(view.find((node) => node.props.role === 'alert'))
    list = async () => ({ ...ready, devices: [] })
    await view.button('重试').props.onClick()
    await view.settle()
    assert.ok(view.find((node) => node.text?.includes('暂无授权给 Yuxi 的设备')))
  } finally {
    await view.close()
  }
})

test('首次读取显示骨架，刷新保留设备和管理入口直到新结果返回', async () => {
  let release
  const view = await render({
    user: { isSuperAdmin: false },
    list: () =>
      new Promise((resolve) => {
        release = resolve
      })
  })
  try {
    assert.ok(view.find((node) => node.props['aria-label'] === '正在加载设备'))
    assert.equal(
      view.find((node) => node.text === '我的电脑'),
      undefined
    )
    release(ready)
    await view.settle()
    assert.equal(
      view.find((node) => node.props['aria-label'] === '正在加载设备'),
      undefined
    )
    const refreshing = view.button('刷新设备').props.onClick()
    await view.settle()
    assert.ok(view.find((node) => node.text === '我的电脑'))
    assert.ok(view.button('进入 aBridge'))
    assert.equal(view.button('刷新设备').props.loading, true)
    release({ ...ready, devices: [{ ...ready.devices[0], name: '更新后的电脑' }] })
    await refreshing
    await view.settle()
    assert.equal(
      view.find((node) => node.text === '我的电脑'),
      undefined
    )
    assert.ok(view.find((node) => node.text === '更新后的电脑'))
  } finally {
    await view.close()
  }
})

test('未开户显示登录引导，危险管理链接不能成为可点击入口', async () => {
  const view = await render({
    user: { isSuperAdmin: false },
    list: async () => ({
      state: 'login_required',
      devices: [],
      management_url: 'javascript:alert(1)'
    })
  })
  try {
    assert.ok(view.find((node) => node.props.message?.includes('使用 Yuxi 账户登录')))
    assert.equal(view.button('进入 aBridge'), undefined)
  } finally {
    await view.close()
  }
})

const optionFixture = () => ({
  key: 'abridge_options',
  name: '设备管理',
  description: 'aBridge 实例连接',
  value: {},
  params: {
    fields: [
      {
        key: 'url',
        label: '服务 URL',
        environment: 'ABRIDGE_URL',
        default: '',
        help: '留空读取 ABRIDGE_URL。请填写不带账号、路径、查询和片段的 HTTP(S) 服务根地址。全局管理请进入 aBridge。'
      }

    ]
  }
})

test('管理员编辑地址、取消丢弃草稿，留空恢复环境配置', async () => {
  const saves = []
  const option = optionFixture()
  const view = await render({
    user: { isSuperAdmin: true },
    list: async () => ready,
    getOptions: async () => ({ options: [option] }),
    updateOption: async (key, value) => {
      saves.push([key, value])
      return { option: { ...option, value } }
    },
    success() {},
    error() {}
  })
  try {
    assert.equal(view.button('查看全局概况'), undefined)
    assert.equal(view.find((node) => node.props['aria-label'] === '服务 URL').props.disabled, '')
    assert.ok(view.find((node) => node.text?.includes('不带账号、路径、查询和片段')))
    await view.button('编辑').props.onClick()
    await view.settle()
    view
      .find((node) => node.props['aria-label'] === '服务 URL')
      .props['onUpdate:value']('https://new.example')
    await view.button('取消').props.onClick()
    await view.settle()
    assert.deepEqual(saves, [])
    await view.button('编辑').props.onClick()
    await view.settle()
    assert.equal(view.find((node) => node.props['aria-label'] === '服务 URL').props.value, '')
    view
      .find((node) => node.props['aria-label'] === '服务 URL')
      .props['onUpdate:value']('https://new.example')
    await view.button('保存').props.onClick()
    await view.settle()
    assert.deepEqual(saves, [['abridge_options', { url: 'https://new.example' }]])
    await view.button('编辑').props.onClick()
    await view.settle()
    assert.equal(view.find((node) => node.type === 'a-checkbox'), undefined)
    view.find((node) => node.props['aria-label'] === '服务 URL').props['onUpdate:value']('  ')
    await view.button('保存').props.onClick()
    await view.settle()
    assert.deepEqual(saves[1], ['abridge_options', { url: '' }])
  } finally {
    await view.close()
  }
})

test('配置更新后的设备回读不会被更早的请求覆盖', async () => {
  let finishOld
  let list = async () => ready
  const option = optionFixture()
  const view = await render({
    user: { isSuperAdmin: true },
    list: () => list(),
    getOptions: async () => ({ options: [option] }),
    updateOption: async () => ({ option }),
    success() {},
    error() {}
  })
  try {
    list = () =>
      new Promise((resolve) => {
        finishOld = resolve
      })
    const old = view.button('刷新设备').props.onClick()
    list = async () => ({ ...ready, devices: [] })
    await view.button('编辑').props.onClick()
    await view.settle()
    await view.button('保存').props.onClick()
    await view.settle()
    finishOld(ready)
    await old
    await view.settle()
    assert.equal(
      view.find((node) => node.text === '我的电脑'),
      undefined
    )
    assert.ok(view.find((node) => node.text?.includes('暂无授权给 Yuxi 的设备')))
  } finally {
    await view.close()
  }
})
