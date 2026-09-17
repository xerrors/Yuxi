# 跨平台测试路径使用标准转换

状态：implemented
类型：bug-fix
Owner：scripts/verify_engineering_contracts.py

## 问题
Windows上决策及事故记录投影以平台反斜杠输出，与现有仓库相对路径契约不一致。PDF资产测试直接取file URL的pathname，Windows盘符和非ASCII路径仍为URL形式，构建器因此找不到入口。两者在Linux常见英文路径下易被遗漏。

## 决策
决策及事故记录投影在序列化边界使用Path.as_posix()，保持已有JSON路径约定；PDF构建入口使用Node标准fileURLToPath()，不手动剥离盘符或URL解码。不改变测试预期，不关闭检查。

## 替代方案
按平台改预期将使同一投影不稳定；手工替换斜杠或decodeURIComponent不能完整处理URL和盘符语义。使用已有标准库，不加依赖。

## 后果
仅影响工程投影和测试路径，不改变业务行为。其他面向人类的本地报错路径仍保持原样。

## 验证
原工程测试test_projection_is_derived_from_current_owners在Windows失败，修复后应通过；原PDF本地CMap开发响应/构建资产字节测试在含中文的Windows工作目录因入口解析失败，修复后仍检查真实HTTP及构建产物字节。执行结果由PR记录，不以新预期遮盖失败。
