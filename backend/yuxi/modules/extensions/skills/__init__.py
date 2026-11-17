"""Skill 管理与运行边界。

管理任务从 shared（共享治理）、personal（个人文件）、install_draft（安装暂存）
或 bound（Agent 绑定）进入；内容编辑共用 edit/content 的修订与提交规则。
access 拥有授权，projection 拥有用户文件投影，runtime/resolved 拥有运行快照；
这些共同边界被多个来源与其他业务域消费，不归某一种 Skill 的管理入口。
内置定义发现与同步从 builtin.sync 进入，不经过 shared。
"""
