# Yuxi Initialization Script for PowerShell
# This script helps set up the environment for the Yuxi project
# Note: API keys will be visible during input - use with care

function New-RandomHex($ByteCount) {
    $bytes = [byte[]]::new($ByteCount)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
        return -join ($bytes | ForEach-Object { $_.ToString("x2") })
    } finally {
        $rng.Dispose()
    }
}

function Test-EnvValue($Name) {
    return [bool](Select-String -Path ".env" -Pattern "^$Name=.+" -Quiet)
}

function Set-EnvValue($Name, $Value) {
    $escapedName = [regex]::Escape($Name)
    if (Select-String -Path ".env" -Pattern "^$escapedName=" -Quiet) {
        $written = $false
        $envContent = Get-Content -Path ".env" | ForEach-Object {
            if ($_ -match "^$escapedName=") {
                if (-not $written) {
                    "$Name=$Value"
                    $written = $true
                }
            } else {
                $_
            }
        }
        $envContent | Set-Content -Path ".env" -Encoding UTF8
    } else {
        "`n$Name=$Value" | Add-Content -Path ".env" -Encoding UTF8
    }
}

function Ensure-RequiredApiEnv {
    if (Test-EnvValue "SILICONFLOW_API_KEY") {
        return
    }

    Write-Host "SILICONFLOW_API_KEY is missing in .env." -ForegroundColor Yellow
    do {
        $SILICONFLOW_API_KEY = Read-Host "Please enter your SILICONFLOW_API_KEY"
        if ([string]::IsNullOrEmpty($SILICONFLOW_API_KEY)) {
            Write-Host "❌ API Key cannot be empty. Please try again." -ForegroundColor Red
        }
    } while ([string]::IsNullOrEmpty($SILICONFLOW_API_KEY))
    Set-EnvValue "SILICONFLOW_API_KEY" $SILICONFLOW_API_KEY
}

function Ensure-JwtEnv {
    if (-not (Test-EnvValue "JWT_SECRET_KEY")) {
        Write-Host "JWT_SECRET_KEY is missing in .env." -ForegroundColor Yellow
        $JWT_SECRET_KEY = Read-Host "Please enter your JWT_SECRET_KEY (press Enter to auto-generate)"
        if ([string]::IsNullOrEmpty($JWT_SECRET_KEY)) {
            $JWT_SECRET_KEY = New-RandomHex 32
            Write-Host "Generated JWT_SECRET_KEY and saved it to .env." -ForegroundColor Green
        }

        Set-EnvValue "JWT_SECRET_KEY" $JWT_SECRET_KEY
    }

    if (-not (Test-EnvValue "YUXI_INSTANCE_ID")) {
        Write-Host "YUXI_INSTANCE_ID is missing in .env." -ForegroundColor Yellow
        $YUXI_INSTANCE_ID = Read-Host "Please enter your YUXI_INSTANCE_ID (press Enter to auto-generate)"
        if ([string]::IsNullOrEmpty($YUXI_INSTANCE_ID)) {
            $YUXI_INSTANCE_ID = "instance-$(New-RandomHex 8)"
            Write-Host "Generated YUXI_INSTANCE_ID and saved it to .env." -ForegroundColor Green
        }

        Set-EnvValue "YUXI_INSTANCE_ID" $YUXI_INSTANCE_ID
    }
}

function Ensure-SandboxEnv {
    if (Test-EnvValue "SANDBOX_PROVISIONER_TOKEN") {
        return
    }

    Write-Host "SANDBOX_PROVISIONER_TOKEN is missing in .env." -ForegroundColor Yellow
    $SANDBOX_PROVISIONER_TOKEN = Read-Host "Please enter your SANDBOX_PROVISIONER_TOKEN (press Enter to auto-generate)"
    if ([string]::IsNullOrEmpty($SANDBOX_PROVISIONER_TOKEN)) {
        $SANDBOX_PROVISIONER_TOKEN = New-RandomHex 32
        Write-Host "Generated SANDBOX_PROVISIONER_TOKEN and saved it to .env." -ForegroundColor Green
    }

    Set-EnvValue "SANDBOX_PROVISIONER_TOKEN" $SANDBOX_PROVISIONER_TOKEN
}

function Test-SkipExistingImage($ImageTag) {
    & docker image inspect $ImageTag *> $null
    if ($LASTEXITCODE -ne 0) {
        return $false
    }

    Write-Host "⏭️  $ImageTag already exists. Skipping pull." -ForegroundColor Green
    return $true
}

Write-Host "🚀 Initializing Yuxi project..." -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan

# Check if .env file exists
if (Test-Path ".env") {
    Write-Host "✅ .env file already exists. Checking required settings." -ForegroundColor Green
    Ensure-RequiredApiEnv
    Ensure-JwtEnv
    Ensure-SandboxEnv
} else {
    Write-Host "📝 .env file not found. Let's set up your environment variables." -ForegroundColor Yellow
    Write-Host ""

    # Get SILICONFLOW_API_KEY
    Write-Host "🔑 SiliconFlow API Key required" -ForegroundColor Yellow
    Write-Host "Get your API key from: https://cloud.siliconflow.cn/i/Eo5yTHGJ" -ForegroundColor Blue
    Write-Host "Note: Press Ctrl+C at any time to cancel" -ForegroundColor Gray
    Write-Host ""

    do {
        $apiKey = Read-Host "Please enter your SILICONFLOW_API_KEY"
        if ([string]::IsNullOrEmpty($apiKey)) {
            Write-Host "❌ API Key cannot be empty. Please try again." -ForegroundColor Red
        }
    } while ([string]::IsNullOrEmpty($apiKey))

    # Get TAVILY_API_KEY (optional)
    Write-Host ""
    Write-Host "🔍 Tavily API Key (optional) - for search service" -ForegroundColor Yellow
    Write-Host "Get your API key from: https://app.tavily.com/" -ForegroundColor Blue

    $TAVILY_API_KEY = Read-Host "Please enter your TAVILY_API_KEY (press Enter to skip)"

    Write-Host ""
    Write-Host "JWT security settings" -ForegroundColor Yellow
    $JWT_SECRET_KEY = Read-Host "Please enter your JWT_SECRET_KEY (press Enter to auto-generate)"
    if ([string]::IsNullOrEmpty($JWT_SECRET_KEY)) {
        $JWT_SECRET_KEY = New-RandomHex 32
        Write-Host "Generated JWT_SECRET_KEY and saved it to .env." -ForegroundColor Green
    }

    $YUXI_INSTANCE_ID = Read-Host "Please enter your YUXI_INSTANCE_ID (press Enter to auto-generate)"
    if ([string]::IsNullOrEmpty($YUXI_INSTANCE_ID)) {
        $YUXI_INSTANCE_ID = "instance-$(New-RandomHex 8)"
        Write-Host "Generated YUXI_INSTANCE_ID and saved it to .env." -ForegroundColor Green
    }

    $SANDBOX_PROVISIONER_TOKEN = Read-Host "Please enter your SANDBOX_PROVISIONER_TOKEN (press Enter to auto-generate)"
    if ([string]::IsNullOrEmpty($SANDBOX_PROVISIONER_TOKEN)) {
        $SANDBOX_PROVISIONER_TOKEN = New-RandomHex 32
        Write-Host "Generated SANDBOX_PROVISIONER_TOKEN and saved it to .env." -ForegroundColor Green
    }

    # Create .env file
    $envContent = @"
# SiliconFlow API Key (required)
SILICONFLOW_API_KEY=$apiKey

# Tavily API Key (optional - for search service)
"@

    if (-not [string]::IsNullOrEmpty($TAVILY_API_KEY)) {
        $envContent += "`nTAVILY_API_KEY=$TAVILY_API_KEY"
    }

    $envContent += @"

# JWT security settings
JWT_SECRET_KEY=$JWT_SECRET_KEY
YUXI_INSTANCE_ID=$YUXI_INSTANCE_ID
SANDBOX_PROVISIONER_TOKEN=$SANDBOX_PROVISIONER_TOKEN
"@

    $envContent | Out-File -FilePath ".env" -Encoding UTF8
    Write-Host "✅ .env file created successfully!" -ForegroundColor Green

    # Clear the variables from memory
    Remove-Variable -Name "apiKey" -ErrorAction SilentlyContinue
    Remove-Variable -Name "TAVILY_API_KEY" -ErrorAction SilentlyContinue
    Remove-Variable -Name "JWT_SECRET_KEY" -ErrorAction SilentlyContinue
    Remove-Variable -Name "YUXI_INSTANCE_ID" -ErrorAction SilentlyContinue
    Remove-Variable -Name "SANDBOX_PROVISIONER_TOKEN" -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "📦 Pulling Docker images..." -ForegroundColor Cyan
Write-Host "=========================" -ForegroundColor Cyan

# List of Docker images to pull
$images = @(
    "python:3.13-slim",
    "node:24-slim",
    "node:24-alpine",
    "milvusdb/milvus:v2.5.6",
    "neo4j:5.26",
    "minio/minio:RELEASE.2023-03-20T20-16-18Z",
    "ghcr.io/astral-sh/uv:0.11.26",
    "nginx:alpine",
    "quay.io/coreos/etcd:v3.5.5",
    "postgres:16",
    "redis:7-alpine"
)

# Pull each image
foreach ($image in $images) {
    if (Test-SkipExistingImage $image) {
        continue
    }

    Write-Host "🔄 Pulling ${image}..." -ForegroundColor Yellow
    try {
        & scripts/pull_image.ps1 $image
        if ($LASTEXITCODE -eq 0) {
            Write-Host "✅ Successfully pulled ${image}" -ForegroundColor Green
        } else {
            Write-Host "❌ Failed to pull ${image}" -ForegroundColor Red
            exit 1
        }
    } catch {
        Write-Host "❌ Error pulling ${image}: $_" -ForegroundColor Red
        exit 1
    }
}

$sandboxImage = "enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest"
if (-not (Test-SkipExistingImage $sandboxImage)) {
    Write-Host "🔄 Pulling ${sandboxImage}..." -ForegroundColor Yellow
    docker pull $sandboxImage
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ Successfully pulled ${sandboxImage}" -ForegroundColor Green
    } else {
        Write-Host "❌ Failed to pull ${sandboxImage}" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "🎉 Initialization complete!" -ForegroundColor Green
Write-Host "==========================" -ForegroundColor Green
Write-Host "You can now run: docker compose up -d --build" -ForegroundColor Cyan
Write-Host "This will start all services in development mode with hot-reload enabled." -ForegroundColor Cyan
