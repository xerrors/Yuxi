// 在开发页面执行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/toolResultImages.js
// 使用独立页面和合成 HTTP 响应，不依赖登录或修改业务数据。
// eslint-disable-next-line no-unused-vars -- playwright-cli 将整个文件作为函数表达式调用。
async function toolResultImages(page) {
  /** 用实际 DOM 和网络请求核对图片读取与文本布局。 */
  const check = (condition, message) => {
    if (!condition) throw Error(message)
  }
  const origin = await page.evaluate(() => location.origin)
  const protectedPath = '/api/v1/agents/sessions/tool-result-fixture/artifacts/image.png'
  const externalUrl = 'https://tool-result-image.example/image.png'
  const token = 'tool-result-image-fixture'
  const requests = []
  const failures = []
  const viewport = page.viewportSize()
  let releaseLate
  const late = new Promise((resolve) => {
    releaseLate = resolve
  })
  const imageRoute = async (route) => {
    const request = route.request()
    requests.push({ url: request.url(), authorization: request.headers().authorization })
    if (request.url().startsWith(externalUrl)) {
      await route.fulfill({
        path: 'frontend/test/browser/fixtures/imgA.png',
        contentType: 'image/png'
      })
      return
    }
    if (request.headers().authorization !== `Bearer ${token}`) {
      await route.fulfill({ status: 403, json: { detail: '没有图片读取权限' } })
      return
    }
    const mode = request.url().match(/[?&]mode=([^&]+)/)?.[1]
    if (mode === 'late') await late
    if (mode === 'denied')
      await route.fulfill({ status: 403, json: { detail: '没有图片读取权限' } })
    else if (mode === 'text') await route.fulfill({ json: { supported: false } })
    else
      await route.fulfill({
        path: 'frontend/test/browser/fixtures/imgA.png',
        contentType: 'image/png'
      })
  }
  const documentRoute = (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: `<!doctype html><html><head><meta charset="utf-8"><title>工具结果图片回归</title></head><body><div id="probe"></div>
      <script type="module">
        const source = await (await fetch('/src/modules/session/ui/tools/BaseToolCall.vue')).text();
        const imports = [...source.matchAll(/from ["']([^"']+)["']/g)].map(match => match[1]);
        const { createApp, h, reactive } = await import(imports.find(url => url.includes('/vue.js')));
        const { createPinia } = await import(imports.find(url => url.includes('/pinia.js')));
        const { default: ToolResultContent } = await import('/src/modules/session/ui/tools/ToolResultContent.vue');
        const { default: ImageTool } = await import('/src/modules/session/ui/tools/renderers/ImageTool.vue');
        const { default: BaseToolCall } = await import('/src/modules/session/ui/tools/BaseToolCall.vue');
        const { configureApiAuth } = await import('/src/apis/auth_context.js');
        await import('/src/assets/css/main.css');
        configureApiAuth({ getToken: () => '${token}' });
        const urls = [], revoked = [];
        const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
        URL.createObjectURL = blob => { const url = create(blob); urls.push(url); return url; };
        URL.revokeObjectURL = url => { revoked.push(url); revoke(url); };
        const state = reactive({ content: [{ type: 'image', url: '${protectedPath}' }], show: true });
        const app = createApp({ render: () => h('main', { style: 'padding:24px;max-width:100%' }, [
          h('h2', '工具结果图片与长文本'),
          h('section', { id: 'protected-image', style: 'margin:16px 0' }, state.show ? [h(ToolResultContent, { content: state.content })] : []),
          h('section', { id: 'long-text', style: 'width:300px;max-width:100%' }, [h(ImageTool, {
            toolCall: { name: 'text_to_img_qwen_image', result: '生成说明 https://example.com/' + 'long-token'.repeat(60) + '\\n多行说明\\n'.repeat(40) },
            defaultExpanded: true
          })]),
          h('section', { id: 'error-result', style: 'width:300px;max-width:100%;margin-top:16px' }, [h(BaseToolCall, {
            toolCall: { name: 'read_file', status: 'error', error_message: '图片读取失败' }, defaultExpanded: true
          })])
        ]) }).use(createPinia());
        app.mount('#probe');
        window.toolResultProbe = { state, app, urls, revoked };
      </script></body></html>`
    })
  await page.route(`${origin}${protectedPath}*`, imageRoute)
  await page.route(`${externalUrl}*`, imageRoute)
  await page.route(`${origin}/__tool_result_images_test`, documentRoute)
  try {
    await page.goto(`${origin}/__tool_result_images_test`)
    await page.waitForFunction(() => window.toolResultProbe)
    await page.waitForFunction(
      () =>
        document.querySelector('#protected-image img')?.naturalWidth > 0 ||
        document.querySelector('#protected-image [role=alert]')
    )
    const authenticated = requests.some((request) => request.authorization === `Bearer ${token}`)
    if (!authenticated) failures.push('受保护图片请求未携带认证头')
    const layout = await page.locator('#long-text pre').evaluate((element) => ({
      width: element.clientWidth,
      scrollWidth: element.scrollWidth,
      height: element.clientHeight,
      scrollHeight: element.scrollHeight,
      overflowY: getComputedStyle(element).overflowY
    }))
    if (layout.scrollWidth > layout.width + 1)
      failures.push(`长文本横向溢出：${layout.scrollWidth}/${layout.width}`)
    check(failures.length === 0, failures.join('；'))
    check(layout.scrollHeight > layout.height && layout.overflowY === 'auto', '长文本缺少有界滚动')
    const errorColor = await page.locator('#error-result pre').evaluate((element) => {
      const expected = document.createElement('span')
      expected.style.color = 'var(--color-error-700)'
      element.appendChild(expected)
      const matches = getComputedStyle(expected).color === getComputedStyle(element).color
      expected.remove()
      return matches
    })
    check(errorColor, '公共结果样式覆盖了错误颜色')
    const loaded = () =>
      page.waitForFunction(() => document.querySelector('#protected-image img')?.naturalWidth > 0)
    await loaded()
    const thumbnail = await page.locator('#protected-image img').getAttribute('src')
    check(thumbnail.startsWith('blob:'), '受保护图片未转换成对象 URL')
    await page.locator('#protected-image button').click()
    await page.waitForFunction(() =>
      document.querySelector('[role=dialog]')?.contains(document.activeElement)
    )
    check(
      (await page.locator('.chat-image-viewer-image').getAttribute('src')) === thumbnail,
      '放大预览未复用已授权字节'
    )
    const requestCount = requests.length
    for (let index = 0; index < 12; index++) {
      await page.evaluate(() => {
        const probe = window.toolResultProbe
        probe.state.content = JSON.parse(JSON.stringify(probe.state.content))
      })
    }
    check(requests.length === requestCount, '相同工具结果重建时重复请求图片')
    check((await page.locator('#protected-image img').getAttribute('src')) === thumbnail,
      '流式更新替换了已加载图片')
    check(await page.locator('.chat-image-viewer').isVisible(), '流式更新关闭了图片预览')
    await page.keyboard.press('Escape')
    await page.locator('.chat-image-viewer').waitFor({ state: 'hidden' })

    await page.evaluate((url) => {
      window.toolResultProbe.state.content = [{ type: 'image', url }]
    }, externalUrl)
    await loaded()
    check(
      requests.find((request) => request.url.startsWith(externalUrl))?.authorization === undefined,
      '外源图片泄漏认证头'
    )
    check(
      await page.evaluate((url) => window.toolResultProbe.revoked.includes(url), thumbnail),
      '切换结果未释放对象 URL'
    )

    for (const mode of ['denied', 'text']) {
      await page.evaluate((url) => {
        window.toolResultProbe.state.content = [{ type: 'image', url }]
      }, `${protectedPath}?mode=${mode}`)
      await page.locator('#protected-image [role=alert]').waitFor()
      check(
        (await page.locator('#protected-image img').count()) === 0,
        `${mode} 响应仍进入图片 DOM`
      )
    }

    await page.evaluate(() => {
      window.toolResultProbe.state.content = [
        { type: 'image', url: 'data:image/png;base64,bm90LWFuLWltYWdl' }
      ]
    })
    await page.locator('#protected-image [role=alert]').waitFor()
    check(
      (await page.locator('#protected-image img').count()) === 0,
      '图片解码失败后仍保留损坏图片'
    )

    const lateRequest = page.waitForRequest((request) => request.url().includes('mode=late'))
    await page.evaluate((url) => {
      window.toolResultProbe.state.content = [{ type: 'image', url }]
    }, `${protectedPath}?mode=late`)
    await page.locator('#protected-image [role=status]').waitFor()
    await lateRequest
    const pendingCount = requests.length
    for (let index = 0; index < 12; index++) {
      await page.evaluate(() => {
        const probe = window.toolResultProbe
        probe.state.content = JSON.parse(JSON.stringify(probe.state.content))
      })
    }
    check(requests.length === pendingCount, '图片加载中相同结果更新堆积请求')
    const skeleton = page.locator('#protected-image .tool-result-image-skeleton')
    check(await skeleton.getAttribute('aria-label') === '图片加载中', '图片骨架缺少加载说明')
    const bounds = await skeleton.boundingBox()
    check(bounds.width > 0 && bounds.height > 0 && bounds.width <= 400, '骨架没有预留有界图片区域')
    await skeleton.screenshot({ path: '/tmp/yuxi-tool-image-skeleton.png' })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    check(await skeleton.evaluate(node => getComputedStyle(node, '::after').animationName) === 'none', '减少动态效果时骨架仍播放动画')
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.evaluate((url) => {
      window.toolResultProbe.state.content = [{ type: 'image', url }]
    }, externalUrl)
    await loaded()
    const lateResponse = page.waitForResponse((response) => response.url().includes('mode=late'))
    releaseLate()
    await lateResponse
    check(
      (await page.locator('#protected-image img').getAttribute('src')) === externalUrl,
      '迟到响应覆盖了新结果'
    )

    await page.evaluate((url) => {
      window.toolResultProbe.state.content[0].url = url
    }, protectedPath)
    await loaded()
    check(
      (await page.locator('#protected-image img').getAttribute('src')).startsWith('blob:'),
      '原地更新图片来源未重新读取'
    )
    for (const dark of [false, true]) {
      await page.evaluate((dark) => document.documentElement.classList.toggle('dark', dark), dark)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 900 })
        const fits = await page
          .locator('#long-text pre')
          .evaluate((element) => element.scrollWidth <= element.clientWidth + 1)
        check(fits, `${dark ? '深色' : '浅色'} ${width}px 文本横向溢出`)
        await page.screenshot({
          path: `/tmp/yuxi-tool-result-images-${dark ? 'dark' : 'light'}-${width}.png`
        })
      }
    }
    await page.evaluate(() => {
      window.toolResultProbe.state.show = false
    })
    await page.waitForFunction(() =>
      window.toolResultProbe.urls.every((url) => window.toolResultProbe.revoked.includes(url))
    )
    return { authenticated, layout, blobUrlsReleased: true, themes: 2, widths: [1440, 1024, 768] }
  } finally {
    releaseLate()
    await page.evaluate(() => {
      window.toolResultProbe?.app.unmount()
    })
    await page.unroute(`${origin}${protectedPath}*`, imageRoute)
    await page.unroute(`${externalUrl}*`, imageRoute)
    await page.unroute(`${origin}/__tool_result_images_test`, documentRoute)
    if (viewport) await page.setViewportSize(viewport)
  }
}
