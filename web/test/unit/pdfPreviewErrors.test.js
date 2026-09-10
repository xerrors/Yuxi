import assert from 'node:assert/strict'
import test from 'node:test'
import { resolvePdfLoadErrorMessage } from '../../src/utils/pdfPreviewErrors.js'

test('PDF 错误提示区分文件、网络、加密与 Worker 加载失败', () => {
  const cases = [
    [{ name: 'InvalidPDFException' }, '无法加载 PDF 文件或文件格式受损'],
    [{ name: 'MissingPDFException' }, '未找到 PDF 文件，请刷新后重试'],
    [{ name: 'UnexpectedResponseException' }, 'PDF 文件加载失败，请稍后重试'],
    [{ name: 'PasswordException' }, 'PDF 文件已加密，暂不支持在线预览'],
    [
      new Error('Setting up fake worker failed: Failed to fetch dynamically imported module'),
      'PDF 渲染组件加载失败，请刷新页面重试'
    ],
    [new TypeError('Failed to fetch'), 'PDF 文件加载失败，请检查网络后重试'],
    [new Error('Network request failed'), 'PDF 文件加载失败，请检查网络后重试'],
    [new Error('Invalid PDF structure'), '无法加载 PDF 文件或文件格式受损']
  ]
  for (const [error, expected] of cases) {
    assert.equal(resolvePdfLoadErrorMessage(error), expected)
  }
})
