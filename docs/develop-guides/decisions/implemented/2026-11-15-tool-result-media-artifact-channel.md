# 工具结果图片的 artifact 持久化通道

状态：implemented
类型：feature
Owner：backend/yuxi/modules/agents/runtime/middlewares/tool_media.py

## 问题

设备工具（aBridge computer_observe/browser_open 等）返回的截图是 MCP image 内容块，只存在于对话上下文：模型无法在最终答案中引用（widget Image 只认沙盒路径），公开投影把含 image 的混合内容整体 JSON 字符串化（单条 public_items 达 1.9-4.2MB base64），历史回看不可用。模型只能用 screencapture/base64/file_read 搬运或在沙盒 playwright 自拍把图"搬"进答案，造成设备调用 4-6 倍膨胀（docs/vibe/abridge-detour-defects.md D-1/D-2/D-3/D-5）。文本工具结果已有超阈值 spill（outputs/large_tool_results），图片是唯一缺口。

## 决策

### 实现方案

新增 ToolMediaPersistMiddleware，装配在 ToolErrorGuard 内层：工具结果 ToolMessage 中的 image 块（base64 + 白名单 mime）按内容寻址写入当前 Workdir 的 `outputs/tool_media/<tool>-<digest16>.<ext>`（RuntimeFiles 二进制写入），image 块保留供模型视觉使用，末尾追加路径注记，保存事实记入 `additional_kwargs.yuxi_saved_media`。ToolErrorGuard 发布审计与公开投影时优先携带该无损增量。normalize_tool_result 将 image 块投影为 `input_image`：优先经 RunMessageRecorder 构造的线程 artifact 预览地址（`/api/v1/agents/sessions/{id}/artifacts/{path}?preview=true`），退回内嵌 data URL，最后维持 JSON 文本兜底；模型注记不进入公开内容。前端零改动：工具卡片与历史回放按既有 input_image 渲染，模型用 Image widget 的 `path` 直接引用落盘文件。装配只看用户与 Workdir，不看沙盒 backend 是否存在。

## 替代方案

- 仅投影内嵌 base64（Claude Code 式会话记录）：改动最小，但模型仍拿不到可引用路径，D-1 式搬运绕路无法消除，且消息表持续膨胀。
- 在 aBridge 侧落盘并返回 URL：设备文件体系与 Yuxi Workdir 是两个信任域，widget/预览/权限链都要重造；且未来其他 MCP 图片工具仍无通道。
- 持久化为独立 artifact 表 + 新 API：与既有沙盒文件 + 预览通道重复，无当前 consumer。

## 后果

设备与其他 MCP 工具返回的图片统一进入 Workdir `outputs/tool_media/`（内容寻址幂等）；模型上下文与审计保留 image 块原文。public_items 在"已落盘且线程提供预览地址"时只存引用不携带 base64；无 Workdir 线程或非白名单 mime 时退回 data URL（体积与旧 JSON 串同级）。写盘失败仅记录告警并退回原行为，不阻塞工具链路。落盘文件占用用户 Workdir 配额，历史截图随 Workdir 生命周期保留。另有一处行为变化：含保存图片的结果，审计与公开投影发布的是 eviction 之后的注记消息（截断文本 + offload 引用 + image 块 + 保存注记），而旧逻辑发布压缩前全文——对含图结果换来了路径事实，纯文本结果行为不变。非白名单 mime 或空 data 的图片跳过落盘（实证 2026-11-14 库中 aBridge image 块 base64 长度 1.9M，非空）。

## 验证

- 单元：`test/unit/middlewares/test_tool_media_middleware.py`（真实宿主目录落盘、内容寻址、Command 重建、空数据/非白名单跳过、写失败降级、Guard 联合发布携带保存事实、backend 为空仍装配）；`test/unit/agents/test_tool_result_projection.py`（预览 URL、data URL 兜底、index 错位回退、注记过滤、既有纯文本/input 协议回归）。
- 集成：`test/integration/api/test_tool_media_artifact_preview.py` 真实 HTTP + PostgreSQL：真实用户线程内落盘截图 → 公开投影为线程预览 URL（不含 base64）→ 同用户 GET 200 且字节一致，匿名 401、跨用户 404。
- 未验证范围：D-1 基准任务（"两台设备分别截图" ≤12 次调用、0 次搬运、答案含可回看截图）需要 aBridge 真机设备环境，本机无已配对设备，留待设备环境按 docs/vibe/abridge-detour-defects.md 第五节回归清单执行；前端浏览器端到端展示同样依赖真实设备运行。
