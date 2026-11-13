// 独立 Vite 会话：playwright-cli -s=account-actions run-code --filename=frontend/test/browser/accountActions.js
// 复用真实设置弹窗，以浏览器内合成 API 验证 UI；改密持久化与登录由后端 HTTP integration 验证。
// prettier-ignore
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const days = Array.from({ length: 365 }, (_, index) => ({
    date: new Date(Date.UTC(2025, 10, 14 + index)).toISOString().slice(0, 10), message_count: index % 9
  }))
  const submissions = []
  await page.route('**/api/**', async route => {
    const path = '/' + route.request().url().split('/').slice(3).join('/').split('?')[0]
    if (path === '/api/auth/password') {
      const body = route.request().postDataJSON()
      submissions.push(body)
      if (body.current_password !== 'FixturePassword123!') return route.fulfill({ status: 422, json: { detail: { code: 'incorrect_current_password', message: '原密码不正确' } } })
      return route.fulfill({ json: { message: '密码修改成功' } })
    }
    return route.fulfill({ json: path === '/api/user/message-activity' ? { daily_message_counts: days } : path === '/api/user/config' ? { enable_memory: false } : {} })
  })
  const openMenu = () => page.getByRole('button', { name: '更多账户操作', exact: true }).click()
  const menu = page.locator('.ant-dropdown:visible')
  const modal = page.getByRole('dialog').filter({ hasText: '保存密码' })
  const settleAnimations = () => page.evaluate(async () => {
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    await Promise.all(document.getAnimations().filter(animation => Number.isFinite(animation.effect.getComputedTiming().endTime)).map(animation => animation.finished.catch(() => {})))
  })
  try {
    await page.goto(`${origin}/test/browser/settingsStyles.html`)
    const account = page.locator('.account-settings')
    await account.locator('.heatmap-cell').last().waitFor()
    await page.evaluate(async () => {
      const { useUserStore } = await import('/src/modules/identity/model/user.js')
      useUserStore().userRole = 'user'
    })
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => window.settingsStyles.setTheme(theme), theme)
      for (const width of [768, 1024, 1440, 1920]) {
        await page.setViewportSize({ width, height: 1000 })
        await settleAnimations()
        const bounds = await account.evaluate(el => {
          const cells = [...el.querySelectorAll('.heatmap-cell')]
          const first = cells[0].getBoundingClientRect()
          const label = el.querySelector('.heatmap-weekday-labels span').getBoundingClientRect()
          return { cellWidth: first.width, cellHeight: first.height, count: cells.filter(cell => cell.title).length, alignment: Math.abs(label.y + label.height / 2 - first.y - first.height / 2), overflow: el.scrollWidth > el.clientWidth + 1 }
        })
        check(Math.abs(bounds.cellWidth - bounds.cellHeight) < 1 && bounds.alignment < 1, '年度格子应为正方形且星期对齐')
        check(bounds.count === 365 && !bounds.overflow, `日期必须完整且不能横向溢出：${theme} ${width} ${JSON.stringify(bounds)}`)
        await openMenu()
        await menu.waitFor()
        await settleAnimations()
        check(await menu.getByRole('menuitem', { name: '修改密码', exact: true }).count() === 1, '普通用户应能打开修改密码')
        check(await menu.getByRole('menuitem', { name: '复制用户 ID', exact: true }).count() === 1, '菜单必须包含复制用户 ID')
        const box = await menu.boundingBox()
        check(box.x >= 0 && box.x + box.width <= width, '菜单不能超出视口')
        if (width === 1440) await page.screenshot({ path: `/tmp/yuxi-account-actions-${theme}.png` })
        await menu.getByRole('menuitem', { name: '修改密码', exact: true }).click()
        await modal.waitFor()
        await settleAnimations()
        const modalBox = await modal.boundingBox()
        check(modalBox.x >= 0 && modalBox.x + modalBox.width <= width, '改密弹窗不能超出视口')
        if (width === 1440) await page.screenshot({ path: `/tmp/yuxi-account-password-${theme}.png` })
        await modal.getByRole('button', { name: /^取\s*消$/ }).click()
        await modal.waitFor({ state: 'hidden' })
      }
    }
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
    await openMenu()
    await menu.getByRole('menuitem', { name: '复制用户 ID', exact: true }).click()
    check(await page.evaluate(() => navigator.clipboard.readText()) === 'fixture-settings-user', '必须复制完整用户 ID')
    await openMenu()
    await menu.getByRole('menuitem', { name: '修改密码', exact: true }).click()
    await modal.getByRole('button', { name: '保存密码', exact: true }).click()
    await modal.getByText('请输入原密码', { exact: true }).waitFor()
    check(submissions.length === 0, '空表单不能提交')
    await modal.getByLabel('原密码', { exact: true }).fill('incorrect')
    await modal.getByLabel('新密码', { exact: true }).fill('short')
    await modal.getByLabel('确认新密码', { exact: true }).fill('different')
    await modal.getByRole('button', { name: '保存密码', exact: true }).click()
    await modal.getByText('两次输入的新密码不一致', { exact: true }).waitFor()
    check(submissions.length === 0, '短密码和确认不一致不能提交')
    await modal.getByLabel('新密码', { exact: true }).fill('NewFixturePassword456!')
    await modal.getByLabel('确认新密码', { exact: true }).fill('NewFixturePassword456!')
    await modal.getByRole('button', { name: '保存密码', exact: true }).click()
    await modal.getByRole('alert').filter({ hasText: '原密码不正确' }).waitFor()
    check(await modal.getByLabel('新密码', { exact: true }).inputValue() === 'NewFixturePassword456!', '服务失败应保留输入便于修正')
    await modal.getByLabel('原密码', { exact: true }).fill('FixturePassword123!')
    await modal.getByRole('button', { name: '保存密码', exact: true }).click()
    await modal.waitFor({ state: 'hidden' })
    check(submissions.length === 2 && !('confirmation' in submissions[1]), '确认密码不能发送到 API')
    await openMenu()
    await menu.getByRole('menuitem', { name: '修改密码', exact: true }).click()
    check(await modal.getByLabel('原密码', { exact: true }).inputValue() === '' && await modal.getByLabel('新密码', { exact: true }).inputValue() === '', '重新打开不得保留密码')
    await modal.getByRole('button', { name: /^取\s*消$/ }).click()
    await modal.waitFor({ state: 'hidden' })
    await page.evaluate(async () => {
      const { useUserStore } = await import('/src/modules/identity/model/user.js')
      useUserStore().uid = 'oidc:fixture-enterprise'
    })
    await openMenu()
    const enterprisePassword = menu.getByRole('menuitem', { name: '修改密码', exact: true })
    check(await enterprisePassword.getAttribute('aria-disabled') === 'true', '企业账号必须禁用本地改密')
    check((await enterprisePassword.getAttribute('title')).includes('统一身份服务'), '企业账号应说明改密归属')
    await menu.getByRole('menuitem', { name: '复制用户 ID', exact: true }).click()
    check(await page.evaluate(() => navigator.clipboard.readText()) === 'oidc:fixture-enterprise', '企业账号应复制完整 ID')
    return { themes: ['light', 'dark'], widths: [768, 1024, 1440, 1920], validation: true, errors: true, copy: true, enterprise: true }
  } finally {
    await page.unrouteAll({ behavior: 'wait' })
  }
}
