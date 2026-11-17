"""模型调用与供应商配置，不是全局 ORM 模型集合。

chat、embed、rerank 拥有对应模型调用适配；providers 拥有供应商配置与凭据。
各业务域的 models.py/models 目录仍拥有各自持久化映射。
"""

__all__ = ["select_model"]


def __getattr__(name: str):
    if name == "select_model":
        from yuxi.modules.models.chat import select_model

        return select_model

    raise AttributeError(f"module 'yuxi.modules.models' has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))
