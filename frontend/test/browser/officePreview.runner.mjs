// 用系统 Chrome 跑 officePreview.js。仓库里的浏览器脚本默认交给 playwright-cli；
// 当前环境没有该命令时，用已安装的 playwright-core 提供同一个 page。
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const require = createRequire(import.meta.url)

const loadPlaywright = () => {
  try {
    return require('playwright-core')
  } catch (error) {
    throw new Error(
      '未安装 playwright-core。请改用 playwright-cli run-code --filename=frontend/test/browser/officePreview.js，或在能 require("playwright-core") 的环境运行本脚本。',
      { cause: error }
    )
  }
}

const { chromium } = loadPlaywright()
const source = readFileSync(resolve(here, 'officePreview.js'), 'utf8')
const verifyOfficePreview = new Function(`return (${source}\n)`)()
const executablePath = process.env.CHROME_PATH || '/usr/local/bin/google-chrome'
const browser = await chromium.launch({
  executablePath,
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage']
})

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  page.on('pageerror', (error) => console.error('PAGE', error))
  page.on('console', (message) => {
    if (message.type() === 'error') console.error('CONSOLE', message.text())
  })
  const result = await verifyOfficePreview(page)
  console.log(JSON.stringify(result))
} finally {
  await browser.close()
}
