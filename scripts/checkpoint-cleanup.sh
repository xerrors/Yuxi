#!/usr/bin/env bash
# LangGraph checkpoint 保留清理（治理 checkpoint_blobs 无界增长）。
# 规则：（K 默认 5：Yuxi 仅使用最新 checkpoint 恢复执行，不使用历史版本回溯）
#   P1 deleted 会话的线程 → checkpoints/blobs/writes 全量清除（跳过仍有非终态 Run 的线程）
#   P2 活跃会话线程 → 每个 (thread_id, checkpoint_ns) 保留最近 K 个 checkpoint 及其引用的
#      blob 版本与 writes；有 pending/running/cancel_requested Run 的线程整体跳过
# 仅碰 LangGraph 执行内部状态：PG 业务事实（messages/agent_runs）不受影响；
# 面板与 resume 只读最新 checkpoint，不会触达被删版本。
#
# 用法（宿主机 root；postgres 容器按 compose 标签自动识别）:
#   bash checkpoint-cleanup.sh report               # 只读：体量与可删行数
#   bash checkpoint-cleanup.sh purge-deleted   # 执行 P1（分批，可重跑）
#   K=5 bash checkpoint-cleanup.sh retain      # 执行 P2（默认保留最近 5 个版本，可重跑）
# 容器定位优先级: PG_CONTAINER=<容器名> > COMPOSE_PROJECT=<项目> 的 compose 容器 >
# 默认 compose 标签 service=postgres。清理后归还磁盘需停机窗口 VACUUM FULL（脚本会提示）。
set -euo pipefail
COMPOSE_PROJECT="${COMPOSE_PROJECT:-}"
K="${K:-5}"
CMD="${1:-report}"
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/yuxi-checkpoint-cleanup.XXXXXX")
trap 'rm -rf "$WORK_DIR"' EXIT

find_pg() {
  if [ -n "${PG_CONTAINER:-}" ]; then
    if docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
      echo "$PG_CONTAINER"
    else
      echo "PG_CONTAINER=$PG_CONTAINER 不在运行中的容器里" >&2
    fi
    return
  fi
  if [ -n "$COMPOSE_PROJECT" ]; then
    docker ps --format '{{.Names}}\t{{.Label "com.docker.compose.project"}}' \
      | awk -F"\t" -v p="$COMPOSE_PROJECT" '$2==p && $1 ~ /(-|^)postgres(-[0-9]+$|$)/ {print $1; exit}'
    return
  fi
  names=$(docker ps --format '{{.Names}}' --filter "label=com.docker.compose.service=postgres")
  n=$(printf '%s\n' "$names" | grep -c . || true)
  if [ "$n" -gt 1 ]; then
    echo "发现多个 postgres 容器，必须用 PG_CONTAINER 或 COMPOSE_PROJECT 显式指定：" >&2
    printf '%s\n' "$names" >&2
    exit 1
  fi
  printf '%s\n' "$names" | head -1
}
PGC=$(find_pg); [ -n "$PGC" ] || { echo "未找到 postgres 容器：设置 COMPOSE_PROJECT=<compose项目> 或 PG_CONTAINER=<容器名>"; exit 1; }
echo "使用 postgres 容器: $PGC"
U=$(docker exec "$PGC" sh -c 'echo ${POSTGRES_USER:-postgres}')
D=$(docker exec "$PGC" sh -c 'echo ${POSTGRES_DB:-yuxi}')
sql() { docker exec -i "$PGC" psql -U "$U" -d "$D" -At -c "$1"; }

preflight() {
  echo "== 阻塞预检：正在动 checkpoint 表的其它事务 =="
  local blockers
  blockers=$(sql "select pid||' | '||state||' | '||coalesce((now()-query_start)::text,'?')||' | '||left(query,80) from pg_stat_activity where datname='$D' and state<>'idle' and pid<>pg_backend_pid() and (query ilike '%checkpoint%' or query ilike '%cleanup_ck_keep%')")
  if [ -n "$blockers" ]; then printf '%s\n' "$blockers"; echo "  ↑ 若有上一次 retain 的回滚/删除在跑，本脚本会等它；大表回滚可能持续数十分钟"; else echo "  无"; fi
}

run_batch() { # $1=SQL；锁等待超时后自动重试，应对旧事务未退场
  local attempt
  for attempt in 1 2 3 4 5; do
    if sql "SET lock_timeout='120s'; $1" >/dev/null; then return 0; fi
    echo "  批次第 $attempt 次尝试被锁/超时挡住，15s 后重试…"
    sleep 15
  done
  echo "  批次连续 5 次失败，中止（可修锁源后重跑续传）"; return 1
}

case "$CMD" in
  report)
    sql "SELECT 'checkpoints: '||count(*)||' rows, '||pg_size_pretty(pg_total_relation_size('checkpoints')) FROM checkpoints
         UNION ALL SELECT 'blobs: '||count(*)||' rows, '||pg_size_pretty(pg_total_relation_size('checkpoint_blobs')) FROM checkpoint_blobs
         UNION ALL SELECT 'writes: '||count(*)||' rows, '||pg_size_pretty(pg_total_relation_size('checkpoint_writes')) FROM checkpoint_writes;"
    sql "WITH del AS (SELECT s.thread_id FROM sessions s WHERE s.status='deleted'
             AND NOT EXISTS (SELECT 1 FROM agent_runs r WHERE r.thread_id=s.thread_id
                             AND r.status IN ('pending','running','cancel_requested')))
         SELECT 'P1 可整删线程='||(SELECT count(*) FROM del)||'，其 checkpoints='||(SELECT count(*) FROM checkpoints k WHERE k.thread_id IN (SELECT thread_id FROM del))
         ||'，blobs='||(SELECT count(*) FROM checkpoint_blobs b WHERE b.thread_id IN (SELECT thread_id FROM del))
         ||'，writes='||(SELECT count(*) FROM checkpoint_writes w WHERE w.thread_id IN (SELECT thread_id FROM del));"
    sql "SELECT 'P2 超过 K=$K 的 (线程,命名空间) 组='||count(*)||'，超出 checkpoint 行数='||coalesce(sum(n-$K),0) FROM (
           SELECT thread_id, checkpoint_ns, count(*) n FROM checkpoints GROUP BY 1,2 HAVING count(*) > $K) t;"
    echo "（只读完成；purge-deleted / retain 需显式执行）"
    ;;
  purge-deleted)
    threads=$(sql "SELECT s.thread_id FROM sessions s WHERE s.status='deleted'
      AND NOT EXISTS (SELECT 1 FROM agent_runs r WHERE r.thread_id=s.thread_id
                      AND r.status IN ('pending','running','cancel_requested')) ORDER BY 1")
    [ -z "$threads" ] && { echo "P1 无目标线程"; exit 0; }
    # 分批 200 个线程/事务，可中断重跑
    echo "$threads" | split -l 200 - "$WORK_DIR/p1_"
    for f in "$WORK_DIR"/p1_*; do
      list=$(sed "s/^/'/; s/$/'/" "$f" | paste -sd,)
      run_batch "BEGIN;
        DELETE FROM checkpoint_writes WHERE thread_id IN ($list);
        DELETE FROM checkpoint_blobs  WHERE thread_id IN ($list);
        DELETE FROM checkpoints       WHERE thread_id IN ($list);
        COMMIT;"
      echo "P1 批次完成: $(wc -l < "$f") 线程"; rm -f "$f"
    done
    echo "P1 完成。空间归还需停机窗口: VACUUM FULL checkpoint_blobs; VACUUM FULL checkpoint_writes; VACUUM FULL checkpoints;"
    ;;
  retain)
    preflight
    echo "keep 集合构建中（扫描 checkpoints 全表做窗口排序，大库需数分钟，此间日志静默属正常）…"
    # keep 表持久化（psql 每次调用是独立会话，temp 表活不过批间），分批走 blobs/checkpoints 的
    # (thread_id,...) 主键前缀，单事务只锁 200 线程，Ctrl+C 可续跑；活跃 Run 线程整体保留。
    run_batch "DROP TABLE IF EXISTS cleanup_ck_keep;
      CREATE TABLE cleanup_ck_keep AS
        SELECT thread_id, checkpoint_ns, checkpoint_id FROM (
          SELECT thread_id, checkpoint_ns, checkpoint_id,
                 row_number() OVER (PARTITION BY thread_id, checkpoint_ns ORDER BY checkpoint_id DESC) rn
          FROM checkpoints) x
        WHERE rn <= $K
          OR thread_id IN (SELECT thread_id FROM agent_runs
                           WHERE status IN ('pending','running','cancel_requested'));
      CREATE UNIQUE INDEX ON cleanup_ck_keep(thread_id, checkpoint_ns, checkpoint_id);
      ANALYZE cleanup_ck_keep"
    echo "keep 集合就绪: $(sql 'select count(*) from cleanup_ck_keep') 个保留版本"
    threads=$(sql "SELECT DISTINCT thread_id FROM (SELECT thread_id, checkpoint_ns FROM checkpoints GROUP BY 1,2 HAVING count(*) > $K) t ORDER BY 1")
    if [ -z "$threads" ]; then echo "P2 无目标线程"; sql "DROP TABLE cleanup_ck_keep" >/dev/null; exit 0; fi
    total=$(printf '%s\n' "$threads" | wc -l); done_n=0
    echo "P2 目标线程: $total，开始分批删除（每批 200 线程独立事务）"
    printf '%s\n' "$threads" | split -l 200 - "$WORK_DIR/p2_"
    for f in "$WORK_DIR"/p2_*; do
      list=$(sed "s/^/'/; s/$/'/" "$f" | paste -sd,)
      run_batch "BEGIN;
        DELETE FROM checkpoint_writes w WHERE w.thread_id IN ($list)
          AND w.thread_id NOT IN (SELECT thread_id FROM agent_runs WHERE status IN ('pending','running','cancel_requested'))
          AND NOT EXISTS (SELECT 1 FROM cleanup_ck_keep k
                          WHERE k.thread_id=w.thread_id AND k.checkpoint_ns=w.checkpoint_ns AND k.checkpoint_id=w.checkpoint_id);
        DELETE FROM checkpoint_blobs b WHERE b.thread_id IN ($list)
          AND b.thread_id NOT IN (SELECT thread_id FROM agent_runs WHERE status IN ('pending','running','cancel_requested'))
          AND NOT EXISTS (SELECT 1 FROM cleanup_ck_keep k
                          JOIN checkpoints c ON c.thread_id=k.thread_id AND c.checkpoint_ns=k.checkpoint_ns AND c.checkpoint_id=k.checkpoint_id
                          WHERE k.thread_id=b.thread_id AND k.checkpoint_ns=b.checkpoint_ns
                            AND (c.checkpoint->'channel_versions' ->> b.channel) = b.version);
        DELETE FROM checkpoints c WHERE c.thread_id IN ($list)
          AND c.thread_id NOT IN (SELECT thread_id FROM agent_runs WHERE status IN ('pending','running','cancel_requested'))
          AND NOT EXISTS (SELECT 1 FROM cleanup_ck_keep k
                          WHERE k.thread_id=c.thread_id AND k.checkpoint_ns=c.checkpoint_ns AND k.checkpoint_id=c.checkpoint_id);
        COMMIT;"
      n=$(wc -l < "$f"); done_n=$((done_n + n)); rm -f "$f"
      echo "P2 进度: $done_n/$total 线程"
    done
    sql "DROP TABLE IF EXISTS cleanup_ck_keep" >/dev/null
    echo "P2 完成。空间归还需停机窗口: VACUUM FULL checkpoint_blobs; VACUUM FULL checkpoint_writes; VACUUM FULL checkpoints;"
    ;;
  vacuum-full)
    # 停机窗口专用：VACUUM 不能进多语句隐式事务，每表独立 psql 会话自动提交。
    # 需先停掉 api/worker（checkpoint 写入方），否则等 AccessExclusive 锁；与 retain 不可并行。
    for t in checkpoint_blobs checkpoint_writes checkpoints; do
      echo "VACUUM FULL $t ..."
      sql "VACUUM (FULL) $t" >/dev/null
    done
    echo "VACUUM FULL 完成，空间已归还 OS"
    ;;
  *) echo "用法: report | purge-deleted | retain | vacuum-full  (env: COMPOSE_PROJECT PG_CONTAINER K)"; exit 1 ;;
esac
