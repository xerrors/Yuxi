# 共享权限指定用户下拉改为服务端搜索

状态：implemented
类型：feature
Owner：backend/yuxi/modules/identity/repositories/users.py

## 问题

`GET /api/auth/users/access-options` 只有 skip/limit（上限 1000），共享权限编辑的"指定人"下拉一次性预取首页并在本地按 label 过滤。万级用户部署下：首页之外的用户搜索不到；已选但不在结果窗口内的用户从弹层消失，无法核对或取消；编辑弹窗打开即全量预取 1000 条。下拉按 label 本地过滤还会把手机号/UID 命中的候选隐藏成空列表。

## 决策

搜索下推到后端，并把已选用户随结果回填；目录请求改为展开时惰性加载。

### 实现方案

- 后端：access-options 新增 `search`（用户名/UID/手机号模糊，`icontains autoescape`）与 `include_uids`（逗号分隔）参数，`UserRepository.search_access_options` 返回"命中项 ∪ 已选项"，沿用 `is_deleted == 0` 与 `user_kind == 'human'` 过滤（终端影子账号不经搜索泄漏）；无论是否有搜索词，命中窗口（前 limit 条）之外的已选用户都经第二次补查随结果返回（响应上界 = limit + 已选数），无参数调用退回既有"前 limit 条"全量口径。服务层解析逗号串（逐项 strip、丢弃空项），路由保持薄。
- 前端：`getUserAccessOptions({search, includeUids, limit})` 经 `buildQuery` 拼查询参数；ShareConfigForm 搜索输入 300ms 防抖下推（limit 50 + 已选并集），"可管理/可读取"两个半区各按变更来源取词（避免 read 有词吞掉 manage 输入）；user 候选不再本地按 label 二次过滤；用户/部门目录只在展开选择器时首次加载（非管理员维持不发请求口径）。
- 展开加载、防抖下推与回填共用同一批状态（usersLoaded/departmentsLoaded），弹窗关闭不重复请求。

## 替代方案

- 继续加大 limit 全量预取：把一次性拉取放大成整表扫描，15k 级实例首屏响应和内存都不可接受，仍解决不了窗口外已选项丢失。
- 前端本地模糊匹配 UID/手机号：数据本来就没进浏览器，本地过滤无从谈起；且会复现按 label 隐藏命中项的缺陷。
- 分页无限滚动下拉：选择器交互改造面大，回填已选与"搜索+已选并集"一步到位更简单。

## 后果

- access-options 的响应集合语义变为"命中 ∪ 已选回填"，无参数调用（如知识库共享页既有调用方）保持旧首页行为不变；`include_uids` 含空白或未知 uid 时逐项容错。
- 命中集按 id 升序截断到 limit，已选回填不承诺顺序；下拉不做总数与翻页计数（不是目录页）。
- 手机号/UID 命中不再被本地过滤吞掉，属可见性修复而非扩展承诺。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| search 命中用户名/UID/手机号、include_uids 并集回填、双空退回首页且 limit 生效 | 窗口外已选项丢失、命中被隐藏 | UserRepository.search_access_options | `pytest test/integration/services/test_user_access_options_repository.py`（真实 PostgreSQL 四形态参数化） | 去掉 include_uids 并集 → 回填用例红；去掉 human 过滤 → 影子账号负向红 | Passed |
| HTTP 面参数解析与 include_uids 容错（逐项 strip、空项与未知 uid 不致错） | 路由吞参数或畸形输入打穿 SQL | api/routers/identity/auth.py + services/users.py | `pytest test/integration/api/test_auth_router.py::test_user_access_options_search_and_selected_backfill`（真实 HTTP：收窄/带空白与幽灵 uid 的回填/limit=1 首屏补回且不放大） | 路由不传参 → 收窄断言红；去掉逐项 strip → 回填断言红 | Passed |
| 有/无搜索词两分支下，命中窗口外的已选用户都经独立补查回填，畸形 include_uids 逐项 strip |
  回填只在无词分支生效会复现"选中项被挤出窗口" | services/users.list_access_options | `pytest test/unit/services/test_access_options_search_service.py`（fake repo 记录查询序列） | keyword 分支改回单条 OR 查询（无补查）→ 两参数化用例的补查断言红 | Passed |
| 下拉输入 300ms 防抖下推、两半区各自取词、展开才首次加载、非管理员不发请求 | 每键一请求打爆 access-options；打开弹窗全量预取 | frontend/src/modules/agents/ui/ShareConfigForm.vue | 文件级 eslint + `pnpm run build`；交互行为浏览器活体 | — | Inspected |
