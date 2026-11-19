// 在 Vite 页面运行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/widgetCompositionLayout.js
// 挂载真实 Markdown/Vue 组件，只检查 DOM，不创建会话或发送消息。
async (page) => {
  const originalViewport = page.viewportSize()
  const originalTheme = await page.evaluate(() => localStorage.getItem('theme'))
  const failures = []
  const measurements = []
  try {
    await page.evaluate(async () => {
      const module = await (await fetch('/src/shared/model/theme.js')).text()
      const dependency = name => module.match(new RegExp(`from "([^"]*/${name}\\.js[^"]*)"`))[1]
      const { createApp, h, reactive } = await import(dependency('vue'))
      const { createPinia } = await import(dependency('pinia'))
      const { default: MarkdownPreview } = await import('/src/modules/workspace/ui/MarkdownPreview.vue')
      const { parseWidget } = await import('/src/shared/lib/chatWidgets.js')
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      const { default: AntDesign } = await import(module.match(/from "([^"]*ant-design-vue\.js[^"]*)"/)[1])
      const text = value => ({ type: 'Text', value })
      const card = (children, size) => ({ type: 'Card', ...(size ? { size } : {}), children })
      const list = { type: 'ListView', children: [{ type: 'ListViewItem', children: [text('任务说明'), { type: 'Badge', label: '完成', color: 'success' }] }] }
      const form = { type: 'Form', onSubmitAction: { type: 'send_message', payload: { text: '继续' } }, children: [{ type: 'Input', name: 'city', label: '城市' }, { type: 'Button', label: '提交', submit: true }] }
      const cases = {
        chart: card([{ type: 'Chart', label: '每周订单', xAxis: 'week', series: [{ type: 'bar', dataKey: 'orders', label: '订单' }], data: [{ week: '周一', orders: 12 }, { week: '周二', orders: 20 }] }]),
        nested: card([card([text('默认子卡片')]), card([text('小卡片')], 'sm'), card([text('大卡片')], 'lg'), list], 'full'),
        rootList: list,
        rootCard: card([text('独立卡片')]),
        rootForm: card([form]),
        columns: card([{ type: 'Row', children: Array.from({ length: 4 }, (_, i) => ({ type: 'Col', flex: 1, children: [{ type: 'Title', value: `任务 ${i + 1}` }, text('需要完整阅读的执行结果说明')] })) }], 'full'),
        fixed: card([{ type: 'Row', children: [{ type: 'Col', flex: 0, children: [{ type: 'Badge', label: '执行完成', color: 'success' }] }, { type: 'Col', flex: 1, children: [text('这是任务实际执行结果的说明。')] }] }], 'full'),
        form: card([text('外层说明'), card([{ type: 'Row', children: [{ type: 'Col', children: [form] }] }])]),
        formRoot: { ...form, children: [card([form.children[0]]), list, form.children[1]] },
        density: card([
          { type: 'Title', value: '卡片标题' },
          { type: 'ListView', children: [
            { type: 'ListViewItem', children: [{ type: 'Title', value: '普通列表标题' }, text('必要说明')] },
            { type: 'ListViewItem', onClickAction: { type: 'compose', text: '继续' }, children: [{ type: 'Title', size: 'sm', value: '可点击列表标题' }] },
            { type: 'ListViewItem', children: [{ type: 'Title', size: 'lg', value: '强调标题' }] }
          ] }
        ], 'full')
      }
      for (const node of Object.values(cases)) parseWidget(JSON.stringify(node))
      const state = reactive({ key: 'nested', width: 800 })
      const host = document.createElement('div')
      host.id = 'widget-composition-probe'
      host.style.cssText = 'position:fixed;inset:0;z-index:9999;background:var(--color-bg-container);padding:24px;overflow:auto;box-sizing:border-box'
      document.body.append(host)
      const app = createApp({ render: () => h('main', { style: { width: `${state.width}px`, maxWidth: '100%', margin: '0 auto' } }, [h(MarkdownPreview, { key: state.key, widgets: true, content: '```widget\n' + JSON.stringify(cases[state.key]) + '\n```' })]) })
      const pinia = createPinia()
      app.use(pinia)
      app.use(AntDesign)
      app.mount(host)
      window.widgetCompositionProbe = { app, host, state, cases, theme: useThemeStore(pinia) }
    })
    for (const theme of ['light', 'dark']) {
      await page.evaluate(value => window.widgetCompositionProbe.theme.setTheme(value), theme)
      for (const viewport of [1440, 1024, 768]) {
        await page.setViewportSize({ width: viewport, height: 1000 })
        for (const key of ['nested', 'rootList', 'rootCard', 'rootForm', 'columns', 'fixed', 'form', 'formRoot', 'density']) {
          await page.evaluate(async key => {
            const p = window.widgetCompositionProbe
            p.state.width = key === 'columns' ? 400 : 800
            p.state.key = key
            const module = await (await fetch('/src/shared/model/theme.js')).text()
            const { nextTick } = await import(module.match(/from "([^"]*\/vue\.js[^"]*)"/)[1])
            await nextTick()
          }, key)
          await page.waitForFunction(() => document.querySelector('#widget-composition-probe .yuxi-widget .widget-node'))
          const result = await page.evaluate(key => {
            const root = document.querySelector('#widget-composition-probe main')
            const check = (valid, message) => { if (!valid) errors.push(message) }
            const close = (a, b) => Math.abs(a - b) < 1
            const rect = el => el.getBoundingClientRect()
            const css = el => getComputedStyle(el)
            const errors = []
            const cards = [...root.querySelectorAll('.widget-card')]
            const outer = cards[0]
            const available = rect(root).width
            const contentWidth = el => el.clientWidth - parseFloat(css(el).paddingLeft) - parseFloat(css(el).paddingRight)
            if (key === 'nested') {
              check(close(rect(outer).width, available), '全宽卡片没有铺满')
              check(close(rect(cards[1]).width, contentWidth(outer)), '默认子卡片仍限制宽度')
              check(close(rect(cards[2]).width, Math.min(360, contentWidth(outer))), '显式小卡片尺寸丢失')
              check(close(rect(cards[3]).width, Math.min(560, contentWidth(outer))), '显式大卡片尺寸丢失')
              const list = root.querySelector('.widget-listview')
              check(close(rect(list).width, contentWidth(outer)), '嵌套列表没有铺满')
              check(css(list).borderTopWidth === '0px' && css(list).boxShadow === 'none' && css(list).padding === '0px', '嵌套列表保留重复外框或间距')
            } else if (key === 'rootList' || key === 'rootCard') {
              const container = root.querySelector('.widget-node')
              check(close(rect(container).width, Math.min(440, available)), '独立容器默认宽度改变')
              check(css(container).borderTopWidth === '1px', '独立容器丢失外框')
            } else if (key === 'columns') {
              const cols = [...root.querySelectorAll('.widget-col')]
              check(rect(cols[3]).top > rect(cols[0]).top, '等分列空间不足仍不换行')
              check(cols.every(el => rect(el).width >= 100), '列宽低于换行基准')
            } else if (key === 'fixed') {
              const cols = [...root.querySelectorAll('.widget-col')]
              const badge = root.querySelector('.widget-badge')
              check(rect(cols[0]).width > 0, '非扩展列被压成零宽')
              check(rect(badge).right <= rect(cols[0]).right + 1 && rect(badge).right <= rect(cols[1]).left, '状态标签与正文重叠')
            } else if (key === 'form') {
              check(close(rect(outer).width, Math.min(440, available)), '内层表单改变外层宽度')
              check(css(outer).padding === '20px' && css(outer).gap === '12px', '内层表单改变外层间距')
              check(css(cards[1]).padding === '20px 24px' && css(cards[1]).gap === '8px', '中间布局节点阻断表单卡片样式')
            } else if (key === 'rootForm') {
              check(close(rect(outer).width, available), '独立默认表单卡片没有铺满')
            } else if (key === 'formRoot') {
              check(close(rect(outer).width, available), '根 Form 中的默认卡片仍限制宽度')
              const list = root.querySelector('.widget-listview')
              check(close(rect(list).width, available), '根 Form 中的列表没有铺满')
              check(css(list).borderTopWidth === '0px' && css(list).boxShadow === 'none', '根 Form 中的列表保留重复外框')
            } else if (key === 'density') {
              const titles = [...root.querySelectorAll('.widget-title')]
              check(titles.slice(1).every(el => close(rect(el).left, rect(titles[0]).left)), '嵌套列表叠加横向 padding，标题未对齐')
              check(css(titles[0]).fontSize === '16px' && css(titles[1]).fontSize === '16px', '默认标题仍然过大')
              check(css(titles[2]).fontSize === '14px' && css(titles[3]).fontSize === '18px', '显式标题档位未按紧凑比例呈现')
            }
            check(root.scrollWidth <= root.clientWidth + 1, '组件超出正文宽度')
            return { key, available, errors }
          }, key)
          measurements.push({ theme, viewport, ...result })
          failures.push(...result.errors.map(error => `${theme}/${viewport}/${key}: ${error}`))
          if (viewport === 1024 && ['nested', 'columns', 'fixed', 'form', 'density'].includes(key)) {
            await page.locator('#widget-composition-probe main').screenshot({ path: `/tmp/yuxi-widget-composition-${key}-${theme}.png` })
          }
        }
      }
    }
    if (failures.length) throw new Error(failures.join('\n'))
    await page.evaluate(() => { window.widgetCompositionProbe.state.key = 'chart' })
    const chart = page.locator('#widget-composition-probe figure[aria-label="每周订单"]')
    await chart.locator('.widget-chart-canvas svg path').first().waitFor({ state: 'attached' })
    const legend = chart.getByRole('button', { name: '订单', exact: true })
    await legend.click()
    if (await legend.getAttribute('aria-pressed') !== 'false') throw new Error('首次加载的图例无法隐藏系列')
    await legend.click()
    if (await legend.getAttribute('aria-pressed') !== 'true') throw new Error('图例无法恢复系列')
    await chart.locator('summary').click()
    await chart.getByRole('cell', { name: '20', exact: true }).waitFor()
    await chart.screenshot({ path: '/tmp/yuxi-chat-entry-chart.png' })
    return { passed: measurements.length, measurements, chart: true }
  } finally {
    await page.evaluate(theme => {
      const probe = window.widgetCompositionProbe
      if (!probe) return
      probe.theme.setTheme(theme || 'light')
      probe.app.unmount()
      probe.host.remove()
      delete window.widgetCompositionProbe
      if (theme === null) localStorage.removeItem('theme')
    }, originalTheme)
    if (originalViewport) await page.setViewportSize(originalViewport)
  }
}
