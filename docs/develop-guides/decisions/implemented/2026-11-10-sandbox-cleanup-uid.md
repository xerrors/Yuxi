# 沙盒清理使用创建时的 uid 目录名

状态：implemented
类型：bug-fix
Owner：backend/yuxi/sandbox_runner/manager.py

## 问题

容器标签 `yuxi.uid` 在创建时保存的是 `workspace_uid_dirname` 的结果。清理 stdin 时，`_cleanup_stdin_dir` 把该标签原样交给 `_validated_workdir`，而后者把 uid 当作 `shared/<uid>/workspace` 的一个目录段。标签若带有斜杠或 `..`，`openat` 与随后的目录遍历会离开数据根。只在 workdir 组件里拒绝 `.` 和 `..` 盖不住这个目录段。

## 决策

### 实现方案

打开 Workdir 之前，uid 必须已经是创建路径写入标签的目录名。判定复用 `workspace_uid_dirname` 与 `_validate_uid`：值必须等于 `workspace_uid_dirname` 的返回值，因此空值、NUL、斜杠、反斜杠、`.`、`..` 以及其他不能作为目录名保留的字符被拒绝；长度仍不得超过 `_validate_uid` 的 128。不把非法标签重新哈希成另一个目录，也不另写一套更宽的字符规则。

合法目录名继续逐段 no-follow 打开。清理遇到非法 uid 时放弃本次 stdin 删除，不访问数据根之外的路径。

## 替代方案

- 只在 workdir 里拒绝 `.` 和 `..`：uid 段仍可带斜杠离开数据根。
- 对非法标签再做一次哈希：路径留在数据根内，但会打开一个创建时并不存在的目录名，而不是拒绝该标签。
- 用单独的宽松正则只拦斜杠：会放过创建规则已经排除的其他目录名。

## 后果

清理只会打开创建时允许出现在 `yuxi.uid` 里的目录名。带斜杠或 `..` 的标签不会删除数据根外的文件，超长或含 NUL 的标签同样不会打开。创建路径的目录名算法不变，已有安全 uid 的 stdin 清理仍只删除本 Run 的临时文件。

## 验证

`backend/test/unit/sandbox/test_sandbox_runner_manager.py` 在数据根外放置可被斜杠标签到达的 stdin 文件，断言斜杠、`..`、`.`、反斜杠、NUL 和超长标签既不删除该文件，也以 uid 非法被拒绝。既有用例继续覆盖合法 uid 只删除本 Run 文件。
