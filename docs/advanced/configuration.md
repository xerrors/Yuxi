# 配置系统详解

Yuxi 采用了现代化的配置管理系统，基于 Pydantic BaseModel 和 TOML 格式，提供了类型安全、智能提示和选择性持久化等特性。这套系统既满足了开发者对灵活配置的需求，又保证了运行时的稳定性。

## 设计理念

传统的配置文件往往存在以下问题：格式不统一、类型安全缺失、难以追踪哪些配置是用户修改过的。Yuxi 的配置系统针对这些问题给出了解决方案：

- **类型安全**：基于 Pydantic，所有配置项都有明确的类型定义
- **智能提示**：IDE 可以根据类型定义提供自动补全
- **选择性持久化**：只保存用户修改过的配置，避免版本冲突
- **多层覆盖**：代码默认值 → TOML 文件 → 环境变量，按优先级覆盖

## 配置层次

系统采用三层配置结构，每一层都有不同的优先级和适用场景：

```
配置优先级（从低到高）
━━━━━━━━━━━━━━━━━━━━━━━━━━
环境变量 (.env)      → 最高优先级，用于运行时覆盖
用户配置 (TOML)      → 持久化的用户修改
代码默认值           → 最低优先级，定义在 Python 代码中
━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 各层作用

1. **代码默认值**：定义在 `backend/package/yuxi/config/app.py` 和 `backend/package/yuxi/config/static/models.py` 中，提供所有配置项的初始值

2. **用户配置**：保存在 `saves/config/base.toml`，只包含用户实际修改过的配置项。这种设计的好处是：当代码更新添加了新配置项时，不会被用户的旧配置文件覆盖

3. **环境变量**：适用于容器化部署场景，可以方便地在启动时覆盖任意配置项

## 核心组件

### 应用配置

主配置类 `Config` 继承自 Pydantic BaseModel：

```python
class Config(BaseModel):
    # 功能开关
    enable_reranker: bool = Field(default=False, description="是否开启重排序")
    enable_content_guard: bool = Field(default=False, description="是否启用内容审查")

    # 模型配置
    default_model: str = Field(default="siliconflow/Pro/deepseek-ai/DeepSeek-V3.2")
    embed_model: str = Field(default="siliconflow/BAAI/bge-m3")
```

### 模型配置

模型配置独立管理，支持多种模型提供商：

```python
DEFAULT_CHAT_MODEL_PROVIDERS: dict[str, ChatModelProvider] = {
    "siliconflow": ChatModelProvider(
        name="SiliconFlow",
        url="https://cloud.siliconflow.cn/models",
        base_url="https://api.siliconflow.cn/v1",
        default="deepseek-ai/DeepSeek-V3.2",
        env="SILICONFLOW_API_KEY",
        models=["deepseek-ai/DeepSeek-V3.2", "Qwen/Qwen3-235B-A22B-Instruct-2507"],
    ),
    # 其他提供商...
}
```

## 使用指南

### 读取配置

```python
from yuxi.config import config

# 访问配置项
model = config.default_model
reranker_enabled = config.enable_reranker
```

### 修改配置

```python
from yuxi.config import config

# 修改配置
config.enable_reranker = True
config.default_model = "custom-model-name"

# 保存到 TOML 文件
config.save()
```

### 配置验证

```python
from yuxi.config import config

# 检查模型提供商可用性
for provider, status in config.model_provider_status.items():
    print(f"{provider}: {'可用' if status else '不可用'}")

# 获取可用模型列表
models = config.get_model_choices()
embed_models = config.get_embed_model_choices()
```

## 添加新模型提供商

需要支持新的模型提供商时，按以下步骤操作：

### 步骤 1：添加提供商配置

在 `backend/package/yuxi/config/static/models.py` 的 `DEFAULT_CHAT_MODEL_PROVIDERS` 字典中添加新条目：

```python
"new-provider": ChatModelProvider(
    name="新提供商",
    url="https://provider.com/docs",
    base_url="https://api.provider.com/v1",
    default="default-model",
    env="NEW_PROVIDER_API_KEY",
    models=["model1", "model2"],
),
```

### 步骤 2：配置 API Key

在 `.env` 文件中添加对应的环境变量：

```env
NEW_PROVIDER_API_KEY=your_api_key_here
```

### 步骤 3：重启服务

配置完成后，重启服务使配置生效。

## 高级特性

### 动态更新

配置可以动态修改，无需重启服务：

```python
from yuxi.config import config

# 更新单个配置项
config.enable_reranker = True

# 更新模型列表
config.model_names["siliconflow"].models.append("new-model")

# 保存修改
config.save()
```

### 导出配置

```python
# 导出完整配置（包含运行时状态）
full_config = config.dump_config()

# 导出用户配置（仅保存到文件的部分）
user_config = {
    field: getattr(config, field)
    for field in config._user_modified_fields
}
```

### 选择性持久化机制

系统会跟踪哪些配置项被修改过：

```python
# 假设用户只修改了 enable_reranker
config.enable_reranker = True
config.save()  # 只保存 enable_reranker 到 TOML 文件
```

生成的 TOML 文件只包含修改过的项：

```toml
enable_reranker = true
```

这种设计的优势：
- 用户升级程序时，新配置项会自动使用默认值
- 避免配置文件版本冲突
- 便于查看用户做了哪些自定义修改

## 常见问题

**Q：新增的配置项没有生效？**

A：请检查：
1. 配置项名称是否正确拼写
2. 环境变量是否正确设置（环境变量优先级最高）
3. 是否需要重启服务

**Q：如何查看当前所有配置？**

A：访问 `/api/config` 接口或查看 `config.dump_config()` 的输出。

**Q：配置文件格式错误导致启动失败？**

A：可以删除 `saves/config/base.toml` 文件，让系统重新生成默认配置。

---

配置系统的设计遵循了「约定优于配置」的原则，大多数情况下使用默认值即可工作。当需要自定义行为时，只需要修改少量配置项即可。理解这套系统的层次结构和优先级，能够帮助你更好地控制和调试应用行为。
