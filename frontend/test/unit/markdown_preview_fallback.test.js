import assert from 'node:assert/strict'
import test from 'node:test'

import { createMarkdownRenderer } from '../../src/shared/lib/markdown_preview.js'

test('无代码高亮器时 Markdown 仍保留结构化渲染', () => {
  const renderer = createMarkdownRenderer({ themeName: 'github-light', highlighter: null })
  const html = renderer.render('# Skill\n\n```python\nprint(42)\n```')
  assert.match(html, /<h1>Skill<\/h1>/)
  assert.match(html, /<pre><code/)
})

test('定位预览保留空行后的标题、正文与代码块原始行号', () => {
  const renderer = createMarkdownRenderer({ themeName: 'github-light', highlighter: null })
  const content = '# 标题\n\n正文\n\n```txt\n代码\n```'
  const html = renderer.render(content, { sourceLines: true })
  assert.match(html, /<h1 data-source-start="1" data-source-end="1" data-reference-excluded="">/)
  assert.match(html, /<p data-source-start="3" data-source-end="3" data-reference-start="3" data-reference-end="3">/)
  assert.match(html, /<div data-source-start="5" data-source-end="7" data-reference-excluded><pre>/)
  assert.doesNotMatch(renderer.render(content), /data-source-start/)
})

test('引用映射保留前导空行与 SVG 预览后正文的原始行号', () => {
  const renderer = createMarkdownRenderer({ themeName: 'github-light', highlighter: null })
  const content = '\n\n结论。\n\n```svg\n<svg><text>图示</text></svg>\n```\n\n后续结论。'
  const html = renderer.render(content, { sourceLines: true })
  assert.match(html, /<p data-source-start="3" data-source-end="3" data-reference-start="3" data-reference-end="3">结论。/)
  assert.match(html, /<div data-source-start="5" data-source-end="7" data-reference-excluded><div class="svg-inline-render">/)
  assert.match(html, /<p data-source-start="9" data-source-end="9" data-reference-start="9" data-reference-end="9">后续结论。/)
  assert.doesNotMatch(html, /language-svg/)
})

test('来源范围仅包含正文、图片、表格行，紧凑列表不扩大到内部组件', () => {
  const renderer = createMarkdownRenderer({ themeName: 'github-light', highlighter: null })
  const content = '- 正文 `code` 与 $x$。\n\n  ```widget\n  {"type":"Card","children":[]}\n  ```\n\n![图片](https://example.org/a.png)\n\n| 项目 | 结果 |\n| --- | --- |\n| A | B |\n\n$$\nx^2\n$$\n\n后续'
  const html = renderer.render(content, { sourceLines: true, widgets: true, widgetLines: content.split('\n') })
  assert.match(html, /<p[^>]*data-reference-start="1" data-reference-end="1"/)
  const tight = renderer.render('- 正文\n  ```python\n  print(1)\n  ```', { sourceLines: true })
  assert.match(tight, /<li[^>]*data-reference-start="1" data-reference-end="1"/)
  assert.match(html, /class="widget-preview-render"[^>]*data-source-start="3" data-source-end="5" data-reference-excluded/)
  assert.match(html, /<p[^>]*data-reference-start="7" data-reference-end="7"><img/)
  assert.match(html, /<tr[^>]*data-reference-start="9" data-reference-end="9"/)
  assert.match(html, /<tr[^>]*data-reference-start="11" data-reference-end="11"/)
  assert.match(html, /data-source-start="13" data-source-end="15" data-reference-excluded/)
  assert.match(html, /<p[^>]*data-reference-start="17" data-reference-end="17">后续/)
  assert.doesNotMatch(renderer.render(content), /data-reference-/)
})

test('任务列表生成的 checkbox 不排除文字，源 Markdown 内的 HTML 仍排除', () => {
  const renderer = createMarkdownRenderer({ themeName: 'github-light', highlighter: null })
  const html = renderer.render('- [x] 文字结论\n- [ ] 后续结论\n- <span>HTML</span>', { sourceLines: true })
  assert.match(html, /<li[^>]*data-reference-start="1" data-reference-end="1"/)
  assert.match(html, /<li[^>]*data-reference-start="2" data-reference-end="2"/)
  assert.match(html, /<li[^>]*data-source-start="3"[^>]*data-reference-excluded/)
  assert.doesNotMatch(html, /data-reference-start="3"/)
})

test('未闭合公式也排除标注，前面的正文保持可标注', () => {
  const renderer = createMarkdownRenderer({ themeName: 'github-light', highlighter: null })
  const html = renderer.render('正文\n\n$$\nx^2', { sourceLines: true })
  assert.match(html, /<p[^>]*data-reference-start="1" data-reference-end="1"/)
  assert.match(html, /<div data-source-start="3"[^>]*data-reference-excluded/)
  assert.doesNotMatch(html, /data-reference-start="[34]"/)
})

test('frontmatter 保留多行字段的 YAML 缩进', () => {
  const renderer = createMarkdownRenderer({ themeName: 'github-light', highlighter: null })
  const html = renderer.render(`---
name: minimax-pdf
description:
  Use this skill when visual quality and design identity matter for a PDF.
  CREATE (generate from scratch): "make a PDF", "generate a report".
license: MIT
metadata:
  version: "1.0"
  category: document-generation
---
`)

  assert.match(html, /class="frontmatter-card"/)
  assert.match(html, /minimax-pdf/)
  assert.match(html, /Use this skill when visual quality and design identity matter for a PDF\./)
  assert.match(html, /document-generation/)
})

test('relative document images resolve beside their artifact with the same authorization boundary', async () => {
  const { resolveMarkdownImageUrl } = await import('../../src/shared/lib/file_preview.js')
  const base = '/api/v1/agents/sessions/thread-1/artifacts/home/user/workspace/project/parsed/document.md'
  const origin = 'https://yuxi.example'
  assert.equal(
    resolveMarkdownImageUrl('images/chart.png', base, origin),
    '/api/v1/agents/sessions/thread-1/artifacts/home/user/workspace/project/parsed/images/chart.png'
  )
  assert.equal(resolveMarkdownImageUrl('images/chart.png', '', origin), null)
  assert.equal(resolveMarkdownImageUrl('https://outside.example/api/knowledge/knowledge-bases/a/images/x.png', base, origin), null)
  assert.equal(resolveMarkdownImageUrl('//outside.example/x.png', base, origin), null)
  assert.equal(resolveMarkdownImageUrl('/api/knowledge/knowledge-bases/kb/images/kb-images/chart.png', '', origin), '/api/knowledge/knowledge-bases/kb/images/kb-images/chart.png')
})

test('公式插件保留行内、块级、源行定位与不可信命令限制', () => {
  const renderer = createMarkdownRenderer({ themeName: 'github-light', highlighter: null })
  const html = renderer.render('行内 $x^2$。\n\n$$\n\\frac{1}{2}\n$$', { sourceLines: true })
  assert.match(html, /class="katex"/)
  assert.match(html, /class="katex-display"/)
  assert.match(html, /<mfrac>/)
  assert.match(html, /data-source-start="3" data-source-end="5"/)
  assert.doesNotMatch(renderer.render('$\\href{https://example.org}{link}$'), /href="https:/)
  assert.doesNotMatch(renderer.render('$\\includegraphics{https://example.org/x.png}$'), /<img/)
  assert.match(renderer.render('$\\invalidcommand$'), /color:#cc0000/)
})

test('公式插件不读取原型中注入的宏定义', () => {
  const renderer = createMarkdownRenderer({ themeName: 'github-light', highlighter: null })
  const source = '$\\yuxipolluted$'
  Object.defineProperty(Object.prototype, '\\yuxipolluted', {
    configurable: true,
    value: 'POLLUTED'
  })
  try {
    const html = renderer.render(source)
    assert.doesNotMatch(html.replace(/<[^>]*>/g, ''), /POLLUTED/)
    assert.match(html, /\\yuxipolluted/)
    assert.match(html, /#cc0000/)
  } finally {
    delete Object.prototype['\\yuxipolluted']
  }
})
