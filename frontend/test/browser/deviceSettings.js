// 仅在浏览器 HTTP 边界提供合成结果，验证真实设备设置组件与弹窗导航。
// playwright-cli -s=yuxi-devices run-code --filename=frontend/test/browser/deviceSettings.js
// prettier-ignore
async page => {
  const origin = page.url().split('/').slice(0, 3).join('/')
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const screenshotDir = '.playwright-cli'
  const devices = [
    { id: 'fixture-computer', name: '研发工作站 · 长名称设备的展示与布局验证', kind: 'computer', platform: 'macOS', status: 'online', paused: false },
    { id: 'fixture-browser', name: '办公浏览器', kind: 'browser', status: 'offline', paused: false },
    { id: 'fixture-paused', name: '临时工作站', kind: 'computer', platform: 'Windows 11', status: 'online', paused: true },
    { id: 'fixture-android', name: 'Android 测试设备', kind: 'computer', platform: 'Android 15', status: 'online', paused: false },
    { id: 'fixture-unknown', name: 'macOS Windows Chrome · 未知平台设备', kind: 'computer', platform: 'Linux', status: 'offline', paused: false }
  ]
  const option = { key: 'abridge_options', name: '设备管理', description: 'aBridge 实例连接；个人设备权限仍由 aBridge 管理。', value: {}, params: { fields: [{ key: 'url', label: '服务 URL', environment: 'ABRIDGE_URL', help: '留空读取 ABRIDGE_URL。请填写不带账号、路径、查询和片段的 HTTP(S) 服务根地址。全局管理请进入 aBridge。' }] } }
  let mode = 'loading', overviewReads = 0, configReads = 0, releaseLoading
  const saves = [], evidence = []
  await page.route('**/api/**', async route => {
    const path = '/' + route.request().url().split('/').slice(3).join('/').split('?')[0]
    const respond = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/api/devices') {
      if (mode === 'loading') await new Promise(resolve => { releaseLoading = resolve })
      if (mode === 'error') return route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ detail: { code: 'abridge_unavailable', message: '设备服务暂时不可用，请重试' } }) })
      return respond({ state: mode === 'login_required' ? mode : 'ready', configured: true, management_url: 'https://bridge.example/api/auth/oidc/login', devices: mode === 'empty' ? [] : devices })
    }
    if (path === '/api/system/config/options') { configReads++; return respond({ options: [option] }) }
    if (path === '/api/system/config/options/abridge_options') {
      const value = route.request().postDataJSON().value
      saves.push(value)
      Object.assign(option.value, value)
      return respond({ option })
    }
    if (path === '/api/devices/overview') { overviewReads++; throw new Error('Yuxi 不应读取全局概况') }
    return respond({})
  })
  const panel = page.locator('.device-settings')
  const shot = async name => { if (!name.includes('loading')) await page.waitForFunction(() => !document.querySelector('.device-settings .ant-spin-blur')); const path = `${screenshotDir}/${name}.png`; await page.screenshot({ path, animations: 'disabled' }); evidence.push(path) }
  await page.setViewportSize({ width: 1440, height: 1080 })
  await page.goto(`${origin}/test/browser/deviceSettings.html`)
  await panel.getByRole('status', { name: '正在加载设备' }).waitFor()
  await shot('yuxi-devices-initial-loading-light-1440')
  await page.setViewportSize({ width: 768, height: 1024 })
  await page.evaluate(() => window.deviceSettingsTheme.setTheme('dark'))
  await shot('yuxi-devices-initial-loading-dark-768')
  mode = 'ready'; releaseLoading()
  await page.setViewportSize({ width: 1440, height: 1080 })
  await page.evaluate(() => window.deviceSettingsTheme.setTheme('light'))
  await panel.getByText('默认授权本人全部设备与能力', { exact: false }).waitFor()
  await panel.getByText(devices[0].name, { exact: true }).waitFor()
  for (const label of ['macOS · 电脑', 'Chrome · 浏览器', 'Windows · 电脑', 'Android · 设备', 'Linux · 电脑']) await panel.getByText(label, { exact: true }).waitFor()
  check(await panel.locator('.device-icon').count() === 5, '设备行缺少平台或通用图标')
  check(await panel.locator('img.device-icon').evaluateAll(images => images.length === 4 && images.every(image => image.complete && image.naturalWidth > 0)), '平台 SVG 未加载')
  check(configReads === 0 && overviewReads === 0, '普通用户读取了管理员数据')
  check(await panel.getByRole('heading', { name: '实例配置' }).count() === 0, '普通用户看到了实例配置')
  check(await panel.getByRole('link', { name: '进入 aBridge' }).getAttribute('rel') === 'noopener noreferrer', '外部管理链接缺少隔离')
  check(await page.locator('.settings-sider').getByRole('button', { name: '设备管理' }).count() === 1, '桌面导航缺少设备管理')
  await shot('yuxi-devices-user-light-1440')
  mode = 'loading'
  await panel.getByRole('button', { name: '刷新设备' }).click()
  await panel.locator('.device-actions .ant-btn-loading').waitFor()
  check(await panel.getByText(devices[0].name, { exact: true }).isVisible(), '刷新时设备列表消失')
  check(await panel.getByRole('link', { name: '进入 aBridge' }).isVisible(), '刷新时管理入口消失')
  check(await panel.locator('.device-skeleton').count() === 0, '刷新时用骨架覆盖了已有设备')
  await shot('yuxi-devices-loading-1440')
  mode = 'error'; releaseLoading()
  await panel.getByRole('alert').filter({ hasText: '设备服务暂时不可用' }).waitFor()
  check(await panel.getByText(devices[0].name, { exact: true }).count() === 0, '错误时保留了过期设备列表')
  await shot('yuxi-devices-error-1440')
  mode = 'empty'
  await panel.getByRole('button', { name: /重\s*试/ }).click()
  await panel.getByText('暂无授权给 Yuxi 的设备。请进入 aBridge 绑定设备或检查连接授权。').waitFor()
  await page.setViewportSize({ width: 768, height: 1024 })
  await page.evaluate(() => window.deviceSettingsTheme.setTheme('dark'))
  mode = 'ready'
  await panel.getByRole('button', { name: '刷新设备' }).click()
  await panel.getByText('Android · 设备', { exact: true }).waitFor()
  await shot('yuxi-devices-platforms-dark-768')
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '平台设备列表导致横向溢出')
  mode = 'empty'
  await panel.getByRole('button', { name: '刷新设备' }).click()
  await panel.getByText('暂无授权给 Yuxi 的设备。请进入 aBridge 绑定设备或检查连接授权。').waitFor()
  await shot('yuxi-devices-empty-dark-768')
  check(await page.locator('.settings-mobile-nav').getByRole('button', { name: '设备管理' }).isVisible(), '窄屏导航缺少设备管理')
  mode = 'login_required'
  await panel.getByRole('button', { name: '刷新设备' }).click()
  await panel.getByText('请先进入 aBridge，使用 Yuxi 账户登录并绑定设备，再刷新列表。').waitFor()
  mode = 'ready'
  await page.goto(`${origin}/test/browser/deviceSettings.html?role=superadmin`)
  await panel.getByRole('textbox', { name: '服务 URL' }).waitFor()
  await panel.getByText('不带账号、路径、查询和片段', { exact: false }).waitFor()
  check(overviewReads === 0 && await panel.getByRole('heading', { name: '全局概况' }).count() === 0, 'Yuxi 不应显示全局概况')
  check(await panel.getByRole('textbox', { name: '管理员 Token' }).count() === 0, '无消费者的管理员凭据仍可编辑')
  check(await panel.getByRole('textbox', { name: '服务 URL' }).isDisabled(), '配置默认应只读')
  await panel.locator('.settings-service-card').hover()
  await panel.getByRole('button', { name: /^编\s*辑$/ }).click()
  await shot('yuxi-devices-admin-edit-768')
  await panel.getByRole('textbox', { name: '服务 URL' }).fill('https://bridge.example')
  await panel.getByRole('button', { name: /^保\s*存$/ }).click()
  await panel.getByText('研发工作站 · 长名称设备的展示与布局验证', { exact: true }).waitFor()
  check(saves.length === 1 && saves[0].url === 'https://bridge.example' && !('admin_token' in saves[0]), '保存地址修改了未编辑的凭据')
  await page.setViewportSize({ width: 1440, height: 1080 })
  await page.evaluate(() => window.deviceSettingsTheme.setTheme('light'))
  await shot('yuxi-devices-admin-light-1440')
  await page.setViewportSize({ width: 1024, height: 900 })
  await shot('yuxi-devices-platforms-light-1024')
  await page.setViewportSize({ width: 768, height: 1024 })
  await page.evaluate(() => window.deviceSettingsTheme.setTheme('dark'))
  await shot('yuxi-devices-admin-dark-768')
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '设备设置导致页面横向溢出')
  return { passed: true, configReads, overviewReads, screenshots: evidence }
}
