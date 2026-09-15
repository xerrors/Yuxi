import assert from 'node:assert/strict'
import test from 'node:test'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createServer } from 'vite'

test('PageShoulder 保留默认搜索、过滤器和自定义搜索插槽契约', async () => {
  const server = await createServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  try {
    const { default: PageShoulder } = await server.ssrLoadModule(
      '/src/components/shared/PageShoulder.vue'
    )
    const render = async (slots) => {
      const app = createSSRApp({
        render: () => h(PageShoulder, { search: '合同', searchPlaceholder: '搜索工具' }, slots)
      })
      app.component('a-input', {
        props: ['value', 'placeholder'],
        render() {
          return h('input', { value: this.value, placeholder: this.placeholder })
        }
      })
      return renderToString(app)
    }
    const standard = await render({
      filters: () => h('select', { 'aria-label': '分类' }),
      actions: () => h('button', '刷新')
    })
    assert.match(standard, /value="合同"/)
    assert.match(standard, /placeholder="搜索工具"/)
    assert.match(standard, /aria-label="分类"/)
    assert.match(standard, /page-shoulder-right/)
    assert.match(standard, /<button>刷新<\/button>/)
    const custom = await render({ search: () => h('button', '自定义搜索') })
    assert.match(custom, /自定义搜索/)
    assert.doesNotMatch(custom, /<input/)
    assert.doesNotMatch(custom, /page-shoulder-right/)
  } finally {
    await server.close()
  }
})
