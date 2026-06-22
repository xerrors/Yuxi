from __future__ import annotations

import argparse
from datetime import UTC, datetime

from langfuse import Langfuse


PYTHON_TASK_ITEMS = [
    {
        "id": "py-task-001",
        "input": (
            "请用 Python 完成任务并给出最终答案：给定 nums = [7, -2, 5, 11, -8, 4]，"
            "只保留正数，平方后求和。请只输出最终整数。"
        ),
        "expected_output": "211",
    },
    {
        "id": "py-task-002",
        "input": (
            "请用 Python 完成任务并给出最终答案：给定文本 'alpha,beta;gamma delta\\nepsilon'，"
            "按逗号、分号、空白和换行切分 token，统计 token 数。请只输出最终整数。"
        ),
        "expected_output": "5",
    },
    {
        "id": "py-task-003",
        "input": (
            "请用 Python 完成任务并给出最终答案：计算 1 到 200 中所有能被 7 整除但不能被 5 整除的整数个数。"
            "请只输出最终整数。"
        ),
        "expected_output": "23",
    },
    {
        "id": "py-task-004",
        "input": (
            "请用 Python 完成任务并给出最终答案：给定 records = [('a', 3), ('b', 5), ('a', 4), ('c', 2), ('b', -1)]，"
            "按 key 汇总数值，并按 key 字母序输出形如 a=7,b=4,c=2 的字符串。"
        ),
        "expected_output": "a=7,b=4,c=2",
    },
    {
        "id": "py-task-005",
        "input": (
            "请用 Python 完成任务并给出最终答案：给定矩阵 [[1, 2, 3], [4, 5, 6], [7, 8, 9]]，"
            "计算主对角线之和减去副对角线之和。请只输出最终整数。"
        ),
        "expected_output": "0",
    },
    {
        "id": "py-task-006",
        "input": (
            "请用 Python 完成任务并给出最终答案：给定 s = 'Yuxi Agent Evaluation'，"
            "忽略大小写统计元音字母 a/e/i/o/u 的总数。请只输出最终整数。"
        ),
        "expected_output": "10",
    },
    {
        "id": "py-task-007",
        "input": (
            "请用 Python 完成任务并给出最终答案：给定区间 [10, 50]，找出所有质数并求和。"
            "请只输出最终整数。"
        ),
        "expected_output": "311",
    },
    {
        "id": "py-task-008",
        "input": (
            "请用 Python 完成任务并给出最终答案：给定 JSON 字符串 "
            "'{\"items\":[{\"price\":12.5,\"qty\":2},{\"price\":3,\"qty\":5},{\"price\":8,\"qty\":1}]}'，"
            "计算所有 price * qty 的总和。请只输出最终数字，保留一位小数。"
        ),
        "expected_output": "48.0",
    },
    {
        "id": "py-task-009",
        "input": (
            "请用 Python 完成任务并给出最终答案：给定 words = ['graph', 'rag', 'agent', 'tool', 'trace']，"
            "按单词长度升序、长度相同时按字母序排序，并用 '-' 连接。"
        ),
        "expected_output": "rag-tool-agent-graph-trace",
    },
    {
        "id": "py-task-010",
        "input": (
            "请用 Python 完成任务并给出最终答案：生成 Fibonacci 序列前 12 项（从 0, 1 开始），"
            "取偶数项求和。请只输出最终整数。"
        ),
        "expected_output": "44",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload a Python task dataset to Langfuse.")
    parser.add_argument(
        "--dataset-name",
        default=f"yuxi-python-tasks-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
        help="Langfuse dataset name. Defaults to a timestamped name.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = Langfuse()
    client.create_dataset(
        name=args.dataset_name,
        description="Yuxi agent evaluation dataset: deterministic Python programming tasks.",
        metadata={
            "source": "scripts/eval/upload_langfuse_python_tasks_dataset.py",
            "task_type": "python_programming",
            "item_count": len(PYTHON_TASK_ITEMS),
        },
    )
    for item in PYTHON_TASK_ITEMS:
        client.create_dataset_item(
            dataset_name=args.dataset_name,
            id=item["id"],
            input={"input": item["input"]},
            expected_output=item["expected_output"],
            metadata={"category": "python_programming", "source": "yuxi_eval_smoke"},
        )
    client.flush()
    print(args.dataset_name)


if __name__ == "__main__":
    main()
