// 真实工具入口检查默认图标与自定义插槽的颜色；合成回执不触发工具执行。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/toolIconInteraction.js
// prettier-ignore
async originalPage => {
  const context = await originalPage.context().browser().newContext()
  const evidence = []
  try {
    const page = await context.newPage()
    await page.goto(originalPage.url().split('/').slice(0, 3).join('/'))
    await page.waitForFunction(() => Boolean(document.querySelector('#app').__vue_app__))
    await page.evaluate(async () => {
      const source = await (await fetch('/src/app/main.js')).text()
      const { createApp, h } = await import(source.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
      const { default: Tool } = await import('/src/modules/session/ui/tools/ToolCallRenderer.vue')
      const { default: Base } = await import('/src/modules/session/ui/tools/BaseToolCall.vue')
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      const root = document.querySelector('#app').__vue_app__
      const theme = useThemeStore(root.config.globalProperties.$pinia)
      const calls = {
        text: { name: 'read_file', args: { file_path: '/workspace/notes.txt' }, result: '文件内容' },
        image: { name: 'read_file', args: { file_path: '/workspace/image.png' }, result: [{ type: 'image', base64: 'cGljdHVyZQ==', mime_type: 'image/png' }] },
        generated: { name: 'text_to_img_qwen_image', args: {}, result: '图片生成完成' },
        error: { name: 'read_file', args: {}, status: 'error', error_message: '读取失败' },
        incomplete: { name: 'read_file', args: {}, status: 'incomplete' },
        waiting: { name: 'read_file', args: {}, status: 'waiting' },
        running: { name: 'read_file', args: {} }
      }
      const host = document.createElement('main')
      host.id = 'tool-icon-interaction'
      host.style = 'position:fixed;inset:0;z-index:10000;padding:32px;background:var(--gray-0);color:var(--gray-800);overflow:auto'
      document.body.append(host)
      const app = createApp({ render: () => h('div', [
        h('h2', { style: 'margin-bottom:24px' }, '工具图标交互一致性'),
        ...['timeline', 'card'].map(appearance => h('section', { style: 'margin-bottom:24px' }, [
          h('h3', { style: 'margin-bottom:12px' }, appearance === 'timeline' ? '时间线' : '卡片'),
          ...Object.entries(calls).map(([name, call]) => h('div', { id: `${appearance}-${name}`, style: 'margin-bottom:8px' }, [h(Tool, { toolCall: call, appearance })])),
          h('div', { id: `${appearance}-summary` }, [h(Base, { toolCall: calls.text, appearance, summaryOnly: true })])
        ]))
      ]) })
      app.use(root.config.globalProperties.$pinia)
      app.use(root.config.globalProperties.$router)
      Object.assign(app._context.components, root._context.components)
      app.mount(host)
      window.__iconTheme = theme
    })
    await page.locator('#timeline-image .note').getByText('查看图片', { exact: true }).waitFor()
    const sample = async id => page.locator(`#${id} .tool-header > span svg`).first().evaluate(svg => {
      // 旋转中的加载图标外接矩形会变化，布局尺寸应读取 CSS 盒。
      const style = getComputedStyle(svg)
      return { color: style.color, width: parseFloat(style.width), height: parseFloat(style.height) }
    })
    const token = name => page.locator('#tool-icon-interaction').evaluate((host, name) => {
      const probe = document.createElement('span')
      probe.style.color = `var(${name})`
      host.append(probe)
      const color = getComputedStyle(probe).color
      probe.remove()
      return color
    }, name)
    for (const dark of [false, true]) {
      await page.evaluate(dark => { window.__iconTheme.themeMode = dark ? 'dark' : 'light' }, dark)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 1100 })
        for (const appearance of ['timeline', 'card']) {
          const expectedNormal = await token(appearance === 'timeline' ? '--gray-600' : '--color-success-500')
          const expectedInteractive = await token(appearance === 'timeline' ? '--gray-700' : '--color-success-500')
          const errorColor = await token('--color-error-500')
          for (const name of ['text', 'image', 'generated', 'error', 'incomplete', 'waiting', 'running', 'summary']) {
            const id = `${appearance}-${name}`
            const header = page.locator(`#${id} .tool-header`)
            await page.locator('h2').hover()
            await page.evaluate(() => document.activeElement?.blur())
            const normal = await sample(id)
            const failed = ['error', 'incomplete'].includes(name)
            const pending = ['waiting', 'running'].includes(name)
            const expected = failed ? errorColor : pending && appearance === 'card' ? await token('--color-info-500') : expectedNormal
            if (normal.color !== expected) throw new Error(`${id} 默认颜色 ${normal.color}，预期 ${expected}`)
            await header.hover()
            const hovered = await sample(id)
            const activeColor = failed || pending || name === 'summary' ? expected : expectedInteractive
            if (hovered.color !== activeColor) throw new Error(`${id} hover 颜色 ${hovered.color}，预期 ${activeColor}`)
            if (normal.width !== 15 || normal.height !== 15 || hovered.width !== 15 || hovered.height !== 15) throw new Error(`${id} 图标尺寸改变`)
            if (name !== 'summary') {
              await page.locator('h2').hover()
              await page.keyboard.press('Tab')
              await header.focus()
              if (!await header.evaluate(node => node.matches(':focus-visible'))) throw new Error(`${id} 未进入键盘焦点态`)
              const focused = await sample(id)
              if (focused.color !== activeColor) throw new Error(`${id} focus 颜色 ${focused.color}，预期 ${activeColor}`)
            } else if (await header.getAttribute('role') === 'button') throw new Error('摘要出现展开角色')
          }
        }
        evidence.push({ dark, width, appearances: 2, cases: 8, hover: true, keyboardFocus: true })
      }
      await page.evaluate(() => document.activeElement?.blur())
      await page.locator('h2').hover()
      await page.screenshot({ path: `/tmp/yuxi-tool-icons-${dark ? 'dark' : 'light'}.png` })
      await page.locator('#timeline-image .tool-header').hover()
      await page.screenshot({ path: `/tmp/yuxi-tool-icons-${dark ? 'dark' : 'light'}-hover.png` })
    }
    return evidence
  } finally { await context.close() }
}
