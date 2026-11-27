// 在真实 Vite 页面挂载实际 Ant Button，验证共享 CSS；不创建业务数据。
// playwright-cli --raw -s=<session> run-code --filename=frontend/test/browser/antButtonIcons.js
// prettier-ignore
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = await page.evaluate(() => location.origin)
  await page.goto(`${origin}/dashboard`)
  await page.getByRole('heading', { name: '系统管理' }).waitFor()
  await page.evaluate(async () => {
    const main = await (await fetch('/src/app/main.js')).text()
    const appSource = await (await fetch('/src/app/App.vue')).text()
    const loadingSource = await (await fetch('/node_modules/ant-design-vue/es/button/LoadingIcon.js')).text()
    const moduleUrl = (source, name) => {
      const match = source.match(new RegExp(`from ["']([^"']*/${name}\\.js[^"']*)["']`))
      if (!match) throw new Error(`缺少 Vite 模块：${name}`)
      return match[1]
    }
    const [{ createApp, h, reactive }, { Button, ConfigProvider, theme }, { RefreshCw }, { default: LoadingOutlined }] = await Promise.all([
      import(moduleUrl(main, 'vue')),
      import(moduleUrl(main, 'ant-design-vue')),
      import(moduleUrl(appSource, '@lucide_vue')),
      import(loadingSource.match(/import LoadingOutlined from "([^"]+)"/)[1])
    ])
    const host = document.createElement('div')
    host.id = 'ant-button-icon-fixture'
    Object.assign(host.style, { position: 'fixed', inset: '80px 32px auto', zIndex: '10000', padding: '24px', background: 'var(--gray-0)', display: 'flex', flexWrap: 'wrap', gap: '16px', border: '1px solid var(--gray-200)' })
    document.body.append(host)
    const state = reactive({ loading: false, clicks: 0, dark: document.documentElement.classList.contains('dark') })
    const variants = [
      { id: 'default' }, { id: 'small', size: 'small' }, { id: 'large', size: 'large' },
      { id: 'primary', type: 'primary' }, { id: 'text', type: 'text' },
      { id: 'link', href: '#button-fixture' }, { id: 'disabled', disabled: true },
      { id: 'icon-only', shape: 'circle', iconOnly: true }, { id: 'existing-class', class: 'lucide-icon-btn' },
      { id: 'plain', plain: true }, { id: 'inline-icon', inline: true }, { id: 'ant-icon', native: true }
    ]
    const app = createApp({
      render: () => h(ConfigProvider, { theme: { algorithm: state.dark ? theme.darkAlgorithm : theme.defaultAlgorithm } }, {
        default: () => variants.map(({ id, iconOnly, plain, inline, native, ...props }) => h(Button, {
          ...props, id: `fixture-${id}`, loading: state.loading, 'aria-label': `验证 ${id}`,
          onClick: event => { event.preventDefault(); state.clicks += 1 }
        }, {
          ...(!plain && !inline ? { icon: () => h(native ? LoadingOutlined : RefreshCw, { size: 16, 'aria-hidden': 'true' }) } : {}),
          ...(!iconOnly ? { default: () => inline ? ['刷新', h(RefreshCw, { size: 16, 'aria-hidden': 'true' })] : '刷新' } : {})
        }))
      })
    })
    app.mount(host)
    window.__antButtonFixture = { app, host, state }
  })
  const measure = async () => page.evaluate(() => [...document.querySelectorAll('#ant-button-icon-fixture .ant-btn')].map(button => {
    const icon = button.querySelector(':scope > .lucide, :scope > .anticon, :scope > .ant-btn-loading-icon')
    const text = [...button.children].find(child => child.tagName === 'SPAN' && !child.classList.contains('ant-btn-loading-icon') && !child.classList.contains('anticon'))
    const a = icon?.getBoundingClientRect(), b = text?.getBoundingClientRect()
    return { id: button.id, gap: a && b ? Math.max(b.left - a.right, a.left - b.right) : null,
      centerDifference: a && b ? Math.abs((a.top + a.height / 2) - (b.top + b.height / 2)) : 0,
      width: button.getBoundingClientRect().width, iconWidth: a?.width,
      loadingMargin: button.querySelector('.ant-btn-loading-icon > .anticon') ? getComputedStyle(button.querySelector('.ant-btn-loading-icon > .anticon')).marginInlineEnd : null }
  }))
  try {
    const idle = await measure()
    for (const value of idle.filter(value => value.gap !== null)) {
      check(Math.abs(value.gap - 8) < 1, `${value.id} 图文间距应为 8px，实际 ${value.gap}`)
      check(value.centerDifference < 1, `${value.id} 图文未垂直居中`)
    }
    await page.locator('#fixture-disabled').click({ force: true })
    check(await page.evaluate(() => window.__antButtonFixture.state.clicks) === 0, '禁用按钮不应触发事件')
    await page.locator('#fixture-default').click()
    check(await page.evaluate(() => window.__antButtonFixture.state.clicks) === 1, '普通按钮应保留点击行为')
    await page.locator('#ant-button-icon-fixture').screenshot({ path: '/tmp/yuxi-ant-button-icons-idle.png', animations: 'disabled' })
    await page.evaluate(() => { window.__antButtonFixture.state.loading = true })
    await page.locator('#fixture-default .ant-btn-loading-icon').waitFor()
    // 无原图标的按钮使用 Ant 进出动画，等待布局完成再核对间距。
    await page.waitForFunction(() => {
      const icon = document.querySelector('#fixture-plain .ant-btn-loading-icon')
      return icon && icon.style.width === ''
    })
    const loading = await measure()
    for (const value of loading.filter(value => value.gap !== null)) {
      check(Math.abs(value.gap - 8) < 1, `${value.id} 加载图文间距重复：${value.gap}`)
      check(value.loadingMargin === '0px', `${value.id} 加载图标仍有内部 margin`)
      check(value.centerDifference < 1, `${value.id} 加载图标未居中`)
    }
    const iconOnly = loading.find(value => value.id === 'fixture-icon-only')
    check(iconOnly.gap === null && iconOnly.iconWidth > 0, '纯图标加载状态应居中且没有空白间距')
    await page.locator('#fixture-default').click({ force: true })
    check(await page.evaluate(() => window.__antButtonFixture.state.clicks) === 1, '加载期间不应触发事件')
    await page.locator('#ant-button-icon-fixture').screenshot({ path: '/tmp/yuxi-ant-button-icons-loading.png', animations: 'disabled' })
    await page.evaluate(() => { window.__antButtonFixture.state.loading = false })
    await page.locator('#fixture-default > .lucide').waitFor()
    const restored = await measure()
    for (const value of idle.filter(value => !['fixture-plain', 'fixture-inline-icon'].includes(value.id))) {
      check(Math.abs(restored.find(item => item.id === value.id).width - value.width) < 1, `${value.id} 加载后宽度未恢复`)
    }
    return { variants: idle.length, gap: '8px', centered: true, loading: true, disabled: true, click: true }
  } finally {
    await page.evaluate(() => {
      window.__antButtonFixture.app.unmount()
      window.__antButtonFixture.host.remove()
      delete window.__antButtonFixture
    })
  }
}
