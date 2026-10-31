// 使用已登录测试账号与测试凭据运行；外层负责快照并恢复配置值及审计字段。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/mineruOfficialOptions.js
// prettier-ignore
async page => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const card = page.locator('.ocr-settings-section .settings-service-card').filter({ has: page.getByRole('heading', { name: 'MinerU 官方云服务', exact: true }) })
  const open = async () => {
    await page.locator('.user-info-dropdown:visible').click()
    await page.getByRole('menuitem', { name: '设置', exact: true }).click()
    await page.getByText('OCR 配置', { exact: true }).filter({ visible: true }).first().click()
    await card.waitFor()
  }
  const read = () => page.evaluate(async () => {
    const { configOptionsApi } = await import('/src/apis/system_api.js')
    return (await configOptionsApi.getOptions()).options.find(option => option.key === 'mineru_official_api_opts')
  })
  const save = async () => {
    const request = page.waitForRequest(request => request.method() === 'PUT' && request.url().endsWith('/config/options/mineru_official_api_opts'))
    const response = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().endsWith('/config/options/mineru_official_api_opts'))
    await card.getByRole('button', { name: /^保\s*存$/ }).click()
    const payload = (await request).postDataJSON()
    check((await response).ok(), '真实保存失败')
    await card.getByRole('textbox', { name: '解析模型' }).waitFor()
    return payload.value
  }
  if (!(await card.isVisible())) await open()
  const cancel = card.getByRole('button', { name: /^取\s*消$/ })
  if (await cancel.count()) await cancel.click()
  check(await card.getByRole('textbox', { name: '解析模型' }).inputValue() === 'Pipeline（传统解析）', '旧记录缺少默认模型')
  check((await read()).sensitive_state.api_key.source === 'database', '需要数据库测试凭据')
  await card.hover()
  await card.getByRole('button', { name: /^编\s*辑$/ }).click()
  await card.getByLabel('API Key', { exact: true }).fill('   ')
  const model = card.getByRole('combobox', { name: '解析模型' })
  await model.focus()
  await model.press('ArrowDown')
  await page.getByTitle('VLM（视觉模型解析）', { exact: true }).click()
  const payload = await save()
  check(payload.model_version === 'vlm' && !Object.hasOwn(payload, 'api_key'), '修改模型不能提交空凭据')
  check((await read()).sensitive_state.api_key.source === 'database', '数据库凭据丢失')
  await page.reload()
  await open()
  check(await card.getByRole('textbox', { name: '解析模型' }).inputValue() === 'VLM（视觉模型解析）', '刷新丢失模型选择')
  const screenshots = []
  for (const [width, theme] of [[1440, 'light'], [768, 'dark']]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.evaluate(async value => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme(value) }, theme)
    await card.hover()
    await card.getByRole('button', { name: /^编\s*辑$/ }).click()
    check(await card.evaluate(el => el.scrollWidth <= el.clientWidth), 'Official 卡片横向溢出')
    const path = `/tmp/yuxi-official-options-${theme}-${width}.png`
    await card.screenshot({ path })
    screenshots.push(path)
    await card.getByRole('button', { name: /^取\s*消$/ }).click()
  }
  await card.hover()
  await card.getByRole('button', { name: /^编\s*辑$/ }).click()
  await card.getByRole('checkbox', { name: '清除已保存凭据' }).check()
  check(await card.getByLabel('API Key', { exact: true }).isDisabled(), '清除时凭据输入框仍可编辑')
  check((await save()).api_key === '', '明确清除没有发送空值')
  check((await read()).sensitive_state.api_key.source !== 'database', '数据库凭据未清除')
  return { default: 'pipeline', persisted: 'vlm', preserveKey: 'passed', explicitClear: 'passed', screenshots }
}
