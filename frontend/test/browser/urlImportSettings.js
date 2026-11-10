// 真实组件配合固定 HTTP 协议验证设置交互；持久化与角色权限由 PostgreSQL integration 验证。
async page => {
  const check = (value, reason) => { if (!value) throw new Error(reason) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  let stored = {}
  let failSave = false
  let failLoad = false
  const option = () => ({ key: 'knowledge_url_import_policy', value: stored,
    effective_value: { allowed_hosts: stored.allowed_hosts ?? ['legacy.example'] } })
  const skillOption = { key: 'remote_skill_source_policy', value: { allowed_hosts: ['github.com', 'modelscope.cn'] },
    params: { fields: [{ key: 'allowed_hosts', label: '允许的来源域名', type: 'list[str]', default: ['github.com', 'modelscope.cn'] }] } }
  await page.route('**/api/**', async route => {
    const path = '/' + route.request().url().split('/').slice(3).join('/').split('?')[0]
    if (!path.startsWith('/api/system/config/options')) return route.fulfill({ json: {} })
    if (route.request().method() === 'PUT') {
      check(route.request().url().endsWith('/knowledge_url_import_policy'), '写入错误的配置项')
      if (failSave) return route.fulfill({ status: 500, json: { detail: 'private-config-diagnostic' } })
      stored = route.request().postDataJSON().value
      return route.fulfill({ json: { option: option() } })
    }
    return failLoad ? route.fulfill({ status: 503, json: { detail: 'private-config-diagnostic' } })
      : route.fulfill({ json: { options: [option(), skillOption] } })
  })
  try {
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto(`${origin}/test/browser/fixtures/urlImportSettings.html`)
    const dialog = page.getByRole('dialog')
    const security = dialog.locator('.security-settings-section')
    await security.getByRole('heading', { name: '安全设置', exact: true }).waitFor()
    const skill = security.locator('.skill-settings-section')
    await skill.getByText('github.com', { exact: true }).waitFor()
    await skill.getByRole('button', { name: /^编\s*辑$/ }).click()
    check(await skill.getByRole('combobox').isVisible(), 'Skill 白名单未迁入安全设置')
    await skill.getByRole('button', { name: /^取\s*消$/ }).click()
    const panel = page.getByRole('region', { name: '知识库 URL 导入' })
    const savedHosts = panel.getByLabel('已允许导入的域名')
    await savedHosts.getByText('legacy.example', { exact: true }).waitFor()
    const edit = panel.getByRole('button', { name: /^编\s*辑$/ })
    const save = panel.getByRole('button', { name: /^保\s*存$/ })
    const input = panel.getByRole('combobox', { name: '网页域名白名单' })
    await edit.click()
    await input.fill('docs.example.com')
    await input.press('Enter')
    await panel.getByRole('button', { name: /^取\s*消$/ }).click()
    check((await savedHosts.innerText()).trim() === 'legacy.example', '取消编辑改变已保存域名')
    await edit.click()
    await panel.locator('.ant-select-selection-item-remove').click()
    await input.fill('docs.example.com')
    await input.press('Enter')
    const navigate = async title => {
      await dialog.locator('.sider-item:visible, .nav-item:visible').filter({ hasText: new RegExp(`^${title}$`) }).click()
    }
    await navigate('网络搜索')
    await navigate('安全设置')
    check((await panel.innerText()).includes('docs.example.com'), '切换设置页丢失 URL 草稿')
    failSave = true
    await save.click()
    await page.locator('.ant-message-error').waitFor()
    check(await input.isVisible(), '保存失败退出编辑或丢失草稿')
    check((await panel.innerText()).includes('docs.example.com'), '保存失败丢失域名')
    check(!Object.hasOwn(stored, 'allowed_hosts'), '失败请求改变保存值')
    failSave = false
    await save.click()
    await savedHosts.getByText('docs.example.com', { exact: true }).waitFor()
    await page.reload()
    await savedHosts.getByText('docs.example.com', { exact: true }).waitFor()

    const screenshots = []
    for (const [width, theme] of [[1440, 'light'], [1024, 'light'], [768, 'dark']]) {
      await page.setViewportSize({ width, height: 1000 })
      await page.evaluate(value => window.urlImportSettingsFixture.theme.setTheme(value), theme)
      await edit.click()
      check(await panel.evaluate(el => el.scrollWidth <= el.clientWidth + 1), '设置面板横向溢出')
      const path = `/tmp/yuxi-security-settings-${theme}-${width}.png`
      await dialog.screenshot({ path, animations: 'disabled' })
      screenshots.push(path)
      await panel.getByRole('button', { name: /^取\s*消$/ }).click()
    }
    await edit.click()
    await panel.locator('.ant-select-selection-item-remove').click()
    await save.click()
    await panel.getByText('未配置域名，URL 导入已关闭', { exact: true }).waitFor()
    check(Array.isArray(stored.allowed_hosts) && stored.allowed_hosts.length === 0, '未提交明确空列表')
    await page.reload()
    await panel.getByText('未配置域名，URL 导入已关闭', { exact: true }).waitFor()
    await skill.getByText('github.com', { exact: true }).waitFor()
    await page.evaluate(() => { window.urlImportSettingsFixture.user.userRole = 'admin' })
    await security.waitFor({ state: 'hidden' })
    check(await dialog.locator('.sider-item:visible, .nav-item:visible').filter({ hasText: /^安全设置$/ }).count() === 0, '普通管理员仍显示安全设置')
    await page.evaluate(() => { window.urlImportSettingsFixture.user.userRole = 'superadmin' })
    await panel.getByText('未配置域名，URL 导入已关闭', { exact: true }).waitFor()
    failLoad = true
    await page.reload()
    await panel.getByRole('button', { name: '重新加载' }).waitFor()
    check(!(await panel.innerText()).includes('private-config-diagnostic'), '加载错误公开内部诊断')
    failLoad = false
    await panel.getByRole('button', { name: '重新加载' }).click()
    await panel.getByText('未配置域名，URL 导入已关闭', { exact: true }).waitFor()
    return { securityNavigation: 'passed', skillPolicy: 'passed', draftRetention: 'passed', roleVisibility: 'passed', save: 'passed', cancel: 'passed', emptyReload: 'passed', errorRecovery: 'passed', screenshots }
  } finally {
    await page.unroute('**/api/**')
  }
}
