
# 项目目录结构 (Project Overview)

Yuxi 是一个基于大模型的智能知识库与知识图谱智能体开发平台，融合了 RAG 技术与知识图谱技术，基于 LangGraph v1 + Vue.js + FastAPI + LightRAG 架构构建。项目完全通过 Docker Compose 进行管理，支持热重载开发。

## 开发准则

Avoid over-engineering. Only make changes that are directly requested or clearly necessary. Keep solutions simple and focused.

Don't add features, refactor code, or make "improvements" beyond what was asked. A bug fix doesn't need surrounding code cleaned up. A simple feature doesn't need extra configurability.

Don't add error handling, fallbacks, or validation for scenarios that can't happen. Trust internal code and framework guarantees. Only validate at system boundaries (user input, external APIs). Don't use backwards-compatibility shims when you can just change the code.

Don't create helpers, utilities, or abstractions for one-time operations. Don't design for hypothetical future requirements. The right amount of complexity is the minimum needed for the current task. Reuse existing abstractions where possible and follow the DRY principle.

## 开发与调试工作流 (Development & Debugging Workflow)

本项目完全通过 Docker Compose 进行管理。所有开发和调试都应在运行的容器环境中进行。使用 `docker compose up -d` 命令进行构建和启动。

**核心原则**: 由于 api-dev 和 web-dev 服务均配置了热重载 (hot-reloading)，本地修改代码后无需重启容器，服务会自动更新。应该先检查项目是否已经在后台启动（`docker ps`），查看日志（`docker logs api-dev --tail 100`）具体的可以阅读 [docker-compose.yml](docker-compose.yml).

### 前端开发规范

- API 接口规范：所有的 API 接口都应该定义在 web/src/apis 下面
- Icon 应该从 @ant-design/icons-vue 或者 lucide-vue-next （推荐，但是需要注意尺寸）
- Vue 中的样式使用 less，非必要情况必须使用[base.css](web/src/assets/css/base.css) 中的颜色变量。
- UI风格要简洁，同时要保持一致性，不要悬停位移，不要过度使用阴影以及渐变色。


### 后端开发规范

```bash
# 代码检查和格式化
make lint          # 检查代码规范
make format        # 格式化代码

# 直接在容器内执行命令
docker compose exec api uv run python test/your_script.py  # 放在 test 文件夹
```

注意：
- Python 代码要符合 Python 的规范，符合 pythonic 风格
- 尽量使用较新的语法，避免使用旧版本的语法（版本兼容到 3.12+）
- 修改完成后使用 YUXI_SUPER_ADMIN_NAME / YUXI_SUPER_ADMIN_PASSWORD 环境变量调试接口（读取 .env 文件）

**其他**：

- 如果需要新建说明文档（仅开发者可见，非必要不创建），则保存在 `docs/vibe` 文件夹下面
- 代码更新后要检查文档部分是否有需要更新的地方，文档的目录定义在 `docs/.vitepress/config.mts` 中
