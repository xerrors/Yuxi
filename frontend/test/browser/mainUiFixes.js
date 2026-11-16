// 已登录当前工作树的 Vite 页面后运行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/mainUiFixes.js
// 仅替换模型目录、会话列表与用户列表读取；不提交消息、不保存设置或智能体。
// prettier-ignore
async (page) => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const spec = 'fixture:deepseek-ai/DeepSeek-V3'
  const observations = []
  await page.route('**/api/v1/agents/sessions?*', route => route.fulfill({ json: { data: [], has_more: false } }))
  await page.route('**/api/system/model-providers/models/v2?*', route => route.fulfill({ json: {
    success: true, data: { fixture: { name: '测试供应商', models: [{ spec, model_id: 'deepseek-ai/DeepSeek-V3', display_name: 'deepseek-ai/DeepSeek-V3' }] } }
  } }))
  await page.route('**/api/auth/users/page?*', route => route.fulfill({ json: {
    total: 1, items: [{ id: 999999, uid: 'fixture-user', username: '展示验收用户', role: 'user', user_kind: 'human', department_name: '默认部门' }]
  } }))
  /** 通过正式主题 store 切换，等待过渡完成再读取样式。 */
  const theme = async (mode) => {
    await page.evaluate(async mode => {
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      useThemeStore().setTheme(mode)
    }, mode)
    await page.waitForFunction(() => document.getAnimations().every(animation =>
      animation.effect.getTiming().iterations === Infinity || animation.playState !== 'running'
    ))
  }
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.evaluate(() => localStorage.removeItem('yuxi_tool_approval_mode'))
  await page.goto(origin + '/agent')
  const composer = page.locator('.input-box')
  await composer.waitFor()
  await composer.getByRole('button', { name: '完全信任', exact: true }).waitFor()
  await page.evaluate(async spec => {
    const { useAgentStore } = await import('/src/modules/agents/index.js')
    useAgentStore().agentConfig.model = spec
  }, spec)
  await composer.locator('.model-select').getByText('DeepSeek-V3', { exact: true }).waitFor()
  await composer.locator('.model-select button[title="deepseek-ai/DeepSeek-V3"]').waitFor()
  for (const mode of ['light', 'dark']) {
    await theme(mode)
    await composer.screenshot({ path: `/tmp/yuxi-main-composer-${mode}.png` })
  }
  await page.locator('.user-info-dropdown').click()
  await page.getByRole('menuitem', { name: '设置', exact: true }).click()
  const settings = page.locator('.settings-modal')
  await settings.locator('.identity-panel').waitFor()
  for (const mode of ['light', 'dark']) {
    await theme(mode)
    const role = settings.locator('.identity-item').filter({ hasText: '权限' }).locator('.profile-value')
    check(await role.textContent() !== '', '权限角色必须可读')
    check(await role.evaluate(node => {
      const probe = document.createElement('span')
      probe.style.color = 'var(--gray-900)'
      node.appendChild(probe)
      const same = getComputedStyle(node).color === getComputedStyle(probe).color
      probe.remove()
      return same
    }), '超级管理员使用中性文本色')
    await settings.locator('.identity-panel').screenshot({ path: `/tmp/yuxi-main-role-${mode}.png` })
  }
  await settings.locator('.settings-sider').getByText('用户管理', { exact: true }).click()
  await settings.locator('.user-uid').getByText('ID: fixture-user', { exact: true }).waitFor()
  check(!await settings.getByText('登录 ID', { exact: false }).count(), '用户管理不再展示登录 ID')
  await settings.locator('.ant-table-tbody').screenshot({ path: '/tmp/yuxi-main-user-id.png' })
  await settings.locator('.settings-sider').getByText('运行环境', { exact: true }).click()
  await settings.getByRole('heading', { name: '运行环境', exact: true }).waitFor()
  check(!await settings.getByText('Agent 环境', { exact: true }).count(), '设置标题和导航使用运行环境')
  await settings.getByRole('button', { name: '关闭设置', exact: true }).click()

  await page.goto(origin + '/extensions?tab=skills')
  const suite = page.locator('.skill-suite-card').first()
  const installed = page.locator('.card-wrapper .info-card').first()
  await suite.waitFor()
  await installed.waitFor()
  for (const mode of ['light', 'dark']) {
    await theme(mode)
    await page.mouse.move(0, 0)
    const borders = []
    for (const card of [suite, installed]) {
      const border = await card.evaluate(node => {
        const style = getComputedStyle(node)
        return { width: style.borderTopWidth, color: style.borderTopColor }
      })
      check(mode === 'dark' ? border.color === 'rgba(0, 0, 0, 0)' : border.width === '1px' && border.color !== 'rgba(0, 0, 0, 0)', `${mode} 技能卡片边框不符`)
      borders.push(border)
    }
    observations.push({ mode, borders })
    await suite.locator('..').locator('..').screenshot({ path: `/tmp/yuxi-main-skills-${mode}.png` })
  }
  await page.goto(origin + '/agent-manage')
  check(await page.locator('.page-header .tab-item').count() === 0, '智能体管理不再显示模型供应商、定时任务或 API 标签')
  await page.goto(origin + '/api-services?tab=api')
  check(await page.locator('.page-header .tab-item').allTextContents().then(labels => labels.map(label => label.trim()).join(',') === '模型供应商,API集成'), 'API服务包含模型供应商和API集成两个标签')
  const integration = page.locator('.api-integration-panel')
  await integration.locator('.metric-strip').waitFor()
  check(!await integration.locator('.settings-section-description, .integration-section-hint').count(), 'API 概览不常驻显示内部协议说明')
  check(await integration.getByRole('button', { name: '创建 API Key', exact: true }).isVisible(), 'API Key 创建入口保留')
  await integration.screenshot({ path: '/tmp/yuxi-main-api-integration.png' })

  await page.goto(origin + '/agent-manage?tab=agents&agent=default-chatbot')
  const dialog = page.getByRole('dialog', { name: '编辑智能体', exact: true })
  await dialog.waitFor()
  check(!await dialog.locator('.agent-backend-summary').count(), '编辑页移除智能体后端摘要')
  check(await dialog.locator('.agent-profile-header').evaluate(node => getComputedStyle(node).marginBottom) === '32px', '头像区下边距为32px')
  check(Math.round((await dialog.locator('.agent-avatar-column').boundingBox()).width) === 56, '头像列跟随头像尺寸，不再固定77px')
  for (const mode of ['light', 'dark']) {
    await theme(mode)
    for (const width of [1440, 1024, 768]) {
      await page.setViewportSize({ width, height: 1000 })
      check(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth + 1), '编辑弹窗横向溢出')
    }
    await page.setViewportSize({ width: 1440, height: 1000 })
    await dialog.locator('.ant-modal-content').screenshot({ path: `/tmp/yuxi-main-agent-edit-${mode}.png` })
  }
  await dialog.getByRole('button', { name: '模型配置', exact: true }).click()
  const model = dialog.locator('.model-select').first()
  const clear = model.getByRole('button', { name: '清空模型选择', exact: true })
  if (await clear.count()) await clear.click()
  await model.getByText('使用系统默认', { exact: true }).waitFor()
  check(await model.getByRole('button', { name: '检查', exact: true }).isDisabled(), '沿用默认时不检查空模型')
  await dialog.locator('.ant-modal-content').screenshot({ path: '/tmp/yuxi-main-agent-model-default.png' })
  await page.goto(origin + '/schedules')
  await page.getByRole('button', { name: '新建任务', exact: true }).click()
  await page.getByRole('region', { name: '任务配置', exact: true }).getByRole('button', { name: '完全信任', exact: true }).waitFor()
  await theme('light')
  await page.goto(origin + '/agent')
  await page.unrouteAll({ behavior: 'wait' })
  return { verified: [1, 2, 4, 5, 6, 7, 8, 9, 10], widths: [1440, 1024, 768], observations }
}
