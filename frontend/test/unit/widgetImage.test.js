import assert from 'node:assert/strict'
import test, { before, after, beforeEach, afterEach } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createRenderer, nextTick, reactive, ref, ssrContextKey } from 'vue'
import { parseWidget } from '../../src/shared/lib/chatWidgets.js'
import { createTestServer } from '../helpers/createTestServer.js'

let server, component
let originalCreate, originalRevoke
beforeEach(() => { originalCreate = URL.createObjectURL; originalRevoke = URL.revokeObjectURL })
afterEach(() => { URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke })
before(async () => {
  server = await createTestServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  ;({ default: component } = await server.ssrLoadModule('/src/shared/ui/WidgetImage.vue'))
})
after(async () => { await server?.close() })
const renderer = createRenderer({
  createElement: () => ({ children: [] }), createText: () => ({}), createComment: () => ({}),
  insert: (child, parent) => parent.children.push(child), remove() {}, setText() {}, setElementText() {},
  parentNode: () => null, nextSibling: () => null, patchProp() {}
})

/** 执行真实 Image setup，以延迟响应检查图片资源生命周期。 */
function setupImage(t, source, loader, sandbox = true, sourceLoader = null) {
  const props = reactive({ node: { [sandbox ? 'path' : 'src']: source, alt: '图片' }, previewable: true })
  const imageLoader = ref(loader)
  const sourceImageLoader = ref(sourceLoader)
  const urls = []
  const revoked = []
  const previews = []
  const objectURL = {
    createObjectURL: () => { const url = `blob:image-${urls.length}`; urls.push(url); return url },
    revokeObjectURL: url => revoked.push(url)
  }
  URL.createObjectURL = objectURL.createObjectURL
  URL.revokeObjectURL = objectURL.revokeObjectURL
  let view
  const app = renderer.createApp({ setup() {
    view = component.setup(props, { expose() {}, emit: (name, value) => previews.push([name, value]) })
    return () => null
  } })
  app.provide(ssrContextKey, {})
  app.provide('widgetImageLoader', imageLoader)
  app.provide('widgetSourceImageLoader', sourceImageLoader)
  app.mount({ children: [] })
  const stop = () => app.unmount()
  t.after(stop)
  return { props, imageLoader, view, urls, revoked, previews, stop }
}
const imageResponse = () => ({ blob: async () => new Blob(['image'], { type: 'image/png' }) })
const path = '/home/user/workspace/projects/example/图片 #1.png'

test('Image 协议接受含空格的沙盒路径，路径跳转和反斜杠拒绝', () => {
  const widget = path => JSON.stringify({ type: 'Card', children: [{ type: 'Image', path, alt: '图片' }] })
  assert.equal(parseWidget(widget(path)).children[0].path, path)
  assert.equal(parseWidget(widget('/workspace/custom/image.png')).children[0].path, '/workspace/custom/image.png')
  assert.equal(parseWidget(widget('/home/user/skills/example/image.png')).children[0].path, '/home/user/skills/example/image.png')
  for (const invalid of ['/home/user/workspace/../secret.png', '/home/user/workspace/a\\b.png', '/home/user/workspace/a\0b'])
    assert.throws(() => parseWidget(widget(invalid)), /沙盒虚拟绝对路径/)
  for (const invalid of ['file:///tmp/a.png', 'data:image/png;base64,a', '//example.com/a.png', 'https://user:pass@example.com/a.png'])
    assert.throws(() => parseWidget(JSON.stringify({ type: 'Card', children: [{ type: 'Image', src: invalid, alt: '图片' }] })), /HTTPS/)
  assert.throws(() => parseWidget(JSON.stringify({ type: 'Card', children: [{ type: 'Image', src: '/a.png', path, alt: '图片' }] })), /之一/)
  assert.throws(() => parseWidget(widget('relative.png')), /绝对路径/)
  assert.throws(() => parseWidget(widget('//outside/image.png')), /绝对路径/)
})

test('Image 解包单个 Markdown 链接后仍校验目标地址', () => {
  const image = src => JSON.stringify({ type: 'Image', src, alt: '商品图片' })
  for (const url of ['https://example.com/a.png?width=120', '/files/product.png']) {
    assert.equal(parseWidget(image(`[商品 图片](${url})`)).src, url)
  }
  for (const url of ['javascript:alert', 'http://example.com/a.png', '//example.com/a.png',
    'https://user:pass@example.com/a.png', '/\\example.com/a.png']) {
    assert.throws(() => parseWidget(image(`[商品](${url})`)), /HTTPS/)
  }
  for (const invalid of ['[图](/a.png) [图](/b.png)', '[图[片](/a.png)']) {
    assert.throws(() => parseWidget(image(invalid)), /HTTPS/)
  }
})

test('沙盒图片只经读取能力取字节，卸载释放 Blob URL', async t => {
  const requests = []
  const { view, urls, revoked, stop } = setupImage(t, path, async src => { requests.push(src); return imageResponse() })
  await setImmediate()
  assert.deepEqual(requests, [path])
  assert.equal(view.src.value, urls[0])
  assert.equal(view.failed.value, false)
  stop()
  assert.deepEqual(revoked, urls)
})

test('切换会话后迟到响应不覆盖新图片，换来源时释放已加载资源', async t => {
  let resolveOld
  const { props, imageLoader, view, urls, revoked } = setupImage(t, path, () => new Promise(resolve => { resolveOld = resolve }))
  imageLoader.value = async () => imageResponse()
  await nextTick()
  await setImmediate()
  const current = view.src.value
  resolveOld(imageResponse())
  await setImmediate()
  assert.equal(view.src.value, current)
  assert.equal(urls.length, 1)
  view.loaded.value = true
  props.node.path = undefined
  props.node.src = 'https://example.com/new.png'
  await nextTick()
  assert.deepEqual(revoked, [current])
  assert.equal(view.loaded.value, false)
  assert.equal(view.src.value, 'https://example.com/new.png')
})

test('无会话、读取拒绝或非图片响应显示失败且不请求沙盒浏览器地址', async t => {
  for (const loader of [null, async () => { throw new Error('403') }, async () => new Response('{"supported":false}')]) {
    const { view, urls } = setupImage(t, path, loader)
    await setImmediate()
    assert.equal(view.src.value, '')
    assert.equal(view.failed.value, true)
    assert.deepEqual(urls, [])
  }
})

test('普通 HTTPS 与同源图片直接渲染，不调用沙盒读取能力', async t => {
  const { props, view } = setupImage(t, 'https://example.com/a.png', () => { throw new Error('不应调用') }, false)
  assert.equal(view.src.value, 'https://example.com/a.png')
  props.node.src = '/images/a.png'
  await nextTick()
  assert.equal(view.src.value, '/images/a.png')
  assert.equal(view.failed.value, false)
})

test('图片可以独立展示，可选短语与来源协议在根节点校验', () => {
  const node = { type: 'Image', path, alt: '图片' }
  assert.deepEqual(parseWidget(JSON.stringify(node)), node)
  assert.deepEqual(parseWidget(JSON.stringify({ ...node, caption: '另一个方案' })), { ...node, caption: '另一个方案' })
  for (const caption of ['', '  ', 123, null])
    assert.throws(() => parseWidget(JSON.stringify({ ...node, caption })), /caption/)
})

test('图片组只包含图片，可混用 URL 与沙盒来源，空组和非图片成员拒绝', () => {
  const children = [
    { type: 'Image', path, alt: '生成的图片' },
    { type: 'Image', src: 'https://example.com/image.png', alt: '网络图片', caption: '参考图' }
  ]
  assert.deepEqual(parseWidget(JSON.stringify({ type: 'ImageGroup', children })).children, children)
  assert.throws(() => parseWidget(JSON.stringify({ type: 'ImageGroup', children: [] })), /至少需要一张图片/)
  assert.throws(() => parseWidget(JSON.stringify({ type: 'ImageGroup', children: [{ type: 'Text', value: '文字' }] })), /只能包含 Image/)
  assert.throws(() => parseWidget(JSON.stringify({ type: 'ImageGroup', children: {} })), /children 数组/)
})


test('图片预览只对已加载且可交互的图片开放，键盘与指针共用入口', async t => {
  const { props, view, previews } = setupImage(t, path, async () => imageResponse())
  await setImmediate()
  const event = { currentTarget: { focus() {} } }
  view.openPreview(event)
  assert.deepEqual(previews, [])
  view.loaded.value = true
  view.openPreview(event)
  assert.deepEqual(previews, [['preview', undefined]])
  view.failed.value = true
  view.openPreview(event)
  assert.equal(previews.length, 1)
  view.failed.value = false
  props.previewable = false
  let prevented = false
  view.handlePreviewKeydown({ ...event, key: ' ', preventDefault() { prevented = true } })
  assert.equal(prevented, false)
  assert.equal(previews.length, 1)
  props.previewable = true
  view.handlePreviewKeydown({ ...event, key: 'Enter', preventDefault() { prevented = true } })
  assert.equal(prevented, true)
  assert.deepEqual(previews[1], ['preview', undefined])
})


test('普通 src 图片不因沙盒会话读取能力切换而重置加载状态', async t => {
  const { imageLoader, view } = setupImage(t, 'https://example.com/a.png', null, false)
  view.loaded.value = true
  imageLoader.value = async () => imageResponse()
  await nextTick()
  assert.equal(view.src.value, 'https://example.com/a.png')
  assert.equal(view.loaded.value, true)
  assert.equal(view.failed.value, false)
})

test('受保护 src 由 source 读取能力提供图片字节，拒绝非图片且不泄漏原地址', async t => {
  const requests = []
  const protectedSrc = '/api/v1/agents/sessions/example/artifacts/image.png'
  const { view, urls, revoked, stop } = setupImage(t, protectedSrc, null, false, async src => {
    requests.push(src)
    return imageResponse()
  })
  await setImmediate()
  assert.deepEqual(requests, [protectedSrc])
  assert.equal(view.src.value, urls[0])
  stop()
  assert.deepEqual(revoked, urls)
  const denied = setupImage(t, protectedSrc, null, false, async () => new Response('{"supported":false}'))
  await setImmediate()
  assert.equal(denied.view.src.value, '')
  assert.equal(denied.view.failed.value, true)
})
