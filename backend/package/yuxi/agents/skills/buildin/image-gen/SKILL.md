---
name: image-gen
description: "在 Agent 沙盒中生成图片并保存到 outputs。当用户要求生成图片、海报、插画、文生图，或指定 Qwen-Image、其它兼容图片生成接口时使用此技能。"
version: "2026.06.02"
tool_dependencies: ["present_artifacts"]
---

# 图片生成技能

当用户要求生成图片、海报、插画、文生图，或明确提到 Qwen-Image 时，使用此技能组织图片生成流程。

## 默认生成接口

默认使用 SiliconFlow 的 Qwen-Image 接口：

- Endpoint: `POST https://api.siliconflow.cn/v1/images/generations`
- Model: `Qwen/Qwen-Image`
- 默认参数：
  - `negative_prompt`: `""`
  - `num_inference_steps`: `20`
  - `guidance_scale`: `7.5`

调用外部接口时，必须在 Agent 沙盒执行环境中读取 `SILICONFLOW_API_KEY`。不要依赖后端进程环境变量。

## 操作流程

1. 明确用户要生成的图片内容、风格、尺寸或约束；信息不足但不影响生成时，使用合理默认值，不要反复追问。
2. 使用可用的执行工具在沙盒中运行脚本，调用图片生成接口，传入用户需求整理后的 `prompt`，并按需传入 `negative_prompt`、`num_inference_steps`、`guidance_scale`。
3. 从生成接口响应中读取图片地址，默认路径为 `images[0].url`。
4. 在同一个沙盒脚本中用 `Authorization: Bearer $SILICONFLOW_API_KEY` 下载该图片地址；如果接口直接返回 base64，则直接解码保存。
5. 将最终图片保存到当前 Workdir 的 `outputs/` 下，例如 `outputs/generated-image.png`。
6. 调用 `present_artifacts`，传入保存后的 outputs 虚拟路径，让前端展示图片产物。
7. 最终回复简要说明图片已生成，不要把外部临时 URL 当作最终结果展示。

## 脚本示例

可根据用户需求调整 prompt 和输出文件名：

```python
import os
import requests
from pathlib import Path

api_key = os.environ["SILICONFLOW_API_KEY"]
prompt = "根据用户需求整理后的图片提示词"

response = requests.post(
    "https://api.siliconflow.cn/v1/images/generations",
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    json={
        "model": "Qwen/Qwen-Image",
        "prompt": prompt,
        "negative_prompt": "",
        "num_inference_steps": 20,
        "guidance_scale": 7.5,
    },
    timeout=120,
)
response.raise_for_status()
image_url = response.json()["images"][0]["url"]

image_response = requests.get(
    image_url,
    headers={"Authorization": f"Bearer {api_key}"},
    timeout=120,
)
image_response.raise_for_status()

output_path = Path("outputs/generated-image.png")
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_bytes(image_response.content)
print(output_path.as_posix())
```

## 多模型扩展

如果用户指定其它图片生成模型或兼容接口，可以按该接口的协议先生成图片。只要最终拿到图片 bytes 或 base64，就保存到当前 Workdir 的 `outputs/`，再调用 `present_artifacts` 展示。

## 关键约束

- 不要把外部生成接口返回的临时 URL 当作最终结果直接展示给用户。
- 不要调用后端 MinIO 上传工具；图片生成和下载都应在沙盒内完成。
- 如果 `SILICONFLOW_API_KEY` 缺失，应明确提示用户需要在 Agent 沙盒环境变量中配置。
- 保存到 outputs 后必须调用 `present_artifacts`，否则前端不会自动展示生成图片。
