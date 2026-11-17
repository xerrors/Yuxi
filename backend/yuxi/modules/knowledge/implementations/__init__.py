"""知识库具体实现包。

初始化只做 re-export：`__all__` 是外部导入契约，导入本包仅触发子模块
加载，不注册执行器；KnowledgeBaseFactory 的注册由 factory.py 导入
具体子模块时完成。

- MilvusKB: 基于 Milvus 的向量知识库
- DifyKB: 基于 Dify 检索 API 的只读知识库
- FeishuKB: 访问飞书知识空间的只读知识库
- NotionKB: 连接 Notion Data Source 的只读知识库
- ReadOnlyConnectors: 上述只读连接器的共同基类
"""

from yuxi.modules.knowledge.implementations.dify import DifyKB
from yuxi.modules.knowledge.implementations.feishu import FeishuKB
from yuxi.modules.knowledge.implementations.milvus import MilvusKB
from yuxi.modules.knowledge.implementations.notion import NotionKB
from yuxi.modules.knowledge.implementations.read_only_connectors import ReadOnlyConnectors

__all__ = ["MilvusKB", "DifyKB", "FeishuKB", "NotionKB", "ReadOnlyConnectors"]
