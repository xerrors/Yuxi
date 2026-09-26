#!/bin/bash

# Yuxi Initialization Script for Bash/Linux/macOS
# This script helps set up the environment for the Yuxi project

set -e
umask 077

generate_hex() {
    local length="$1"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$length"
    else
        tr -dc 'a-f0-9' < /dev/urandom | head -c $((length * 2))
    fi
}

set_env_value() {
    local name="$1"
    local value="$2"

    if grep -Eq "^${name}=" .env; then
        ENV_VALUE="$value" awk -v name="$name" '
            $0 ~ "^" name "=" {
                if (!updated) {
                    print name "=" ENVIRON["ENV_VALUE"]
                    updated = 1
                }
                next
            }
            { print }
        ' .env > .env.tmp
        mv .env.tmp .env
    else
        printf '\n%s=%s\n' "$name" "$value" >> .env
    fi
}

get_env_value() {
    local name="$1"
    awk -v name="$name" '
        index($0, name "=") == 1 && !found {
            print substr($0, length(name) + 2)
            found = 1
        }
    ' .env
}

trim_whitespace() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

security_secret_is_valid() {
    local value="$1"
    shift
    local trimmed
    trimmed=$(trim_whitespace "$value")
    if [ "$value" != "$trimmed" ] || [ "${#value}" -lt 32 ]; then
        return 1
    fi
    case "$value" in
        \"*|*\"|\'*|*\') return 1 ;;
    esac
    local other
    for other in "$@"; do
        if [ -n "$other" ] && [ "$value" = "$other" ]; then
            return 1
        fi
    done
}

read_security_secret() {
    local name="$1"
    shift
    local value
    while true; do
        read -s -p "Please enter your ${name} (press Enter to auto-generate): " value
        echo ""
        if [ -z "$value" ]; then
            value=$(generate_hex 32)
            echo "Generated ${name} and saved it to .env."
        fi
        if security_secret_is_valid "$value" "$@"; then
            SECURITY_SECRET_VALUE="$value"
            return
        fi
        echo "❌ ${name} must contain at least 32 non-whitespace characters and must not reuse another security secret."
    done
}

ensure_security_secret() {
    local name="$1"
    shift
    local current
    current=$(get_env_value "$name")
    local other_values=()
    local other_name
    for other_name in "$@"; do
        other_values+=("$(get_env_value "$other_name")")
    done
    if security_secret_is_valid "$current" "${other_values[@]}"; then
        return
    fi

    echo "${name} is missing, too short, or reuses another security secret in .env."
    read_security_secret "$name" "${other_values[@]}"
    set_env_value "$name" "$SECURITY_SECRET_VALUE"
}

validate_security_env() {
    local jwt_secret api_key_secret sandbox_secret
    jwt_secret=$(get_env_value "JWT_SECRET_KEY")
    api_key_secret=$(get_env_value "API_KEY_DERIVATION_SECRET")
    sandbox_secret=$(get_env_value "SANDBOX_PROVISIONER_TOKEN")

    security_secret_is_valid "$jwt_secret" || {
        echo "JWT_SECRET_KEY must contain at least 32 non-whitespace characters." >&2
        return 1
    }
    security_secret_is_valid "$api_key_secret" "$jwt_secret" || {
        echo "API_KEY_DERIVATION_SECRET must be at least 32 characters and independent from JWT_SECRET_KEY." >&2
        return 1
    }
    security_secret_is_valid "$sandbox_secret" "$jwt_secret" "$api_key_secret" || {
        echo "SANDBOX_PROVISIONER_TOKEN must be at least 32 characters and independent from other security secrets." >&2
        return 1
    }
}

ensure_required_api_env() {
    if grep -Eq '^SILICONFLOW_API_KEY=.+' .env; then
        return
    fi

    echo "SILICONFLOW_API_KEY is missing in .env."
    while true; do
        read -s -p "Please enter your SILICONFLOW_API_KEY: " SILICONFLOW_API_KEY
        echo ""
        if [ -n "$SILICONFLOW_API_KEY" ]; then
            break
        fi
        echo "❌ API Key cannot be empty. Please try again."
    done
    set_env_value "SILICONFLOW_API_KEY" "$SILICONFLOW_API_KEY"
}

ensure_jwt_env() {
    ensure_security_secret "JWT_SECRET_KEY"
    ensure_security_secret "API_KEY_DERIVATION_SECRET" "JWT_SECRET_KEY"

    if ! grep -Eq '^YUXI_INSTANCE_ID=.+' .env; then
        echo "YUXI_INSTANCE_ID is missing in .env."
        read -p "Please enter your YUXI_INSTANCE_ID (press Enter to auto-generate): " YUXI_INSTANCE_ID
        if [ -z "$YUXI_INSTANCE_ID" ]; then
            YUXI_INSTANCE_ID="instance-$(generate_hex 8)"
            echo "Generated YUXI_INSTANCE_ID and saved it to .env."
        fi

        set_env_value "YUXI_INSTANCE_ID" "$YUXI_INSTANCE_ID"
    fi
}

ensure_sandbox_env() {
    ensure_security_secret "SANDBOX_PROVISIONER_TOKEN" "JWT_SECRET_KEY" "API_KEY_DERIVATION_SECRET"
}

if [ "${1:-}" = "--validate-security-env" ]; then
    if [ ! -f ".env" ]; then
        echo ".env does not exist" >&2
        exit 1
    fi
    validate_security_env
    exit 0
fi

skip_existing_image() {
    local image="$1"

    if ! docker image inspect "$image" >/dev/null 2>&1; then
        return 1
    fi

    echo "⏭️  ${image} already exists. Skipping pull."
    return 0
}

echo "🚀 Initializing Yuxi project..."
echo "=================================="

# Check if .env file exists
if [ -f ".env" ]; then
    echo "✅ .env file already exists. Checking required settings."
    ensure_required_api_env
    ensure_jwt_env
    ensure_sandbox_env
    validate_security_env
    chmod 600 .env
else
    echo "📝 .env file not found. Let's set up your environment variables."
    echo ""

    # Get SILICONFLOW_API_KEY
    echo "🔑 SiliconFlow API Key required"
    echo "Get your API key from: https://cloud.siliconflow.cn/i/Eo5yTHGJ"
    while true; do
        read -s -p "Please enter your SILICONFLOW_API_KEY: " SILICONFLOW_API_KEY
        echo ""
        if [ -z "$SILICONFLOW_API_KEY" ]; then
            echo "❌ API Key cannot be empty. Please try again."
        else
            break
        fi
    done

    # Get Web Search Provider and API Key (optional)
    echo ""
    echo "🔍 Web Search Provider (optional)"
    echo "1) doubao (Doubao Custom Search)"
    echo "2) tavily (Tavily Search)"
    read -p "Please select web search provider (1 for doubao, 2 for tavily, press Enter to skip): " SEARCH_CHOICE

    WEB_SEARCH_PROVIDER=""
    DOUBAO_SEARCH_API_KEY=""
    TAVILY_API_KEY=""

    if [ "$SEARCH_CHOICE" = "1" ] || [ "$SEARCH_CHOICE" = "doubao" ]; then
        WEB_SEARCH_PROVIDER="doubao"
        echo "Get your Doubao API Key from Volcengine Console https://console.volcengine.com/search-infinity/api-key"
        read -s -p "Please enter your DOUBAO_SEARCH_API_KEY: " DOUBAO_SEARCH_API_KEY
        echo ""
    elif [ "$SEARCH_CHOICE" = "2" ] || [ "$SEARCH_CHOICE" = "tavily" ]; then
        WEB_SEARCH_PROVIDER="tavily"
        echo "Get your Tavily API key from: https://app.tavily.com/"
        read -s -p "Please enter your TAVILY_API_KEY: " TAVILY_API_KEY
        echo ""
    fi

    echo ""
    echo "JWT security settings"
    read_security_secret "JWT_SECRET_KEY"
    JWT_SECRET_KEY="$SECURITY_SECRET_VALUE"

    read_security_secret "API_KEY_DERIVATION_SECRET" "$JWT_SECRET_KEY"
    API_KEY_DERIVATION_SECRET="$SECURITY_SECRET_VALUE"

    read -p "Please enter your YUXI_INSTANCE_ID (press Enter to auto-generate): " YUXI_INSTANCE_ID
    if [ -z "$YUXI_INSTANCE_ID" ]; then
        YUXI_INSTANCE_ID="instance-$(generate_hex 8)"
        echo "Generated YUXI_INSTANCE_ID and saved it to .env."
    fi

    read_security_secret "SANDBOX_PROVISIONER_TOKEN" "$JWT_SECRET_KEY" "$API_KEY_DERIVATION_SECRET"
    SANDBOX_PROVISIONER_TOKEN="$SECURITY_SECRET_VALUE"

    # Create .env file
    cat > .env << EOF
# SiliconFlow API Key (required)
SILICONFLOW_API_KEY=${SILICONFLOW_API_KEY}

# Web Search Provider settings
EOF

    if [ -n "$WEB_SEARCH_PROVIDER" ]; then
        echo "WEB_SEARCH_PROVIDER=${WEB_SEARCH_PROVIDER}" >> .env
    fi
    if [ -n "$DOUBAO_SEARCH_API_KEY" ]; then
        echo "DOUBAO_SEARCH_API_KEY=${DOUBAO_SEARCH_API_KEY}" >> .env
    fi
    if [ -n "$TAVILY_API_KEY" ]; then
        echo "TAVILY_API_KEY=${TAVILY_API_KEY}" >> .env
    fi

    cat >> .env << EOF

# JWT security settings
JWT_SECRET_KEY=${JWT_SECRET_KEY}
API_KEY_DERIVATION_SECRET=${API_KEY_DERIVATION_SECRET}
YUXI_INSTANCE_ID=${YUXI_INSTANCE_ID}
SANDBOX_PROVISIONER_TOKEN=${SANDBOX_PROVISIONER_TOKEN}
EOF

    validate_security_env
    chmod 600 .env
    echo "✅ .env file created successfully!"
fi

echo ""
echo "📦 Pulling Docker images..."
echo "========================="

# List of Docker images to pull
images=(
    "python:3.13-slim"
    "node:24-slim"
    "node:24-alpine"
    "milvusdb/milvus:v2.5.6"
    "neo4j:5.26.29"
    "ghcr.io/astral-sh/uv:0.12.6"
    "nginx:alpine"
    "quay.io/coreos/etcd:v3.5.5"
    "postgres:16"
    "redis:7.4.10-alpine"
)

# Pull each image
for image in "${images[@]}"; do
    if skip_existing_image "$image"; then
        continue
    fi

    echo "🔄 Pulling ${image}..."
    if bash scripts/pull_image.sh "$image"; then
        echo "✅ Successfully pulled ${image}"
    else
        echo "❌ Failed to pull ${image}"
        exit 1
    fi
done

# MinIO 官方镜像已全面下架，改为从仓库内 Dockerfile 构建
echo "🔄 Building minio image..."
if docker compose build minio; then
    echo "✅ Successfully built minio image"
else
    echo "❌ Failed to build minio image"
    exit 1
fi

sandbox_image="enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:1.11.0"
if ! skip_existing_image "$sandbox_image"; then
    echo "🔄 Pulling ${sandbox_image}..."
    docker pull "$sandbox_image"
    echo "✅ Successfully pulled ${sandbox_image}"
fi

echo ""
echo "🎉 Initialization complete!"
echo "=========================="
echo "You can now run: docker compose up -d --build"
echo "This will start all services in development mode with hot-reload enabled."
