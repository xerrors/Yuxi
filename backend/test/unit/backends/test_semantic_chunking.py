def test_heading_inference():
    """测试标题层级推断工具类"""
    from yuxi.knowledge.chunking.ragflow_like.utils.md_parser_utils import infer_heading_level

    assert infer_heading_level("1. 简介") == 1
    assert infer_heading_level("1.1 详细设计") == 2
    assert infer_heading_level("1.2.3 核心逻辑") == 3
    assert infer_heading_level("一、 背景") == 1
    assert infer_heading_level("普通文本") == 1
