from __future__ import annotations

import math
from typing import Any

import json_repair

from yuxi.models.chat import select_model

from .base import GraphExtractor

DEFAULT_TRIPLE_EXTRACTION_PROMPT = """请从下面文本中抽取实体和实体关系，返回严格 JSON，不要输出解释。
JSON 格式：
{
  "relations": [
    {
      "source": {"text": "实体文本", "label": "实体类型", "attributes": [{"text": "属性值", "label": "属性名称"}]},
      "target": {"text": "实体文本", "label": "实体类型", "attributes": [{"text": "属性值", "label": "属性名称"}]},
      "text": "关系显示文本",
      "label": "关系类型"
    }
  ]
}
"""

SCHEMA_INSTRUCTION = """抽取 Schema 约束：
{schema}
"""

# 单次抽取调用的超时。抽取产出（实体+关系 JSON）通常远长于普通对话，推理型模型
# 单块耗时可达数十秒；超时会触发重试，重试仍超时该分块才判定失败。可通过
# extractor_options.timeout_seconds 调大，默认保持历史行为不变。
DEFAULT_EXTRACTION_TIMEOUT_SECONDS = 60.0
MAX_EXTRACTION_TIMEOUT_SECONDS = 600.0


class LLMGraphExtractor(GraphExtractor):
    extractor_type = "llm"

    def _resolve_timeout_seconds(self) -> float:
        raw = self.options.get("timeout_seconds", DEFAULT_EXTRACTION_TIMEOUT_SECONDS)
        # bool 是 int 的子类：float(True) == 1.0 会被区间校验放行，配出一个 1 秒的超时，
        # 构建时每块必超时——配置保存成功、整个图谱全挂，是最难排查的失败形态。
        if isinstance(raw, bool):
            raise ValueError("LLM 抽取器 timeout_seconds 必须是数字")
        try:
            timeout = float(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            # 超大整数（JSON 里合法）会在 float() 上抛 OverflowError，必须与 TypeError 同等对待，
            # 否则它会穿透到路由的兜底分支变成 500，而非法配置应当是 400。
            raise ValueError("LLM 抽取器 timeout_seconds 必须是数字") from exc
        # NaN 与 ±inf 会让下面的区间比较全部为 False，必须显式挡掉
        if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_EXTRACTION_TIMEOUT_SECONDS:
            raise ValueError(f"LLM 抽取器 timeout_seconds 必须大于 0 且不超过 {MAX_EXTRACTION_TIMEOUT_SECONDS:g} 秒")
        return timeout

    def validate_options(self) -> None:
        if not self.options.get("model_spec"):
            raise ValueError("LLM 抽取器需要 model_spec")
        if self.options.get("prompt"):
            raise ValueError("LLM 图谱抽取器不支持自定义完整 Prompt，请使用 schema 配置抽取约束")
        concurrency_count = self.options.get("concurrency_count", 1)
        try:
            concurrency_count = int(concurrency_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("LLM 抽取器 concurrency_count 必须是整数") from exc
        if concurrency_count < 1 or concurrency_count > 1000:
            raise ValueError("LLM 抽取器 concurrency_count 必须在 1 到 1000 之间")
        # 注意：timeout_seconds 不能通过 model_params 设置——select_model 会把显式的
        # timeout 参数覆盖到 model_params 之上，所以只能在这里读取并显式传入。
        self._resolve_timeout_seconds()
        model_params = self.options.get("model_params")
        if model_params is not None and not isinstance(model_params, dict):
            raise ValueError("LLM 抽取器 model_params 必须是对象")
        # model_params 会展开成 ChatOpenAI 的构造参数，顶层 enable_thinking 最终落到
        # AsyncCompletions.create() 上，接口直接报未知参数，每块抽取都失败。
        if model_params and "enable_thinking" in model_params:
            raise ValueError(
                "LLM 抽取器 model_params 不支持顶层 enable_thinking，请写在 extra_body 中，"
                '例如 {"extra_body": {"enable_thinking": false}}'
            )

    async def extract(self, text: str, *, chunk_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        self.validate_options()
        model = select_model(
            model_spec=self.options["model_spec"],
            timeout=self._resolve_timeout_seconds(),
            model_params=self.options.get("model_params") or {},
        )
        prompt = self._build_prompt(text)
        response = await model.call(prompt, stream=False)
        parsed = json_repair.loads(response.content if response else "")
        return parsed

    def _build_prompt(self, text: str) -> str:
        extraction_prompt = DEFAULT_TRIPLE_EXTRACTION_PROMPT
        schema = str(self.options.get("schema") or "").strip()
        if schema:
            extraction_prompt = f"{extraction_prompt}\n{SCHEMA_INSTRUCTION.format(schema=schema)}"
        return f"{extraction_prompt}\n\n文本：\n{text}"
