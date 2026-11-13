// 以管理员登录后运行：playwright-cli run-code --filename=frontend/test/browser/agentCreateDialog.js
// prettier-ignore
async (page) => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const origin = await page.evaluate(() => location.origin)
  const token = await page.evaluate(() => localStorage.getItem('user_token'))
  const headers = { Authorization: `Bearer ${token}` }
  const readAgent = async slug => {
    const response = await page.request.get(`${origin}/api/agent/${slug}`, { headers })
    check(response.ok(), '创建结果必须能够回读')
    return (await response.json()).agent
  }
  const created = []
  const originalTheme = await page.evaluate(() => localStorage.getItem('theme'))
  const originalViewport = page.viewportSize()
  const setTheme = async theme => {
    await page.evaluate(value => localStorage.setItem('theme', value), theme)
    await page.reload()
    await page.getByRole('button', { name: '新增智能体', exact: true }).waitFor()
    check(await page.evaluate(value => document.documentElement.classList.contains('dark') === (value === 'dark'), theme), '真实主题必须生效')
  }
  const modal = page.getByRole('dialog')
  const screenshot = async path => {
    await modal.evaluate(async el => {
      await Promise.all(el.getAnimations({ subtree: true })
        .filter(animation => animation.effect.getComputedTiming().iterations !== Infinity)
        .map(animation => animation.finished.catch(() => {})))
    })
    await page.mouse.move(0, 0)
    await modal.locator('.ant-modal-content').screenshot({ path })
  }
  /** 验证高级配置滚轮可达底部且页脚固定。 */
  const checkAdvancedScroll = async () => {
    await page.waitForFunction(() => document.querySelector('.config-item') && !document.querySelector('.agent-edit-modal .ant-spin-spinning, .agent-edit-modal .ant-spin-blur')).catch(() => { throw new Error('高级配置加载后才能验证滚动') })
    const content = modal.locator('.agent-modal-main')
    check(await content.evaluate(el => el.scrollHeight > el.clientHeight), '高级配置必须在内容区形成滚动范围，不能被外层裁切')
    const footer = modal.locator('.create-dialog-footer')
    const before = await footer.boundingBox()
    const bounds = await content.boundingBox()
    await page.mouse.move(bounds.x + bounds.width - 12, bounds.y + bounds.height / 2)
    await page.mouse.wheel(0, 10000)
    await page.waitForFunction(() => {
      const el = document.querySelector('.agent-modal-main')
      return el.scrollTop > 0 && el.scrollTop + el.clientHeight >= el.scrollHeight - 1
    }).catch(async () => { throw new Error(`滚轮未到达底部：${JSON.stringify(await content.evaluate(el => ({ top: el.scrollTop, height: el.clientHeight, scrollHeight: el.scrollHeight })))}`) })
    const lastField = await modal.locator('.config-item').last().boundingBox()
    const after = await footer.boundingBox()
    check(lastField.y >= bounds.y && lastField.y + lastField.height <= bounds.y + bounds.height, '滚轮必须能到达最后一个高级配置字段')
    check(Math.abs(before.y - after.y) < 1, '滚动高级配置时页脚必须保持固定')
    await screenshot(`/tmp/yuxi-create-advanced-scroll-${page.viewportSize().width}.png`)
    await page.mouse.move(bounds.x + bounds.width - 12, bounds.y + bounds.height / 2)
    await page.mouse.wheel(0, -10000)
    await page.waitForFunction(() => document.querySelector('.agent-modal-main').scrollTop === 0).catch(() => { throw new Error('滚轮未返回顶部') })
  }
  const openCreate = async () => {
    await page.getByRole('button', { name: '新增智能体', exact: true }).click()
    await modal.waitFor()
  }
  const create = async slug => {
    const received = page.waitForResponse(response => response.url().split('?')[0] === `${origin}/api/agent` && response.request().method() === 'POST')
    await modal.getByRole('button', { name: /^创\s*建$/ }).click()
    const response = await received
    check(response.ok(), '真实创建必须成功')
    const agent = (await response.json()).agent
    created.push(agent.slug)
    check(agent.slug === slug, '创建必须使用高级配置中的标识')
    await modal.waitFor({ state: 'hidden' })
    return readAgent(slug)
  }
  try {
    await page.goto(`${origin}/agent-manage`)
    await page.setViewportSize({ width: 1440, height: 1000 })
    await setTheme('dark')
    await openCreate()
    check(await modal.getByRole('radio', { name: '个人使用', exact: true }).isChecked(), '默认必须个人使用')
    check(await modal.locator('.ant-modal-header').getByRole('radio', { name: '团队共享', exact: true }).count() === 1, '使用方式分段选择必须位于标题栏')
    check(await modal.locator('.agent-modal-title').count() === 0, '分段选择必须取代创建标题')
    const description = modal.locator('.agent-description-textarea')
    check(await description.evaluate(el => {
      const style = getComputedStyle(el)
      return parseFloat(style.borderTopWidth) > 0 && style.borderTopColor !== 'rgba(0, 0, 0, 0)'
    }), '描述框在未聚焦时必须保留边框')
    check(!await modal.getByRole('textbox', { name: '智能体标识', exact: true }).isVisible(), '标识不应出现在基本信息')
    check(await modal.locator('.agent-inline-slug:visible').count() === 0, '基本信息不应泄露备用标识文本')
    check(await modal.locator('.share-config-form').count() === 0, '个人使用不应展示共享范围')
    await modal.locator('.agent-modal-titlebar').click()
    await screenshot('/tmp/yuxi-create-personal-dark.png')
    await modal.getByText('团队共享', { exact: true }).click()
    const scopes = modal.locator('.share-config-form')
    await scopes.waitFor()
    await modal.evaluate(async el => {
      await Promise.all(el.getAnimations({ subtree: true })
        .filter(animation => animation.effect.getComputedTiming().iterations !== Infinity)
        .map(animation => animation.finished.catch(() => {})))
    })
    check(await scopes.getByRole('radiogroup', { name: '读取权限', exact: true }).isVisible(), '团队共享必须直接展示通用权限组件')
    check(await scopes.evaluate(el => el.getBoundingClientRect().top > document.querySelector('.agent-description-textarea').getBoundingClientRect().bottom), '权限组件必须放在描述下方')
    check(await scopes.getByRole('radio', { name: /全局共享/ }).getAttribute('aria-checked') === 'true', '新建切换团队共享必须默认全局共享')
    const activeCard = scopes.locator('.share-mode-card.active').first()
    const activeBackground = await activeCard.evaluate(el => getComputedStyle(el).backgroundColor)
    const inactiveCard = scopes.locator('.share-mode-card:not(.active)').first()
    await inactiveCard.hover()
    check(await inactiveCard.evaluate(el => getComputedStyle(el).backgroundColor) !== activeBackground, '暗色悬停态不能伪装成选中态')
    const readScope = scopes.getByRole('radiogroup', { name: '读取权限', exact: true })
    await readScope.getByRole('radio', { name: /指定人/ }).click()
    await modal.getByText('个人使用', { exact: true }).click()
    await scopes.waitFor({ state: 'hidden' })
    await modal.getByText('团队共享', { exact: true }).click()
    await scopes.waitFor()
    check(await readScope.getByRole('radio', { name: /指定人/ }).getAttribute('aria-checked') === 'true', '切回共享必须保留范围草稿')
    await readScope.getByRole('radio', { name: /指定人/ }).click()
    await scopes.getByRole('button', { name: '选择用户', exact: true }).click()
    const selection = page.locator('.selection-dropdown:visible')
    await selection.waitFor()
    const bounds = await selection.boundingBox()
    check(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= 1440 && bounds.y + bounds.height <= 1000, '选人浮层必须在视口内')
    await modal.locator('.agent-modal-titlebar').click()
    await screenshot('/tmp/yuxi-create-shared-dark.png')
    const sharedSlug = `dialog-shared-${Date.now()}`
    await modal.getByRole('textbox', { name: '智能体名称', exact: true }).fill('创建弹窗共享验证')
    await scopes.getByRole('switch', { name: '管理权限已关闭', exact: true }).click()
    await modal.getByRole('button', { name: '高级配置', exact: true }).click()
    const slugInput = modal.getByRole('textbox', { name: '智能体标识', exact: true })
    await slugInput.fill(sharedSlug)
    await checkAdvancedScroll()
    await screenshot('/tmp/yuxi-create-advanced-dark.png')
    await modal.getByRole('button', { name: /^创\s*建$/ }).click()
    await modal.getByText('管理权限至少需要选择一个用户', { exact: true }).waitFor()
    check(await modal.locator('.create-sharing-section').evaluate(el => el === document.activeElement), '共享校验失败必须回到基本信息并聚焦')
    await scopes.getByRole('switch', { name: '管理权限已开启', exact: true }).click()
    await modal.getByRole('button', { name: '高级配置', exact: true }).click()
    check(await slugInput.inputValue() === sharedSlug, '切换页面必须保留标识')
    const shared = await create(sharedSlug)
    check(shared.visibility === 'shared' && shared.share_config.read_scope.access_level === 'user' && shared.share_config.read_scope.user_uids.length > 0, '共享创建必须回读正确范围')
    await setTheme('light')
    await openCreate()
    for (const width of [1440, 1024, 768]) {
      await page.setViewportSize({ width, height: 900 })
      await modal.getByText('团队共享', { exact: true }).click()
      await scopes.waitFor()
      await modal.locator('.agent-modal-titlebar').click()
      check(await modal.evaluate(el => el.scrollWidth <= el.clientWidth), '共享弹窗不能水平溢出')
      const submitBounds = await modal.getByRole('button', { name: /^创\s*建$/ }).boundingBox()
      check(submitBounds.y >= 0 && submitBounds.y + submitBounds.height <= 900, '提交操作必须保持可达')
      await screenshot(`/tmp/yuxi-create-shared-light-${width}.png`)
      await modal.getByText('个人使用', { exact: true }).click()
    }
    const privateSlug = `dialog-personal-${Date.now()}`
    await modal.getByRole('textbox', { name: '智能体名称', exact: true }).fill('创建弹窗个人验证')
    await modal.getByRole('button', { name: '高级配置', exact: true }).click()
    await slugInput.fill(privateSlug)
    await checkAdvancedScroll()
    await modal.getByRole('button', { name: '基本信息', exact: true }).click()
    const personal = await create(privateSlug)
    check(personal.visibility === 'private', '切回个人使用后创建必须回读为私有')
    await openCreate()
    await modal.getByText('团队共享', { exact: true }).click()
    await modal.getByRole('textbox', { name: '智能体名称', exact: true }).fill('默认全局共享验证')
    await modal.getByRole('button', { name: '高级配置', exact: true }).click()
    const globalSlug = `dialog-global-${Date.now()}`
    await slugInput.fill(globalSlug)
    const global = await create(globalSlug)
    check(global.visibility === 'shared' && global.share_config.read_scope.access_level === 'global' && global.share_config.manage_scope === null, '默认全局共享必须由真实创建结果回读证明')
    return { defaultGlobalCreation: true, sharedCreation: true, personalCreation: true, permissionValidation: true, draftRetention: true, themes: ['dark', 'light'], widths: [1440, 1024, 768] }
  } finally {
    for (const slug of created) {
      const deleted = await page.request.delete(`${origin}/api/agent/${slug}`, { headers })
      check(deleted.ok(), '测试必须清理自己创建的智能体')
      check((await page.request.get(`${origin}/api/agent/${slug}`, { headers })).status() === 404, '删除后必须回读为不可访问')
    }
    await page.evaluate(value => value === null ? localStorage.removeItem('theme') : localStorage.setItem('theme', value), originalTheme)
    if (originalViewport) await page.setViewportSize(originalViewport)
    await page.reload()
  }
}
