// 在已登录测试环境验证云端三模型共用配置，以及退役服务不再出现。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/paddleocrOptions.js
// prettier-ignore
async page => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  await page.goto(`${page.url().split('/').slice(0, 3).join('/')}/agent`)
  await page.locator('.user-info-dropdown:visible').click()
  await page.getByRole('menuitem', { name: '设置', exact: true }).click()
  await page.getByText('OCR 配置', { exact: true }).filter({ visible: true }).first().click()
  const panel = page.locator('.ocr-settings-section')
  const card = panel.locator('.settings-service-card').filter({ has: page.getByRole('heading', { name: 'PaddleOCR API', exact: true }) })
  await card.waitFor()
  check(await panel.locator('.settings-service-card').count() === 3, '服务配置没有收敛为三组')
  for (const model of ['PaddleOCR-VL', 'PP-OCRv6', 'PP-StructureV3']) {
    check((await card.innerText()).includes(model), `共享配置缺少 ${model} 说明`)
  }
  await panel.locator('.ocr-selector-trigger').click()
  await panel.getByLabel('正在检测 OCR 状态').waitFor({ state: 'hidden' })
  const dropdown = page.locator('.ocr-selector-dropdown:visible')
  const unavailable = dropdown.locator('.unavailable-toggle')
  if (await unavailable.count()) await unavailable.click()
  const names = await dropdown.locator('.ocr-option-name').allTextContents()
  for (const name of ['PaddleOCR-VL-1.6', 'PP-OCRv6', 'PP-StructureV3（云服务）']) {
    check(names.includes(name), `缺少官网模型 ${name}`)
  }
  check(!names.includes('PP-Structure-V3'), '旧自托管引擎仍然展示')
  await panel.locator('.ocr-selector-trigger').click()
  const screenshots = []
  for (const [width, theme] of [[1440, 'light'], [768, 'dark']]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.evaluate(async value => {
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      useThemeStore().setTheme(value)
    }, theme)
    await card.hover()
    await card.getByRole('button', { name: /^编\s*辑$/ }).click()
    check(await card.getByLabel('API 地址', { exact: true }).count() === 1, '共享地址字段不唯一')
    check(await card.getByLabel('Access Token', { exact: true }).count() === 1, '共享凭据字段不唯一')
    check(await card.evaluate(el => el.scrollWidth <= el.clientWidth), '共享配置卡片横向溢出')
    const path = `/tmp/yuxi-paddleocr-options-${theme}-${width}.png`
    await card.screenshot({ path })
    screenshots.push(path)
    await card.getByRole('button', { name: /^取\s*消$/ }).click()
  }
  return { models: 'passed', sharedConfiguration: 'passed', retiredEngineAbsent: 'passed', screenshots }
}
