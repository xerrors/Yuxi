"""
RAG评估指标计算工具
简化版：只保留Recall/F1（检索）和 LLM Judge（答案准确性）
"""

import textwrap
from typing import Any

import json_repair

from yuxi.utils import logger


class RetrievalMetrics:
    """检索评估指标计算"""

    @staticmethod
    def precision_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
        """计算Precision@K"""
        if not retrieved_ids[:k]:
            return 0.0
        retrieved_set = set(retrieved_ids[:k])
        relevant_set = set(relevant_ids)
        return len(retrieved_set & relevant_set) / k

    @staticmethod
    def recall_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
        """计算Recall@K"""
        if not relevant_ids:
            return 0.0
        retrieved_set = set(retrieved_ids[:k])
        relevant_set = set(relevant_ids)
        return len(retrieved_set & relevant_set) / len(relevant_set)

    @staticmethod
    def f1_score_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
        """计算F1@K"""
        precision = RetrievalMetrics.precision_at_k(retrieved_ids, relevant_ids, k)
        recall = RetrievalMetrics.recall_at_k(retrieved_ids, relevant_ids, k)
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)


class AnswerMetrics:
    """答案评估指标计算"""

    @staticmethod
    async def judge_correctness(query: str, generated_answer: str, gold_answer: str, judge_llm: Any) -> dict[str, Any]:
        """
        使用LLM判断生成的答案是否正确
        """
        if not generated_answer:
            return {"score": 0.0, "reasoning": "未生成答案"}
        if not gold_answer:
            return {"score": 0.0, "reasoning": "无参考答案"}

        prompt = textwrap.dedent(f"""你是一个公正的评判者，请评估AI生成的答案相对于标准答案的准确性。

            问题：{query}

            标准答案：
            {gold_answer}

            AI生成的答案：
            {generated_answer}

            请判断AI生成的答案是否在事实层面与标准答案一致。
            忽略措辞、标点符号或格式上的细微差异。
            只关注核心事实是否准确包含。

            请返回以下JSON格式的结果（不要包含其他文本、Markdown 或注释）：
            {{
                "score": 1.0,
                "reasoning": "简要说明判定理由"
            }}
            score 只能是 1.0 或 0.0。
            """)
        try:
            response = await judge_llm.call(prompt, stream=False)
            content = response.content.strip()

            # 尝试清理可能的 markdown 代码块
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            result = json_repair.loads(content)
            return {"score": float(result.get("score", 0.0)), "reasoning": result.get("reasoning", "")}
        except Exception as e:
            logger.error(f"LLM 评判失败: {e}")
            return {"score": 0.0, "reasoning": f"评判出错: {str(e)}"}


class EvaluationMetricsCalculator:
    """综合评估指标计算器"""

    @staticmethod
    def calculate_retrieval_metrics(
        retrieved_chunks: list[dict[str, Any]], gold_chunk_ids: list[str], k_values: list[int] = [1, 3, 5, 10]
    ) -> dict[str, float]:
        """计算检索指标 (Recall, F1)"""
        if not retrieved_chunks or not gold_chunk_ids:
            return {}

        # 提取 ID
        retrieved_ids = []
        for chunk in retrieved_chunks:
            chunk_id = chunk.get("chunk_id") or chunk.get("metadata", {}).get("chunk_id")
            retrieved_ids.append(str(chunk_id) if chunk_id else "")

        metrics = {}
        for k in k_values:
            metrics[f"recall@{k}"] = RetrievalMetrics.recall_at_k(retrieved_ids, gold_chunk_ids, k)
            metrics[f"f1@{k}"] = RetrievalMetrics.f1_score_at_k(retrieved_ids, gold_chunk_ids, k)

        return metrics

    @staticmethod
    async def calculate_answer_metrics(
        query: str, generated_answer: str, gold_answer: str, judge_llm: Any = None
    ) -> dict[str, Any]:
        """计算答案指标 (LLM Judge)"""
        if not judge_llm:
            return {}

        return await AnswerMetrics.judge_correctness(query, generated_answer, gold_answer, judge_llm)

    @staticmethod
    def calculate_overall_score(
        retrieval_metrics_list: list[dict[str, float]], answer_metrics_list: list[dict[str, Any]]
    ) -> float:
        """计算整体平均分"""
        total_score = 0.0
        count = 0

        # 简单的平均策略：将所有retrieval metric的值和answer metric的score一起平均
        # 用户可能希望分开看，但calculate_overall_score返回一个单值。

        # 计算检索平均分
        for m in retrieval_metrics_list:
            if m:
                total_score += sum(m.values()) / len(m)
                count += 1

        # 计算答案平均分
        for m in answer_metrics_list:
            if "score" in m:
                total_score += m["score"]
                count += 1

        return total_score / count if count > 0 else 0.0
