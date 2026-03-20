#!/usr/bin/env python3
"""
简单测试 MySQL 工具导入
"""

import sys

sys.path.insert(0, ".")

try:
    from yuxi.agents.toolkits.mysql.tools import mysql_list_tables, mysql_describe_table, mysql_query

    print("✅ MySQL 工具导入成功")
    print(f"工具列表: {[tool.name for tool in [mysql_list_tables, mysql_describe_table, mysql_query]]}")

    # 检查工具描述
    for tool in [mysql_list_tables, mysql_describe_table, mysql_query]:
        print(f"  - {tool.name}: {tool.description[:60]}...")

except Exception as e:
    print(f"❌ 导入失败: {e}")
    import traceback

    traceback.print_exc()
