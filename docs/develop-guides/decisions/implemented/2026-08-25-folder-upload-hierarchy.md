# 文件夹上传目录层级

状态：implemented
类型：feature
Owner：web/src/components/FileUploadModal.vue、backend/package/yuxi/repositories/knowledge_file_repository.py

## 问题
浏览器文件夹上传虽然携带每个文件的相对路径，但入库请求未传递该路径，导致知识库文件列表把所有文件显示在同一目录。

## 决策
仅在“上传文件夹”模式中，将每个已上传文件原始 `File` 对象的 `webkitRelativePath` 作为现有 `source_paths` 参数提交。上传二进制仍使用原始文件名；后端已有 source_path 归一化和虚拟目录查询逻辑据此生成可展开的目录层级。

## 替代方案
- 为每层路径创建持久化文件夹记录：会增加写入、冲突处理和回滚语义，而现有列表已拥有路径型虚拟文件夹。
- 保持平铺并把路径显示为文件名：不能提供文件夹导航。

## 后果
浏览器仅在目录选择时提供 `webkitRelativePath`；没有该字段的文件继续按普通文件处理。已入库但未保存相对路径的历史文件不能从展示名推断目录，需依据保留的对象路径恢复或重新上传。

## 验证
- `docker compose exec web pnpm run lint:check`
- `docker compose exec web pnpm run test:unit`：85 passed
- `docker compose exec web pnpm run build`
- `docker compose exec api uv run --group test pytest test/unit/routers/test_knowledge_router_cleanup.py test/unit/knowledge/test_kb_utils.py test/unit/knowledge/test_file_listing_scaling.py -q`：39 passed
- 当前知识库 `kb_jm16om7ivy` 的 140 条历史文件已恢复为 `articles/分类/文件`；数据查询确认包含 10 个分类目录。
