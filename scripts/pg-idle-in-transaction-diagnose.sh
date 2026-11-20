#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# PostgreSQL "idle in transaction" 归因诊断（只读）
# =============================================================================
# 目的：判断连接池耗尽（QueuePool limit reached）是不是"长请求/ SSE 把鉴权事务
#       钉成 idle in transaction"造成的，而不是在线数或图谱写入。
#
# 用法:
#   ./pg-idle-in-transaction-diagnose.sh                    # 单次采集（单栈机器直接跑）
#   ./pg-idle-in-transaction-diagnose.sh -p runtime         # 多栈机器指定 compose 项目
#   ./pg-idle-in-transaction-diagnose.sh -w 5               # 每 5 秒刷新，盯 QueuePool 复发时刻
#   ./pg-idle-in-transaction-diagnose.sh --top 20           # 明细列出最久 20 条（默认 10）
#   ./pg-idle-in-transaction-diagnose.sh -h                 # 打印本帮助
#   ./pg-idle-in-transaction-diagnose.sh --whois 172.22.0.6 # 把明细里的 client_addr 映射回容器名
#   参数可组合: ./pg-idle-in-transaction-diagnose.sh -p runtime --top 20 -w 5（-w/--top 必须紧跟数值）
#
# 部署机步骤（compose 项目名为 <project>）:
#   1. 在仓库根直接跑（-p 用你的 compose 项目名）:
#        ./scripts/pg-idle-in-transaction-diagnose.sh -p <project> --top 20
#   2. QueuePool 报错正在发生时用 watch 连续采样:
#        ./scripts/pg-idle-in-transaction-diagnose.sh -p <project> -w 5
#   无需手填库名/账号：脚本按 com.docker.compose.service=postgres 标签找容器，
#   POSTGRES_USER/POSTGRES_DB 直接从容器环境变量读取；不给 -p 时用容器 project 标签兜底。
#
# 输出判读:
#   - idleInTxn>0、明细 client_addr 集中在 api 容器、query 为 SELECT users...
#       → 长请求/SSE 把鉴权事务钉成 idle in transaction（在线数多≠并发流多，别按 presence 归因）
#   - 明细 client_addr 在 worker 容器 → worker 侧构图会话跨外部 await 持有连接
#   - 长桶(>300s、>600s)持续有量且 leaseLost>0 → 僵尸 Run 把客户端流拖满 SSE 上限(30min)
#   - idleInTxn≈0 但仍在报 QueuePool timeout → 瓶颈在 PG 本身（并行查询/shm），不是连接被钉
#
# 只执行只读查询（pg_stat_activity / count / 时间差），不改任何业务状态。
# 兼容 PostgreSQL 14-16：pg_stat_activity 取 query 列（17 才新增 last_query，勿混用）。
# =============================================================================

command -v docker >/dev/null || { echo "需要 docker 命令（本脚本面向 compose 部署机）" >&2; exit 1; }

PG_SERVICE="${PG_SERVICE:-postgres}"
PROJECT="${COMPOSE_PROJECT:-}"
TOPN=10
WATCH_SECONDS=""
WHOIS=""

while [ $# -gt 0 ]; do
  case "$1" in
    -p|--project)      PROJECT="${2:?--project 需要项目名}"; shift 2 ;;
    -w|--watch)        WATCH_SECONDS="${2:?--watch 需要秒数}"; case "$WATCH_SECONDS" in ''|*[!0-9]*) echo "--watch 需要正整数秒" >&2; exit 2 ;; esac; shift 2 ;;
    --top)             TOPN="${2:?--top 需要条数}"; case "$TOPN" in ''|*[!0-9]*) echo "--top 需要正整数条数" >&2; exit 2 ;; esac; shift 2 ;;
    --whois)           WHOIS="${2:?--whois 需要 IP}"; shift 2 ;;
    -h|--help)         awk 'NR>=4 && /^#/{print} NR>4 && !/^#/{exit}' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)                 echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

DOCKER_FILTERS=()
[ -n "$PROJECT" ] && DOCKER_FILTERS+=(--filter "label=com.docker.compose.project=$PROJECT")

find_cid() { docker ps -q --filter "label=com.docker.compose.service=$1" "${DOCKER_FILTERS[@]}" 2>/dev/null | head -1; }
env_of() { local cid; cid="$(find_cid "$1")"; [ -z "$cid" ] && return 0; docker exec "$cid" printenv "$2" 2>/dev/null | tr -d '\r' | head -1; }
psql_at() { local cid; cid="$(find_cid "$PG_SERVICE")"; [ -z "$cid" ] && return 0; docker exec "$cid" psql -U "$1" -d "$2" -At -F '|' -P pager=off --no-psqlrc -c "$3" 2>/dev/null || true; }
psql_row() { local cid; cid="$(find_cid "$PG_SERVICE")"; [ -z "$cid" ] && return 1; docker exec "$cid" psql -U "$1" -d "$2" -At -F '|' -P pager=off --no-psqlrc -c "$3"; }

psql_fmt() { local cid; cid="$(find_cid "$PG_SERVICE")"; [ -z "$cid" ] && return 0; docker exec "$cid" psql -U "$1" -d "$2" -P pager=off --no-psqlrc -c "$3" || echo "[psql 查询失败，见上方 stderr]"; }

# 未给 -p 时用 postgres 容器自身 project 标签兜底，保证多栈自洽。
if [ -z "$PROJECT" ]; then
  first_cid="$(find_cid "$PG_SERVICE")"
  if [ -n "$first_cid" ]; then
    PROJECT="$(docker inspect "$first_cid" --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null || true)"
    DOCKER_FILTERS=(); [ -n "$PROJECT" ] && DOCKER_FILTERS+=(--filter "label=com.docker.compose.project=$PROJECT")
  fi
fi

whois() { # 把 pg_stat_activity 里的 client_addr 映射回容器名（api/worker 归属判定）
  local nets cids
  cids="$(docker ps -q "${DOCKER_FILTERS[@]}" 2>/dev/null || true)"
  [ -z "$cids" ] && { echo "没有可用容器，检查 -p" >&2; return 1; }
  nets="$(docker inspect $cids --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' | sort -u)"
  [ -z "$nets" ] && { echo "未找到网络" >&2; return 1; }
  docker network inspect $nets --format '{{range .Containers}}{{.IPv4Address}} {{.Name}}{{"\n"}}{{end}}' | grep "^$1" || echo "未命中：$1 不在本项目网络（可能已销毁或跨栈）"
}

diagnose() {
  local pg_user pg_db row idle_cnt idle_max total pg_max
  pg_user="$(env_of "$PG_SERVICE" POSTGRES_USER)"; : "${pg_user:=postgres}"
  pg_db="$(env_of "$PG_SERVICE" POSTGRES_DB)"; : "${pg_db:=yuxi}"

  if [ -z "$(find_cid "$PG_SERVICE")" ]; then echo "找不到 $PG_SERVICE 容器${PROJECT:+（project=$PROJECT）}，检查 -p / 容器是否在跑" >&2; return 1; fi

  if ! row="$(psql_row "$pg_user" "$pg_db" "
    SELECT
      count(*) FILTER (WHERE state='idle in transaction'),
      coalesce(max(greatest(0, extract(epoch from now()-xact_start)))::bigint, 0),
      (SELECT count(*) FROM pg_stat_activity),
      current_setting('max_connections')::int
    FROM pg_stat_activity WHERE state <> 'idle';" 2>/dev/null)"; then
    echo "[pg_stat_activity 查询失败：容器不可达或库不可读——不要据此判健康]" >&2
    return 1
fi
  IFS='|' read -r idle_cnt idle_max total pg_max <<<"$(echo "$row" | tr -d ' \t')"
  : "${idle_cnt:=0}" "${idle_max:=0}" "${total:=0}" "${pg_max:=0}"

  printf '[%s] project=%s idleInTxn=%s 最长事务=%ss | 总连接=%s/%s\n' \
    "$(date '+%H:%M:%S')" "${PROJECT:-(unknown)}" "$idle_cnt" "$idle_max" "$total" "$pg_max"

  echo "--- idle in transaction 按事务龄分桶（每桶 5 分钟；长桶持续>0 且随卡住 Run 增长=长流钉鉴权会话）---"
  psql_fmt "$pg_user" "$pg_db" "
    WITH i AS (
      SELECT width_bucket(greatest(0, extract(epoch from now()-xact_start)), 0, 1800, 6) AS b
      FROM pg_stat_activity WHERE state='idle in transaction')
    SELECT CASE WHEN b>=7 THEN '>=1800s' ELSE ((b-1)*300)||'-'||(b*300)||'s' END AS txn_age_range, count(*)
    FROM i GROUP BY b ORDER BY b;"

  echo "--- idle in transaction 明细 top ${TOPN}（按事务龄；看 backend/addr/当前查询）---"
  psql_fmt "$pg_user" "$pg_db" "
    SELECT pid,
           coalesce(nullif(application_name,''),'(无)') AS backend,
           host(client_addr) AS client_addr,
           round(greatest(0, extract(epoch from now()-xact_start)))::int AS txn_age_s,
           round(greatest(0, extract(epoch from now()-state_change)))::int AS idle_s,
           left(regexp_replace(coalesce(query,''), '\s+', ' ', 'g'), 70) AS query
    FROM pg_stat_activity
    WHERE state='idle in transaction' AND pid <> pg_backend_pid()
    ORDER BY xact_start ASC LIMIT ${TOPN};"

  echo "--- 对照：running 非终态与 lease 过期（钉满 SSE 30min 的元凶）---"
  psql_fmt "$pg_user" "$pg_db" "
    SELECT 'running='||count(*) FILTER (WHERE status='running')||
           ' leaseLost='||count(*) FILTER (WHERE status='running' AND lease_expires_at < now())||
           ' maxRunAge='||coalesce(max(CASE WHEN status='running' THEN greatest(0,extract(epoch from now()-started_at))::int END),0)||'s'
    FROM agent_runs;"

  echo "--- 判读：client_addr 集中在 api 容器且 txn_age 数十秒~分钟 → 长流钉鉴权事务；"
  echo "          集中在 worker 容器 → worker 侧会话跨 await 持有；两者都低但 idle 少 → 别甩锅给在线数。---"
}

if [ -n "$WHOIS" ]; then
  whois "$WHOIS"
elif [ -z "$WATCH_SECONDS" ]; then
  diagnose
else
  while true; do clear 2>/dev/null || printf '\n'; diagnose; sleep "$WATCH_SECONDS"; done
fi
