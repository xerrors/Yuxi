#!/usr/bin/env bash
# 临时托管执行器使用 container 沙盒与独立状态目录；不输出密钥。
set -euo pipefail
if [[ ${GITHUB_ACTIONS:-} != true || ${RUNNER_ENVIRONMENT:-} != github-hosted ]]; then
    echo '环境准备仅允许在 GitHub 临时托管执行器运行' >&2
    exit 1
fi
: "${RUNNER_TEMP:?需要临时托管执行器的 RUNNER_TEMP}"
python3 - <<'PY'
import base64
import os
import secrets
from pathlib import Path

keys = ("JWT_SECRET_KEY", "API_KEY_DERIVATION_SECRET", "SILICONFLOW_API_KEY", "OPENAI_REPLAY_URL", "COOPERATION_REPLAY_URL")
values = {key: os.environ[key] for key in keys if os.environ.get(key)}
state = Path(os.environ["RUNNER_TEMP"]) / "yuxi-state"
values.update(
    YUXI_INSTANCE_ID="ci-system-tests",
    YUXI_STATE_DIR=str(state),
    COMPOSE_PROFILES="container",
    SANDBOX_BACKEND="container",
    SANDBOX_RUNNER_URL="http://sandbox-runner:8090",
    SANDBOX_RUNNER_TOKEN=secrets.token_urlsafe(32),
    YUXI_ENCRYPTION_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    CUBE_API_URL="",
    CUBE_API_KEY="",
    CUBE_PROXY_NODE_IP="",
)
lines = [line for line in Path(".env.template").read_text().splitlines() if line.split("=", 1)[0] not in values]
lines.extend(f"{key}={value}" for key, value in values.items())
path = Path(".env")
path.write_text("\n".join(lines) + "\n")
path.chmod(0o600)
PY
