# 共享权限指定用户下拉按候选和已选分别读取

状态：implemented
类型：feature
Owner：backend/yuxi/modules/identity/repositories/users.py

## 问题

用户量超过单页上限时，前端预取或按显示名过滤无法覆盖所有账号，也会漏掉 UID、手机号命中。已选用户与候选混在一起时，既有授权可能在搜索中消失，已降级的管理员还可能被回填成可添加项。旧响应若在下一次防抖请求开始前到达，也可能覆盖刚更改的输入。

## 决策

候选按当前输入服务端查询，已选名单按持久 UID 单独展示并解析标签。

### 实现方案

- 候选搜索按用户名、UID、手机号匹配有效真人用户。「可管理」先按 `admin,superadmin` 收窄，再取前 50 条。前端不按显示名重新筛选。无参数调用保留原来的分页；显式组合 `include_uids` 和搜索或角色仍可在命中窗口外补回已选用户。
- `limit=0` 搭配 `include_uids` 只查标签，不查询候选页，候选角色限制不适用于既有授权。弹层打开或所选 UID 变化时只查询本次尚未解析的 UID；点选当前候选可直接复用其标签。缺失用户按原 UID 显示；已降级的管理员保留在已选区且可移除，不能由已选区加入候选。
- `ShareConfigForm` 的「可读取」和「可管理」分别经 `shareUserPicker` 拥有弹层请求。非空关键词防抖 300ms；输入变化立即作废旧响应，清空立即加载默认候选，关闭或外部替换配置时取消待发请求并丢弃在途结果。角色校验与最终授权仍由后端负责。

## 替代方案

- 增大预取量或分页滚动：增加加载和交互成本，且不能代替对历史授权的独立读取。
- 每次搜索合并已选 UID：不用新增 HTTP 路由，但会反复查询不变的标签，并模糊既有授权与可新增候选的边界。

## 后果

首次展开有既有授权的弹层时，候选与标签可并行请求；之后每次关键词变化只读候选。候选仍按 ID 升序截断，不做总数与翻页计数。不存在或不再可查的已选用户标识仍保留，操作者可主动移除；加载标签不改动授权。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 |
|---|---|---|---|---|
| 角色先筛后取、服务端搜索；无参调用兼容，零候选时只回填指定 UID | 搜不到窗口外账号；降级用户作为候选或无关用户混进标签结果 | identity repository / service | `pytest test/unit/services/test_access_options_search_service.py`；`pytest test/integration/api/test_auth_router.py::test_user_access_options_search_and_selected_backfill`；`node --test test/unit/api_boundary.test.js` | 取消零候选分支会发出空候选查询；不传有效角色参数会弱化管理候选范围 |
| 搜索不触发已选回填，已选 UID 不会因搜索丢失；关闭、清空和关键词反复切换时旧响应不发布 | 旧结果覆盖新输入；降级或缺失 UID 移除后挤回候选 | shareUserPicker / ShareConfigForm | `node --test test/unit/shareUserPicker.test.js`；浏览器中展开弹层后核对列表和交互 | 把失效推迟到请求发出时，防抖期间旧结果会反写；把候选和已选混用，移除旧授权后会显示成可添加 |
