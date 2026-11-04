# Runtime 构建缓存回收无引用层

状态：implemented
类型：bug-fix
Owner：scripts/ci/build_topology_images.sh

## 问题

Runtime System Tests 在导入 sandbox 镜像时耗尽托管 runner 的 72 GB 根盘。该 job 从旧缓存命名恢复了约 5.12 GB 的 API 缓存归档；日志不能证明解压后的体积或其中无引用层的比例。local cache 默认保留旧 manifest 使用过的 blob，BuildKit 内部 GC 不管理这些外部导出文件，旧缓存与构建器、Docker 镜像同时占用磁盘。

## 决策

### 实现方案

构建脚本给有本地缓存的导出增加 `reset=true`，让 Buildx 原地删除无 tag 引用的 blob；保留独立缓存目录、`mode=max`、镜像构建顺序和 `--load`。共享 action 固定使用支持该能力的 Buildx 0.38.0，并同时升级三个镜像的缓存 key 与 restore prefix，使未进行回收的不可变旧条目不再被恢复。

## 替代方案

- 只重跑失败 job：保留旧缓存累积入口。
- 导出到新目录后替换：导出期间同时保留两份完整缓存，增加峰值占用。
- 删除 runner 预装工具：扩大清理范围，不能约束缓存自身的增长；缓存回收后若容量仍不足，再按真实磁盘证据评估。

## 后果

首次运行重新建立三组镜像缓存。独立缓存目录与命中策略延续[系统构建缓存决定](./2026-09-23-ci-system-tests-build-cache.md)，无引用 blob 不再永久累积；旧 manifest 不能再按 digest 导入。构建脚本要求 Buildx 至少 0.35，CI 固定版本为 0.38.0。

回收未引用层不能保证任意镜像都适配固定容量，实际容量结论由真实 hosted job 的完整镜像构建与拓扑运行证明。

## 验证

- Passed：`python3 -m unittest scripts.ci.test_resource_boundaries scripts.ci.test_release_workflows`，30 项通过。修改前真实 shell 测试因遗漏 `reset=true` 失败；恢复任一旧 key 或 restore prefix、移除兼容 Buildx 版本时，接线负控失败。
- Passed：临时官方 Buildx 0.38.0、独立 BuildKit 0.34.2 与 scratch/COPY 镜像进行真实缓存往返。不传 reset 时合成的无引用 blob 保留；传入时删除。导出后回读当前 OCI manifest、config 和 layer 的 SHA-256、大小，均正确，且清理后的缓存可再次导入构建。
- Passed：工程契约检查及其 56 项单测，`bash -n scripts/ci/build_topology_images.sh`、文档构建和 `git diff --check`。
- Passed：`docker compose exec -T api uv run --no-sync --group test pytest test/unit -m 'not slow' -q -p no:cacheprovider`，3605 项通过；容器缺少仓库根目录的 127 项跳过，由宿主机配置测试的 209 项通过补足。

- Passed：[Runtime System Tests](https://github.com/xerrors/Yuxi/actions/runs/41781428345) 在代码提交 `03b1acc94` 上的五个业务 job 及汇总检查全部成功，全部拓扑镜像完成构建与导入。回读五组 JUnit 报告，454 项无失败、无跳过，其中包含知识库 MCP 的 4 项与 Container 执行的 2 项。
