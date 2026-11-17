# 事故标题

日期：YYYY-MM-DD
Owner：path/to/current-owner
关联决策：path/to/decision-or-无

## 影响

说明真实用户、数据、安全、可用性或外部系统影响，以及已确认的范围。未知范围明确写为未知。

## 事实时间线

只记录可验证事件、时间和观察来源，不保存推理流水账或人员评价。

## 因果链

从触发条件、系统行为、失效边界到用户影响解释必要因果。区分直接事实和推断。

## 安全网为何漏过

说明既有测试、oracle、workflow、Review 或运行时 guard 为什么没有在更早位置拒绝该错误。

## 修正与验证

链接修复 Owner，列出 reproducer、直接 oracle、负向案例、准确命令、实际结果和未验证范围。

## 防复发措施

记录已经落地的 generalized guard、gate、decision 或 standing order。每项必须有 Owner 和拒绝后果。

## 未解决风险

列出仍未知、未执行或需要外部校准的范围。没有则写“无”。
