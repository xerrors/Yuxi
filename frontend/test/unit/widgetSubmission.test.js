import assert from 'node:assert/strict'
import test, { before, after, afterEach } from 'node:test'
import { createRenderer, h, reactive, ssrContextKey } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'
import { mountSessionWorkspace } from '../helpers/sessionWorkspace.js'

let server
let rendererComponent
let nodeComponent
const apps = []
const hostNode = () => ({ children: [], parent: null })
const renderer = createRenderer({
  createElement: hostNode, createText: hostNode, createComment: hostNode,
  insert(child, parent) { child.parent = parent; parent.children.push(child) },
  remove(child) { const i = child.parent?.children.indexOf(child) ?? -1; if (i >= 0) child.parent.children.splice(i, 1) },
  setText() {}, setElementText() {}, parentNode: (n) => n.parent, nextSibling: () => null, patchProp() {}
})
before(async () => {
  server = await createTestServer({ server: { middlewareMode: true, hmr: false }, ssr: { noExternal: ['ant-design-vue'] }, plugins: [{
    name: 'widget-modal-boundary', enforce: 'pre',
    resolveId(id) { if (id === 'ant-design-vue') return '\0widget-ant' },
    load(id) { if (id === '\0widget-ant') return 'export const Modal = {}' }
  }] })
  rendererComponent = (await server.ssrLoadModule('/src/shared/ui/WidgetRenderer.vue')).default
  nodeComponent = (await server.ssrLoadModule('/src/shared/ui/WidgetNode.vue')).default
})
after(async () => { await server.close() })
afterEach(() => { for (const app of apps.splice(0)) app.unmount() })

/** 挂载完整组件，以真实响应式状态驱动动作。 */
function setupComponent(component, props, emit = () => {}) {
  let state
  const app = renderer.createApp({ setup() { state = component.setup(props, { expose() {}, emit }); return () => h('div') } })
  app.provide(ssrContextKey, { modules: new Set() })
  app.mount(hostNode())
  apps.push(app)
  return state
}

/** 装配真实会话，隔离发送网络边界。 */
async function setupSender(t) {
  const sent = []
  const { state, agentStore, runtime } = await mountSessionWorkspace(t, undefined, {
    threadId: 'thread', stubChildren: true,
    send: async (thread, draft, options) => { sent.push({ thread, draft, options }); return true }
  })
  t.mock.method(agentStore, 'fetchAgentDetail', async () => ({ can_run: true }))
  await state.refreshSessionAgent('thread', 'agent')
  state.userInput.value = '尚未发送的草稿'
  state.draftFilesByThread.value.thread = [{ id: 'attachment' }]
  return { ...state, sent, agentStore, runtime }
}

test('表单发送独立文本而不带编辑器草稿和附件，普通发送仍带附件', async (t) => {
  const view = await setupSender(t)
  assert.equal(await view.handleSendMessage({ text: '目的地：南京', fromWidget: true }), true)
  assert.equal(view.sent[0].draft.text, '目的地：南京')
  assert.deepEqual(view.sent[0].draft.attachments, [])
  assert.deepEqual(view.sent[0].draft.images, [])
  assert.equal(view.userInput.value, '尚未发送的草稿')
  assert.deepEqual(view.currentPendingThreadAttachments.value, [{ id: 'attachment' }])
  view.sendCooldownActive.value = false
  await view.handleSendMessage()
  assert.equal(view.sent[1].draft.text, '尚未发送的草稿')
  assert.deepEqual(view.sent[1].draft.attachments, [{ id: 'attachment' }])
})

test('等待回答、接收失败和发送禁用不允许表单重复创建输入', async (t) => {
  const view = await setupSender(t)
  const threadState = view.getThreadState('thread')
  for (const setBlocked of [
    (blocked) => { threadState.turnStatus = blocked ? 'requires_action' : null },
    (blocked) => { threadState.readingLoading = blocked },
    (blocked) => { view.sendCooldownActive.value = blocked }
  ]) {
    setBlocked(true)
    assert.equal(view.widgetActionsDisabled.value, true)
    assert.equal(await view.handleSendMessage({ text: '答案', fromWidget: true }), false)
    setBlocked(false)
  }
  view.pendingSends.value.thread = { text: '上一次回答' }
  assert.equal(view.widgetActionsDisabled.value, true)
  assert.equal(await view.handleSendMessage({ text: '答案', fromWidget: true }), false)
  assert.deepEqual(view.sent, [])
})

/** 执行完整反馈组件，只有会话确认接收才显示已发送。 */
function setupFeedback(handler) {
  const props = reactive({ source: '', closed: false, disabled: false, actionHandler: handler })
  const state = setupComponent(rendererComponent, props)
  return { ...state, props, handle: state.handleAction }
}

test('提交中阻止重复操作，接收后显示已发送，错误允许重试', async () => {
  let accept
  const view = setupFeedback(
    () =>
      new Promise((resolve) => {
        accept = resolve
      })
  )
  const pending = view.handle({ type: 'send_message', text: '填写结果' })
  assert.equal(view.submitting.value, true)
  assert.equal(view.feedback.value, '正在发送…')
  assert.equal(view.submitted.value, false)
  await view.handle({ type: 'send_message', text: '重复提交' })
  accept(true)
  await pending
  assert.equal(view.submitted.value, true)
  assert.equal(view.feedback.value, '已发送')
  const rejected = setupFeedback(async () => false)
  await rejected.handle({ type: 'send_message', text: '填写结果' })
  assert.equal(rejected.failed.value, true)
  assert.equal(rejected.submitted.value, false)
  assert.match(rejected.feedback.value, /未能发送/)
  rejected.props.actionHandler = async () => true
  await rejected.handle({ type: 'send_message', text: '填写结果' })
  assert.equal(rejected.feedback.value, '已发送')
})

test('保留旧草稿动作，不把草稿反馈成已发送', async () => {
  const view = setupFeedback(() => undefined)
  await view.handle('续聊草稿')
  assert.equal(view.submitted.value, false)
  assert.equal(view.feedback.value, '已填写到消息草稿，请检查后发送。')
})

test('同一输入经会话重试接收后，原表单消除失败提示并显示已发送', async () => {
  let submission
  const view = setupFeedback(async (action) => {
    submission = action
    return false
  })
  await view.handle({ type: 'send_message', text: '用户回答' })
  assert.equal(view.failed.value, true)
  submission.onAccepted()
  assert.equal(view.submitted.value, true)
  assert.equal(view.failed.value, false)
  assert.equal(view.feedback.value, '已发送')
})

test('合法字段名 reportValidity 遮蔽表单属性时仍使用原生校验，非法输入不发送', () => {
  const actions = []
  const props = {
    disabled: false,
    node: {
      type: 'Form',
      onSubmitAction: { type: 'send_message', payload: { text: '请确认结果。' } },
      children: [{ type: 'Input', name: 'reportValidity', label: '验证结果', required: true }]
    }
  }
  const originalFormElement = globalThis.HTMLFormElement
  const originalFormData = globalThis.FormData
  globalThis.HTMLFormElement = class { reportValidity() { return this.valid } }
  globalThis.FormData = class { constructor(form) { return form.entries } }
  try {
  const state = setupComponent(nodeComponent, reactive(props), (...action) => actions.push(action))
  const submit = state.submitForm
  const form = {
    reportValidity: { name: 'reportValidity' },
    valid: true,
    entries: [['reportValidity', '符合要求']]
  }
  assert.doesNotThrow(() => submit({ currentTarget: form }))
  assert.deepEqual(actions, [
    ['action', { type: 'send_message', text: '请确认结果。\n验证结果：符合要求' }]
  ])
  form.valid = false
  submit({ currentTarget: form })
  assert.equal(actions.length, 1)
  form.valid = true
  form.entries[0][1] = ' '
  submit({ currentTarget: form })
  assert.equal(state.formError.value, '请填写验证结果')
  assert.equal(actions.length, 1)
  } finally { globalThis.HTMLFormElement = originalFormElement; globalThis.FormData = originalFormData }
})


test('智能体不可用时，历史 widget 和直接发送均不创建输入', async (t) => {
  const view = await setupSender(t)
  t.mock.method(view.agentStore, 'fetchAgentDetail', async () => ({ can_run: false }))
  await view.refreshSessionAgent('thread', 'agent')
  assert.equal(view.widgetActionsDisabled.value, true)
  assert.equal(await view.handleSendMessage({ text: '继续', fromWidget: true }), false)
  assert.equal(await view.handleSendMessage(), false)
  assert.deepEqual(view.sent, [])
})
