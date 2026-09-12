import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createSSRApp, createRenderer, h, nextTick, ref } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createServer } from 'vite'

// 编译真实模板，仅隔离无关网络、剪贴板和来源子组件。
test('反馈按钮只面向完成的非审计助手，模型与复制操作保持', async () => {
  const server = await createServer({
    configFile: false,
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
    plugins: [
      {
        name: 'refs-feedback-fixture',
        resolveId(id) {
          if (id === 'virtual:refs-feedback') return '\0' + id
          if (
            id === '@vueuse/core' ||
            id === 'ant-design-vue' ||
            id === '@lucide/vue' ||
            id.startsWith('@/')
          )
            return '\0stub:' + id
        },
        load(id) {
          if (id === '\0virtual:refs-feedback') {
            const source = readFileSync(
              new URL('../../src/components/RefsComponent.vue', import.meta.url),
              'utf8'
            )
            return compileScript(parse(source).descriptor, {
              id: 'refs-feedback',
              inlineTemplate: true
            }).content
          }
          if (!id.startsWith('\0stub:')) return
          if (id.endsWith('@vueuse/core'))
            return 'export const useClipboard = () => ({ copy: async () => {}, isSupported: true })'
          if (id.endsWith('ant-design-vue'))
            return 'export const message = { info() {}, success() {}, error() {} }'
          if (id.endsWith('@lucide/vue'))
            return 'export const ThumbsUp = {}, ThumbsDown = {}, Bot = {}, Copy = {}, Check = {}, RotateCcw = {}, BookOpen = {}, ChevronDown = {}'
          if (id.endsWith('@/apis'))
            return 'export const calls = []; export const agentApi = { submitMessageFeedback(...args) { calls.push(args); return Promise.resolve() } }'
          if (id.endsWith('@/utils/time')) return 'export const formatChatTime = () => ""'
          if (id.endsWith('@/utils/runTiming'))
            return 'export const formatRunTimingDuration = () => ""; export const getRunTotalLatencyMs = () => null'
          return 'export default { render() { return null } }'
        }
      }
    ]
  })
  try {
    const { default: Refs } = await server.ssrLoadModule('virtual:refs-feedback')
    const cases = [
      ['assistant', 'text', 'finished', true],
      ['received', null, 'finished', true],
      ['assistant', undefined, 'finished', true],
      [undefined, 'text', undefined, true],
      [undefined, null, undefined, true],
      [undefined, 'model_audit', undefined, false],
      ['assistant', 'text', 'loading', false],
      ['assistant', 'model_audit', 'finished', false],
      ['assistant', 'tool_audit', 'finished', false],
      ['user', 'text', 'finished', false],
      ['system', 'text', 'finished', false],
      ['tool', 'text', 'finished', false]
    ]
    for (const [role, messageType, status, allowed] of cases) {
      const app = createSSRApp(Refs, {
        message: {
          id: 1,
          type: 'ai',
          role,
          message_type: messageType,
          status,
          content: 'fixture',
          response_metadata: { model_name: 'fixture-model' }
        },
        showRefs: ['model', 'copy']
      })
      app.config.warnHandler = () => {}
      app.component('a-modal', {
        render() {
          return null
        }
      })
      app.component('a-textarea', {
        render() {
          return null
        }
      })
      const html = await renderToString(app)
      assert.equal(html.includes('title="点赞"'), allowed, `${role}/${messageType}/${status}`)
      assert.equal(html.includes('title="点踩"'), allowed)
      assert.ok(html.includes('fixture-model'))
      assert.ok(html.includes('title="复制"'))
    }
    const makeNode = (type) => ({ type, children: [], props: {}, parent: null })
    const renderer = createRenderer({
      createElement: makeNode,
      createText: (text) => ({ ...makeNode('text'), text }),
      createComment: (text) => ({ ...makeNode('comment'), text }),
      insert(child, parent, anchor = null) {
        child.parent = parent
        const index = anchor ? parent.children.indexOf(anchor) : -1
        if (index < 0) parent.children.push(child)
        else parent.children.splice(index, 0, child)
      },
      remove(child) {
        child.parent.children.splice(child.parent.children.indexOf(child), 1)
      },
      setText(node, text) {
        node.text = text
      },
      setElementText(node, text) {
        node.text = text
        node.children = []
      },
      parentNode: (node) => node.parent,
      nextSibling: (node) => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
      patchProp(node, key, _old, value) {
        node.props[key] = value
      }
    })
    const find = (node, predicate) =>
      predicate(node) ? node : node.children.map((child) => find(child, predicate)).find(Boolean)
    const current = ref({
      id: 1,
      type: 'ai',
      role: 'assistant',
      status: 'loading',
      message_type: 'text'
    })
    const app = renderer.createApp(() =>
      h(Refs, { message: current.value, showRefs: ['model', 'copy'] })
    )
    app.config.warnHandler = () => {}
    app.component('a-modal', {
      render() {
        return h('modal-fixture', this.$attrs)
      }
    })
    app.component('a-textarea', {
      render() {
        return null
      }
    })
    const host = makeNode('root')
    app.mount(host)
    try {
      assert.equal(
        find(host, (node) => node.props.title === '点赞'),
        undefined
      )
      current.value = { ...current.value, status: 'finished' }
      await nextTick()
      assert.ok(find(host, (node) => node.props.title === '点赞'))
      const dislike = find(host, (node) => node.props.title === '点踩')
      dislike.props.onClick()
      await nextTick()
      assert.equal(find(host, (node) => node.type === 'modal-fixture').props.open, true)
      current.value = { ...current.value, message_type: 'model_audit' }
      await nextTick()
      assert.equal(
        find(host, (node) => node.props.title === '点赞'),
        undefined
      )
      // 模态框已打开后目标变成审计行，提交仍须在API前停止。
      await find(host, (node) => node.type === 'modal-fixture').props.onOk()
      await nextTick()
      assert.equal(find(host, (node) => node.type === 'modal-fixture').props.confirmLoading, false)
      const { calls } = await server.ssrLoadModule('@/apis')
      assert.equal(calls.length, 0)
    } finally {
      app.unmount()
    }
  } finally {
    await server.close()
  }
})
