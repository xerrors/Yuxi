# 知识库文件夹移动与重命名

状态：archived
类型：feature
Owner：backend/package/yuxi/knowledge/base.py

## 问题

知识库文件表格原本只能进入、创建和删除文件夹。移动接口虽然存在，但前端没有装配入口，且把记录移动到根目录的 `null` 请求被 HTTP 参数边界拒绝；真实文件夹也缺少重命名 API。虚拟文件夹由上传文件的相对路径派生，不具备可独立修改的持久记录。

## 决策

真实目录树继续由 `knowledge_files.file_id` 和 `parent_id` 表达。移动只更新目标记录的 `parent_id`；同一知识库的目录树移动通过 PostgreSQL transaction advisory lock 串行化，使防环检查与后续写入之间不被另一移动穿插。重命名只更新真实文件夹记录的 `filename` 与文件夹展示路径，不递归改写子记录或 MinIO 对象。后端管理权限、防循环校验、只读 connector 和文档型知识库能力检查继续作为最终边界，并拒绝重命名非文件夹、空名称或包含路径分隔符的名称。移动 HTTP 契约要求请求字段存在，同时接受显式 `null` 作为根目录。

`FileTable.vue` 为真实文件和真实文件夹提供 HTML 拖放。当前表格中的真实文件夹以及面包屑中的根目录和真实祖先目录可作为放置目标，并显示主色高亮。只读、筛选、虚拟路径视图和虚拟文件夹不提供修改入口。

## 替代方案

- 把文件夹名称编码进所有子文件路径并递归重写：这会无谓扩大 PostgreSQL、MinIO 和索引副作用，不符合当前 `parent_id` 树模型。
- 同时保留行内“移动到”按钮和目录选择弹窗：键盘可达性更完整，但重复了拖放入口并增加每行操作噪声；当前产品选择只保留拖放。
- 提供跨层级完整目录选择器：能力更强，但需要额外树加载、分页和导航状态；当前实现只覆盖当前页可见文件夹和面包屑祖先，避免引入这套复杂度。

## 后果

重命名是单记录元数据更新，子记录、对象存储和向量索引保持不变。新建真实文件夹保存 `created_at` 和当前操作者 `created_by`，列表展示其创建时间与创建人；历史 `created_by` 为空的文件夹不做不可靠回填。用户可以把当前行拖入可见真实文件夹，或拖到面包屑祖先和根目录。当前页和面包屑祖先以外的目录仍不在首期范围内；移动暂不提供键盘替代入口。

共享 `FileBrowserTable.vue` 增加了默认关闭的面包屑放置能力；只有调用方显式启用时才接收拖放，因此工作区文件列表行为不变。

## 验证

- `docker compose exec api uv run --no-sync --group test pytest test/integration/api/test_knowledge_router.py -k 'folder_rename_and_move or folder_mutations or concurrent_folder_moves or folder_move_waits'`：Passed；真实 HTTP 和 PostgreSQL 覆盖新建文件夹的创建时间与创建人、精确字段更新后的子文件夹名称与父关系、移动到文件夹、移动到根目录、缺失目标字段、非法名称、顺序目录环、并发相向移动，以及外部持有同一 advisory lock 时移动请求确定等待的负控。
- `cd backend && uv run --group test pytest test/unit/plugins/test_dify_kb.py -k folder_rename`：Passed；验证只读 connector 拒绝重命名。
- `cd web && pnpm exec node --test --test-concurrency=1 test/unit/knowledge_file_mutations.test.js`：Passed；验证前端 PUT 请求契约。
- `docker compose exec web pnpm run lint:check`：Passed。
- `docker compose exec web pnpm run build`：Passed；仅有既有编译宏和大 chunk 警告。
- 真实浏览器：Passed；用户确认拖放和重命名通过，另以 Playwright 验证从真实子目录拖到“全部文件”后，服务端刷新显示子文件夹已位于根目录；验证数据均已清理。
- `python3 scripts/verify_engineering_contracts.py`：Passed。
