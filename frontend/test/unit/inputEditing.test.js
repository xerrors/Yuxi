import assert from 'node:assert/strict'
import test from 'node:test'
import { createRenderer, reactive, ssrContextKey } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

/** 加载完整输入组件，只替代浏览器事件与渲染宿主。 */
async function mountInput(t, initialProps = {}) {
  const previousDocument = globalThis.document
  const previousWindow = globalThis.window
  const server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  let app
  t.after(async () => {
    app?.unmount()
    await server.close()
    if (previousDocument === undefined) delete globalThis.document
    else globalThis.document = previousDocument
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
  })
  const { default: component } = await server.ssrLoadModule('/src/modules/session/ui/MessageInputComponent.vue')
  globalThis.document = { addEventListener() {}, removeEventListener() {} }
  globalThis.window = { getSelection: () => null }
  const props = reactive({ modelValue: '正文', enableUploads: true, disabled: false, isLoading: false, ...initialProps })
  const emitted = []
  let state
  const renderer = createRenderer({
    createComment: () => ({}), insert() {}, remove() {}, parentNode: () => null, nextSibling: () => null
  })
  app = renderer.createApp({ setup() {
    state = component.setup(props, { expose() {}, emit: (...args) => emitted.push(args) })
    return () => null
  } })
  app.provide(ssrContextKey, {})
  app.mount({})
  return { state, props, emitted }
}

test('输入法组合期间按键不能发送、换行或移动提及选项，独立浏览器信号也生效', async (t) => {
  const { state, emitted } = await mountInput(t)
  for (const source of ['component', 'event', 'keycode']) {
    state.isComposing.value = source === 'component'
    state.mentionPopupVisible.value = true
    state.mentionSelectedIndex.value = 0
    for (const key of ['ArrowDown', 'Enter', 'Backspace']) {
      let prevented = false
      state.handleKeyPress({ key, shiftKey: true,
        isComposing: source === 'event', keyCode: source === 'keycode' ? 229 : 0,
        preventDefault() { prevented = true }
      })
      assert.equal(prevented, false, source + '应保留输入法原生按键')
      assert.equal(state.mentionSelectedIndex.value, 0, source + '不能切换提及项')
      assert.deepEqual(emitted, [], source + '不能提交或改写正文')
    }
  }
})

test('运行中可以粘贴图片与拖拽附件，关闭上传能力或禁用输入时拒绝', async (t) => {
  const { state, props, emitted } = await mountInput(t, { isLoading: true })
  const image = { name: 'draft.png', type: 'image/png' }
  const attachment = { name: 'draft.txt', type: 'text/plain' }
  const paste = () => state.handlePaste({
    clipboardData: { files: [image], getData: () => '' }, preventDefault() {}
  })
  const drop = () => state.handleDrop({
    dataTransfer: { types: ['Files'], files: [attachment] }, preventDefault() {}, stopPropagation() {}
  })
  paste()
  drop()
  assert.deepEqual(emitted, [['paste-images', [image]], ['drop-files', [attachment]]])
  emitted.length = 0
  for (const guard of ['enableUploads', 'disabled']) {
    props.enableUploads = guard !== 'enableUploads'
    props.disabled = guard === 'disabled'
    paste()
    drop()
    assert.deepEqual(emitted, [], guard + '不能接收草稿文件')
  }
})
