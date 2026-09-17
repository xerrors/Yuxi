# HTML 块内的行内公式渲染

状态：implemented
类型：bug-fix
Owner：web/src/utils/markdown_preview.js

## 问题

解析产物常把表格输出成一整行 HTML（`<table>...</table>`）。`markdown-it` 在 `html: true` 下把该行整体当作 `html_block` **原样透传**，而原始 HTML 内容不经过 inline 解析，因此单元格里的 `$...$` 永远到不了 KaTeX 插件，页面上显示为源码。

表现为「同一篇文档里公式有的渲染、有的不渲染」，规律是**只有表格里的不渲染**——表格之外的公式位于普通段落，走 inline 解析，故不受影响。

## 决策

启用 `@vscode/markdown-it-katex` 自带的两个开关：

```js
.use(markdownKatexPlugin, {
  throwOnError: false,
  errorColor: '#cc0000',
  trust: false,
  enableMathInlineInHtml: true,
  enableMathBlockInHtml: true
})
```

插件在 `html_block` token 内部按其**自身分隔符规则**（尊重 `\$` 转义与词边界）再扫一次数学分隔符。零新增代码、零新增依赖、无新增抽象。

## 替代方案

1. **在渲染层对已渲染结果做正则后处理**（包裹 `html_block` / `html_inline` / `text` 三个渲染规则，再对输出串匹配 `$...$`）。实测否决：`text` 规则工作在**已转义**的 HTML 上，且正则缺少插件的分隔符规则，因此引入三类新失败——普通正文的货币写法 `$5 ... $10` 被当作公式渲染（连中文字符都会进入数学模式）、`\$` 转义产生 `katex-error`、含 `<` 的公式被二次转义后报错。此外它需要直接 `import katex`，而插件解析到的是另一份 katex 实例，实测打包体积增加约 269 KB。
2. **用 `DOMParser` 解析 HTML 块，只遍历文本节点做替换**。这是唯一能同时消除下面「后果」中跨标签配对与属性污染两类问题的方案（每个单元格是独立文本节点；属性不是文本节点）。未采纳的原因：它把 HTML 解析与序列化引入渲染热路径，代码量与风险显著高于启用一个既有开关，且本缺陷的实际触发场景（表格单元格内的公式）已由开关覆盖。若后续需要处理这些边界，这是首选方向。
3. **保持现状、只在文档中说明**。否决：输出为 HTML 的表格是常见形态，公式不渲染会直接影响内容可用性。

**未采用插件的 `enableMathBlockInHtml` 之外的配置组合**：只开 inline 开关时，HTML 块内的 `$$...$$` 会连同定界符一起被吃掉（正文里既不渲染也不保留）；同时打开两个开关后，`$$...$$` 正常渲染为 display 模式。

## 后果

以下限制由「在原始 HTML 上按 `$` 配对扫描」这一思路本身带来，**插件开关与方案 1 的正则实现共有**，不是本次引入的特有缺陷：

- **跨标签配对会吞结构**：`<table><tr><td>价格 $5</td><td>成本 $10</td></tr></table>` 中两个 `$` 会被配成一对，导致两个单元格被合并。
- **HTML 属性内的 `$` 会被替换**：`<td title="价格 $5 到 $10">` 的属性值会被插入数学节点。
- **`\$` 在 `html_block` 内不被识别为转义**，会产生 `katex-error` 节点。
- **`html_inline` 不覆盖**：`<span>$x$</span>` 这类行内 HTML 片段仍不渲染（插件只处理 `html_block`）。

`$$...$$` 在 HTML 块内现渲染为 display 模式，这是相对改动前（原样透传）的行为变化。

## 验证

- Passed：`node --test test/unit/markdown_html_table_math.test.js` — 6 项通过。其中「HTML 表格单元格内的行内公式渲染为 KaTeX」「HTML 块内的块级公式渲染为 display 模式」两项在关闭开关后失败，即这两项是该缺陷的回归测试；其余四项为负向与边界守卫（正文货币写法不得渲染、表格结构不被破坏、表格外公式不受影响、未配对 `$` 不报错）。
- Passed：全量 `node --test --test-concurrency=1 "test/**/*.test.js" "test/**/*.spec.js"` — 339 项通过、0 失败；`eslint . --max-warnings=0` 通过；`vite build` 退出码 0。
- Passed：真实页面复核。在运行中的实例打开一份表格被输出为 HTML 的报告类解析产物，预览区渲染出 498 个 KaTeX 节点、0 个 `katex-error`、无 `$...$` 残留，4 个表格结构保留。
- Not run：上述四项限制（跨标签配对、属性内 `$`、`\$` 转义、`html_inline`）未补测试用例。它们记录的是当前**已知不符合期望的行为**，用测试固化会把它当作契约；是否修复留待维护者判断。
- Not run：`html:preview` 代码块源码展示路径未单独验证。该路径经 `renderMarkdown` 与 DOMPurify，与本次单元测试覆盖的 `createMarkdownRenderer` 不是同一条链路。
