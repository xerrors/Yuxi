# 智能体评估

Yuxi 的智能体评估用于回答一个具体问题：某个 Agent 在一组固定任务上能不能稳定完成工作。它不在 Yuxi 内部维护评估数据集、评分规则或对比报表，而是把这些能力交给 Langfuse；Yuxi 只负责按真实 Agent 运行链路执行每条样例，并把结果回写到 Langfuse experiment。

## 适用边界

这个功能面向 Agent 端到端行为评估，不是知识库检索指标评估。如果你要评估 RAG 检索召回、答案准确率和知识库基准，请使用「知识库评估」。如果你要评估一个 Agent 在编程、研究、工具调用、规划或多步骤任务上的真实表现，则使用本页介绍的 Langfuse dataset experiment 流程。

评估链路保持三个边界：

- Langfuse 负责 dataset、experiment、score、对比和可视化。
- Yuxi 后端负责创建正常 conversation 和 AgentRun，并复用 worker 执行链路。
- `yuxi` CLI 只负责读取 Langfuse dataset、运行 experiment、调用 Yuxi eval API，不负责创建或上传 dataset。

## 前置条件

1. Yuxi 后端已经启用 Langfuse tracing，并在 `.env` 中配置：

```bash
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

2. 本机 CLI 环境也能读取同一组 Langfuse 环境变量。`yuxi agent eval` 需要直接调用 Langfuse SDK 读取 dataset 和创建 experiment。

3. 已经登录 Yuxi CLI：

```bash
yuxi remote add local http://localhost:5173
yuxi login --browser
```

评估命令必须使用当前 remote 的登录态，不支持在 `yuxi agent eval` 上直接传 token。CI 环境也必须先执行登录步骤，例如：

```bash
yuxi login --api-key "$YUXI_API_KEY"
```

4. 要评估的 Agent 已经存在，并且当前 CLI 登录用户有权限访问该 Agent。命令使用的是 Agent slug，例如 `default-chatbot`。

## 准备 Langfuse Dataset

评估数据集必须先在 Langfuse 中准备好。CLI 不提供上传能力，避免把数据集管理职责混进运行命令。

Dataset item 的 `input` 推荐使用下面任一字段承载任务文本：

```json
{"input": "请用 Python 完成任务并给出最终答案：..."}
```

也兼容 `query`、`question`、`prompt`。`expected_output` 可以写标准答案，后续在 Langfuse UI 或 evaluator 中使用。

## 运行评估

上传 dataset 后，用 dataset name 运行：

```bash
yuxi agent eval \
  --dataset-name yuxi-python-tasks-20260619-demo \
  --agent-slug default-chatbot \
  --experiment-name default-chatbot-python-tasks-20260619 \
  --max-concurrency 1 \
  --timeout-seconds 900
```

命令执行流程：

1. 从 Langfuse 读取 dataset。
2. 对每条 dataset item 提取任务文本。
3. 调用 `POST /api/agent-invocation/eval/runs`。
4. Yuxi 后端创建正常 conversation 和 AgentRun。
5. worker 按真实 Agent 链路执行任务。
6. 接口阻塞到 run 终态后返回最终 assistant output。
7. CLI 将 output 写回 Langfuse experiment item。

`--max-concurrency` 控制 Langfuse experiment runner 的并发数。复杂 Agent 或本地开发环境建议从 `1` 开始，避免同时压垮模型服务、worker 或沙盒。

## 查看结果

评估完成后，在 Langfuse 控制台打开对应 dataset，可以看到刚创建的 experiment run。每条 item 会保存本次 Yuxi Agent 的最终输出。Yuxi 后端会在运行内部使用 `agent_invocation_meta.evaluation` 保存评估上下文，并给 Langfuse trace 写入 `agent_evaluation` 标记，方便筛选：

- `source=agent_evaluation`
- `evaluation_dataset_name=<dataset name>`
- `evaluation_dataset_item_id=<item id>`
- `evaluation_experiment_name=<experiment name>`

如果没有看到 experiment，先确认 CLI 环境中的 Langfuse key 和 dataset name 是否正确。如果 experiment 有记录但 Yuxi trace 缺失，检查 `api-dev` 容器是否读取到了同一组 Langfuse 配置。
