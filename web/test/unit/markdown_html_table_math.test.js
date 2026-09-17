import assert from 'node:assert/strict'
import test from 'node:test'

import { createMarkdownRenderer } from '../../src/utils/markdown_preview.js'

// 取自一份真实解析产物：表格被输出成一整行 HTML，单元格内含行内公式。
// 这类产物在把表格输出为 HTML 的解析器上很常见。
const TABLE_WITH_MATH =
  '<table><tr><td>测量参数</td><td>标准差  $\\%$ </td><td>权重</td></tr>' +
  '<tr><td> $N _ { 2 }$ </td><td>0.1</td><td>1</td></tr>' +
  '<tr><td> $W _ { \\mathrm { f m } }$ </td><td>0.3</td><td>0.11</td></tr></table>'

const render = (md) =>
  createMarkdownRenderer({ themeName: 'github-light', highlighter: null }).render(md)

test('HTML 表格单元格内的行内公式渲染为 KaTeX，而非源码', () => {
  const html = render(TABLE_WITH_MATH)
  assert.match(html, /class="katex"/, '公式应渲染为 KaTeX 节点')
  assert.doesNotMatch(html, /\$N _ \{ 2 \}\$/, '不应再出现公式源码')
})

test('HTML 表格结构本身不被破坏（行列仍在）', () => {
  const html = render(TABLE_WITH_MATH)
  assert.equal((html.match(/<tr>/g) || []).length, 3, '三行都应保留')
  assert.equal((html.match(/<td>/g) || []).length, 9, '九个单元格都应保留')
  assert.match(html, /测量参数/)
  assert.match(html, /0\.11/)
})

test('正文里的两个美元符号（货币写法）不得被当作公式', () => {
  // 负向案例：本文档渲染器同时服务普通正文。若不区分词边界，`$5 ... $10` 这类
  // 货币区间会被误当作行内公式渲染，普通段落会凭空出现数学节点。
  const html = render('The price is $5 and the cost is $10 today.')
  assert.doesNotMatch(html, /class="katex"/, '不应渲染出任何数学节点')
  assert.match(html, /\$5 and the cost is \$10/, '原文应原样保留')
})

test('表格之外的公式渲染不受影响（原有 inline 路径）', () => {
  const html = render('雷诺数 $R e$ 是衡量流体黏性的重要准则之一。')
  assert.match(html, /class="katex"/)
})

test('HTML 块内的块级公式渲染为 display 模式', () => {
  const html = render('<table><tr><td>$$E = mc^2$$</td></tr></table>')
  assert.match(html, /katex-display/, '应渲染为展示模式')
  assert.match(html, /E = mc\^2|E=mc\^2|<mi>E<\/mi>/, '不应残留定义式源码')
})

test('未配对的美元符号原样保留且不产生 KaTeX 报错', () => {
  const html = render('<table><tr><td>价格 $100 元</td></tr></table>')
  assert.doesNotMatch(html, /katex-error/, '不应出现报错节点')
  assert.match(html, /价格 \$100 元/, '原文应原样保留')
})
