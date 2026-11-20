# Agent 面板文件树轮询间隔 1s→5s

状态：implemented
类型：simplification
Owner：frontend/src/modules/session/model/agentPanelFilesystemPolling.js

## 问题

流式期间每个打开面板的页签按 `FILESYSTEM_REFRESH_INTERVAL_MS` 轮询线程目录。1s 间隔在并发页签下是最大的单一请求扇出源（内网压测 40 个流式面板实测约 39rps 目录扫描），每次扇出还连带会话文件查询；用户对"新文件出现在面板"的感知并不需要秒级。

## 决策

间隔改为 5s：保留轮询模型（不为此引入新机制），在感知与循环开销之间取折中。

### 实现方案

仅常量与注释更新，测试断言 `interval` 同步为 5000ms。文件变更的即时性仍由既有路径承接：Run 终态通知触发的面板刷新（SessionWorkspace 终态检测 → refreshVersion → AgentPanel watcher）与 `createFilesystemRefreshGate` 的在途/待刷去重，不新增事件通道。

## 替代方案

- 事件驱动推送文件变更：需要新的服务端事件类型与失效协议，收益与 5s 折中相比不足以在本批引入；轮询已有在途/待刷去重门，维持原结构。
- 保持 1s：扇出为服务端主要压力来源之一。

## 后果

- 纯手动写入工作区文件时，面板列出的最长延迟从 ~1s 变为 ~5s；终态刷新与展开即读路径不受影响。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 轮询按 5s 注册且清理生效，其余门语义不变 | 间隔漂移回 1s 或定时器泄漏 | agentPanelFilesystemPolling | `node --test test/unit/agentPanelFilesystemPolling.test.js` 11 passed（含 `interval === 5000` 与 stop 清理） | 还原常量为 1000 后首条用例红 | Passed |
| 面板文件变更即时性主链路不依赖该间隔 | 终态刷新漏触发 | SessionWorkspace/AgentPanel 既有接线 | 代码面核对 onTerminalDetected → refreshVersion 递增 → AgentPanel watcher 链路（未改动） | 不适用 | Inspected |

旧能力不存在：不存在秒级强制刷新的调用方或配置项（间隔唯一消费者是本模块的 start 循环）。
重新引入条件：若事件驱动文件变更推送落地（服务端事件 + 面板订阅），本常量降级为兜底扫描周期并可再调大；若用户报告 5s 延迟造成实际困惑，再按事件化方案处理而非回压缩间隔。
