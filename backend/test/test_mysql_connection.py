#!/usr/bin/env python3
"""
MySQL 数据库连接验证脚本
"""

import os

# 添加项目根目录到 Python 路径
# sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent.parent))


def load_env_file(env_file=".env"):
    """加载 .env 文件"""
    env_vars = {}
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key] = value.strip("\"'")
    return env_vars


def test_mysql_connection():
    """测试 MySQL 连接"""
    print("=== MySQL 数据库连接验证 ===\n")

    # 加载环境变量
    env_vars = load_env_file()

    # 检查必需的环境变量
    required_vars = ["MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"]
    missing_vars = []

    for var in required_vars:
        if var not in env_vars:
            missing_vars.append(var)

    if missing_vars:
        print(f"❌ 缺少必需的环境变量: {', '.join(missing_vars)}")
        print("\n请在 .env 文件中设置以下变量:")
        for var in missing_vars:
            print(f"  {var}=your_value")
        return False

    # 显示配置信息（隐藏密码）
    print("📋 数据库配置:")
    print(f"  Host: {env_vars.get('MYSQL_HOST', 'Not set')}")
    print(f"  User: {env_vars.get('MYSQL_USER', 'Not set')}")
    print(f"  Database: {env_vars.get('MYSQL_DATABASE', 'Not set')}")
    print(f"  Port: {env_vars.get('MYSQL_PORT', '3306')}")
    print(f"  Charset: {env_vars.get('MYSQL_CHARSET', 'utf8mb4')}")
    print()

    try:
        # 导入 MySQL 连接管理器
        from yuxi.agents.toolkits.mysql.connection import MySQLConnectionManager

        # 创建连接配置
        mysql_config = {
            "host": env_vars["MYSQL_HOST"],
            "user": env_vars["MYSQL_USER"],
            "password": env_vars["MYSQL_PASSWORD"],
            "database": env_vars["MYSQL_DATABASE"],
            "port": int(env_vars.get("MYSQL_PORT", "3306")),
            "charset": env_vars.get("MYSQL_CHARSET", "utf8mb4"),
        }

        # 创建连接管理器
        print("🔄 正在连接数据库...")
        conn_manager = MySQLConnectionManager(mysql_config)

        # 测试连接
        with conn_manager.get_cursor() as cursor:
            # 测试基本连接
            cursor.execute("SELECT 1 as test")
            result = cursor.fetchone()
            if result and result["test"] == 1:
                print("✅ 数据库连接成功！")

                # 获取数据库版本
                cursor.execute("SELECT VERSION() as version")
                version_info = cursor.fetchone()
                print(f"📊 MySQL 版本: {version_info['version']}")

                # 获取当前数据库
                cursor.execute("SELECT DATABASE() as db_name")
                db_info = cursor.fetchone()
                print(f"🗄️  当前数据库: {db_info['db_name']}")

                # 获取表数量
                cursor.execute("SHOW TABLES")
                tables = cursor.fetchall()
                print(f"📋 表数量: {len(tables)}")

                if tables:
                    print("\n📝 数据库表列表:")
                    for i, table in enumerate(tables[:10]):  # 只显示前10个表
                        table_name = list(table.values())[0]
                        print(f"  {i + 1}. {table_name}")

                    if len(tables) > 10:
                        print(f"  ... 还有 {len(tables) - 10} 个表未显示")

                return True
            else:
                print("❌ 数据库连接测试失败")
                return False

    except ImportError as e:
        print(f"❌ 导入错误: {e}")
        print("请确保已安装 pymysql 依赖")
        return False
    except Exception as e:
        print(f"❌ 连接失败: {e}")

        # 提供故障排除建议
        print("\n💡 故障排除建议:")
        print("1. 检查数据库服务是否正在运行")
        print("2. 验证主机地址和端口是否正确")
        print("3. 确认用户名和密码是否正确")
        print("4. 检查数据库是否存在")
        print("5. 确认网络连接和防火墙设置")

        return False


def test_tools():
    """测试 MySQL 工具是否正常工作"""
    print("\n=== MySQL 工具测试 ===\n")

    try:
        from yuxi.agents.toolkits.mysql.tools import mysql_list_tables, mysql_describe_table, mysql_query

        print("✅ MySQL 工具导入成功")

        # 测试获取表名
        print("\n🔄 测试获取表名...")
        result = mysql_list_tables.invoke({})
        if "失败" not in result and "错误" not in result:
            print("✅ 获取表名工具正常")
        else:
            print(f"❌ 获取表名工具异常: {result}")
            return False

        # 如果有表，测试获取表结构
        if "数据库中的表:" in result:
            print("\n🔄 测试获取表结构...")
            # 提取第一个表名
            lines = result.split("\n")
            for line in lines:
                if "- " in line and "(" in line:
                    table_name = line.split("- ")[1].split(" ")[0]
                    break
            else:
                table_name = None

            if table_name:
                structure_result = mysql_describe_table.invoke({"table_name": table_name})
                if "失败" not in structure_result and "错误" not in structure_result:
                    print("✅ 获取表结构工具正常")
                else:
                    print(f"❌ 获取表结构工具异常: {structure_result}")
                    return False

            # 测试简单查询
            print("\n🔄 测试SQL查询...")
            query_result = mysql_query.invoke({"sql": f"SELECT COUNT(*) as total FROM `{table_name}`"})
            if "失败" not in query_result and "错误" not in query_result:
                print("✅ SQL查询工具正常")
            else:
                print(f"❌ SQL查询工具异常: {query_result}")
                return False

        return True

    except Exception as e:
        print(f"❌ 工具测试失败: {e}")
        return False


def main():
    """主函数"""
    print("MySQL 数据库连接和工具验证脚本")
    print("=" * 50)

    # 测试连接
    connection_ok = test_mysql_connection()

    if connection_ok:
        # 测试工具
        tools_ok = test_tools()

        print("\n" + "=" * 50)
        if connection_ok and tools_ok:
            print("🎉 所有测试通过！MySQL 工具包可以正常使用")
        else:
            print("❌ 部分测试失败，请检查配置")
    else:
        print("\n" + "=" * 50)
        print("❌ 数据库连接失败，请检查配置")


if __name__ == "__main__":
    main()
