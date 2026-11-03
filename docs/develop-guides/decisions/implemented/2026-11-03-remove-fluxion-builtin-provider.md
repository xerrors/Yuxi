# 移除 Fluxion AI 内置供应商

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/models/providers/builtin.py

## 问题

README 赞助位和内置供应商目录把 Fluxion AI 作为预置模型入口，设置页还为该供应商单独放注册链接和图标。用户要求去掉这段赞助，以及它作为内置供应商的痕迹。

## 决策

### 实现方案

删除 README 赞助章节。`BUILTIN_PROVIDERS` 不再包含 `fluxionai`，新的 app-init 不会再写入该模板。设置页 API Key 字段恢复为普通标签，图标映射不再提供 Fluxion 专用图。

`ensure_builtin_model_providers_in_db` 继续只补缺失模板，不覆盖、不删除已有供应商行。已经初始化的实例若保存过 `fluxionai`，该行、密钥和已启用模型保持原样，管理员可在页面自行删除。

## 替代方案

在同步时删除已有 `fluxionai` 行会清掉管理员已经填写的密钥和模型，破坏“不覆盖已编辑配置”的承诺。保留 README 或只删赞助、继续内置该供应商，不满足本次移除要求。

## 后果

新安装的供应商列表不再出现 Fluxion AI。旧库中的同名行仍会显示，直到管理员删除；它不再被当作代码拥有的内置模板重新补齐。自定义供应商仍可手工使用相同地址。

## 验证

旧能力不存在：README、内置目录、设置页注册链接和图标映射不再提供 Fluxion 赞助或内置模板。回归断言保留 `fluxionai` 这个标识，用来防止它回到 `BUILTIN_PROVIDERS`。

重新引入条件：出现明确的当前赞助或内置供应商需求后，在 `BUILTIN_PROVIDERS` 与对应展示处重新加入，并说明是否回填已有数据库。

`test_builtin_provider_templates_default_to_openai_provider_type` 通过。后端 `pytest test/unit -m "not slow"` 为 3586 通过、125 跳过。前端 `lint:check` 与 1013 项 unit 通过。工程信任检查通过。浏览器打开已有实例的供应商表单，API Key 旁不再出现注册链接；该行仍在列表中，图标按 `provider_type` 使用 OpenAI 图标。
