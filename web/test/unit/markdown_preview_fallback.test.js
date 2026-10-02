import assert from 'node:assert/strict'
import test from 'node:test'

import { createMarkdownRenderer } from '../../src/utils/markdown_preview.js'

test('无代码高亮器时 Markdown 仍保留结构化渲染', () => {
  const renderer = createMarkdownRenderer({ themeName: 'github-light', highlighter: null })
  const html = renderer.render('# Skill\n\n```python\nprint(42)\n```')
  assert.match(html, /<h1>Skill<\/h1>/)
  assert.match(html, /<pre><code/)
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
