# 文档约定

本目录包含用户教程、配置参考、机制详解、开发规范、决策记录、事故复盘和版本材料。仓库根 `AGENTS.md` 的系统不变量仍然适用。完整信息架构、写作标准与检查清单由[文档编写与维护规范](develop-guides/documentation-guidelines.md)拥有，本文件只保留进入 `docs/` 工作时必须执行的指令。

## 层级与事实 Owner

- 每个事实只有一个负责完整解释的 Owner；其他页面只保留完成当前任务需要的上下文并使用相对链接。先按[层级表](develop-guides/documentation-guidelines.md#信息架构与事实-owner)确定页面位置，再写内容。
- `intro/` 拥有从零完成结果的教程，`advanced/` 拥有配置和运维参考，`agents/` 拥有 Agent 配置与扩展方法，`mechanisms/` 拥有运行机制、状态、权限、失败和源码定位。实质性混合内容必须拆页。
- 当前系统边界和主链路属于仓库根 `ARCHITECTURE.md`；测试层级与命令属于[测试规范](develop-guides/testing-guidelines.md)；工程信任闭环属于[工程信任系统](develop-guides/engineering-trust.md)。不要在专题页复制这些完整规则。
- 非显然取舍属于 `develop-guides/decisions/`，达到门槛的事故因果属于 `develop-guides/postmortems/`，已发布事实属于 changelog，未完成方向属于 roadmap。`docs/vibe/` 只用于被忽略的本地临时计划。
- 源码、schema、Compose、数据约束和测试拥有可执行事实；外部 Wiki、旧 changelog、历史 PR 和 Agent 自述只能帮助定位，不能覆盖当前 Owner。

## Agent 写作流程

局部文案、链接或机械清理只读取适用指令与直接相关内容；下列完整流程适用于新增、重写或机制说明变更。所有文档改动均须执行提交前验证与独立 Review。

1. 读取根与当前子树指令、owning 文档和相关源码。架构与开发指南按根文件的任务范围加载。非平凡信息架构或长期约束变化先创建 tracked proposed decision。
2. 写明预定读者、问题、前置知识、目标、非目标和页面类型。教程必须有顺序结果；参考必须有明确查找范围；实质性混合内容先拆分。
3. 用符号搜索沿入口 → service/executor → repository/持久化或发布点 → 用户/模型结果重建真实链路，同时核对权限、失败路径、可选能力和相关测试。
4. 先列 Section 及每节唯一问题，按“概念 → 组件关系 → 状态/Owner → 权限/失败 → 源码定位”渐进；教程按完成任务所需的前置依赖排序。
5. 一次最多撰写或重写一个 Section。完成后立即核对事实、相对链接、与相邻章节的重复及当前页面职责，再进入下一节。
6. 先更新事实 Owner，再更新导航、索引和引用页。页面移动或拆分必须在同一变更中修复全部入站链接，不保留两个可独立编辑的副本。
7. 提交前运行最小相关 gate，并由不继承开发上下文的全新 Reviewer 对照完整需求、源码、diff、测试和未验证范围审查。

## 写作与维护

完整的语言、结构、示例、安全和决策记录规则由[文档编写与维护规范](develop-guides/documentation-guidelines.md)拥有。进入 `docs/` 工作时至少执行：

- 使用直接、具体、可验证的现在时，写明执行者、条件、时序、结果、失败和后果；无法核实时明确条件与 `Not run` 范围。
- 不写“以前/现在/这次修改”、PR 过程、Review 对话、实现步骤或推理流水账；原因写入 decision，事故时间线写入 postmortem。
- 不手写易漂移的完整文件、API、测试或配置清单；保留稳定分类并链接源码、schema、Compose 或生成 Owner。
- 站内页面使用相对 Markdown 链接；站点外公开源码使用 GitHub `blob/main` 链接。示例只使用占位凭据、保留域名或假 ID，禁止写入 `.env`、Token、真实账号、用户数据或本地绝对路径。
- 决策记录遵循[生命周期与格式](develop-guides/decisions/README.md)。implemented 记录使用现在时，不保留提案 checklist。

## 导航、预算与验证

- 新增正式页面时更新 `.vitepress/config.mts` 的正确父级和阅读顺序；内部 `AGENTS.md`、decision 与 postmortem 不因位于 `docs/` 就自动成为用户导航入口。
- `scripts/ci/verify_engineering_contracts.py` 的 AGENTS 字符预算是 standing-order guardrail。超限时先下沉示例、背景和专题解释，再压缩重复；确需提高预算时同步更新 decision 和 gate，不能静默删掉必要约束或关闭检查。
- 修改配置、API、状态、权限或机制说明时检查相关测试。文档构建只证明 Markdown、导航和链接有效，不证明语义正确。Reviewer 必须逐页对照当前源码、配置和风险相称的 oracle。
- 构建失败要修正文档、锚点或导航，不扩大 `ignoreDeadLinks` 掩盖问题。报告只写实际执行的命令、结果和未验证范围。

```bash
python3 scripts/ci/verify_engineering_contracts.py
python3 -m unittest scripts.ci.test_verify_engineering_contracts
cd docs && pnpm run build
git diff --check
```
