# 已落盘工具图片的审计副本去掉 base64

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/agents/tool_results.py

## 问题

工具图片会写入 `outputs/tool_media`，模型看到的 ToolMessage 仍保留 image 块。审计关闭时把同一份输出写入 `extra_metadata.output`，调试投影再把其中的 base64 序列化出来。图片已经有路径，审计和调试却继续保存整段 base64。

## 决策

只有带 `yuxi_saved_media` 且下标对应的 image 块，才从审计副本中删除 `base64` 和 `source.data`。模型消息、没有落盘记录的图片，以及公开投影在没有预览地址时的 data URL，保持原样。

### 实现方案

`durable_tool_output` 返回新的字典，不修改调用方对象。`ToolMessageAuditRepository._finish` 把这份副本写入 `extra_metadata.output`，重复关闭时用同样的规则比较，避免已去掉 base64 的记录被当成另一份结果。`serialize_tool_audit` 和 `normalize_tool_result` 的审计文本也走这份副本，所以尚未重写的旧行在调试视图里同样不回放 base64。公开内容仍按原块投影。

## 替代方案

- 审计继续保存完整输出：调试和数据库保留已经落盘的图片正文。
- 从模型 ToolMessage 删除 image 块：模型后续轮次看不到图片。
- 只在调试序列化时隐藏：新写入的审计行仍保存 base64。

## 后果

新关闭的工具审计不再保存已落盘图片的 base64。旧行要等再次以相同结果关闭才会被改写；在此之前调试读取已经不返回这段 base64，数据库里的旧值还在。没有落盘记录的图片仍完整进入审计。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 已落盘图片的审计文本不含 base64，原输出不被改写 | 审计文本仍含 base64，或原字典被清空 | durable_tool_output | test_saved_media_audit_drops_base64_and_leaves_model_output_intact | 直接改 output 本身 | Passed |
| 未落盘图片的审计保留 base64 | 没有路径也删掉图片 | normalize_tool_result | test_unsaved_image_audit_keeps_base64 | 无条件删除 base64 | Passed |
| 调试投影的 content 与 tool_output 都不含已落盘 base64 | tool_output 仍回放原块 | serialize_tool_audit | test_debug_projection_hides_saved_image_base64 | 投影直接返回 metadata.output | Passed |
