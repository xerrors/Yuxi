# Milvus 日志轮转与 CPU 上界

状态：implemented
类型：bug-fix
Owner：docker-compose.yml

## 问题

宿主机 dev 栈 `milvus` 容器 stdout 日志累计 4.49GB（1600 万行 INFO，约 236MB/天），根分区 98% 满；Compose 里的 `MILVUS_LOG_LEVEL: error` 从未生效（v2.5.6 二进制 grep 0 命中），是冒充控制的死配置。“日志占内存”的假设被证伪：RSS 4.52GB 中 Pss_Anon 4.48GB、Pss_File 16MB，Go heap inuse 243MB，内存主体是加载数据的 C++ 分配（dev 14 个 collection、Sealed 实体约 980MB；prod 单 collection 时 RSS 275MB），随知识库数据量线性增长，与日志无关。

## 决策

两份 Compose 删除无效的 `MILVUS_LOG_LEVEL`，对 `milvus` 与 `etcd` 启用 json-file 日志轮转（`max-size=50m`、`max-file=3`，单容器封顶约 150MB；dev etcd 已累计约 70MB、18.9 万行，另两栈 10.7MB/1.7MB，属预防性同批封顶）。对 `milvus` 只设 `cpus`（默认 2，`YUXI_MILVUS_CPUS` 可覆盖），让镜像内置 maxprocs 按 cgroup quota 收敛 GOMAXPROCS。**不设 `mem_limit`**：内存随知识库数据量线性增长，任何硬上界终将被合法增长击穿，而击穿模式是 OOM kill → 重启 → 重新加载同样数据 → 再 OOM 的重启循环，比渐进降级更糟；确需上界时应先建工作集监控与告警。日志级别本身只由镜像内 `milvus.yaml` 拥有。

## 替代方案

- 挂载整份覆盖 `milvus.yaml` 设 `log.level`：能消除 INFO 洪水，但需 vendored 767 行配置并随镜像版本同步，升级时静默漂移，被拒绝。
- 设 `mem_limit`（曾评估 6g/8g）：当时工作集已 4.26GiB，上界迟早被正常增长撞到，撞到即重启循环，被拒绝。
- 只诊断不设上界：不满足日志/资源上界目标，被拒绝。

## 后果

轮转与 cpus 都要重建容器才生效，既有 4.49GB json.log 不自动回收，需合并后 `docker compose up -d milvus etcd` 并手工清理旧日志。不设内存上界意味着 milvus 只受宿主可用内存约束（宿主 62.6GiB、可用 29.9GiB、swap 已满；宿主级 OOM 的首选受害者恰是 RSS 最大的 milvus），可控手段是 unload 冷 collection 与三套并行栈去重。`YUXI_MILVUS_CPUS` 会限制索引构建吞吐，可调。

## 验证

- `docker compose config --quiet`（dev/prod）渲染通过；`backend/test/unit/config/test_docker_compose_service_boundaries.py` 断言两份 Compose 的轮转、`cpus` 与“无 MILVUS_LOG_LEVEL、无 mem_limit”。
- 实验容器实测 `docker inspect` 返回 `LogConfig={"Type":"json-file","Config":{"max-size":"50m","max-file":"3"}}`；移除字段后断言失败（unit 负向）。
- Not run：重建后的轮转与内存观察，留待合并后执行；Milvus 工作集监控告警为后续项。
