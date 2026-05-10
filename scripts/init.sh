#!/bin/bash

# Yuxi Initialization Script for Bash/Linux/macOS
# This script helps set up the environment for the Yuxi project

set -e

generate_hex() {
    local length="$1"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$length"
    else
        tr -dc 'a-f0-9' < /dev/urandom | head -c $((length * 2))
    fi
}

ensure_jwt_env() {
    if grep -Eq '^JWT_SECRET_KEY=.+' .env && grep -Eq '^YUXI_INSTANCE_ID=.+' .env; then
        return
    fi

    echo "JWT security settings are missing in .env."
    read -s -p "Please enter your JWT_SECRET_KEY (press Enter to auto-generate): " JWT_SECRET_KEY
    echo ""
    if [ -z "$JWT_SECRET_KEY" ]; then
        JWT_SECRET_KEY=$(generate_hex 32)
        echo "Generated JWT_SECRET_KEY and saved it to .env."
    fi

    read -p "Please enter your YUXI_INSTANCE_ID (press Enter to auto-generate): " YUXI_INSTANCE_ID
    if [ -z "$YUXI_INSTANCE_ID" ]; then
        YUXI_INSTANCE_ID="instance-$(generate_hex 8)"
        echo "Generated YUXI_INSTANCE_ID and saved it to .env."
    fi

    cat >> .env << EOF

# JWT security settings
JWT_SECRET_KEY=${JWT_SECRET_KEY}
YUXI_INSTANCE_ID=${YUXI_INSTANCE_ID}
EOF
}

echo "🚀 Initializing Yuxi project..."
echo "=================================="

# Check if .env file exists
if [ -f ".env" ]; then
    echo "✅ .env file already exists. Skipping environment setup."
    ensure_jwt_env
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

    # Get TAVILY_API_KEY (optional)
    echo ""
    echo "🔍 Tavily API Key (optional) - for search service"
    echo "Get your API key from: https://app.tavily.com/"
    read -p "Please enter your TAVILY_API_KEY (press Enter to skip): " TAVILY_API_KEY

    echo ""
    echo "JWT security settings"
    read -s -p "Please enter your JWT_SECRET_KEY (press Enter to auto-generate): " JWT_SECRET_KEY
    echo ""
    if [ -z "$JWT_SECRET_KEY" ]; then
        JWT_SECRET_KEY=$(generate_hex 32)
        echo "Generated JWT_SECRET_KEY and saved it to .env."
    fi

    read -p "Please enter your YUXI_INSTANCE_ID (press Enter to auto-generate): " YUXI_INSTANCE_ID
    if [ -z "$YUXI_INSTANCE_ID" ]; then
        YUXI_INSTANCE_ID="instance-$(generate_hex 8)"
        echo "Generated YUXI_INSTANCE_ID and saved it to .env."
    fi

    # Create .env file
    cat > .env << EOF
# SiliconFlow API Key (required)
SILICONFLOW_API_KEY=${SILICONFLOW_API_KEY}

# Tavily API Key (optional - for search service)
EOF

    if [ -n "$TAVILY_API_KEY" ]; then
        echo "TAVILY_API_KEY=${TAVILY_API_KEY}" >> .env
    fi

    cat >> .env << EOF

# JWT security settings
JWT_SECRET_KEY=${JWT_SECRET_KEY}
YUXI_INSTANCE_ID=${YUXI_INSTANCE_ID}
EOF

    echo "✅ .env file created successfully!"
fi

echo ""
echo "📦 Pulling Docker images..."
echo "========================="

# List of Docker images to pull
images=(
    "python:3.12-slim"
    "node:24-slim"
    "node:24-alpine"
    "milvusdb/milvus:v2.5.6"
    "neo4j:5.26"
    "minio/minio:RELEASE.2023-03-20T20-16-18Z"
    "ghcr.io/astral-sh/uv:0.7.2"
    "nginx:alpine"
    "quay.io/coreos/etcd:v3.5.5"
    "postgres:16"
    "redis:7-alpine"
)

# Pull each image
for image in "${images[@]}"; do
    echo "🔄 Pulling ${image}..."
    if bash scripts/pull_image.sh "$image"; then
        echo "✅ Successfully pulled ${image}"
    else
        echo "❌ Failed to pull ${image}"
        exit 1
    fi
done

echo ""
echo "🎉 Initialization complete!"
echo "=========================="
echo "You can now run: docker compose up -d --build"
echo "This will start all services in development mode with hot-reload enabled."