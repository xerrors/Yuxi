# 图检索 PageRank 离开事件循环，子图在库内截断

状态：implemented
类型：bug-fix
Owner：backend/yuxi/modules/knowledge/graphs/service.py

## 问题

知识库图检索把 Neo4j 读取卸到 `run_neo4j_query_io`，但 `query_and_rank_chunks_by_ppr` 随后在请求协程里同步执行 networkx PageRank。默认子图可达一万个节点，上限五万。种子查询先按 `max_nodes * 4` 收集整条路径，再把路径上的全部节点和边一次性返回，Python 侧才按 `max_nodes` 截断。一次带图谱的检索会占住处理该请求的事件循环，并把远超排序所需的图传回进程。

## 决策

PageRank 使用独立的有界线程卸载，不占用 Neo4j 查询槽。种子子图仍先用路径预算取样，然后在 Neo4j 内按节点上限截断，种子实体优先保留；只返回这些节点以及它们之间的关系。

### 实现方案

`run_graph_rank_io` 与 Milvus、Neo4j 卸载分开，容量为 2。`query_and_rank_chunks_by_ppr` 取回可见子图后，把 `rank_chunks_by_ppr` 交给该卸载执行。种子 Cypher 在路径 `LIMIT` 之后对节点 `DISTINCT`、种子优先排序，再 `LIMIT $max_nodes`，随后只匹配保留节点之间的关系。空关系在列表里丢掉，不在 `OPTIONAL MATCH` 的 `WHERE` 里过滤，避免没有边时把已经截断的节点行一起去掉。`_process_subgraph_record` 继续丢掉端点不在节点集里的边。

## 替代方案

- 继续在协程内计算：图一大就会堵住同进程的其他请求。
- 与 Neo4j 共用 8 个卸载槽：排序占槽时图读取一起排队。
- 只在 Python 截断：驱动仍接收完整路径展开后的节点和边。

## 后果

排序等待线程时，该次检索的协程让出事件循环。并发排序最多 2 个。节点截断发生在返回之前，极多邻居时仍会先走过路径预算，但不再把全部路径边传回进程。种子优先使截断时仍保留个性化起点；若路径预算内没有种子实体，排序结果仍为空。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| PageRank 不在请求事件循环线程执行 | 同步调用 rank | query_and_rank_chunks_by_ppr | test_ppr_ranking_runs_outside_event_loop | 改回直接 return rank | Passed |
| 种子查询在 Cypher 中限制节点数，且无边时仍返回节点 | 仍 collect 全部路径，或空关系把节点行过滤掉 | query_seed_subgraph | test_seed_subgraph_limits_nodes_in_cypher_before_returning | 去掉 LIMIT $max_nodes，或在 OPTIONAL MATCH 上用 rel IS NOT NULL 丢掉节点 | Passed |
| 既有 PPR 排序不变 | 种子找不到 chunk | rank_chunks_by_ppr | test_graph_ppr_ranks_chunk_nodes_from_seed_entities | 排名顺序颠倒 | Passed |
