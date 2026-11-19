#!/usr/bin/env bash
# LangGraph checkpoint 保留清理（治理 checkpoints/checkpoint_blobs/checkpoint_writes 无界增长）。
# 规则（K 默认 5：Yuxi 的审批、resume 与面板只读每个 (thread_id, checkpoint_ns) 的最新版本）：
#   每个 (thread_id, checkpoint_ns) 保留最近 K 个 checkpoint 及其引用的 blob 版本与 writes；
#   存在 pending/running/cancel_requested Run 的线程整体跳过。
# 仅碰 LangGraph 执行内部状态：PG 业务事实（messages/agent_runs）不受影响。
#
# 清理期间必须停止 checkpoint 写入：先停 api 与 worker，再执行 retain。脚本每次删除都现场
# 重算保留集（与 Run 终态在线裁剪同一形态），因此不会按旧快照删掉清理期间新写入的版本；
# 但保留集与业务 Run 的收敛仍需静止状态才能核对，开始前也会硬检查非终态 Run 并直接中止。
#
# 用法（宿主机 root；postgres 容器按 compose 标签自动识别）:
#   bash checkpoint-cleanup.sh report            # 只读：体量、可删行数与非终态 Run 数
#   K=5 bash checkpoint-cleanup.sh retain        # 执行保留裁剪（分批，可中断重跑）
#   bash checkpoint-cleanup.sh vacuum-full       # 停机窗口内归还磁盘
# 容器定位优先级: PG_CONTAINER=<容器名> > COMPOSE_PROJECT=<项目> 的 compose 容器 >
# 默认 compose 标签 service=postgres；库名默认取容器 POSTGRES_DB，预演克隆库用 DB=<库名> 指定。
set -euo pipefail
COMPOSE_PROJECT="${COMPOSE_PROJECT:-}"
DB="${DB:-}"  # 缺省取 postgres 容器的 POSTGRES_DB；预演时指向克隆库
K="${K:-5}"
CMD="${1:-report}"
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/yuxi-checkpoint-cleanup.XXXXXX")
trap 'rm -rf "$WORK_DIR"' EXIT

case "$K" in
  '' | *[!0-9]*) echo "K 必须是大于 0 的整数（收到 K=$K）：0 或负数会清空非活跃线程的全部历史" >&2; exit 1 ;;
esac
[ "$K" -ge 1 ] || { echo "K 必须 >= 1（收到 K=$K）" >&2; exit 1; }

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
PGC=$(find_pg)
[ -n "$PGC" ] || { echo "未找到 postgres 容器：设置 COMPOSE_PROJECT=<compose项目> 或 PG_CONTAINER=<容器名>"; exit 1; }
echo "使用 postgres 容器: $PGC"
U=$(docker exec "$PGC" sh -c 'echo ${POSTGRES_USER:-postgres}')
D="${DB:-$(docker exec "$PGC" sh -c 'echo ${POSTGRES_DB:-yuxi}')}"
sql() { docker exec -i "$PGC" psql -U "$U" -d "$D" -At -c "$1"; }

nonterminal_runs() {
  sql "SELECT count(*) FROM agent_runs WHERE status IN ('pending','running','cancel_requested')"
}

preflight() {
  echo "== 阻塞预检：正在动 checkpoint 表的其它事务 =="
  local blockers
  blockers=$(sql "select pid||' | '||state||' | '||coalesce((now()-query_start)::text,'?')||' | '||left(query,80) from pg_stat_activity where datname='$D' and state<>'idle' and pid<>pg_backend_pid() and query ilike '%checkpoint%'")
  if [ -n "$blockers" ]; then printf '%s\n' "$blockers"; echo "  ↑ 若有上一次 retain 的回滚/删除在跑，本脚本会等它；大表回滚可能持续数十分钟"; else echo "  无"; fi

  echo "== 写入静止预检：非终态 Run 必须为 0 =="
  local active
  active=$(nonterminal_runs)
  if [ "$active" != "0" ]; then
    echo "仍有 $active 个 pending/running/cancel_requested Run，checkpoint 还在被写入。" >&2
    echo "请先停掉 api 与 worker（或等它们全部收敛）再执行 retain。" >&2
    echo "若 api/worker 已停而计数仍不为 0，这些是崩溃/被杀 worker 留下的在途行：等 lease reconcile" >&2
    echo "（worker 启动或周期收敛）把它们结算成终态后重跑，不要手工改 status。" >&2
    exit 1
  fi
  echo "  0，可以清理"
}

run_batch() { # $1=SQL；锁等待超时后自动重试，应对旧事务未退场
  local attempt
  for attempt in 1 2 3 4 5; do
    if sql "BEGIN; SET LOCAL lock_timeout='120s'; $1; COMMIT;" >/dev/null; then return 0; fi
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
    sql "SELECT '非终态 Run（retain 前必须为 0）='||(SELECT count(*) FROM agent_runs WHERE status IN ('pending','running','cancel_requested'))
         ||'，超过 K=$K 的 (线程,命名空间) 组='||(SELECT count(*) FROM (SELECT thread_id, checkpoint_ns FROM checkpoints GROUP BY 1,2 HAVING count(*) > $K) t)
         ||'，超出 checkpoint 行数='||coalesce((SELECT sum(n-$K) FROM (SELECT thread_id, checkpoint_ns, count(*) n FROM checkpoints GROUP BY 1,2 HAVING count(*) > $K) t),0);"
    echo "（只读完成；retain 需先停 api/worker 再显式执行）"
    ;;
  retain)
    preflight
    # 分批 200 线程/事务，可中断重跑；每条 DELETE 现场重算本批线程的保留集，
    # 与 Run 终态在线裁剪（modules/agents/services/checkpoint_retention.py）同形：
    # 清理期间新写入的 checkpoint 只会落入"最新版必然入选"的保留集，不存在按旧快照误删。
    threads=$(sql "SELECT DISTINCT t.thread_id FROM (SELECT thread_id, checkpoint_ns FROM checkpoints GROUP BY 1,2 HAVING count(*) > $K) t
      WHERE NOT EXISTS (SELECT 1 FROM agent_runs r WHERE r.thread_id = t.thread_id
                        AND r.status IN ('pending','running','cancel_requested')) ORDER BY 1")
    if [ -z "$threads" ]; then echo "无目标线程"; exit 0; fi
    total=$(printf '%s\n' "$threads" | wc -l)
    printf '%s\n' "$threads" | split -l 200 - "$WORK_DIR/retain_"
    echo "目标线程: $total，开始分批裁剪（每批 200 线程独立事务；此间日志静默属正常）"
    done_n=0
    for f in "$WORK_DIR"/retain_*; do
      list=$(sed "s/^/'/; s/$/'/" "$f" | paste -sd,)
      run_batch "
        WITH per_ns AS (
          SELECT thread_id, checkpoint_ns, checkpoint_id,
                 row_number() OVER (PARTITION BY thread_id, checkpoint_ns ORDER BY checkpoint_id DESC) rn
          FROM checkpoints WHERE thread_id IN ($list)),
        keep AS (SELECT thread_id, checkpoint_ns, checkpoint_id FROM per_ns WHERE rn <= $K)
        DELETE FROM checkpoint_writes w
        WHERE w.thread_id IN ($list)
          AND NOT EXISTS (SELECT 1 FROM keep k
                          WHERE k.thread_id = w.thread_id AND k.checkpoint_ns = w.checkpoint_ns
                            AND k.checkpoint_id = w.checkpoint_id);
        WITH per_ns AS (
          SELECT thread_id, checkpoint_ns, checkpoint_id,
                 row_number() OVER (PARTITION BY thread_id, checkpoint_ns ORDER BY checkpoint_id DESC) rn
          FROM checkpoints WHERE thread_id IN ($list)),
        keep AS (SELECT thread_id, checkpoint_ns, checkpoint_id FROM per_ns WHERE rn <= $K)
        DELETE FROM checkpoint_blobs b
        WHERE b.thread_id IN ($list)
          AND NOT EXISTS (SELECT 1 FROM keep k
                          JOIN checkpoints c
                            ON c.thread_id = k.thread_id AND c.checkpoint_ns = k.checkpoint_ns
                           AND c.checkpoint_id = k.checkpoint_id
                          WHERE k.thread_id = b.thread_id AND k.checkpoint_ns = b.checkpoint_ns
                            AND (c.checkpoint -> 'channel_versions' ->> b.channel) = b.version);
        WITH per_ns AS (
          SELECT thread_id, checkpoint_ns, checkpoint_id,
                 row_number() OVER (PARTITION BY thread_id, checkpoint_ns ORDER BY checkpoint_id DESC) rn
          FROM checkpoints WHERE thread_id IN ($list)),
        keep AS (SELECT thread_id, checkpoint_ns, checkpoint_id FROM per_ns WHERE rn <= $K)
        DELETE FROM checkpoints c
        WHERE c.thread_id IN ($list)
          AND NOT EXISTS (SELECT 1 FROM keep k
                          WHERE k.thread_id = c.thread_id AND k.checkpoint_ns = c.checkpoint_ns
                            AND k.checkpoint_id = c.checkpoint_id);"
      n=$(wc -l < "$f")
      done_n=$((done_n + n))
      rm -f "$f"
      echo "进度: $done_n/$total 线程"
    done
    echo "retain 完成。空间归还需停机窗口: VACUUM FULL checkpoint_blobs; VACUUM FULL checkpoint_writes; VACUUM FULL checkpoints;"
    ;;
  vacuum-full)
    # 停机窗口专用：VACUUM 不能进多语句隐式事务，每表独立 psql 会话自动提交。
    # 需先停掉 api/worker（checkpoint 写入方），否则等 AccessExclusive 锁；与 retain 不可并行。
    # 必须是 FULL：只有 FULL 归还磁盘给 OS，非 FULL 的 VACUUM 只整理页内空闲空间（子命令名承诺归还磁盘）。
    for t in checkpoint_blobs checkpoint_writes checkpoints; do
      echo "VACUUM FULL $t ..."
      sql "VACUUM (FULL) $t" >/dev/null
    done
    echo "VACUUM FULL 完成，空间已归还 OS"
    ;;
  *) echo "用法: report | retain | vacuum-full  (env: COMPOSE_PROJECT PG_CONTAINER DB K)" >&2; exit 1 ;;
esac
