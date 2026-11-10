// playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/knowledgeFileSorting.js
// 挂载真实文件表，HTTP fixture 按请求排序并分页。
async originalPage => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = originalPage.url().split('/').slice(0, 3).join('/')
  const context = await originalPage.context().browser().newContext()
  const requests = []
  const files = [
    ['alpha.txt', 40], ['beta.txt', 5], ['gamma.txt', 30], ['tiny.txt', 1], ['zeta.txt', 10]
  ].map(([filename, token_count]) => ({ file_id: filename, filename, token_count, status: 'indexed', is_folder: false }))
  try {
    const page = await context.newPage()
    await page.route('**/api/**', route => {
      const url = route.request().url()
      if (url.includes('/documents?')) {
        requests.push(url)
        const pageNumber = Number(url.match(/[?&]page=(\d+)/)?.[1] || 1)
        const sorted = [...files]
        if (url.includes('sort_by=content_amount')) {
          const direction = url.includes('sort_order=descend') ? -1 : 1
          sorted.sort((a, b) => direction * (a.token_count - b.token_count))
        }
        return route.fulfill({ json: { items: sorted.slice((pageNumber - 1) * 2, pageNumber * 2), page: pageNumber, page_size: 2, total: 5, has_more: pageNumber < 3 } })
      }
      return route.fulfill({ json: { data: [], files: [], skills: [], first_run: false, status: 'ok' } })
    })
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(origin)
    await page.waitForFunction(() => Boolean(document.querySelector('#app').__vue_app__))
    await page.evaluate(async () => {
      const source = await (await fetch('/src/app/main.js')).text()
      const { createApp, h } = await import(source.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
      const { createPinia } = await import(source.match(/from "([^"]*\/pinia\.js[^"']*)"/)[1])
      const { default: FileTable } = await import('/src/modules/knowledge/ui/FileTable.vue')
      const { useKnowledgeBaseStore } = await import('/src/modules/knowledge/model/knowledgeBase.js')
      const { useUserStore } = await import('/src/modules/identity/model/user.js')
      const { configureApiAuth } = await import('/src/apis/auth_context.js')
      const root = document.querySelector('#app').__vue_app__
      const pinia = createPinia()
      const user = useUserStore(pinia)
      user.userRole = 'admin'
      user.token = 'sort-fixture-token'
      configureApiAuth({ getToken: () => user.token, isAdmin: () => true })
      const store = useKnowledgeBaseStore(pinia)
      store.knowledgeBase = { kb_id: 'sort-fixture', name: '排序验证' }
      const host = document.createElement('div')
      document.querySelector('#app').style.display = 'none'
      document.body.append(host)
      const app = createApp({ render: () => h(FileTable) })
      app.use(pinia)
      Object.assign(app._context.components, root._context.components)
      app.mount(host)
      store.kbId = 'sort-fixture'
    })
    const table = page.locator('.knowledge-file-browser')
    await table.getByText('alpha.txt', { exact: true }).waitFor()
    check(!requests.at(-1).includes('sort_by='), '默认列表请求包含排序')
    await table.locator('button[title="多选"]').click()
    await table.locator('tbody input[type=checkbox]').first().check()
    await table.getByRole('columnheader', { name: '内容量' }).click()
    await table.getByText('tiny.txt', { exact: true }).waitFor()
    check(requests.at(-1).includes('sort_by=content_amount') && requests.at(-1).includes('sort_order=ascend') && requests.at(-1).includes('page=1'), '表头未请求全量升序第一页')
    check(await table.locator('tbody input[type=checkbox]:checked').count() === 0, '排序后保留了旧页选择')
    await table.locator('.ant-pagination-item-2').click()
    await table.getByText('zeta.txt', { exact: true }).waitFor()
    check(requests.at(-1).includes('page=2') && requests.at(-1).includes('sort_by=content_amount') && requests.at(-1).includes('sort_order=ascend'), '翻页没有保留排序')
    return { defaultOrder: true, ascendingAcrossPages: true, selectionCleared: true, paginationKeepsSort: true }
  } finally {
    await context.close()
  }
}
