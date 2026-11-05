// 完整普通输入组件与真实 DOM：换行、提及、输入法、剪贴板和运行中上传。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/inputEditing.js
// prettier-ignore
async page => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.evaluate(async () => {
    const source = await (await fetch('/src/app/main.js')).text()
    const vueUrl = source.match(/from "([^"\n]*\/vue\.js[^"']*)"/)[1]
    const { createApp, h, ref, reactive } = await import(vueUrl)
    const { default: Input } = await import('/src/modules/session/ui/MessageInputComponent.vue')
    const draft = ref(''), captured = [], uploads = []
    const flags = reactive({ isLoading: false, disabled: false, enableUploads: true })
    const host = document.createElement('div')
    host.id = 'ordinary-editing-check'
    Object.assign(host.style, { position: 'fixed', inset: '40px', zIndex: 10000, padding: '24px', background: 'var(--gray-0)' })
    document.body.append(host)
    const app = createApp({ render: () => h(Input, { modelValue: draft.value, ...flags,
      mention: { agents: [{ agent_id: 'editing-agent', name: '编辑验证' }] },
      'onUpdate:modelValue': value => { draft.value = value }, onKeydown: event => captured.push(event.key),
      onPasteImages: files => uploads.push({ event: 'paste', names: files.map(file => file.name) }),
      onDropFiles: files => uploads.push({ event: 'drop', names: files.map(file => file.name) }) }) })
    const root = document.querySelector('#app').__vue_app__
    app.use(root.config.globalProperties.$pinia)
    Object.assign(app._context.components, root._context.components)
    app.mount(host)
    window.__ordinaryEditing = { app, host, draft, captured, flags, uploads }
  })
  const editor = page.locator('#ordinary-editing-check .mention-editor')
  const raw = () => page.evaluate(() => window.__ordinaryEditing.draft.value)
  const setDraft = async value => {
    await page.evaluate(value => { window.__ordinaryEditing.draft.value = value }, value)
    await editor.focus()
  }
  try {
    await setDraft('seed')
    await editor.press('ControlOrMeta+End')
    await editor.press('Shift+Enter')
    check(await raw() === 'seed\n', 'Shift+Enter 必须保留末尾换行')
    await editor.press('Backspace')
    check(await raw() === 'seed', '末尾 Backspace 只能删除换行')
    await editor.press('Shift+Enter')
    await editor.press('Shift+Enter')
    await page.keyboard.type('b')
    check(await raw() === 'seed\n\nb', '连续换行后必须在最后一行输入')
    await editor.press('Home')
    await editor.press('Backspace')
    check(await raw() === 'seed\nb', 'Backspace 只删除一个换行')
    await setDraft('@agent:editing-agent\n 尾')
    await editor.press('ControlOrMeta+End')
    await editor.press('Home')
    await editor.press('Backspace')
    check(await raw() === '@agent:editing-agent 尾', '提及后的换行删除必须保留芯片')
    check(await editor.locator('[data-mention-raw]').count() === 1, '提及芯片被换行删除破坏')
    await editor.evaluate(node => {
      const range = document.createRange()
      range.setStartAfter(node.querySelector('[data-mention-raw]')); range.collapse(true)
      const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range)
    })
    await editor.press('Backspace')
    check(await raw() === ' 尾', '提及必须整块删除并保留后文')
    await setDraft('@skill:research\n选区')
    await editor.press('ControlOrMeta+a')
    await editor.press('Delete')
    check(await raw() === '' && await editor.locator('[data-mention-raw]').count() === 0, '选区删除不得残留提及或换行')
    await setDraft('输入法正文')
    const composing = await editor.evaluate(node => {
      const state = window.__ordinaryEditing, before = state.captured.length
      node.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }))
      for (const key of ['Enter', 'Backspace']) node.dispatchEvent(new KeyboardEvent('keydown', { key, shiftKey: true, isComposing: true, bubbles: true, cancelable: true }))
      node.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 229, bubbles: true, cancelable: true }))
      const result = { value: state.draft.value, emitted: state.captured.length - before }
      node.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true }))
      return result
    })
    check(composing.value === '输入法正文' && composing.emitted === 0, '输入法确认不能编辑或发送')
    await setDraft('复制正文\n')
    await editor.press('ControlOrMeta+a')
    await editor.press('ControlOrMeta+c')
    check(await page.evaluate(() => navigator.clipboard.readText()) === '复制正文\n', '尾占位不能进入剪贴板')
    await setDraft('')
    await editor.press('ControlOrMeta+v')
    check(await raw() === '复制正文\n', '原生粘贴必须保留换行')
    for (const mode of ['running', 'uploads-off', 'disabled']) {
      await page.evaluate(mode => {
        const { flags, uploads } = window.__ordinaryEditing
        Object.assign(flags, { isLoading: true, enableUploads: mode !== 'uploads-off', disabled: mode === 'disabled' })
        uploads.length = 0
      }, mode)
      const uploads = await editor.evaluate(node => {
        const clipboard = new DataTransfer()
        clipboard.items.add(new File(['image'], 'draft.png', { type: 'image/png' }))
        node.dispatchEvent(new ClipboardEvent('paste', { clipboardData: clipboard, bubbles: true, cancelable: true }))
        const transfer = new DataTransfer()
        transfer.items.add(new File(['attachment'], 'draft.txt', { type: 'text/plain' }))
        node.dispatchEvent(new DragEvent('drop', { dataTransfer: transfer, bubbles: true, cancelable: true }))
        return window.__ordinaryEditing.uploads
      })
      check(JSON.stringify(uploads) === JSON.stringify(mode === 'running'
        ? [{ event: 'paste', names: ['draft.png'] }, { event: 'drop', names: ['draft.txt'] }] : []), '上传边界错误：' + mode)
    }
    await page.evaluate(() => { window.__ordinaryEditing.flags.disabled = false })
    await setDraft('普通输入保留正文\n第二行')
    await page.locator('#ordinary-editing-check').screenshot({ path: '/tmp/yuxi-ordinary-editor.png' })
    return { trailingNewline: true, consecutiveNewlines: true, mentionDeletion: true,
      selectionDeletion: true, ime: true, nativeClipboard: true, runningUploads: true, uploadGuards: true }
  } finally {
    await page.context().clearPermissions()
    await page.evaluate(() => { const state = window.__ordinaryEditing; state.app.unmount(); state.host.remove(); delete window.__ordinaryEditing })
  }
}
