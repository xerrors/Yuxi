// 使用独立浏览器会话运行；所有 API 都在浏览器内拦截，不访问或修改真实业务数据。
// playwright-cli -s=settings-style-review run-code --filename=frontend/test/browser/settingsStyles.js
// prettier-ignore
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const originalViewport = page.viewportSize()
  const originalTheme = await page.evaluate(() => localStorage.getItem('theme'))
  const artifactDirectory = '/tmp/yuxi-settings-style-artifacts'
  const screenshots = []
  const failures = []
  const writes = []
  const requests = []
  const modes = new Map()
  const pending = new Map()
  const labels = {
    account: '账户设置', apiKeys: 'API Keys', base: '基本设置', ocr: 'OCR 配置',
    webSearch: '网络搜索', user: '用户管理', department: '部门管理', agentEnv: 'Agent 环境'
  }
  const selectors = {
    menu: '.user-info-component', account: '.account-settings', apiKeys: '.apikey-management', base: '.basic-settings-section',
    ocr: '.ocr-settings-section', webSearch: '.web-settings', user: '.user-management',
    department: '.department-management', agentEnv: '.agent-env-settings',
    schedules: '.inline-editor', skill: '.skill-detail', providers: '.model-provider-manage-panel', debug: '.debug-modal'
  }
  const longName = '用于验证设置列表长名称与操作边界的知识平台研发部门'
  const options = [
    {
      key: 'mineru_ocr_host_opts', name: 'MinerU 自托管',
      description: '文档版面与内容解析服务，长说明应在卡片内自然换行。',
      value: { server_url: '', tier: 'standard' },
      params: { fields: [
        { key: 'server_url', label: '服务地址', environment: 'MINERU_API_URI', default: '' },
        { key: 'tier', label: '解析档位', choices: ['standard', 'advanced'], choice_labels: { standard: '标准（Standard）', advanced: '高精度（Advanced）' }, help: '服务必须开放所选档位', default: 'standard' }
      ] }
    },
    {
      key: 'mineru_official_api_opts', name: 'MinerU 官方 API', description: '验证单字段配置的标题与凭据布局。',
      value: { api_key: '' }, sensitive_state: { api_key: { configured: false, source: 'none' } },
      params: { fields: [{ key: 'api_key', label: 'API Key', environment: 'MINERU_API_KEY', sensitive: true }] }
    },
    {
      key: 'remote_skill_source_policy', value: { allowed_hosts: ['github.com', 'skills.example.com'] },
      params: { fields: [{ key: 'allowed_hosts', label: '允许的来源域名', default: [] }] }
    },
    ...['tavily', 'deepseek', 'doubao'].map((id) => ({
      key: `${id}_search_opts`, name: { tavily: 'Tavily', deepseek: 'DeepSeek', doubao: '豆包搜索' }[id],
      value: { api_key: '' },
      sensitive_state: { api_key: { configured: id !== 'doubao', source: id === 'doubao' ? 'none' : 'environment' } },
      params: { provider: { id, supports_fetch: id !== 'doubao', environment: `${id.toUpperCase()}_API_KEY` } }
    }))
  ]
  const departments = [{ id: 7, name: longName, description: `${longName}，用于验证长说明的换行与表格滚动。`, user_count: 3 }]
  const users = [{ id: 101, uid: 'fixture-settings-user-with-a-long-readable-identifier', username: '设置样式验证用户', role: 'superadmin', department_id: 7, department_name: longName, phone_number: '', user_kind: 'human', last_login: '2026-10-23T00:00:00Z' }]
  const apiKeys = [{ id: 4, name: '知识平台外部调用与长名称显示验证密钥', key_prefix: 'fixture_', access_level: 'agents', app_id: 'fixture-knowledge-platform-client', is_enabled: true, last_used_at: null, expires_at: null }]
  departments.push({ id: 8, name: '产品设计部', description: '第二行用于核对行分隔线', user_count: 2 })
  users.push({ ...users[0], id: 102, uid: 'fixture-second-user', username: '第二位测试用户' })
  apiKeys.push({ ...apiKeys[0], id: 5, name: '检索服务测试密钥', key_prefix: 'sample_' })
  const skill = {
    slug: 'settings-style-fixture', name: '设置样式隔离验证 Skill', description: '仅在浏览器内存在的合成 Skill',
    dir_path: '/fixture/skill', enabled: true, source_type: 'local', can_manage: true,
    share_config: { version: 2, read_scope: { access_level: 'user', department_ids: [], user_uids: [] }, manage_scope: null }
  }
  const provider = {
    provider_id: 'fixture', display_name: '样式隔离供应商', is_enabled: true, credential_status: 'ok',
    provider_type: 'openai', capabilities: ['chat'], base_url: 'https://api.example.com',
    enabled_models: [{ id: 'fixture-chat', name: '样式隔离模型', type: 'chat', source: 'manual' }]
  }
  const config = {
    default_model: 'fixture:chat', fast_model: 'fixture:chat', reference_model: '',
    embed_model: 'fixture:embedding', reranker: 'fixture:rerank', max_cooperation_depth: 2,
    default_ocr_engine: 'mineru', web_search_provider: 'tavily', web_fetch_provider: 'deepseek'
  }
  /** 在浏览器内响应全部业务请求，拒绝写入与未知端点。 */
  const routeHandler = async route => {
    const request = route.request()
    const path = ('/' + request.url().split('/').slice(3).join('/').split('?')[0]).replace(/\/$/, '')
    requests.push(`${request.method()} ${path}`)
    const respond = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (request.method() !== 'GET') {
      writes.push(`${request.method()} ${path}`)
      return respond({ detail: { code: 'fixture_write_forbidden', message: '样式验证禁止写入' } }, 403)
    }
    const mode = modes.get(path)
    if (mode === 'loading') await new Promise(resolve => pending.set(path, resolve))
    if (mode === 'error') return respond({ detail: { code: 'fixture_unavailable', message: '样式验证：服务暂不可用，请重试' } }, 503)
    switch (path) {
      case '/api/system/config': return respond(config)
      case '/api/system/config/options': return respond({ options: mode === 'empty' ? [] : options })
      case '/api/user/message-activity': return respond({ daily_message_counts: [] })
      case '/api/user/config': return respond({ enable_memory: true })
      case '/api/user/apikey': return respond({ api_keys: mode === 'empty' ? [] : apiKeys })
      case '/api/auth/users/page': return respond({ items: mode === 'empty' ? [] : users, total: mode === 'empty' ? 0 : users.length })
      case '/api/departments': return respond(mode === 'empty' ? [] : departments)
      case '/api/user/agent-env': return respond({ env: { FIXTURE_ENVIRONMENT_WITH_LONG_NAME: '浏览器内合成数据' }, updated_at: '2026-10-23T00:00:00Z' })
      case '/api/user/sandbox': return respond({ backend: 'container', state: 'not_created', busy: false, active_runs: [], active_operations: 0 })
      case '/api/system/ocr/options': return respond({ option_keys: ['mineru_ocr_host_opts', 'mineru_official_api_opts'], engines: [{ engine_id: 'mineru', display_name: 'MinerU', description: '文档解析' }, { engine_id: 'disable', display_name: '不使用 OCR' }] })
      case '/api/system/ocr/health': return respond({ health: {} })
      case '/api/system/model-providers/models/v2': return respond({ success: true, data: {} })
      case '/api/system/model-providers': return respond({ data: [provider] })
      case '/api/system/model-providers/fixture/remote-models': return respond({ data: [] })
      case '/api/system/logs': return respond({ log: '2026-10-23 08:00:00 | INFO | 样式隔离验证日志' })
      case '/api/projects': return respond([])
      case '/api/system/skills': return respond({ data: [skill], allowed_access_levels: ['user'] })
      case '/api/system/skills/settings-style-fixture': return respond({ data: skill })
      case '/api/system/skills/settings-style-fixture/content': return respond({ data: { files: { 'SKILL.md': '---\nname: settings-style-fixture\ndescription: 浏览器内样式隔离验证\n---\n# 样式隔离验证\n\n浏览器合成内容。\n' }, tree: [{ name: 'SKILL.md', path: 'SKILL.md', is_dir: false }], revision: 'fixture-revision' } })
      case '/api/system/skills/dependency-options': return respond({ data: { tools: [], mcps: [], skills: [] } })
      case '/api/auth/users/access-options': return respond(users)
      case '/api/auth/me': return respond({ ...users[0], department_name: longName })
      default:
        failures.push(`未声明的 API：${path}`)
        return respond({ detail: { code: 'fixture_unknown_request', message: '样式验证未声明此请求' } }, 501)
    }
  }
  const visiblePanel = tab => page.locator(`${selectors[tab]}:visible`)
  /** 等待真实面板与有限动画稳定后交还 DOM。 */
  const mount = async (mode, tab) => {
    await page.evaluate(({ mode, tab }) => window.settingsStyles.mount(mode, tab), { mode, tab })
    const panel = visiblePanel(tab)
    await panel.waitFor()
    await panel.locator('.ant-spin-spinning').waitFor({ state: 'hidden' })
    await waitForAnimations()
    return panel
  }
  const waitForAnimations = async () => page.evaluate(async () => {
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    const animations = document.getAnimations().filter(animation => animation.effect.getTiming().iterations !== Infinity)
    await Promise.all(animations.map(animation => animation.finished.catch(() => {})))
  })
  const screenshot = async name => {
    await waitForAnimations()
    await page.waitForFunction(() => document.querySelectorAll('.ant-message-notice').length === 0)
    const path = `${artifactDirectory}/${name}.png`
    await page.screenshot({ path, fullPage: true, animations: 'disabled' })
    screenshots.push(path)
  }
  /** 按信息层级验收真实级联结果，保留角色之间的间距差异。 */
  const checkStyles = async (panel, context) => {
    const result = await panel.evaluate(element => {
      const visible = el => el.getClientRects().length > 0
      const descriptions = [...element.querySelectorAll('.settings-section-description, .settings-form-description, .settings-service-description')].filter(visible)
      const titles = [...element.querySelectorAll('.settings-section-title')].filter(visible)
      const labels = [...element.querySelectorAll('.settings-form-label')].filter(visible)
      const rootStyle = getComputedStyle(document.documentElement)
      const probe = document.createElement('span')
      probe.style.color = rootStyle.getPropertyValue('--color-text-secondary').trim()
      document.body.appendChild(probe)
      const secondary = getComputedStyle(probe).color
      probe.remove()
      const style = el => {
        const css = getComputedStyle(el)
        return { text: el.textContent.trim().slice(0, 32), fontSize: css.fontSize, fontWeight: css.fontWeight, lineHeight: css.lineHeight, color: css.color, marginTop: css.marginTop, marginBottom: css.marginBottom, inHeader: Boolean(el.closest('.settings-header')), sectionDescription: el.classList.contains('settings-section-description') }
      }
      return {
        titles: titles.map(style), descriptions: descriptions.map(style), labels: labels.map(style),
        secondary,
        headers: [...element.querySelectorAll('.settings-header')].filter(visible).map(el => {
          const css = getComputedStyle(el)
          return { gap: css.gap, marginBottom: css.marginBottom, flexWrap: css.flexWrap }
        }),
        actions: [...element.querySelectorAll('.settings-header-actions')].filter(visible).map(el => {
          const css = getComputedStyle(el)
          return { gap: css.gap, flexWrap: css.flexWrap }
        })
      }
    })
    for (const title of result.titles) {
      check(title.fontSize === '16px' && title.fontWeight === '600' && Math.abs(parseFloat(title.lineHeight) - 22.4) < 0.1, `${context} 标题层级漂移：${JSON.stringify(title)}`)
      check(title.marginTop === '0px' && title.marginBottom === (title.inHeader ? '6px' : '12px'), `${context} 标题间距漂移：${JSON.stringify(title)}`)
    }
    for (const description of result.descriptions) {
      check(description.fontSize === '12px' && Math.abs(parseFloat(description.lineHeight) - 19.2) < 0.1, `${context} 说明层级漂移：${JSON.stringify(description)}`)
      check(description.color === result.secondary, `${context} 说明未跟随次级文字 token：${JSON.stringify(description)}`)
      if (description.sectionDescription) {
        check(description.marginBottom === (description.inHeader ? '0px' : '12px'), `${context} 分区说明间距漂移：${JSON.stringify(description)}`)
      }
    }
    for (const label of result.labels) check(label.fontSize === '13px' && label.fontWeight === '500', `${context} 表单标签漂移：${JSON.stringify(label)}`)
    for (const header of result.headers) check(header.gap === '16px' && header.marginBottom === '16px', `${context} 标题操作区间距漂移：${JSON.stringify(header)}`)
    for (const actions of result.actions) check(actions.gap === '8px' && actions.flexWrap === 'wrap', `${context} 标题操作未统一换行：${JSON.stringify(actions)}`)
    check(result.titles.length > 0, `${context} 没有可验证的分区标题`)
    return { titles: result.titles.length, descriptions: result.descriptions.length, labels: result.labels.length }
  }
  /** 检查面板外溢及标题和操作的边界。 */
  const checkBoundaries = async (panel, context) => {
    const violations = await panel.evaluate(element => {
      const visible = el => el.getClientRects().length > 0
      const targets = [element, ...element.querySelectorAll('.settings-header, .settings-form-panel, .settings-service-card, .settings-table-wrapper')].filter(visible)
      return targets.flatMap(el => {
        const box = el.getBoundingClientRect()
        const issues = []
        if (el.scrollWidth > el.clientWidth + 1) issues.push(`${el.className}: ${el.scrollWidth} > ${el.clientWidth}`)
        if (box.left < -1 || box.right > window.innerWidth + 1) issues.push(`${el.className}: 横向超出视口`)
        return issues
      })
    })
    check(violations.length === 0, `${context} 布局溢出：${violations.join('; ')}`)
    const headers = panel.locator('.settings-header')
    for (let index = 0; index < await headers.count(); index++) {
      const header = headers.nth(index)
      const copy = header.locator('.settings-header-copy')
      const actions = header.locator('.settings-header-actions')
      if (!await actions.count()) continue
      const a = await copy.boundingBox()
      const b = await actions.boundingBox()
      check(a.x + a.width <= b.x + 1 || a.y + a.height <= b.y + 1, `${context} 标题与操作重叠`)
    }
  }
  /** 依据容器宽度检查排列、控件宽度与纵排占位。 */
  const checkFormLayout = async (panel, context) => {
    const rows = await panel.locator('.settings-form-row').evaluateAll(elements => elements.map(row => {
      const control = row.querySelector('.settings-form-control')
      const form = row.closest('.settings-form-panel')
      const css = getComputedStyle(row)
      const formCss = getComputedStyle(form)
      return {
        direction: css.flexDirection, gap: css.gap,
        containerWidth: form.clientWidth - parseFloat(formCss.paddingLeft) - parseFloat(formCss.paddingRight),
        controlWidth: control.getBoundingClientRect().width, flexBasis: getComputedStyle(control).flexBasis,
        availableWidth: row.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight),
        limit: control.classList.contains('is-number') ? 88 : control.classList.contains('is-compact') ? 260 : 340
      }
    }))
    for (const row of rows) {
      const narrow = row.containerWidth <= 520
      check(row.direction === (narrow ? 'column' : 'row') && row.gap === (narrow ? '8px' : '16px'), `${context} 表单未按容器宽度排列：${JSON.stringify(row)}`)
      if (narrow) check(row.flexBasis === 'auto', `${context} 窄容器控件仍以宽度值占据纵向高度：${JSON.stringify(row)}`)
      check(row.controlWidth <= row.limit + 1 && row.controlWidth <= row.availableWidth + 1, `${context} 控件宽度超出契约：${JSON.stringify(row)}`)
      if (narrow || row.availableWidth >= 650) check(Math.abs(row.controlWidth - Math.min(row.limit, row.availableWidth)) < 1, `${context} 控件宽度漂移：${JSON.stringify(row)}`)
    }
  }
  /** 检查服务操作的悬停、焦点、编辑与窄容器入口。 */
  const checkServiceActions = async (panel, context) => {
    const card = panel.locator('.settings-service-card').first()
    if (!await card.count()) return
    const actions = card.locator('.settings-service-actions')
    const narrow = await card.evaluate(element => element.closest('.settings-service-list').clientWidth <= 520)
    await page.mouse.move(1, 1)
    await page.evaluate(() => document.activeElement?.blur())
    await page.waitForFunction(({ narrow, selector }) => {
      const panel = document.querySelector(selector)
      return getComputedStyle(panel.querySelector('.settings-service-actions')).opacity === (narrow ? '1' : '0')
    }, { narrow, selector: `${selectors[context.split('/').at(-1)]}:not([style*="display: none"])` })
    await card.hover()
    await actions.evaluate(async element => {
      await Promise.all(element.getAnimations().map(animation => animation.finished.catch(() => {})))
    })
    check(await actions.evaluate(element => getComputedStyle(element).opacity) === '1', `${context} 悬停未显示服务操作`)
    await page.mouse.move(1, 1)
    await actions.getByRole('button').first().focus()
    await page.waitForFunction(() => {
      const actions = document.activeElement?.closest('.settings-service-actions')
      return actions && getComputedStyle(actions).opacity === '1'
    })
    await actions.getByRole('button', { name: /^编\s*辑$/ }).click()
    check(await card.getByRole('button', { name: /^取\s*消$/ }).isVisible(), `${context} 服务编辑入口失效`)
    await checkBoundaries(panel, `${context}/editing`)
    await card.getByRole('button', { name: /^取\s*消$/ }).click()
    if (narrow) {
      const columns = await card.locator('.settings-service-fields').evaluateAll(elements => elements.map(element => getComputedStyle(element).gridTemplateColumns.split(' ').length))
      check(columns.every(count => count === 1), `${context} 窄容器服务字段没有纵向排列`)
    }
  }
  /** 核对实际边界色相对背景的亮度差，透明线直接视为不可见。 */
  const boundaryContrast = (foreground, background) => {
    const channels = color => color.match(/[\d.]+/g).map(Number)
    const fg = channels(foreground), bg = channels(background)
    if (fg[3] === 0) return 1
    const luminance = values => values.slice(0, 3).map(value => {
      const c = value / 255
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
    }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0)
    const a = luminance(fg), b = luminance(bg)
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
  }
  /** 检查管理表格的共享外观与内部横向滚动。 */
  const checkTable = async (panel, context) => {
    const table = panel.locator('.settings-table-wrapper:has(.ant-table)')
    if (!await table.count()) return
    await table.locator('tbody tr.ant-table-row').nth(1).waitFor()
    const styles = await table.evaluate(element => {
      const head = getComputedStyle(element.querySelector('thead th'))
      const body = getComputedStyle(element.querySelector('tbody tr:not(.ant-table-measure-row) td'))
      const content = element.querySelector('.ant-table-content')
      return { dark: document.documentElement.classList.contains('dark'), tableBackground: getComputedStyle(element.querySelector('.ant-table')).backgroundColor, headerBackground: head.backgroundColor, background: getComputedStyle(element).backgroundColor, headerBorder: head.borderBottomColor, rowBorder: body.borderBottomColor, rowBorderWidth: body.borderBottomWidth, headerSize: head.fontSize, headerWeight: head.fontWeight, headerPadding: head.padding, bodyPadding: body.padding, scrollable: content.scrollWidth > content.clientWidth + 1, overflowX: getComputedStyle(content).overflowX }
    })
    check(styles.tableBackground !== 'rgba(0, 0, 0, 0)' && styles.tableBackground === styles.background, `${context} 普通表格背景不应透明：${JSON.stringify(styles)}`)
    check(styles.headerSize === '12px' && styles.headerWeight === '500' && styles.headerPadding === '9px 14px' && styles.bodyPadding === '10px 14px', `${context} 管理表格样式漂移：${JSON.stringify(styles)}`)
    if (styles.dark) {
      check(styles.rowBorderWidth === '1px' && boundaryContrast(styles.rowBorder, styles.background) >= 1.5 && boundaryContrast(styles.headerBorder, styles.headerBackground) >= 1.5 && boundaryContrast(styles.headerBackground, styles.background) >= 1.15, `${context} 暗色表格分隔或表头层级不可辨认：${JSON.stringify(styles)}`)
    }
    if (styles.scrollable) check(['auto', 'scroll'].includes(styles.overflowX), `${context} 宽表格没有内部滚动：${JSON.stringify(styles)}`)
  }
  await page.route('**/api/**', routeHandler)
  const pageErrors = []
  const onPageError = error => pageErrors.push(error.message)
  page.on('pageerror', onPageError)
  try {
    await page.goto(`${origin}/test/browser/settingsStyles.html`)
    await page.waitForFunction(() => Boolean(window.settingsStyles))
    const initialPanel = await mount('modal', 'ocr')
    await initialPanel.locator('.settings-service-card').first().waitFor()
    const oldOverride = await page.addStyleTag({ content: '.settings-modal .settings-content .settings-section-description { font-size:14px;line-height:1.4;margin:0; }' })
    let driftRejected = false
    try {
      await checkStyles(initialPanel, '负向样式漂移')
    } catch (error) {
      driftRejected = error.message.includes('说明层级漂移')
    } finally {
      await oldOverride.evaluate(element => element.remove())
    }
    check(driftRejected, '样式 oracle 没有拒绝旧 Modal 规则覆盖 OCR 说明')
    await checkStyles(initialPanel, '清理旧 Modal 覆盖后的 OCR')
    await page.evaluate(() => window.settingsStyles.setTheme('dark'))
    const boundaryPanel = await mount('modal', 'apiKeys')
    await checkTable(boundaryPanel, '边界修复')
    const transparentTable = await page.addStyleTag({ content: '.settings-table-wrapper .ant-table { background:transparent!important }' })
    let transparentTableRejected = false
    try { await checkTable(boundaryPanel, '透明背景负控') } catch (error) { transparentTableRejected = error.message.includes('普通表格背景不应透明') }
    finally { await transparentTable.evaluate(element => element.remove()) }
    check(transparentTableRejected, '未拒绝普通表格透明背景')
    const hiddenLines = await page.addStyleTag({ content: ':root.dark .settings-table-wrapper th, :root.dark .settings-table-wrapper td {border-bottom-color:transparent!important}' })
    let hiddenLinesRejected = false
    try { await checkTable(boundaryPanel, '恢复透明分隔') } catch (error) { hiddenLinesRejected = error.message.includes('暗色表格分隔') }
    finally { await hiddenLines.evaluate(element => element.remove()) }
    check(hiddenLinesRejected, '未拒绝暗色表格透明分隔线')
    await checkTable(boundaryPanel, '恢复可见分隔')
    await mount('standalone', 'menu')
    await page.locator('.user-info-dropdown').click()
    const menu = page.locator('.user-account-menu:visible')
    await menu.waitFor()
    await waitForAnimations()
    /** 菜单分组线应在浮层底色上可见。 */
    const checkMenuDividers = async () => {
      const colors = await menu.evaluate(element => ({ background: getComputedStyle(element).backgroundColor, dividers: [...element.querySelectorAll('.ant-dropdown-menu-item-divider')].map(item => getComputedStyle(item).backgroundColor) }))
      check(colors.dividers.length === 2 && colors.dividers.every(color => boundaryContrast(color, colors.background) >= 1.3), '账户菜单分组线不可见：'+JSON.stringify(colors))
    }
    await checkMenuDividers()
    await screenshot('dark-account-menu-boundaries')
    const hiddenMenuLines = await page.addStyleTag({ content: '.user-account-menu .ant-dropdown-menu-item-divider {background:transparent!important}' })
    let hiddenMenuLinesRejected = false
    try { await checkMenuDividers() } catch (error) { hiddenMenuLinesRejected = error.message.includes('账户菜单分组线不可见') }
    finally { await hiddenMenuLines.evaluate(element => element.remove()) }
    check(hiddenMenuLinesRejected, '未拒绝账户菜单透明分隔线')
    await checkMenuDividers()
    const coverage = []
    let compactBasisRejected = false
    for (const theme of ['light', 'dark']) {
      await page.evaluate(value => window.settingsStyles.setTheme(value), theme)
      await page.waitForFunction(value => document.documentElement.classList.contains('dark') === (value === 'dark'), theme)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 1000 })
        await mount('modal', 'account')
        for (const tab of Object.keys(labels)) {
          if (tab !== 'account') {
            const navigation = page.locator('.settings-sider-nav:visible, .settings-mobile-nav:visible')
            await navigation.getByText(labels[tab], { exact: true }).click()
          }
          const panel = visiblePanel(tab)
          await panel.waitFor()
          await panel.locator('.ant-spin-spinning').waitFor({ state: 'hidden' })
          await waitForAnimations()
          if (tab === 'ocr') await panel.locator('.ocr-selector-trigger').filter({ hasText: 'MinerU' }).waitFor()
          const context = `${theme}/${width}/${tab}`
          const styles = await checkStyles(panel, context)
          await checkBoundaries(panel, context)
          await checkFormLayout(panel, context)
          await checkServiceActions(panel, context)
          await checkTable(panel, context)
          coverage.push({ theme, width, tab, ...styles })
          await screenshot(`${theme}-${width}-${tab}`)
        }
      }
      for (const tab of ['account', 'apiKeys', 'user', 'department']) {
        const panel = await mount('standalone', tab)
        await checkStyles(panel, `${theme}/独立/${tab}`)
        await checkBoundaries(panel, `${theme}/独立/${tab}`)
        await checkTable(panel, `${theme}/独立/${tab}`)
        await screenshot(`${theme}-standalone-${tab}`)
      }
      for (const tab of ['base', 'ocr', 'webSearch']) {
        const panel = await mount('modal', tab)
        const previousStyle = await panel.getAttribute('style')
        try {
          await panel.evaluate(element => { element.style.maxWidth = '500px' })
          if (theme === 'light' && tab === 'ocr') {
            const oldBasis = await page.addStyleTag({ content: '.settings-form-control.is-compact { flex-basis:260px !important; }' })
            try {
              await checkFormLayout(panel, '负向窄容器布局')
            } catch (error) {
              compactBasisRejected = error.message.includes('窄容器控件仍以宽度值占据纵向高度')
            } finally {
              await oldBasis.evaluate(element => element.remove())
            }
            check(compactBasisRejected, '布局 oracle 没有拒绝窄容器中的旧 compact flex-basis')
          }
          await checkFormLayout(panel, `${theme}/500容器/${tab}`)
          await checkServiceActions(panel, `${theme}/500容器/${tab}`)
          await checkBoundaries(panel, `${theme}/500容器/${tab}`)
          await screenshot(`${theme}-500-container-${tab}`)
        } finally {
          await panel.evaluate((element, value) => {
            if (value === null) element.removeAttribute('style')
            else element.setAttribute('style', value)
          }, previousStyle)
        }
      }
      const narrowAccount = await mount('standalone', 'account')
      await narrowAccount.evaluate(element => { element.style.maxWidth = '480px' })
      check(await narrowAccount.locator('.settings-header').first().evaluate(element => getComputedStyle(element).flexDirection) === 'column', `${theme} 480px 容器标题操作没有纵排`)
      await checkBoundaries(narrowAccount, `${theme}/480容器/account`)
      await screenshot(`${theme}-480-container-account`)
      await narrowAccount.evaluate(element => element.style.removeProperty('max-width'))
    }
    for (const [tab, path] of [['apiKeys', '/api/user/apikey'], ['user', '/api/auth/users/page'], ['department', '/api/departments']]) {
      modes.set(path, 'empty')
      const empty = await mount('standalone', tab)
      await empty.locator('.ant-empty').waitFor()
      await checkBoundaries(empty, `${tab}/empty`)
      await screenshot(`empty-${tab}`)
      modes.set(path, 'error')
      const error = await mount('standalone', tab)
      await error.getByRole('alert').waitFor()
      await checkBoundaries(error, `${tab}/error`)
      await screenshot(`error-${tab}`)
      modes.set(path, 'loading')
      await page.evaluate(tab => window.settingsStyles.mount('standalone', tab), tab)
      const loading = visiblePanel(tab)
      await page.waitForFunction(selector => document.querySelector(`${selector} .ant-spin-spinning`)?.getAttribute('aria-busy') === 'true', selectors[tab])
      await screenshot(`loading-${tab}`)
      check(pending.has(path), `${tab} 没有等待中的真实 HTTP 请求`)
      modes.delete(path)
      pending.get(path)()
      pending.delete(path)
      await loading.locator('.ant-spin-spinning').waitFor({ state: 'hidden' })
      await loading.locator('tbody tr:not(.ant-table-measure-row)').first().waitFor()
    }
    // 长列表必须在弹窗内部滚动，导航和关闭入口保持在视口内。
    departments.push(...Array.from({ length: 35 }, (_, index) => ({ id: 100 + index, name: `布局测试部门 ${index + 1}`, description: longName, user_count: 1 })))
    /** 从 DOM 边界和实际滚动结果证明设置弹窗没有随内容无限增高。 */
    const checkModalScroll = async () => {
      const result = await page.locator('.settings-modal').evaluate(modal => {
        const box = modal.querySelector('.ant-modal-content').getBoundingClientRect()
        const close = modal.querySelector('.settings-close-btn').getBoundingClientRect()
        const content = modal.querySelector('.settings-content')
        content.scrollTop = content.scrollHeight
        return { contained: box.top >= 0 && box.bottom <= innerHeight, closeVisible: close.top >= 0 && close.bottom <= innerHeight, scrolled: content.scrollTop > 0 }
      })
      check(result.contained && result.closeVisible && result.scrolled, `设置长列表未在视口内滚动：${JSON.stringify(result)}`)
    }
    for (const width of [1440, 1024, 768]) {
      await page.setViewportSize({ width, height: 1000 })
      await mount('modal', 'department')
      await page.locator('.settings-modal tbody tr').nth(20).waitFor()
      await checkModalScroll()
      await screenshot(`long-departments-${width}`)
    }
    const unboundedModal = await page.addStyleTag({ content: '.settings-container {height:auto!important;min-height:75vh!important}.settings-content-wrapper {min-height:auto!important;height:100%!important}' })
    let unboundedModalRejected = false
    try { await checkModalScroll() } catch (error) { unboundedModalRejected = error.message.includes('设置长列表未在视口内滚动') }
    finally { await unboundedModal.evaluate(element => element.remove()) }
    check(unboundedModalRejected, '未拒绝设置弹窗随长列表无限增高')
    await checkModalScroll()
    const isolation = []
    for (const theme of ['light', 'dark']) {
      await page.evaluate(value => window.settingsStyles.setTheme(value), theme)
      await page.setViewportSize({ width: 1280, height: 1000 })
      const schedule = await mount('standalone', 'schedules')
      const scheduleStyles = await schedule.evaluate(element => ({
        cards: [...element.querySelectorAll('.settings-card')].map(card => getComputedStyle(card).padding),
        sections: [...element.querySelectorAll('.settings-section')].map(section => getComputedStyle(section).marginBottom)
      }))
      check(scheduleStyles.cards.length > 0 && scheduleStyles.cards.every(padding => padding === '0px'), `${theme} 定时任务卡片受设置样式污染：${JSON.stringify(scheduleStyles)}`)
      check(scheduleStyles.sections.length > 0 && scheduleStyles.sections.every(margin => margin === '0px'), `${theme} 定时任务分区受设置样式污染：${JSON.stringify(scheduleStyles)}`)
      await screenshot(`${theme}-isolated-schedule`)
      const detail = await mount('standalone', 'skill')
      await detail.locator('.skill-editor-path').filter({ hasText: 'SKILL.md' }).waitFor()
      await detail.getByRole('tab', { name: '配置', exact: true }).click()
      await detail.locator('.settings-card:visible').first().waitFor()
      const skillStyles = await detail.locator('.settings-card:visible').evaluateAll(cards => cards.map(card => {
        const css = getComputedStyle(card)
        return { background: css.backgroundColor, borderTop: css.borderTopWidth, borderRight: css.borderRightWidth, borderBottom: css.borderBottomWidth, borderLeft: css.borderLeftWidth, radius: css.borderRadius, padding: css.padding }
      }))
      check(skillStyles.every(css => css.background === 'rgba(0, 0, 0, 0)' && css.borderTop === '0px' && css.borderRight === '0px' && css.borderLeft === '0px' && css.radius === '0px' && css.padding === '18px 0px'), `${theme} Skill 配置行受设置卡片表面污染：${JSON.stringify(skillStyles)}`)
      await screenshot(`${theme}-isolated-skill`)
      const providers = await mount('standalone', 'providers')
      await providers.getByRole('button', { name: '选择 样式隔离供应商 模型', exact: true }).click()
      const modelDialog = page.getByRole('dialog', { name: '样式隔离供应商 - 模型配置', exact: true })
      await modelDialog.locator('.model-name').filter({ hasText: '样式隔离模型' }).waitFor()
      check(await modelDialog.locator('[class*="settings-section-"], .settings-form-control').count() === 0, `${theme} 模型列表意外使用设置面板皮肤`)
      const modelTitle = await modelDialog.locator('.models-section-title').first().evaluate(element => ({ size: getComputedStyle(element).fontSize, weight: getComputedStyle(element).fontWeight }))
      check(modelTitle.size === '13px' && modelTitle.weight === '600', `${theme} 模型列表标题受设置样式污染：${JSON.stringify(modelTitle)}`)
      await screenshot(`${theme}-isolated-model-list`)
      await modelDialog.getByRole('button', { name: 'Close', exact: true }).click()
      const debug = await mount('standalone', 'debug')
      await debug.getByPlaceholder('过滤日志关键字...').waitFor()
      check(await debug.locator('[class*="settings-section-"], .settings-form-control').count() === 0, `${theme} Debug 意外使用设置面板皮肤`)
      const debugInput = await debug.getByPlaceholder('过滤日志关键字...').evaluate(element => ({ height: element.getBoundingClientRect().height, size: getComputedStyle(element).fontSize }))
      check(debugInput.height > 0 && debugInput.size === '14px', `${theme} Debug 基础输入框异常：${JSON.stringify(debugInput)}`)
      await screenshot(`${theme}-isolated-debug`)
      isolation.push({ theme, schedule: scheduleStyles, skill: skillStyles, modelTitle, debugInput })
    }
    check(writes.length === 0, `样式检查产生写入请求：${writes.join(', ')}`)
    check(failures.length === 0, failures.join('; '))
    check(pageErrors.length === 0, `浏览器异常：${pageErrors.join('; ')}`)
    return { themes: ['light', 'dark'], widths: [1440, 1024, 768], panels: Object.keys(labels), checkedContexts: coverage.length, hiddenLinesRejected, hiddenMenuLinesRejected, unboundedModalRejected, driftRejected, compactBasisRejected, standalonePanels: 4, isolation, states: ['loading', 'empty', 'error'], writes: writes.length, interceptedRequests: requests.length, screenshotCount: screenshots.length, artifactDirectory }
  } finally {
    for (const resolve of pending.values()) resolve()
    await page.evaluate(value => {
      window.settingsStyles?.setTheme(value ?? 'light')
      if (value === null) localStorage.removeItem('theme')
      window.settingsStyles?.destroy()
    }, originalTheme)
    if (originalViewport) await page.setViewportSize(originalViewport)
    page.off('pageerror', onPageError)
    await page.unroute('**/api/**', routeHandler)
  }
}
