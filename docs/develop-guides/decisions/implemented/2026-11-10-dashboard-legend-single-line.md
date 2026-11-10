# 知识库文件类型图例恢复单行显示

状态：implemented
类型：bug-fix
Owner：frontend/src/modules/dashboard/ui/KnowledgeStatsComponent.vue

## 问题

文件类型达到 5 类时，图例拆成两列。此前为消除名称截断，两列模式把名称放在第一行、数量与占比放到第二行，并隐藏了数量；实际渲染中百分比与名称分离、每项占两行，读者会把第二行误读为独立条目或缺失值。数量被隐藏也与图例 `aria-label="文件类型数量与占比"` 的承诺不一致。

## 决策

### 实现方案

图例每项恢复为“色块 + 名称 + 数量 + 占比”单行，不再使用双行 `grid-template-areas`，也不再隐藏 `.legend-value`。列数由卡片自身宽度决定：

- 图例容器宽度小于 400px 时使用单列，避免窄卡片压缩名称或数值。
- 容器宽度不小于 400px 时使用两列；每项约 190px，可容纳当前最长类型名与 `3,000` / `100.0%` 等最宽数值。
- 名称仍保留 `overflow: hidden`、省略号和原生 `title`，作为未知长扩展名的兜底。

## 替代方案

- 保留双行布局并继续隐藏数量：虽能保证窄卡片名称完整，但百分比与名称分离，且丢失数量信息。
- 始终两列并允许名称省略：曾在 375px 附近出现多项截断，窄卡片可读性差。
- 始终单列：宽卡片浪费横向空间，图例高度翻倍。

## 后果

- 窄卡片下图例变为 8 行，卡片高度增加；宽卡片继续使用两列，信息密度不变。
- 数量列重新可见；极端未知扩展名仍可能省略，但可通过 `title` 查看全名。
- 本决定取代 [2026-11-03 QA 深测修复](./2026-11-03-qa-deep-test-fixes.md) 中“两列时名称独占一行”的图例布局；该记录的其他修复继续有效。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 每类文件保持单行 | 名称与数量/占比分成两行 | KnowledgeStatsComponent 图例样式 | 浏览器断言：各明细行高 35.4px、名称与占比顶边差 <1px | 旧布局行高 57.8px 且名称/占比不同行 | Passed |
| 数量重新可见 | `.legend-value` 被 `display: none` | KnowledgeStatsComponent 图例样式 | 浏览器断言：8 项数量均可见且文本正确 | 移除样式前 8/8 不可见 | Passed |
| 窄卡片不挤压、不截断 | 两列强行压缩名称 | 图例容器查询 | 375/768/1024/1440 宽度下截断数为 0；343px 卡片回退为单列 | 单列容器仍出现截断即失败 | Passed |
| 两列上限与单行约束 | 布局退回三列或双行 | 图例容器查询 | 浏览器断言列数 ≤2、行高 <45px | 任一宽度列数 >2 或行高 ≥45px | Passed |
| 浅色与深色可读 | 深色下数量/分割线对比不足 | 图例样式 + 主题 token | 真实页面截图：浅色、深色各一张 | 深色截图出现白底或文字不可辨 | Passed |

实际执行：

- `docker compose exec -T frontend pnpm run lint:check`
- `docker compose exec -T frontend pnpm run test:unit`
- `docker compose exec -T frontend pnpm run build`
- `playwright-cli -s=login-refine run-code --filename=frontend/test/browser/dashboardStats.js`
