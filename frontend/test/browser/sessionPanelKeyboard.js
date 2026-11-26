// playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/sessionPanelKeyboard.js
// prettier-ignore
async page => {
  const origin = page.url().split('/').slice(0, 3).join('/')
  const previewReads = []
  const referenceReads = []
  await page.route('**/api/**', route => {
    const reference = route.request().url().match(/\/threads\/([^/]+)\/turns\/shared-turn\/references$/)
    if (reference) {
      const threadId = reference[1]
      referenceReads.push(threadId)
      return route.fulfill({ json: { available: true, sources: [{ kind: 'web', title: threadId === 'root' ? '主会话引用' : '成员引用', url: 'https://example.org/' }] } })
    }
    const preview = route.request().url().match(/\/sessions\/([^/]+)\/artifacts\/report\.txt\?preview=true$/)
    if (!preview) return route.fulfill({ json: { entries: [], data: [] } })
    previewReads.push(preview[1])
    return route.fulfill({ contentType: 'text/plain', body: preview[1] === 'root' ? '主会话正文' : '成员报告正文' })
  })
  await page.goto(origin + '/test/browser/fixtures/threadSession.html')
  await page.waitForFunction(() => window.readingFixture)
  await page.evaluate(async () => {
    const fixture = window.readingFixture
    for (const view of fixture.views) view.active = false
    fixture.user.token = 'session-panel-fixture'
    const source = await (await fetch('/src/app/main.js')).text()
    const { computed, createApp, h, ref } = await import(source.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
    const [{ default: AgentPanel }, { useSessionPanel }] = await Promise.all([
      import('/src/modules/session/ui/workspace/AgentPanel.vue'),
      import('/src/modules/session/model/useSessionPanel.js')
    ])
    const host = document.createElement('div')
    host.style = 'position:fixed;inset:16px;z-index:1000;display:flex;justify-content:flex-end;background:var(--gray-0);'
    document.body.append(host)
    const app = createApp({
      setup() {
        const width = ref(host.clientWidth)
        const observer = new ResizeObserver(() => { width.value = host.clientWidth })
        observer.observe(host)
        const panel = useSessionPanel({ threadId: ref('root'), containerWidth: width, statePanelOpen: ref(false) })
        panel.showSection({ key: 'member-one', type: 'session', title: '资料整理', avatarSeed: 'member-one' })
        panel.showSection({ key: 'member-two', type: 'session', title: '数据核对', avatarSeed: 'member-two' })
        const currentTitle = computed(() => panel.activeAgentPanelSection.value?.title)
        return () => [h('div', { style: 'margin:16px auto 0 16px;display:flex;gap:8px;align-items:start' }, [
          h('button', { type: 'button', onClick: () => panel.openPanelPreview({ name: '主会话报告', path: '/report.txt' }) }, '打开主会话报告'),
          h('button', { type: 'button', onClick: () => panel.openPanelPreview({ name: '协作报告', path: '/report.txt' }, 'member-two') }, '打开协作报告'),
          ...[['root', '主会话来源'], ['member-two', '成员来源']].map(([threadId, title]) => h('button', { type: 'button', onClick: () => panel.showSection({ key: `sources:${threadId}`, type: 'turn-sources', threadId, turnId: 'shared-turn', title }) }, `打开${title}`))
        ]), h('div', {
          ref: panel.panelWrapperRef, 'data-host-width': width.value,
          style: { width: panel.filePanelWidthStyle.value, borderLeft: '1px solid var(--gray-200)' }
        }, [h(AgentPanel, {
          visible: true, sections: panel.agentPanelSections.value, activeSectionKey: panel.agentPanelActiveSectionKey.value,
          panelWidthPercentage: panel.panelWidthPercentage.value,
          activePreview: panel.activeAgentPanelPreview.value,
          previewCache: panel.agentPanelPreviewCache,
          onActivateSection: panel.activateAgentPanelSection, onCloseSection: panel.closeAgentPanelSection,
          onResizeBy: panel.resizePanelBy
        }, { 'session-content': () => h('p', { style: 'padding: 12px 20px' }, currentTitle.value) })])]
      }
    })
    app.use(fixture.app.config.globalProperties.$pinia)
    Object.assign(app._context.components, fixture.app._context.components)
    app.mount(host)
  })
  const tabs = page.getByRole('tablist', { name: '侧边栏内容' })
  const checkFocused = async (name) => {
    const active = await tabs.getByRole('tab', { name }).evaluate(el =>
      document.activeElement === el && el.getAttribute('aria-selected') === 'true'
    )
    if (!active) throw Error(`键盘操作后应选中并聚焦 ${name}`)
  }
  await tabs.getByRole('tab', { name: '数据核对' }).focus()
  await page.keyboard.press('ArrowLeft')
  await checkFocused('资料整理')
  await page.keyboard.press('Delete')
  if (await tabs.getByRole('tab', { name: '资料整理' }).count()) throw Error('Delete 未关闭成员标签')
  await checkFocused('数据核对')
  await page.keyboard.press('End')
  await checkFocused('数据核对')

  for (const [width, dark] of [[1440, false], [768, true]]) {
    await page.setViewportSize({ width, height: 860 })
    await page.evaluate(async value => {
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      useThemeStore(window.readingFixture.app.config.globalProperties.$pinia).setTheme(value)
    }, dark)
    const separator = page.getByRole('separator', { name: '调整侧边栏宽度' })
    const panel = page.locator('.agent-panel')
    const waitForWidth = (percent, key) => page.waitForFunction(expected =>
      Number(document.querySelector('.agent-panel [role="separator"]')?.getAttribute('aria-valuenow')) === expected,
    percent, { timeout: 2000 }).catch(() => { throw Error(`${width}px ${key} 应调整到 ${percent}%`) })
    const available = width - 32
    const minWidth = width === 768 ? 280 : 320
    await page.waitForFunction(expected =>
      Number(document.querySelector('.agent-panel').parentElement.dataset.hostWidth) === expected,
    available)
    await separator.focus()
    await page.keyboard.press('Home')
    await waitForWidth(Math.round(minWidth * 100 / available), 'Home')
    const narrow = (await panel.boundingBox()).width
    await page.keyboard.press('ArrowLeft')
    await waitForWidth(Math.round((minWidth + 24) * 100 / available), 'ArrowLeft')
    if ((await panel.boundingBox()).width <= narrow) throw Error('左方向键未扩大侧栏')
    await page.keyboard.press('End')
    await waitForWidth(75, 'End')
    const wide = (await panel.boundingBox()).width
    if (wide <= narrow || wide > width - 32) throw Error('键盘最大宽度超出面板范围')
    await page.keyboard.press('Home')
    await waitForWidth(Math.round(minWidth * 100 / available), 'Home')
    await page.screenshot({ path: `/tmp/yuxi-session-panel-${dark ? 'dark' : 'light'}.png` })
  }
  await page.getByRole('button', { name: '打开主会话报告' }).click()
  await page.getByText('主会话正文', { exact: true }).waitFor()
  await page.getByRole('button', { name: '打开协作报告' }).click()
  await page.getByText('成员报告正文', { exact: true }).waitFor()
  await tabs.getByRole('tab', { name: '主会话报告' }).click()
  await page.getByText('主会话正文', { exact: true }).waitFor()
  if (previewReads.join(',') !== 'root,member-two') throw Error('同路径文件应分别请求源 Thread 并复用各自缓存')
  await page.getByRole('button', { name: '打开主会话来源' }).click()
  await page.getByText('主会话引用', { exact: true }).waitFor()
  await page.getByRole('button', { name: '打开成员来源' }).click()
  await page.getByText('成员引用', { exact: true }).waitFor()
  await tabs.getByRole('tab', { name: '主会话来源' }).click()
  await page.getByText('主会话引用', { exact: true }).waitFor()
  if (referenceReads.join(',') !== 'root,member-two') throw Error('来源标签应使用各自的 Thread 并复用已读取的结果')
  return '标签切换、关闭焦点、两种主题与宽度的键盘调整、主会话与成员的文件和来源隔离通过'
}
