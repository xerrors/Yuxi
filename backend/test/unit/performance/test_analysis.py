"""阶段汇总不能把未知 CPU 变零，也不能累加首个模型发送后的线程时间。"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test.performance.analysis import MissingStageData, aggregate, main, summarize_phases


class StageAnalysisTest(unittest.TestCase):
    """用独立时点验证截断、未知值与阶段顺序。"""

    def test_absent_boundary_is_unavailable_not_zero(self):
        """空请求或缺少接入时点必须明确为不可计算，不能产生零耗时。"""
        with self.assertRaisesRegex(MissingStageData, "没有请求"):
            summarize_phases({"requests": []})
        spans = [
            {"name": name, "start_ns": start, "end_ns": start + 1}
            for name, start in (
                ("run_worker.prepare_and_record_run_execution", 2),
                ("ChatbotAgent.get_graph", 4),
                ("AsyncPostgresSaver.aget_tuple", 6),
            )
        ]
        with self.assertRaisesRegex(MissingStageData, "边界时点"):
            summarize_phases(
                {
                    "events": [{"event": "model_send", "run_id": "r", "start_ns": 1}],
                    "requests": [{"run_id": "r", "api_received_ns": None, "model_send_ns": 8, "spans": spans}],
                }
            )

    def test_phase_mean_and_percentiles_share_one_extraction(self):
        """分布由可手算样本证明，不能重复解析同一批探针。"""
        with patch(
            "test.performance.analysis.critical_phase_samples",
            side_effect=[[{"phase": 10}, {"phase": 20}, {"phase": 30}]],
        ):
            result = summarize_phases({})
        self.assertEqual(result, {"phase": {"mean": 20, "p50": 20, "p95": 30}})

    def test_refresh_returns_and_persists_the_observed_events(self):
        """刷新后的分析与报告必须共享新探针，不能混用旧 Worker 起点。"""
        spans = [
            {"name": name, "start_ns": start * 1_000_000, "end_ns": (start + 1) * 1_000_000}
            for name, start in (
                ("run_worker.prepare_and_record_run_execution", 2),
                ("ChatbotAgent.get_graph", 4),
                ("AsyncPostgresSaver.aget_tuple", 6),
            )
        ]
        data = {
            "groups": [
                {
                    "workers": 1,
                    "concurrency": 1,
                    "p95_ms": 8,
                    "requests": [
                        {"run_id": "r", "api_received_ns": 0, "model_send_ns": 8_000_000, "spans": spans, "db": {}}
                    ],
                    "events": [{"event": "model_send", "run_id": "r", "start_ns": 1_000_000}],
                }
            ]
        }
        events = [{"event": "model_send", "run_id": "r", "start_ns": 1_500_000}]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("test.performance.analysis.read_probe_events", return_value=events),
            patch("test.performance.analysis.join_timings"),
        ):
            path = Path(directory) / "matrix.json"
            path.write_text(json.dumps(data))
            result = main(path, True)
            self.assertEqual(result["groups"][0]["events"], events)
            self.assertEqual(json.loads(path.with_name("complete.json").read_text())["groups"][0]["events"], events)
            phases = json.loads(path.with_name("stages.json").read_text())[0]["critical_phases"]
            self.assertEqual(phases["API 接入至 Worker 开始"], 1.5)
            self.assertEqual(phases["领取、读取输入/用户/工作目录"], 0.5)

    def test_duplicate_groups_preserve_existing_outputs_before_refresh(self):
        """分组错误在读取容器或覆盖既有派生结果前失败。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matrix.json"
            path.write_text(json.dumps({"groups": [{"workers": 1, "concurrency": 10}] * 2}))
            output = path.with_name("stages.json")
            output.write_text("previous result\n")
            with self.assertRaisesRegex(ValueError, "重复"):
                main(path, True)
            self.assertEqual(output.read_text(), "previous result\n")
            self.assertFalse(path.with_name("complete.json").exists())

    def test_unknown_greenlet_cpu_stays_unknown(self):
        span = {
            "name": "pool",
            "kind": "greenlet",
            "start_ns": 0,
            "end_ns": 1000000,
            "cpu_ms": None,
            "active_ms": None,
        }
        row = aggregate({"requests": [{"model_send_ns": 2000000, "spans": [span]}]}, "spans")["pool"]
        self.assertEqual(row["ms"], 1)
        self.assertIsNone(row["cpu_ms"])
        self.assertIsNone(row["active_ms"])

    def test_thread_intervals_share_the_model_send_cutoff(self):
        span = {
            "name": "thread:dump",
            "kind": "thread",
            "start_ns": 1000000,
            "end_ns": 7000000,
            "work_start_ns": 2000000,
            "work_end_ns": 4000000,
            "cpu_ms": 1,
            "active_ms": 0,
            "queue_ms": 99,
            "work_ms": 99,
            "resume_ms": 99,
        }
        for cutoff, expected, cpu in [
            (3000000, (2, 1, 1, 0), None),
            (5000000, (4, 1, 2, 1), 1),
        ]:
            with self.subTest(cutoff=cutoff):
                row = aggregate({"requests": [{"model_send_ns": cutoff, "spans": [span]}]}, "spans")["thread:dump"]
                self.assertEqual(
                    tuple(row[key] for key in ("ms", "queue_ms", "work_ms", "resume_ms")),
                    expected,
                )
                self.assertEqual(row["cpu_ms"], cpu)
                self.assertIsNone(row["active_ms"])

    def test_out_of_order_critical_phase_is_rejected(self):
        spans = [
            {"name": "run_worker.prepare_and_record_run_execution", "start_ns": 20, "end_ns": 40},
            {"name": "ChatbotAgent.get_graph", "start_ns": 30, "end_ns": 50},
            {"name": "AsyncPostgresSaver.aget_tuple", "start_ns": 60, "end_ns": 70},
        ]
        group = {
            "events": [{"event": "model_send", "run_id": "r", "start_ns": 10}],
            "requests": [
                {
                    "run_id": "r",
                    "api_received_ns": 0,
                    "model_send_ns": 80,
                    "spans": spans,
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "顺序阶段"):
            summarize_phases(group)

    def test_three_groups_label_the_actual_last_two_concurrencies(self):
        """三组输入的末两组比较必须标为 10→20，不能沿用 1→10。"""
        data = {"groups": [{"concurrency": level, "p95_ms": 0, "requests": [], "events": []} for level in (1, 10, 20)]}
        output = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as directory,
            redirect_stdout(output),
            patch("test.performance.analysis.summarize_phases", return_value={}),
        ):
            path = Path(directory) / "matrix.json"
            path.write_text(json.dumps(data))
            main(path, False)
        self.assertEqual(
            json.loads(output.getvalue().splitlines()[0]),
            {
                "workers": 1,
                "baseline_concurrency": 10,
                "comparison_concurrency": 20,
            },
        )

    def test_comparison_does_not_pair_different_workers(self):
        """跨 Worker 的同一并发档位不能冒充并发增长对照。"""
        data = {
            "groups": [
                {
                    "workers": workers,
                    "concurrency": 20,
                    "p95_ms": 0,
                    "requests": [],
                    "events": [],
                }
                for workers in (1, 4)
            ]
        }
        output = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as directory,
            redirect_stdout(output),
            patch("test.performance.analysis.summarize_phases", return_value={}),
        ):
            path = Path(directory) / "matrix.json"
            path.write_text(json.dumps(data))
            main(path, False)
        self.assertEqual(output.getvalue(), "")

    def test_missing_baseline_cannot_be_used_as_zero_increase(self):
        """任一组缺少阶段时不输出增量，完整组的阶段仍单独保存。"""
        spans = [
            {"name": name, "start_ns": start * 1_000_000, "end_ns": (start + 1) * 1_000_000}
            for name, start in (
                ("run_worker.prepare_and_record_run_execution", 2),
                ("ChatbotAgent.get_graph", 4),
                ("AsyncPostgresSaver.aget_tuple", 6),
            )
        ]
        request = {"run_id": "r", "api_received_ns": 0, "model_send_ns": 8_000_000, "spans": spans}
        for missing in (10, 20):
            data = {
                "groups": [
                    {
                        "workers": 1,
                        "concurrency": concurrency,
                        "p95_ms": 8,
                        "requests": [] if concurrency == missing else [request],
                        "events": [{"event": "model_send", "run_id": "r", "start_ns": 1_000_000}],
                    }
                    for concurrency in (10, 20)
                ]
            }
            output = io.StringIO()
            with tempfile.TemporaryDirectory() as directory, redirect_stdout(output):
                path = Path(directory) / "matrix.json"
                path.write_text(json.dumps(data))
                main(path, False)
                result = json.loads(path.with_name("stages.json").read_text())
                complete = next(row for row in result if row["concurrency"] != missing)
                self.assertEqual(complete["critical_phases"]["API 接入至 Worker 开始"], 1)
            self.assertEqual(output.getvalue(), "")
