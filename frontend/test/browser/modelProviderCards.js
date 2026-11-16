// 在真实页面使用固定 HTTP 夹具，验证卡片摘要和两个独立弹窗入口。
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const providers = [
    {
      provider_id: 'deepseek', display_name: 'DeepSeek', is_enabled: true,
      provider_type: 'openai', base_url: 'https://api.example.com',
      credential_status: 'ok', capabilities: ['chat'],
      enabled_models: [{ id: 'fixture-chat', name: 'Fixture Chat', type: 'chat', source: 'manual' }]
    },
    {
      provider_id: 'empty', display_name: 'Empty Provider', is_enabled: true,
      base_url: '', credential_status: 'warning', capabilities: ['chat'], enabled_models: []
    },
    {
      provider_id: 'disabled', display_name: 'Disabled Provider', is_enabled: false,
      capabilities: ['chat'], enabled_models: []
    }
  ]
  let remoteMode = 'pending'
  let remoteRequested
  let remoteStarted = new Promise(resolve => { remoteRequested = resolve })
  let remoteModels = [{ id: 'remote-first', name: 'Remote First', type: 'chat' }]
  const remoteRequests = []
  let mode = 'normal'
  let releaseLoading
  let loadingReady
  const loadingStarted = new Promise(resolve => { loadingReady = resolve })
  let writes = 0
  const handler = async route => {
    const path = route.request().url().split('?')[0]
    const respond = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
    if (route.request().method() !== 'GET') {
      writes++
      return respond({})
    }
    if (path.endsWith('/auth/me')) return respond({ id: 1, uid: 'fixture', username: 'Fixture', role: 'superadmin' })
    if (path.endsWith('/system/config')) return respond({ default_model: 'deepseek:fixture-chat' })
    if (path.endsWith('/system/info')) return respond({ data: { branding: { name: 'Yuxi' } } })
    if (path.endsWith('/system/health')) return respond({ status: 'ok' })
    if (path.endsWith('/model-providers')) {
      if (mode === 'loading') await new Promise(resolve => { releaseLoading = resolve; loadingReady() })
      if (mode === 'error') return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'Fixture load failure' }) })
      return respond({ data: mode === 'empty' ? [] : providers })
    }
    if (path.endsWith('/remote-models')) {
      remoteRequests.push(path)
      if (remoteMode === 'pending') return remoteRequested(route)
      if (remoteMode === 'error') return route.fulfill({ status: 500, contentType: 'application/json', body: '{}' })
      return respond({ data: remoteModels })
    }
    if (path.endsWith('/projects')) return respond([])
    if (path.includes('/agents/sessions')) return respond({ data: [], has_more: false })
    if (path.includes('knowledge')) return respond({ knowledge_bases: [] })
    if (path.includes('/agents/backends')) return respond({ backends: [] })
    return respond({ data: [], agents: [], projects: [], items: [] })
  }
  await page.route('**/api/**', handler)
  await page.evaluate(() => {
    localStorage.setItem('user_token', 'model-provider-browser-fixture')
    localStorage.setItem('theme', 'light')
  })
  await page.goto(`${origin}/api-services?tab=providers`)
  const panel = page.locator('.model-provider-manage-panel')
  const deepseek = panel.locator('.info-card').filter({ hasText: 'DeepSeek' })
  await deepseek.waitFor()
  check(await deepseek.getByText('已启用 1 个', { exact: true }).count() === 1, '模型数量不正确')
  check(await panel.getByText('已启用 0 个', { exact: true }).count() === 1, '缺少零模型摘要')
  check(await panel.getByText('凭证缺失', { exact: true }).count() === 1, '凭证缺失提示丢失')
  check(await panel.locator('.info-card-footer').count() === 0, '管理模型页脚仍存在')
  check(!/请求用户 ID|能力|管理模型/.test(await panel.innerText()), '卡片仍有多余字段')
  const modelsButton = panel.getByRole('button', { name: '选择 DeepSeek 模型', exact: true })
  await modelsButton.click({ position: { x: 20, y: 20 } })
  const modelsDialog = page.getByRole('dialog', { name: 'DeepSeek - 模型配置', exact: true })
  await modelsDialog.waitFor()
  check(await modelsDialog.getByText('Fixture Chat', { exact: true }).count() === 1, '未打开对应供应商模型列表')
  check(await page.getByRole('dialog', { name: '编辑供应商' }).count() === 0, '卡片误开供应商设置')
  // 截图前等待浮层进入动画结束，避免把过渡尺寸当作最终布局。
  const waitForModalAnimation = (dialog = modelsDialog) => dialog.evaluate(async element => {
    const root = element.closest('.ant-modal-root') || element
    const animations = root.getAnimations({ subtree: true })
      .filter(animation => animation.effect.getTiming().iterations !== Infinity)
    await Promise.all(animations.map(animation => animation.finished.catch(() => {})))
  })
  const firstRemoteRoute = await remoteStarted
  const refreshRemote = modelsDialog.getByRole('button', { name: '刷新远端模型', exact: true })
  await modelsDialog.locator('.ant-spin-spinning .ant-spin-text').waitFor()
  check(await refreshRemote.isDisabled(), '远端加载中允许重复刷新')
  check(await modelsDialog.getByText('获取远程模型', { exact: true }).count() === 0, '旧获取按钮仍存在')
  const remoteHeader = modelsDialog.locator('.remote-header')
  check(await remoteHeader.getByRole('button', { name: '刷新远端模型', exact: true }).count() === 1, '刷新按钮没有位于远端列表标题右侧')
  await waitForModalAnimation()
  await modelsDialog.locator('.ant-modal-content').screenshot({ path: '/tmp/yuxi-provider-remote-loading.png' })
  await firstRemoteRoute.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: remoteModels }) })
  await modelsDialog.getByText('Remote First', { exact: true }).waitFor()
  await modelsDialog.locator('.ant-spin-spinning .ant-spin-text').waitFor({ state: 'hidden' })
  remoteStarted = new Promise(resolve => { remoteRequested = resolve })
  await refreshRemote.click()
  const refreshRoute = await remoteStarted
  await modelsDialog.locator('.ant-spin-spinning .ant-spin-text').waitFor()
  remoteModels = [{ id: 'remote-updated', name: 'Remote Updated', type: 'chat' }]
  await refreshRoute.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: remoteModels }) })
  await modelsDialog.getByText('Remote Updated', { exact: true }).waitFor()
  check(await modelsDialog.getByText('Remote First', { exact: true }).count() === 0, '刷新后旧远端结果未替换')
  remoteMode = 'error'
  await refreshRemote.click()
  await modelsDialog.getByRole('alert').waitFor()
  check(await modelsDialog.getByText('Remote Updated', { exact: true }).isVisible(), '远端刷新失败丢失旧结果')
  remoteMode = 'normal'
  remoteModels = []
  await refreshRemote.click()
  await modelsDialog.getByText('暂无远端模型', { exact: true }).waitFor()
  check(await modelsDialog.getByRole('alert').count() === 0, '重试成功后错误未消失')
  remoteModels = [{ id: 'remote-updated', name: 'Remote Updated', type: 'chat' }]

  await modelsDialog.getByRole('button', { name: 'Close' }).click()
  await modelsDialog.waitFor({ state: 'hidden' })
  await panel.getByRole('button', { name: '配置 DeepSeek 供应商', exact: true }).click()
  const settings = page.getByRole('dialog').filter({ hasText: '编辑供应商' })
  await settings.waitFor()
  check(await modelsDialog.isVisible() === false, '设置点击冒泡打开模型弹窗')
  check(await settings.locator('input').evaluateAll(inputs => inputs.some(input => input.value === 'DeepSeek')), '配置对象不正确')
  await settings.getByRole('button', { name: /^取\s*消$/ }).click()
  await settings.waitFor({ state: 'hidden' })
  const requestsBeforeReopen = remoteRequests.length
  await modelsButton.focus()
  await page.keyboard.press('Enter')
  await modelsDialog.waitFor()
  await modelsDialog.getByText('Remote Updated', { exact: true }).waitFor()
  check(remoteRequests.length === requestsBeforeReopen + 1, '重新打开弹窗没有自动加载远端模型')
  await modelsDialog.getByRole('button', { name: 'Close' }).click()
  await modelsDialog.waitFor({ state: 'hidden' })
  await modelsButton.focus()
  await page.keyboard.press('Space')
  await modelsDialog.waitFor()
  await modelsDialog.getByRole('button', { name: 'Close' }).click()
  await modelsDialog.waitFor({ state: 'hidden' })
  for (const name of ['Empty Provider']) {
    await panel.getByRole('button', { name: `选择 ${name} 模型`, exact: true }).click({ position: { x: 20, y: 20 } })
    const dialog = page.getByRole('dialog', { name: `${name} - 模型配置`, exact: true })
    await dialog.waitFor()
    check(await dialog.getByText('已启用模型 (0)', { exact: true }).count() === 1, '空模型列表不正确')
    await dialog.getByRole('button', { name: 'Close' }).click()
    await dialog.waitFor({ state: 'hidden' })
    await panel.getByRole('button', { name: `配置 ${name} 供应商`, exact: true }).click()
    await settings.waitFor()
    await settings.getByRole('button', { name: /^取\s*消$/ }).click()
    await settings.waitFor({ state: 'hidden' })
  }
  const disabledRequestsBefore = remoteRequests.length
  const disabledCardTrigger = panel.getByRole('button', { name: '配置 Disabled Provider', exact: true })
  for (const key of [null, 'Enter', 'Space']) {
    if (key) {
      await disabledCardTrigger.focus()
      await page.keyboard.press(key)
    } else {
      await disabledCardTrigger.click({ position: { x: 20, y: 20 } })
    }
    await settings.waitFor()
    check(await settings.getByRole('textbox', { name: 'Provider ID', exact: true }).inputValue() === 'disabled', '未启用卡片打开了错误供应商配置')
    check(await settings.getByRole('switch', { name: '启用供应商', exact: true }).isChecked() === false, '配置中未保留供应商停用状态')
    check(await modelsDialog.isVisible() === false, '未启用卡片误开模型列表')
    await waitForModalAnimation(settings)
    if (!key) await settings.locator('.ant-modal-content').screenshot({ path: '/tmp/yuxi-disabled-provider-settings.png' })
    await settings.getByRole('button', { name: /^取\s*消$/ }).click()
    await settings.waitFor({ state: 'hidden' })
  }
  await panel.getByRole('button', { name: '配置 Disabled Provider 供应商', exact: true }).click()
  await settings.waitFor()
  await settings.getByRole('button', { name: /^取\s*消$/ }).click()
  await settings.waitFor({ state: 'hidden' })
  check(remoteRequests.length === disabledRequestsBefore, '未启用供应商配置意外读取远端模型')
  const screenshots = []
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => localStorage.setItem('theme', value), theme)
    await page.reload()
    await deepseek.waitFor()
    for (const width of [1440, 1024, 768]) {
      await page.setViewportSize({ width, height: 1000 })
      const settingsBox = await panel.getByRole('button', { name: '配置 DeepSeek 供应商', exact: true }).boundingBox()
      const cardBox = await deepseek.boundingBox()
      check(settingsBox.x >= cardBox.x && settingsBox.x + settingsBox.width <= cardBox.x + cardBox.width, '设置按钮溢出卡片')
      const path = `/tmp/yuxi-provider-cards-${theme}-${width}.png`
      await page.screenshot({ path, fullPage: true })
      screenshots.push(path)
      await panel.getByRole('button', { name: '选择 DeepSeek 模型', exact: true }).click()
      await modelsDialog.getByText('Remote Updated', { exact: true }).waitFor()
      await waitForModalAnimation()
      const refreshBox = await modelsDialog.getByRole('button', { name: '刷新远端模型', exact: true }).boundingBox()
      const headerBox = await modelsDialog.locator('.remote-header').boundingBox()
      check(refreshBox.x + refreshBox.width <= headerBox.x + headerBox.width + 1, '远端刷新按钮溢出标题行')
      await modelsDialog.locator('.ant-modal-content').screenshot({ path: `/tmp/yuxi-provider-remote-${theme}-${width}.png` })
      await modelsDialog.getByRole('button', { name: 'Close' }).click()
      await modelsDialog.waitFor({ state: 'hidden' })
    }
  }
  await panel.getByRole('textbox', { name: '搜索供应商' }).fill('no-such-provider')
  await panel.getByText('无匹配供应商', { exact: true }).waitFor()
  await panel.getByRole('textbox', { name: '搜索供应商' }).fill('')
  mode = 'empty'
  await panel.getByRole('button', { name: '刷新供应商', exact: true }).click()
  await panel.getByText('暂无供应商，点击上方按钮新增', { exact: true }).waitFor()
  mode = 'loading'
  await panel.getByRole('button', { name: '刷新供应商', exact: true }).click()
  check(await panel.getByRole('button', { name: '刷新供应商', exact: true }).isDisabled(), '加载中未禁用刷新')
  await loadingStarted
  mode = 'normal'
  releaseLoading()
  await deepseek.waitFor()
  mode = 'error'
  await panel.getByRole('button', { name: '刷新供应商', exact: true }).click()
  await page.getByText('服务器内部错误，请稍后重试', { exact: true }).waitFor()
  check(await deepseek.isVisible(), '刷新失败丢失已加载卡片')
  check(writes === 0, '打开弹窗产生配置写入')
  mode = 'normal'
  return { autoRemoteLoad: true, remoteRefresh: true, remoteLoadingAndRetry: true, cardSummary: true, distinctDialogs: true, keyboard: true, emptyAndDisabled: true, loadingAndError: true, writes, screenshots }
}
