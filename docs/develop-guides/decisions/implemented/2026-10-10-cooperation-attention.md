# 协作会话人工待办入口

状态：implemented
类型：feature
Owner：frontend/src/modules/session/ui/SessionWorkspace.vue

## 问题

用户停留在主会话时，子会话的回答和审批卡片只在对应会话显示，无法及时发现需要处理的请求。

## 决策

### 实现方案

`SessionWorkspace` 在输入框上方显示其他协作成员的人工待办，`cooperationAttention` 根据当前 Turn 的 `waiting` 状态及 `approval` / `answer` 原因投影待审批和待回答。`CooperationAttention` 单项直接导航，多项在胶囊原位展开为圆角卡片，摘要按钮与待办列表共享同一容器，不使用弹出浮层；状态查询失败保留最近已知待办，显示失败与重试入口。

待办胶囊使用中性背景、边框和文字，warning 色只用于提示图标与列表状态文字。问题标题使用 400 字重，题型标签使用次要文字，保持长时间阅读的轻量层级。

卡片复用 `CollapseTransition` 展开列表，圆角和折叠图标同步过渡；减少动态效果偏好下关闭过渡。列表高度受当前视口约束，超长待办在卡片内部滚动；待办减少至单项或清空时收起列表。

导航复用侧栏标签并登记当前人工等待轮次的一次性聚焦意图。历史与等待点恢复后，真实人工卡片可见才聚焦；视图失活、轮次变化或恢复执行时撤销意图。多项列表展开完成后聚焦首项，再次点击摘要按钮或按 Escape 收起，Escape 返回摘要按钮。`AgentPanel` 从同一协作摘要展示成员标签徽标。

`useSessionCooperation` 继续读取持久协作摘要。查看、关闭标签不消费等待点；恢复或终态刷新后移除提示。回答与审批仍由对应成员的 Thread/Run/waitpoint 处理。

## 替代方案

只使用 Tool Call 状态容易因折叠或滚动遗漏；只使用状态面板需要用户主动查看。直接在主会话处理子审批增加上下文与提交归属的认知负担。

弹出浮层会遮挡会话正文，并将摘要入口与待办列表分成两个定位区域。原位展开让摘要、选择与收起动作位于同一卡片，复用输入区的自然布局。

## 后果

协作摘要使用现有三秒轮询，提示移除有短暂延迟。进入成员后重新读取等待点，提交归属与权限由现有后端执行。范围包含前端提醒、导航及 owning 文档，审批协议、主会话输入语义和 Tool Call 参数继续由原实现拥有。

展开卡片占据输入框上方的真实布局高度，沿用输入区的自然布局，无需额外浮层定位或测量机制。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 主会话持续展示其他成员的待回答和待审批 | 把协作等待或旧等待原因当成人工待办 | cooperationAttention、CooperationAttention | `node --test test/unit/cooperationAttention.test.js`、浏览器 DOM | completed/cancelled/running 携带旧等待原因不显示 | Passed |
| 点击复用标签并定位对应人工卡片 | 开错会话或慢恢复时遗漏卡片 | SessionWorkspace、AgentPanel | 待办 unit、浏览器点击与 DOM | 700ms 延迟历史恢复仍聚焦，失活或恢复执行撤销意图，关闭后请求仍可打开 | Passed |
| 多项请求在同一卡片展开，失败与窄屏可处理 | 列表脱离摘要容器、展开尺寸溢出、查询失败清空待办 | CooperationAttention | 浏览器 Enter/Tab/Escape、浅深色和 1440/1024/768px 验证 | 原位展开与收起、长列表滚动、减少动态效果、503 保留待办并可重试，终态移除提示和徽标 | Passed |

浏览器证据由 `frontend/test/browser/cooperationAttention.js` 使用确定性 HTTP fixture 驱动实际 `/agent` 页面并读取 DOM、焦点和恢复请求归属，实际主题 Store 切换验证卡片明暗主题。该证据证明前端装配与交互，不替代真实 PostgreSQL、worker 或模型链路验证。

验证命令包括 `docker compose exec -T frontend pnpm run lint:check`、`docker compose exec -T frontend pnpm run test:unit`（1012 项通过）、`docker compose exec -T frontend pnpm run build`、`pnpm --dir docs run build`、工程契约检查及其 56 项单测，均通过。浏览器命令为 `playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/cooperationAttention.js`。本交互调整没有修改后端，未重跑后端 unit、真实 PostgreSQL、worker 与模型协作链路。
