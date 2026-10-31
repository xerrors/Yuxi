// 在本工作树 Vite 页面运行：playwright-cli -s=chunk-preview run-code --filename=frontend/test/browser/knowledgeChunkBrowser.js
// HTTP 夹具只读，验证实际组件 DOM、光标与滚动；截图写入 /tmp。
async (page) => {
  const check = (value, reason) => {
    if (!value) throw new Error(reason)
  }
  await page.unrouteAll({ behavior: 'wait' })
  const content = Array.from(
    { length: 180 },
    (_, i) => `第 ${i + 1} 行：${'长文本用于验证自动换行后的定位。'.repeat(i < 80 ? 8 : 1)}`
  ).join('\n')
  const chunks = [
    {
      id: 'chunk-alpha',
      chunk_order_index: 0,
      content:
        '<table>\n' +
        Array.from({ length: 80 }, (_, i) => `<tr><td>表格第 ${i + 1} 行</td></tr>`).join('\n') +
        '\n</table>完整末尾标记',
      start_line: 90,
      end_line: 170
    },
    { id: 'chunk-beta', chunk_order_index: 1, content: '完全重叠的分块也必须保留' },
    { id: 'chunk-duplicate', chunk_order_index: 2, content: '完全重叠的分块也必须保留' }
  ]
  let contentFailure = false,
    empty = false,
    readFailure = false,
    delayedRead
  let releaseContent
  let holdContent = false
  let writes = 0
  await page.route('**/api/**', async (route) => {
    const path = route
      .request()
      .url()
      .replace(/^https?:\/\/[^/]+/, '')
      .split('?')[0]
    if (route.request().method() !== 'GET') writes++
    if (path.endsWith('/basic'))
      return route.fulfill({
        json: {
          meta: {
            file_id: path.split('/').at(-2),
            filename: '跨页表格与长内容定位.pdf',
            status: 'indexed',
            has_original_file: false,
            has_parsed_markdown: true
          }
        }
      })
    if (path.endsWith('/content')) {
      if (holdContent)
        await new Promise((resolve) => {
          releaseContent = resolve
        })
      if (contentFailure)
        return route.fulfill({ status: 503, json: { detail: '分块内容读取失败' } })
      return route.fulfill({
        json: { content, lines: empty ? [] : chunks, line_location: { available: true } }
      })
    }
    if (path.endsWith('/markdown')) {
      if (delayedRead) await delayedRead
      if (readFailure) return route.fulfill({ status: 503, json: { detail: '完整文件读取失败' } })
      return route.fulfill({
        json: { content, revision: 'fixture-revision', has_parsed_markdown: true }
      })
    }
    return route.fulfill({ json: {} })
  })
  const origin = await page.evaluate(() => location.origin)
  await page.goto(origin)
  await page.evaluate(async () => {
    const main = await (await fetch('/src/app/main.js')).text()
    const { createApp, h, ref } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
    const { createPinia } = await import(main.match(/from "([^"]*\/pinia\.js[^"']*)"/)[1])
    const { default: Detail } = await import('/src/modules/knowledge/ui/FileDetailModal.vue')
    const { useThemeStore } = await import('/src/shared/model/theme.js')
    const { useUserStore } = await import('/src/modules/identity/model/user.js')
    const { configureApiAuth } = await import('/src/apis/auth_context.js')
    configureApiAuth({ getToken: () => 'chunk-fixture', isAdmin: () => true })
    const root = document.querySelector('#app').__vue_app__
    document.querySelector('#app').style.display = 'none'
    const pinia = createPinia()
    const user = useUserStore(pinia)
    user.token = 'chunk-fixture'
    user.userRole = 'admin'
    const theme = useThemeStore(pinia)
    const open = ref(true),
      editable = ref(true),
      editRequested = ref(false),
      fileId = ref('file-a'),
      embedded = ref(false)
    const host = document.createElement('main')
    host.id = 'chunk-ui'
    document.body.append(host)
    const app = createApp({
      render: () =>
        h(root._context.components.AConfigProvider, { theme: theme.currentTheme }, () =>
          h(Detail, {
            open: open.value,
            editable: editable.value,
            editRequested: editRequested.value,
            embedded: embedded.value,
            kbId: 'kb-fixture',
            fileId: fileId.value,
            'onUpdate:open': (value) => {
              open.value = value
            },
            'onUpdate:editRequested': (value) => {
              editRequested.value = value
            }
          })
        )
    })
    app.use(pinia)
    Object.assign(app._context.components, root._context.components)
    app.mount(host)
    window.chunkUi = { open, editable, editRequested, fileId, theme, embedded }
  })
  const menu = () => page.locator('.knowledge-file-actions:visible').last()
  const trigger = () =>
    page
      .getByRole('button', { name: /文件操作，当前视图为|更多文件操作/ })
      .filter({ visible: true })
  const selectView = async (name) => {
    await trigger().click()
    await menu().getByRole('menuitem', { name, exact: true }).click()
    await page.locator('.knowledge-file-actions:visible').waitFor({ state: 'hidden' })
  }
  const openChunks = async () => {
    await page.locator('.markdown-content').waitFor()
    await selectView('Chunks')
    await page.locator('.chunk-browser').waitFor()
  }
  const reopen = async (edit = false) => {
    await page.evaluate(() => {
      window.chunkUi.open.value = false
    })
    await page.waitForTimeout(350)
    await page.evaluate((edit) => {
      window.chunkUi.editRequested.value = edit
      window.chunkUi.open.value = true
    }, edit)
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await openChunks()
  check(
    (await page.locator('.chunk-list-item').count()) === 3,
    'must retain all raw chunks including fully overlapping chunks'
  )
  check(
    (await page.locator('.chunk-preview-body').innerText()).includes('完整末尾标记'),
    'full chunk tail must be in preview DOM'
  )
  const search = page.getByRole('textbox', { name: '搜索分块' })
  await search.fill('CHUNK-DUPLICATE')
  await page.locator('.chunk-id').filter({ hasText: 'chunk-duplicate' }).waitFor()
  await search.fill('完整末尾标记')
  await page.locator('.chunk-id').filter({ hasText: 'chunk-alpha' }).waitFor()
  await search.fill('不可能匹配')
  check(
    (await page.locator('.chunk-preview-body').count()) === 0,
    'unmatched search must not retain unrelated preview'
  )
  await page.screenshot({ path: '/tmp/yuxi-chunk-search-empty.png' })
  await search.fill('')
  for (const [theme, width] of [
    ['light', 1440],
    ['dark', 1024],
    ['dark', 768]
  ]) {
    await page.setViewportSize({ width, height: 900 })
    await page.evaluate((theme) => window.chunkUi.theme.setTheme(theme), theme)
    await page.locator('.chunk-browser').evaluate(async el => {
      await Promise.allSettled(el.getAnimations({ subtree: true }).map(animation => animation.finished))
    })
    const bounds = await page.locator('.chunk-browser').evaluate((el) => {
      const sidebar = el.querySelector('.chunk-sidebar').getBoundingClientRect()
      const preview = el.querySelector('.chunk-preview').getBoundingClientRect()
      return {
        right: preview.right,
        left: sidebar.left,
        sideWidth: sidebar.width,
        previewWidth: preview.width,
        previewHeight: preview.height,
        overlap: sidebar.right > preview.left
      }
    })
    check(
      bounds.left >= 0 &&
        bounds.right <= width &&
        bounds.previewWidth > 300 &&
        bounds.previewHeight > 400 &&
        !bounds.overlap,
      `split layout fits ${width}: ${JSON.stringify(bounds)}`
    )
    const appearance = await page.locator('.chunk-browser').evaluate(el => {
      const rows = [...el.querySelectorAll('.chunk-list-item')]
      const selected = getComputedStyle(el.querySelector('.chunk-list-item.selected'))
      const edit = el.querySelector('.chunk-edit-button')
      return {
        heights: rows.map(row => row.getBoundingClientRect().height),
        labels: el.querySelectorAll('.chunk-search label, .chunk-count, .line-number').length,
        shadow: selected.boxShadow,
        border: selected.borderLeftWidth,
        background: selected.backgroundColor,
        expectedBackground: getComputedStyle(document.documentElement).getPropertyValue('--gray-100').trim(),
        color: getComputedStyle(edit).color,
        expectedColor: getComputedStyle(document.documentElement).getPropertyValue('--gray-600').trim()
      }
    })
    check(appearance.labels === 0, 'search label, count and preview line numbers must be absent')
    check(new Set(appearance.heights).size === 1, 'all chunk list rows must have equal height')
    check(appearance.shadow === 'none' && appearance.border === '0px', 'selection must use only background')
    const colorsMatch = await page.locator('.chunk-browser').evaluate((el, expected) => {
      const probe = document.createElement('span')
      el.append(probe)
      probe.style.color = expected.expectedColor
      const editMatches = getComputedStyle(probe).color === expected.color
      probe.style.backgroundColor = expected.expectedBackground
      const selectedMatches = getComputedStyle(probe).backgroundColor === expected.background
      probe.remove()
      return editMatches && selectedMatches
    }, appearance)
    check(colorsMatch, 'selection and edit link must use gray tokens')
    await page.screenshot({ path: `/tmp/yuxi-chunks-${theme}-${width}.png` })
    await trigger().click()
    await menu().getByRole('menuitem', { name: '编辑文件', exact: true }).waitFor()
    check(
      (await menu().locator('.ant-dropdown-menu-item-divider').count()) === 2,
      'view, download and edit need separate groups'
    )
    check(
      (await menu().getByRole('menuitem', { name: '编辑文件', exact: true }).count()) === 1,
      'menu uses edit file label'
    )
    await menu().evaluate(async (el) => {
      await Promise.allSettled(el.getAnimations().map((animation) => animation.finished))
    })
    await page.screenshot({ path: `/tmp/yuxi-chunk-menu-${theme}-${width}.png` })
    await trigger().click()
    await page.locator('.knowledge-file-actions:visible').waitFor({ state: 'hidden' })
  }
  const textarea = page.getByRole('textbox', { name: '编辑文件内容' })
  await page.getByRole('button', { name: '编辑文件', exact: true }).click()
  await textarea.waitFor()
  const expectedOffset = content.split('\n').slice(0, 89).join('\n').length + 1
  const position = await textarea.evaluate((el) => ({
    cursor: el.selectionStart,
    scroll: el.scrollTop,
    height: el.clientHeight,
    focus: document.activeElement === el
  }))
  check(
    position.cursor === expectedOffset && position.scroll > 0 && position.focus,
    `chunk edit must locate line 90: ${JSON.stringify(position)}`
  )
  check((await textarea.inputValue()) === content, 'editor must read full authoritative file')
  await page.screenshot({ path: '/tmp/yuxi-chunk-edit-line.png' })
  await page.getByRole('button', { name: '取消编辑' }).click()
  await page.locator('.chunk-browser').waitFor()
  await search.fill('chunk-beta')
  await page.getByRole('button', { name: '编辑文件', exact: true }).click()
  await textarea.waitFor()
  check(
    await textarea.evaluate((el) => el.selectionStart === 0 && el.scrollTop === 0),
    'missing line starts at top'
  )
  await page.getByRole('button', { name: '取消编辑' }).click()
  await reopen(true)
  await textarea.waitFor()
  check(
    await page.evaluate(() => window.chunkUi.editRequested.value === false),
    'opening edit consumes intent'
  )
  await page.getByRole('button', { name: '取消编辑' }).click()
  await reopen()
  await page.locator('.markdown-content').waitFor()
  check((await textarea.count()) === 0, 'same file reopens in read mode after cancel')
  await page.evaluate(() => {
    window.chunkUi.fileId.value = 'file-b'
  })
  await page.locator('.markdown-content').waitFor()
  check((await textarea.count()) === 0, 'different file opens in read mode')
  await selectView('编辑文件')
  await textarea.waitFor()
  await page.getByRole('button', { name: '关闭预览' }).click()
  await reopen()
  await page.locator('.markdown-content').waitFor()
  check((await textarea.count()) === 0, 'close editor then reopen remains read mode')
  await page.evaluate(() => {
    window.chunkUi.editable.value = false
  })
  await openChunks()
  check(
    (await page.getByRole('button', { name: '编辑文件', exact: true }).count()) === 0,
    'readonly chunks have no edit link'
  )
  await trigger().click()
  check(
    (await menu().getByRole('menuitem', { name: '编辑文件', exact: true }).count()) === 0,
    'readonly menu has no edit item'
  )
  await trigger().click()
  await page.evaluate(() => {
    window.chunkUi.editable.value = true
  })
  await page.locator('.markdown-content').waitFor()
  await selectView('Chunks')
  readFailure = true
  await page.getByRole('button', { name: '编辑文件', exact: true }).click()
  await page.getByRole('button', { name: '重试读取' }).waitFor()
  check((await textarea.count()) === 0, 'failed full read cannot edit chunk as fallback')
  readFailure = false
  await page.getByRole('button', { name: '取消编辑' }).click()
  let releaseRead
  delayedRead = new Promise((resolve) => {
    releaseRead = resolve
  })
  await page.getByRole('button', { name: '编辑文件', exact: true }).click()
  await page.getByText('正在读取 Markdown…', { exact: true }).waitFor()
  await page.evaluate(() => {
    window.chunkUi.fileId.value = 'file-c'
  })
  await page.locator('.markdown-content').waitFor()
  releaseRead()
  delayedRead = null
  await page.waitForTimeout(150)
  check((await textarea.count()) === 0, 'late read cannot reopen editor on different file')
  contentFailure = true
  await reopen()
  await selectView('Chunks')
  await page.getByRole('alert').filter({ hasText: '服务器内部错误，请稍后重试' }).waitFor()
  await page.screenshot({ path: '/tmp/yuxi-chunks-error.png' })
  contentFailure = false
  empty = true
  await reopen()
  await selectView('Chunks')
  await page.getByText('暂无分块信息，请先完成文件入库。', { exact: true }).waitFor()
  await page.screenshot({ path: '/tmp/yuxi-chunks-empty.png' })
  empty = false
  holdContent = true
  await reopen()
  await selectView('Chunks')
  await page.getByText('正在加载分块内容...', { exact: true }).waitFor()
  await page.screenshot({ path: '/tmp/yuxi-chunks-loading.png' })
  holdContent = false
  releaseContent()
  await page.locator('.chunk-browser').waitFor()
  check(writes === 0, 'reading, locating and canceling must not mutate server files')
  return {
    passed: true,
    chunks: chunks.length,
    locatedLine: 90,
    writes,
    screenshots: '/tmp/yuxi-chunks-*.png'
  }
}
