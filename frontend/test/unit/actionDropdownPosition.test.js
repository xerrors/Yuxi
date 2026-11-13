import assert from 'node:assert/strict'
import test from 'node:test'
import { createSSRApp, h, reactive } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createTestServer } from '../helpers/createTestServer.js'

test('共享弹层默认自适应，输入区向上约束使用实际可见空间', async () => {
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  try {
    const { default: Dropdown } = await server.ssrLoadModule(
      '/src/shared/ui/ActionDropdown.vue'
    )
    assert.equal(Dropdown.props.upward.default, false)
    assert.equal(Dropdown.props.align.default, 'start')
    const props = reactive({ upward: false, open: false, width: 300 })
    let component
    await renderToString(
      createSSRApp({
        setup() {
          component = Dropdown.setup(props, { expose() {}, emit() {} })
          return () => h('div')
        }
      })
    )

    component.viewportWidth.value = 390
    component.left.value = 320
    assert.equal(component.panelWidth.value, 300)
    assert.equal(component.horizontalOffset.value, -242, '右侧触发器的弹层仍在可视区内')
    props.align = 'end'
    component.right.value = 360
    assert.equal(component.horizontalOffset.value, 0, '末端对齐不应再按触发器左侧偏移')
    component.right.value = 40
    assert.equal(component.horizontalOffset.value, 272, '末端对齐在左侧边缘仍留出安全间距')
    component.viewportLeft.value = 40
    assert.equal(component.horizontalOffset.value, 312, '钳制使用实际可视区起点')
    props.align = 'start'
    component.viewportLeft.value = 0
    component.viewportWidth.value = 280
    assert.equal(component.panelWidth.value, 256, '窄屏菜单缩到可视区宽度')
    component.top.value = 100
    component.bottom.value = 130
    component.viewportHeight.value = 600
    assert.equal(component.panelHeight.value, 420, '顶部表单默认使用下方空间')
    props.upward = true
    assert.equal(component.panelHeight.value, 88, '输入区不能借用下方空间来向下翻转')
    component.viewportTop.value = 40
    assert.equal(component.panelHeight.value, 48, '可视区域移动后不越过可见顶部')
    component.top.value = 800
    assert.equal(component.panelHeight.value, 420, '足够空间时仍限制长列表高度')
    component.top.value = 20
    assert.equal(component.panelHeight.value, 0, '不可见触发器不得产生负高度')
  } finally {
    await server.close()
  }
})
