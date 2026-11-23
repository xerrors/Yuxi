#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# PostgreSQL "idle in transaction" 归因诊断（只读）
# =============================================================================
# 目的：连接池耗尽（QueuePool limit reached）时采集 PostgreSQL 现场证据，辅助归因。
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
#   未指定项目时只接受一个候选 Compose 项目；多栈请用 -p 或 COMPOSE_PROJECT。
#   SQL 采集仅支持内置 PostgreSQL 的标准 Compose 连接地址。外部库、不同端口/库名、
#   自定义连接参数或无法核验 API/worker 配置时拒绝采样。
#
# 输出判读:
#   - 用 client_addr 与应用请求/作业日志核对归属；仅凭一次快照、末条 SQL 或 lease
#     过期数不能确认根因。idleInTxn≈0 也不能证明数据库容量不足。
#   - 报错时连续采样，结合 API/worker 本地连接池占用、活跃 SQL 和数据库资源判断。
#   - 明细含末条 SQL 摘要，分享前请脱敏。
#
# 只执行只读查询（pg_stat_activity / count / 时间差），不改任何业务状态。
# 兼容 PostgreSQL 14-16：pg_stat_activity 取 query 列。
# =============================================================================

PG_SERVICE="${PG_SERVICE:-postgres}"
PROJECT="${COMPOSE_PROJECT:-}"
TOPN=10
WATCH_SECONDS=""
WHOIS=""

while [ $# -gt 0 ]; do
  case "$1" in
    -p|--project)      PROJECT="${2:?--project 需要项目名}"; shift 2 ;;
    -w|--watch)        WATCH_SECONDS="${2:?--watch 需要秒数}"; [[ "$WATCH_SECONDS" =~ ^[0-9]+$ && "$WATCH_SECONDS" =~ [1-9] ]] || { echo "--watch 需要正整数秒" >&2; exit 2; }; shift 2 ;;
    --top)             TOPN="${2:?--top 需要条数}"; [[ "$TOPN" =~ ^[0-9]+$ && "$TOPN" =~ [1-9] ]] || { echo "--top 需要正整数条数" >&2; exit 2; }; shift 2 ;;
    --whois)           WHOIS="${2:?--whois 需要 IP}"; shift 2 ;;
    -h|--help)         awk 'NR>=4 && /^#/{print} NR>4 && !/^#/{exit}' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)                 echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

command -v docker >/dev/null || { echo "需要 docker 命令（本脚本面向 compose 部署机）" >&2; exit 1; }

select_project() {
  local rows cid project service
  local -a filters=(--filter 'label=com.docker.compose.project')
  if [ -n "$PROJECT" ]; then filters+=(--filter "label=com.docker.compose.project=$PROJECT"); fi
  if ! rows="$(docker ps "${filters[@]}" --format '{{.ID}} {{.Label "com.docker.compose.project"}} {{.Label "com.docker.compose.service"}}')"; then
    echo "无法读取运行中的 Compose 容器" >&2
    return 1
  fi

  selected_project=""
  postgres_cids=()
  app_cids=()
  while read -r cid project service; do
    case "$service" in
      "$PG_SERVICE"|api|worker) ;;
      *) continue ;;
    esac
    if [ -n "$PROJECT" ] && [ "$project" != "$PROJECT" ]; then continue; fi
    if [ -n "$selected_project" ] && [ "$selected_project" != "$project" ]; then
      echo "检测到多个候选 Compose 项目，请用 -p 或 COMPOSE_PROJECT 指定目标" >&2
      return 1
    fi
    selected_project="$project"
    if [ "$service" = "$PG_SERVICE" ]; then postgres_cids+=("$cid"); else app_cids+=("$cid"); fi
  done <<< "$rows"
  if [ -z "$selected_project" ]; then
    echo "未找到候选 Compose 项目${PROJECT:+（project=$PROJECT）}，请检查 -p 和运行中的容器" >&2
    return 1
  fi
}

check_app_target() {
  # 在应用容器内检查标准地址和实际解析结果；配置与密码不出容器。
  docker exec "$1" python3 -c '
import ipaddress
import os
import socket
import sys

service, user, db, *container_ips = sys.argv[1:]
credentials, _, address = os.environ.get("POSTGRES_URL", "").partition("@")
if not credentials.startswith(f"postgresql+asyncpg://{user}:") or address != f"{service}:5432/{db}":
    raise SystemExit(1)

resolved = {ipaddress.ip_address(item[4][0])
            for item in socket.getaddrinfo(service, 5432, type=socket.SOCK_STREAM)}
expected = {ipaddress.ip_address(value) for value in container_ips}
raise SystemExit(0 if resolved and resolved <= expected else 1)
  ' "$PG_SERVICE" "$pg_user" "$pg_db" "${pg_ips[@]}" >/dev/null 2>&1
}

query_pg() {
  docker exec "$pg_cid" env -u PGHOSTADDR -u PGSERVICE -u PGSERVICEFILE \
    psql -h /var/run/postgresql -p 5432 -U "$pg_user" -d "$pg_db" -P pager=off --no-psqlrc "$@"
}

whois() { # 把 pg_stat_activity 里的 client_addr 映射回容器名（api/worker 归属判定）
  local rows addresses
  local -a cids
  rows="$(docker ps -q --filter "label=com.docker.compose.project=$selected_project")" || return 1
  [ -z "$rows" ] && { echo "没有可用容器，检查 -p" >&2; return 1; }
  mapfile -t cids <<< "$rows"
  addresses="$(docker inspect --format '{{$name := .Name}}{{range .NetworkSettings.Networks}}{{.IPAddress}} {{$name}}{{"\n"}}{{.GlobalIPv6Address}} {{$name}}{{"\n"}}{{end}}' "${cids[@]}")" || return 1
  awk -v address="$1" '$1 == address { print; found=1 } END { exit !found }' <<< "$addresses" ||
    echo "未命中：$1 不在本项目容器中（可能已销毁或跨栈）"
}

diagnose() {
  local pg_cid pg_user pg_db addresses app_cid row idle_cnt idle_max total pg_max
  local -a pg_ips
  select_project || return 1
  if [ "${#postgres_cids[@]}" -ne 1 ] || [ "${#app_cids[@]}" -eq 0 ]; then
    echo "项目 $selected_project 需恰有一个 $PG_SERVICE 容器及至少一个运行中的 API/worker；未采集" >&2
    return 1
  fi
  pg_cid="${postgres_cids[0]}"
  if ! addresses="$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{.GlobalIPv6Address}} {{end}}' "$pg_cid" 2>/dev/null)" ||
     ! read -r -a pg_ips <<< "$addresses" || [ "${#pg_ips[@]}" -eq 0 ]; then
    echo "无法核验项目 $selected_project 的内置 PostgreSQL 网络；未采集" >&2
    return 1
  fi
  if ! pg_user="$(docker exec "$pg_cid" printenv POSTGRES_USER 2>/dev/null)" ||
     ! pg_db="$(docker exec "$pg_cid" printenv POSTGRES_DB 2>/dev/null)" ||
     ! [[ "$pg_user" =~ ^[A-Za-z0-9_.-]+$ && "$pg_db" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "无法核验项目 $selected_project 的内置 PostgreSQL 用户及库名；未采集" >&2
    return 1
  fi
  for app_cid in "${app_cids[@]}"; do
    if ! check_app_target "$app_cid"; then
      echo "项目 $selected_project 的 API/worker 未使用可核验的内置 PostgreSQL；可能为外部库、自定义地址或配置不可读，未采集" >&2
      return 1
    fi
  done

  if ! row="$(query_pg -At -F '|' -c "
    SELECT
      count(*),
      coalesce(max(greatest(0, extract(epoch from now()-xact_start)))::bigint, 0),
      (SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend'),
      current_setting('max_connections')::int
    FROM pg_stat_activity WHERE state='idle in transaction'
    HAVING current_setting('is_superuser')='on' OR pg_has_role(current_user, 'pg_read_all_stats', 'USAGE');" 2>/dev/null)"; then
    echo "[pg_stat_activity 查询失败：容器不可达或库不可读——不要据此判健康]" >&2
    return 1
  fi
  if [ -z "$row" ]; then
    echo "数据库用户无法读取全部 pg_stat_activity 明细（需超级用户或 pg_read_all_stats）；未采集" >&2
    return 1
  fi
  IFS='|' read -r idle_cnt idle_max total pg_max <<<"$(echo "$row" | tr -d ' \t')"
  : "${idle_cnt:=0}" "${idle_max:=0}" "${total:=0}" "${pg_max:=0}"

  printf '[%s] project=%s idleInTxn=%s 空闲会话最长事务龄=%ss | 集群客户端连接=%s/%s\n' \
    "$(date '+%H:%M:%S')" "$selected_project" "$idle_cnt" "$idle_max" "$total" "$pg_max"

  echo "--- idle in transaction 按事务龄分桶（每桶 5 分钟）---"
  query_pg -c "
    WITH i AS (
      SELECT width_bucket(greatest(0, extract(epoch from now()-xact_start)), 0, 1800, 6) AS b
      FROM pg_stat_activity WHERE state='idle in transaction')
    SELECT CASE WHEN b>=7 THEN '>=1800s' ELSE ((b-1)*300)||'-'||(b*300)||'s' END AS txn_age_range, count(*)
    FROM i GROUP BY b ORDER BY b;"

  echo "--- idle in transaction 明细 top ${TOPN}（按事务龄；含末条 SQL 摘要，分享前脱敏）---"
  query_pg -c "
    SELECT pid,
           coalesce(nullif(application_name,''),'(无)') AS backend,
           host(client_addr) AS client_addr,
           round(greatest(0, extract(epoch from now()-xact_start)))::int AS txn_age_s,
           round(greatest(0, extract(epoch from now()-state_change)))::int AS idle_s,
           left(regexp_replace(coalesce(query,''), '\s+', ' ', 'g'), 70) AS query
    FROM pg_stat_activity
    WHERE state='idle in transaction' AND pid <> pg_backend_pid()
    ORDER BY xact_start ASC LIMIT ${TOPN};"

  echo "--- 应用库对照：running Run 数与 lease 过期数（不作归因结论）---"
  query_pg -c "
    SELECT 'running='||count(*) FILTER (WHERE status='running')||
           ' leaseLost='||count(*) FILTER (WHERE status='running' AND lease_expires_at < now())||
           ' maxRunAge='||coalesce(max(CASE WHEN status='running' THEN greatest(0,extract(epoch from now()-started_at))::int END),0)||'s'
    FROM agent_runs;"

  echo "--- 单次快照不能确认根因，idleInTxn 接近 0 也不能证明数据库容量不足。---"
  echo "    在报错时连续采样，并与应用日志、连接池占用、活跃 SQL 和数据库资源对照。"
}

if [ -n "$WHOIS" ]; then
  select_project
  whois "$WHOIS"
elif [ -z "$WATCH_SECONDS" ]; then
  diagnose
else
  while true; do clear 2>/dev/null || printf '\n'; diagnose; sleep "$WATCH_SECONDS"; done
fi
