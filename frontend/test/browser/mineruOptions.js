// 在已登录的真实页面运行，验证全局配置保存、重载、主题与布局；使用测试环境。
// playwright-cli -s=<session> run-code --filename=frontend/test/browser/mineruOptions.js
// prettier-ignore
async page => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const panel = page.locator('.ocr-settings-section')
  const card = panel.locator('.settings-service-card').filter({ has: page.getByRole('heading', { name: 'MinerU 自托管', exact: true }) })
  const open = async () => {
    await page.locator('.user-info-dropdown:visible').click()
    await page.getByRole('menuitem', { name: '设置', exact: true }).click()
    await page.getByText('OCR 配置', { exact: true }).filter({ visible: true }).first().click()
    await card.waitFor()
  }
  if (!(await panel.isVisible())) await open()
  const cancel = card.getByRole('button', { name: /^取\s*消$/ })
  if (await cancel.count()) await cancel.click()
  const original = await page.evaluate(async () => {
    const { configOptionsApi } = await import('/src/apis/system_api.js')
    const data = await configOptionsApi.getOptions()
    return data.options.find(option => option.key === 'mineru_ocr_host_opts').value
  })
  try {
    await card.hover()
    await card.getByRole('button', { name: /^编\s*辑$/ }).click()
    const tier = card.getByRole('combobox', { name: '解析档位' })
    await tier.focus()
    await tier.press('ArrowDown')
    await page.getByTitle('高精度（Advanced）', { exact: true }).click()
    const saved = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().endsWith('/config/options/mineru_ocr_host_opts'))
    await card.getByRole('button', { name: /^保\s*存$/ }).click()
    check((await saved).ok(), '真实配置保存失败')
    await card.getByRole('textbox', { name: '解析档位' }).waitFor()
    await page.reload()
    await open()
    check(await card.getByRole('textbox', { name: '解析档位' }).inputValue() === '高精度（Advanced）', '重载档位选择丢失')
    const screenshots = []
    for (const [width, theme] of [[1440, 'light'], [768, 'dark']]) {
      await page.setViewportSize({ width, height: 1000 })
      await page.evaluate(async value => {
        const { useThemeStore } = await import('/src/shared/model/theme.js')
        useThemeStore().setTheme(value)
      }, theme)
      await card.hover()
      await card.getByRole('button', { name: /^编\s*辑$/ }).click()
      check(await card.getByRole('combobox').count() === 1, '解析选项没有渲染为下拉框')
      check((await card.innerText()).includes('服务必须开放所选档位'), '缺少档位适用范围说明')
      check(await card.evaluate(el => el.scrollWidth <= el.clientWidth), '设置卡片横向溢出')
      const path = `/tmp/yuxi-mineru-options-${theme}-${width}.png`
      await card.screenshot({ path })
      screenshots.push(path)
      await card.getByRole('button', { name: /^取\s*消$/ }).click()
    }
    return { save: 'passed', reload: 'passed', themes: ['light', 'dark'], widths: [1440, 768], screenshots }
  } finally {
    await page.evaluate(async value => {
      const { configOptionsApi } = await import('/src/apis/system_api.js')
      await configOptionsApi.updateOption('mineru_ocr_host_opts', {
        tier: value.tier ?? 'standard'
      })
    }, original)
  }
}
