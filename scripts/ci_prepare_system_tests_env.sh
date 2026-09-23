#!/usr/bin/env bash
# 为 Runtime System Tests 的两个 job 准备隔离的 Compose 环境：
# 从模板生成 .env，注入 CI 密钥，追加 Langfuse 回放端点。
# 密钥值由 job 级 env 提供；调用方可在本脚本之后向 .env 追加自己的 CI-only 变量。
set -euo pipefail

cp .env.template .env
sed -i "s/^JWT_SECRET_KEY=.*/JWT_SECRET_KEY=${JWT_SECRET_KEY}/" .env
sed -i "s/^API_KEY_DERIVATION_SECRET=.*/API_KEY_DERIVATION_SECRET=${API_KEY_DERIVATION_SECRET}/" .env
sed -i "s/^SANDBOX_PROVISIONER_TOKEN=.*/SANDBOX_PROVISIONER_TOKEN=${SANDBOX_PROVISIONER_TOKEN}/" .env
cat >> .env <<'EOF'
LANGFUSE_PUBLIC_KEY=ci-langfuse-public-key
LANGFUSE_SECRET_KEY=ci-langfuse-secret-key
LANGFUSE_BASE_URL=http://api:8765
EOF
grep -E "^(JWT_SECRET_KEY|API_KEY_DERIVATION_SECRET|SANDBOX_PROVISIONER_TOKEN|LANGFUSE_PUBLIC_KEY|LANGFUSE_SECRET_KEY|LANGFUSE_BASE_URL)=.+" .env
