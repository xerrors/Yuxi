# 统一预加载 Skill 的资源选择协议

状态：implemented
类型：simplification
Owner：backend/package/yuxi/agents/context.py

## 问题

MCP 与预加载 Skill 都默认不选择，但预加载字段额外禁止 all，导致类型、验证和界面重复维护例外。用户需要能够显式预加载当前 Agent 已启用的全部 Skill。

## 决策

所有资源配置使用 ResourceSelection 和同一校验、展开函数，mcps 与 preload_skills 默认均为 []。Context 的 dataclass 字段类型 ResourceSelection 是资源选择的唯一声明；运行准备、repository 和 service 从实际后端 schema 读取字段与 metadata.kind，前端读取派生的 supports_all 和字段 default。子类新增、覆盖字段无需修改外部名单。先解析用户可用的各类资源，再以解析后的 skills 为候选范围解析 preload_skills；后续 Skill runtime 继续展开授权依赖并读取完整内容。前端复用全部选择控件，预加载的 all 保存意图并受启用 Skill 选项约束。原迁移对 preload 的 null 转 [] 保持有效，不新增 schema 版本。

## 替代方案

keep：保留数组专用类型会继续维护额外校验和界面例外。narrow：仅增加类型但复制解析分支无法减少维护面。replace：统一 all/列表协议和解析，仅保留候选来源不同，采用此方案。remove：直接取消预加载无法满足首轮读取完整说明的现有 consumer。

## 验收标准

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| MCP 与预加载均默认空并支持 all | 预加载额外拒绝或字符串逐字符迭代 | Context/repository | unit、真实 HTTP 回读 | null/混合数组仍拒绝 | Passed |
| 全部预加载只作用于有效 Skill 范围 | 未启用/无权限 Skill 被读入 | Context/Skill runtime | 真实临时 SKILL.md 内容断言 | skills=[] 或无权限时不读取 | Passed |
| 前端全部模式与固定选择一致 | all 被展平保存或候选不受 skills 约束 | agentConfigUtils/表单 | Web unit、浏览器、build | 手动勾满保持数组 | Passed |

## 验证

旧能力不存在：preload 数组专用类型、拒绝 all 的 guard、后端资源字段常量及前端默认全选名单均已移除。字段别名、子类默认覆盖、普通列表和缺失权限结果的测试验证声明驱动行为。
重新引入条件：只有预加载新增独立且当前协议无法表达的用户约束时重新评估，不恢复任意类型例外。

相关 Context、repository、真实临时 Skill 文件测试 81 passed；配置 service 测试 6 passed；真实 HTTP 保存并独立数据库回读 4 passed。Web unit 376 passed，lint 与 build 通过；真实浏览器勾选全部预加载后保存并重新打开确认保留 all。完整命令、全量测试与独立 Review 记录在 PR。

## 后果

显式全部预加载会增加首轮提示词内容和依赖工具，默认空列表保持不变。依赖闭包与缺失文件失败继续由 Skill runtime 拥有。
