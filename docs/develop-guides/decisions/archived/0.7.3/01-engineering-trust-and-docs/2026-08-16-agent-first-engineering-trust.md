# Agent-first 工程信任体系与治理重构

状态：archived
类型：process
Owner：docs/develop-guides/engineering-trust.md

## 问题

工程完成的判定依赖提交者自述、测试数量和手工演示，缺少可复现的负向案例与可问责的语义 Review；边界、workflow 与 gate 的变更分散在多处，彼此漂移后无人发现。仓库需要一种由真实 Owner 派生、可机械检查的信任基线。

## 决策

以语义 Owner 为权威，用 `scripts/verify_engineering_contracts.py` 从当前代码、测试、workflow 和 decision 记录派生审计投影，代替手工维护的中央 claim 清单。gate 由 workflow 文件自身接线并接受负向测试。决策记录按 lifecycle（proposed/implemented/rejected/archived）管理，`implemented/` 保存当前事实，不保存推理流水账。

## 替代方案

- 维护中央 engineering-claims.json 主张清单：可独立编辑，会复制 Owner 已拥有的事实并形成第二真相，已被 verifier 禁止。
- 仅靠人工 Review：无法机械发现 router 直接执行持久化、web 越界引用 `/api` 等边界漂移，也不产生失败后果。

## 后果

工程信任由 Owner-local 材料与只读 gate 共同构成；非平凡变更必须新增或更新 tracked decision record，否则 trust.yml 阻塞。Runtime 语义仍由真实 PostgreSQL integration/E2E 证明，verifier 只证明引用、接线与部分静态边界。

## 验证

运行 `python3 scripts/verify_engineering_contracts.py` 与 `python3 -m unittest scripts.test_verify_engineering_contracts`；每项检查都有恢复目标缺陷的负向测试，`trust.yml` 在 main 与 PR 上无 path filter 地阻塞。
