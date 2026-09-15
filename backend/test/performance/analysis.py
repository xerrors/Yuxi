"""重读细粒度日志，生成每请求阶段均值及并发增量，不输出消息内容。"""

import json
from collections import defaultdict
from datetime import UTC, datetime
from itertools import pairwise

from .matrix import join_timings, percentile, read_probe_events


class MissingStageData(ValueError):
    """缺失模型前观测，无法计算完整顺序阶段。"""


def critical_phase_samples(group):
    """用顺序边界拆分主链路；不把后台 checkpoint 写入和取消监听相加。"""
    rows = []
    sends = {event["run_id"]: event for event in group.get("events", []) if event["event"] == "model_send"}
    if not group["requests"]:
        raise MissingStageData("没有请求样本")
    for request in group["requests"]:
        spans = request.get("spans", [])
        boundaries = [
            next((s for s in spans if s["name"].endswith(name)), None)
            for name in (
                ("run_worker.prepare_and_record_run_execution", "run_worker.persist_run_manifest"),
                "ChatbotAgent.get_graph",
                "AsyncPostgresSaver.aget_tuple",
            )
        ]
        sent = sends.get(request.get("run_id"))
        if sent is None or any(span is None for span in boundaries):
            raise MissingStageData("缺少模型发送或关键阶段探针")
        manifest, graph, checkpoint = boundaries
        points = [
            ("API 接入至 Worker 开始", request.get("api_received_ns")),
            ("领取、读取输入/用户/工作目录", sent.get("start_ns")),
            ("生成并持久化执行清单", manifest["start_ns"]),
            ("运行配置、附件与开始事件", manifest["end_ns"]),
            ("资源装配与构图", graph["start_ns"]),
            ("记录 prepared、提交与启动图", graph["end_ns"]),
            ("读取首次 checkpoint", checkpoint["start_ns"]),
            ("运行图前置节点至模型 HTTP 发送", checkpoint["end_ns"]),
            ("end", request.get("model_send_ns")),
        ]
        if any(point is None for _, point in points):
            raise MissingStageData("缺少主链路边界时点")
        durations = {name: (end - start) / 1e6 for (name, start), (_, end) in pairwise(points)}
        if any(value < 0 for value in durations.values()):
            raise ValueError("主链路边界不是顺序阶段，不能相加")
        rows.append(durations)
    return rows


def summarize_phases(group):
    """一次提取顺序阶段，同时计算可相加均值和不可相加的分位数。"""
    rows = critical_phase_samples(group)
    summary = {}
    for name in rows[0]:
        values = [row[name] for row in rows]
        summary[name] = {
            "mean": round(sum(values) / len(values), 3),
            "p50": percentile(values, 0.5),
            "p95": percentile(values, 0.95),
        }
    return summary


def aggregate(group, field):
    """同名阶段先在请求内求和，再跨请求求均值；父子跨度不能相加。"""
    totals = defaultdict(lambda: defaultdict(float))
    for request in group["requests"]:
        for span in request.get(field, []):
            if span.get("start_ns", 0) >= request["model_send_ns"] or span.get("end_ns") is None:
                continue
            values = totals[span["name"]]
            values["calls"] += 1
            for key in (
                "ms",
                "cpu_ms",
                "active_ms",
                "queue_ms",
                "work_ms",
                "resume_ms",
            ):
                cutoff = request["model_send_ns"]
                value = span.get(key)
                if key == "ms":
                    value = (min(span["end_ns"], cutoff) - span["start_ns"]) / 1e6
                elif span.get("kind") == "thread":
                    if key == "active_ms":
                        value = None
                    elif key == "cpu_ms" and span.get("work_end_ns", cutoff + 1) > cutoff:
                        # 未在线程内采到截止瞬间的 CPU，不能按 wall 比例猜测。
                        value = None
                    elif key in {"queue_ms", "work_ms", "resume_ms"} and "work_end_ns" in span:
                        intervals = {
                            "queue_ms": (span["start_ns"], span["work_start_ns"]),
                            "work_ms": (span["work_start_ns"], span["work_end_ns"]),
                            "resume_ms": (span["work_end_ns"], span["end_ns"]),
                        }
                        start, end = intervals[key]
                        value = max(0, min(end, cutoff) - start) / 1e6
                elif key in {"cpu_ms", "active_ms"} and span.get("kind") == "sync" and span["end_ns"] > cutoff:
                    value = None
                if value is None or values[key] is None:
                    values[key] = None
                else:
                    values[key] += value
    return {
        name: {key: None if value is None else round(value / len(group["requests"]), 3) for key, value in row.items()}
        for name, row in totals.items()
    }


def main(path, refresh):
    """补齐晚于 SSE end 输出的探针，并保留原始 Run 时点。"""
    data = json.loads(path.read_text())
    keys = [(group.get("workers", 1), group["concurrency"]) for group in data["groups"]]
    if len(keys) != len(set(keys)):
        raise ValueError("重复 Worker/并发组，不能隐式合并独立实验")
    summary = []
    for group in data["groups"]:
        if refresh:
            start = min(r["api_received_ns"] for r in group["requests"]) / 1e9 - 1
            events = read_probe_events(datetime.fromtimestamp(start, UTC).isoformat())
            join_timings(group["requests"], events, [r["db"] for r in group["requests"]])
            group["events"] = events
        try:
            phases = summarize_phases(group)
            api = aggregate(group, "api_spans")
            worker = aggregate(group, "spans")
        except MissingStageData as exc:
            phases, api, worker = {}, {}, {}
            group["phase_error"] = str(exc)
        summary.append(
            {
                "workers": group.get("workers", 1),
                "concurrency": group["concurrency"],
                "p50_ms": group.get("p50_ms"),
                "p95_ms": group.get("p95_ms"),
                **({"phase_error": group["phase_error"]} if group.get("phase_error") else {}),
                "critical_phases": {name: values["mean"] for name, values in phases.items()},
                "phase_percentiles": {
                    name: {"p50": values["p50"], "p95": values["p95"]} for name, values in phases.items()
                },
                "api": api,
                "worker": worker,
            }
        )
    if refresh:
        path.with_name("complete.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    path.with_name("stages.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    indexed = {(group["workers"], group["concurrency"]): group for group in summary}
    for workers in sorted({group["workers"] for group in summary}):
        first, last = indexed.get((workers, 10)), indexed.get((workers, 20))
        if first is None or last is None or first.get("phase_error") or last.get("phase_error"):
            continue
        print(
            json.dumps(
                {
                    "workers": workers,
                    "baseline_concurrency": first["concurrency"],
                    "comparison_concurrency": last["concurrency"],
                }
            )
        )
        for part in ("api", "worker"):
            rows = []
            for name, row in last[part].items():
                base = first[part].get(name, {})
                rows.append(
                    {
                        "name": name,
                        "baseline_ms": base.get("ms", 0),
                        "comparison_ms": row["ms"],
                        "increase_ms": round(row["ms"] - base.get("ms", 0), 2),
                        "comparison_cpu_ms": row["cpu_ms"],
                        "comparison_active_ms": row["active_ms"],
                        "calls": row["calls"],
                    }
                )
            print(part)
            print(
                json.dumps(
                    sorted(rows, key=lambda row: row["increase_ms"], reverse=True)[:35],
                    indent=2,
                )
            )

    return data
