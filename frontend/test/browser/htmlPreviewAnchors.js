// 在 Vite 开发页面运行：playwright-cli run-code --filename=frontend/test/browser/htmlPreviewAnchors.js
async (page) => {
  await page.evaluate(async () => {
    const theme = await (await fetch('/src/shared/model/theme.js')).text()
    const dependency = name => theme.match(new RegExp('from "([^"]*/' + name + '\\.js[^"]*)"'))[1]
    const { createApp, reactive } = await import(dependency('vue'))
    const { createPinia } = await import(dependency('pinia'))
    const { default: Preview } = await import('/src/modules/session/ui/workspace/AgentFilePreview.vue')
    const fallbackBase = document.baseURI
    const host = document.createElement('div')
    host.style.cssText = 'position:fixed;inset:0;z-index:100;background:white'
    document.body.append(host)
    const file = reactive({
      previewType: 'html',
      content: `<style>a[href^="#"] { color: rgb(1, 2, 3) }</style><nav><a href="#ch2">02 产品拆解</a><a href=" &#9;#ch3 &#10;" onclick="event.stopPropagation()">03 后续计划</a>
        <a href="#ch4" id="capture-tab">04 验收结论</a><a href="#ch5" onclick="event.stopImmediatePropagation()">05 下一步</a>
        <a href="#ch6" class="window-capture">06 附注</a><a href="#ch7" id="cancelled-capture" class="window-capture">07 作者取消</a>
        <a href="&nbsp;#appendix">附录链接</a>
        <a href="#custom" id="document-tab">文档自带标签页</a></nav>
        <p id="custom" hidden>文档自带标签页已打开</p>
        <script>
          window.addEventListener('DOMContentLoaded', () => {
            window.addEventListener('click', event => {
              if (!event.target.closest('a[href^="#"]#document-tab')) return
              event.stopImmediatePropagation()
              event.preventDefault()
              const panel = document.getElementById('custom')
              panel.dataset.resource = new URL('./report.json', document.baseURI).href
              panel.hidden = false
            })
            document.addEventListener('click', event => {
              if (event.target.closest('#capture-tab')) event.stopPropagation()
            }, true)
            window.addEventListener('click', event => {
              if (event.target.closest('.window-capture')) event.stopPropagation()
            }, true)
            window.addEventListener('click', event => {
              if (event.target.closest('a[href^="#"]#cancelled-capture')) {
                event.preventDefault()
                event.target.dataset.authorCancelled = 'yes'
              }
            }, true)
            document.documentElement.dataset.ready = 'yes'
          })
        </script>
        <div style="height:200vh"></div>
        <h2 id="ch2">产品拆解</h2><p style="height:100vh">章节正文</p>
        <h2 id="ch3">后续计划</h2><p style="height:100vh">章节正文</p>
        <h2 id="ch4">验收结论</h2><p style="height:100vh">章节正文</p>
        <h2 id="ch5">下一步</h2><p style="height:100vh">章节正文</p>
        <h2 id="ch6">附注</h2><p style="height:100vh">章节正文</p>
        <h2 id="ch7">作者取消</h2><p style="height:100vh">章节正文</p>`
    })
    const app = createApp(Preview, {
      filePath: '/saved_artifacts/report.html',
      file
    })
    app.use(createPinia()).mount(host)
    window.anchorPreview = { file, fallbackBase, close() { app.unmount(); host.remove() } }
  })
  try {
    const originalUrl = page.url()
    const frame = page.frameLocator('iframe.html-preview')
    const relativeHref = await frame.getByRole('link', { name: '附录链接' }).getAttribute('href')
    if (relativeHref !== '\u00a0#appendix') throw new Error('预览改写了含非 ASCII 空格的相对链接')
    await frame.locator('html[data-ready="yes"]').waitFor({ state: 'attached' })
    const customTab = frame.getByRole('link', { name: '文档自带标签页' })
    const custom = await customTab.evaluate(link => ({
      href: link.getAttribute('href'),
      color: getComputedStyle(link).color
    }))
    if (custom.href !== '#custom' || custom.color !== 'rgb(1, 2, 3)') {
      throw new Error('预览改变了文档的锚点属性或选择器')
    }
    await customTab.press('Enter')
    const customPanel = frame.getByText('文档自带标签页已打开')
    await customPanel.waitFor({ timeout: 3000 })
    const expectedResource = await page.evaluate(() => new URL('./report.json', window.anchorPreview.fallbackBase).href)
    if (await customPanel.getAttribute('data-resource') !== expectedResource) {
      throw new Error('预览改变了文档处理点击时的相对资源解析')
    }
    if (await customTab.evaluate(link => link.ownerDocument.location.hash)) {
      throw new Error('预览接管了已被文档取消的默认导航')
    }
    for (const [name, id] of [['02 产品拆解', 'ch2'], ['03 后续计划', 'ch3'], ['04 验收结论', 'ch4'], ['05 下一步', 'ch5'], ['06 附注', 'ch6']]) {
      const link = frame.getByRole('link', { name })
      if (id === 'ch3') await link.press('Enter')
      else await link.click()
      const target = frame.locator(`#${id}:target`)
      await target.waitFor({ timeout: 3000 })
      const visible = await target.evaluate(el => {
        const rect = el.getBoundingClientRect()
        return window.scrollY > 0 && rect.top >= -1 && rect.bottom <= innerHeight
      })
      if (!visible || page.url() !== originalUrl) throw new Error(`${name}未在预览内滚动到目标章节`)
    }
    const cancelled = frame.getByRole('link', { name: '07 作者取消' })
    await cancelled.click()
    if (await cancelled.getAttribute('data-author-cancelled') !== 'yes') throw new Error('作者的原 href 委托处理器未执行')
    if (await frame.locator('#ch6:target').count() !== 1) throw new Error('捕获监听器阻止默认动作后仍发生了导航')
    await frame.locator('#cancelled-capture[href="#ch7"]').waitFor()
    await page.evaluate(() => {
      window.anchorPreview.file.content = '<base href="/reports/index.html"><a href="#ch2">报告链接</a>'
    })
    const link = frame.getByText('报告链接')
    const path = await link.evaluate(el => new URL(el.href).pathname + el.hash)
    if (path !== '/reports/index.html#ch2') {
      throw new Error('预览覆盖了文档显式指定的链接基址')
    }
    await page.evaluate(() => {
      window.anchorPreview.file.content = `<meta http-equiv="Content-Security-Policy" content="script-src 'none'; base-uri 'none'">
        <script>document.documentElement.dataset.authorScriptRan = 'yes'</script>
        <a href="#restricted">禁用脚本的章节链接</a><div style="height:200vh"></div>
        <h2 id="restricted">受限文档章节</h2><div style="height:100vh"></div>`
    })
    await frame.getByRole('link', { name: '禁用脚本的章节链接' }).press('Enter')
    await frame.locator('#restricted:target').waitFor({ timeout: 3000 })
    if (await frame.locator('html').getAttribute('data-author-script-ran')) {
      throw new Error('预览脚本改变了文档对自身脚本的 CSP 限制')
    }
  } finally {
    await page.evaluate(() => window.anchorPreview.close())
  }
}
