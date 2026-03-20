# 中间件系统

中间件是扩展智能体行为的重要机制。系统基于 LangChain 1.0 的中间件标准，支持在关键节点插入自定义逻辑。

## 核心中间件

### RuntimeConfigMiddleware

这是系统的默认中间件，负责在每次模型调用前注入运行时配置：

- 自动注入当前时间到系统提示词
- 根据配置动态加载工具列表
- 处理模型选择和加载

### inject_attachment_context

支持文件上传功能的中间件。如果智能体需要处理用户上传的文档，可以启用此中间件：

```python
from yuxi.agents.middlewares import inject_attachment_context

async def get_graph(self):
    graph = create_agent(
        model=load_chat_model("..."),
        tools=tools,
        middleware=[
            inject_attachment_context,  # 启用附件处理
            context_aware_prompt,         # 其他中间件
        ],
        checkpointer=await self._get_checkpointer(),
    )
    return graph
```

### 启用文件上传

启用文件上传能力需要两步：

1. 在智能体类中声明 `capabilities = ["file_upload"]`
2. 添加上述中间件

## 自定义中间件

新增中间件时，将其放入 `backend/package/yuxi/agents/common/middlewares` 目录，然后在智能体的 `middleware` 列表中引用即可。
