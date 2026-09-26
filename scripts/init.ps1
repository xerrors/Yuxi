# Yuxi Initialization Script for PowerShell
# This script helps set up the environment for the Yuxi project

param([switch]$ValidateSecurityEnv)

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
    return -not [string]::IsNullOrWhiteSpace((Get-EnvValue $Name))
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

function Get-EnvValue($Name) {
    $escapedName = [regex]::Escape($Name)
    $line = Get-Content -Path ".env" | Where-Object { $_ -match "^$escapedName=" } | Select-Object -First 1
    if ($null -eq $line) {
        return ""
    }
    return $line.Substring($line.IndexOf("=") + 1)
}

function Read-HiddenValue($Prompt) {
    $secureValue = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Test-SecuritySecretValue($Value, [string[]]$OtherValues = @()) {
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value.Trim().Length -lt 32 -or $Value -ne $Value.Trim()) {
        return $false
    }
    if ($Value.StartsWith('"') -or $Value.EndsWith('"') -or $Value.StartsWith("'") -or $Value.EndsWith("'")) {
        return $false
    }
    foreach ($other in $OtherValues) {
        if (-not [string]::IsNullOrEmpty($other) -and $Value -eq $other) {
            return $false
        }
    }
    return $true
}

function Read-SecuritySecret($Name, [string[]]$OtherValues = @()) {
    while ($true) {
        $value = Read-HiddenValue "Please enter your $Name (press Enter to auto-generate)"
        if ([string]::IsNullOrEmpty($value)) {
            $value = New-RandomHex 32
            Write-Host "Generated $Name and saved it to .env." -ForegroundColor Green
        }
        if (Test-SecuritySecretValue $value $OtherValues) {
            return $value
        }
        Write-Host "❌ $Name must contain at least 32 non-whitespace characters and must not reuse another security secret." -ForegroundColor Red
    }
}

function Ensure-SecuritySecret($Name, [string[]]$OtherNames = @()) {
    $current = Get-EnvValue $Name
    $otherValues = @($OtherNames | ForEach-Object { Get-EnvValue $_ })
    if (Test-SecuritySecretValue $current $otherValues) {
        return
    }

    Write-Host "$Name is missing, too short, or reuses another security secret in .env." -ForegroundColor Yellow
    $value = Read-SecuritySecret $Name $otherValues
    Set-EnvValue $Name $value
}

function Assert-SecuritySecrets {
    $jwtSecret = Get-EnvValue "JWT_SECRET_KEY"
    $apiKeySecret = Get-EnvValue "API_KEY_DERIVATION_SECRET"
    $sandboxSecret = Get-EnvValue "SANDBOX_PROVISIONER_TOKEN"
    if (-not (Test-SecuritySecretValue $jwtSecret)) {
        throw "JWT_SECRET_KEY must contain at least 32 non-whitespace characters."
    }
    if (-not (Test-SecuritySecretValue $apiKeySecret @($jwtSecret))) {
        throw "API_KEY_DERIVATION_SECRET must be at least 32 characters and independent from JWT_SECRET_KEY."
    }
    if (-not (Test-SecuritySecretValue $sandboxSecret @($jwtSecret, $apiKeySecret))) {
        throw "SANDBOX_PROVISIONER_TOKEN must be at least 32 characters and independent from other security secrets."
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
    Ensure-SecuritySecret "JWT_SECRET_KEY"
    Ensure-SecuritySecret "API_KEY_DERIVATION_SECRET" @("JWT_SECRET_KEY")

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
    Ensure-SecuritySecret "SANDBOX_PROVISIONER_TOKEN" @("JWT_SECRET_KEY", "API_KEY_DERIVATION_SECRET")
}

if ($ValidateSecurityEnv) {
    if (-not (Test-Path ".env")) {
        throw ".env does not exist"
    }
    Assert-SecuritySecrets
    exit 0
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
    Assert-SecuritySecrets
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

    # Get Web Search Provider and API Key (optional)
    Write-Host ""
    Write-Host "🔍 Web Search Provider (optional)" -ForegroundColor Yellow
    Write-Host "1) doubao (Doubao Custom Search)" -ForegroundColor Blue
    Write-Host "2) tavily (Tavily Search)" -ForegroundColor Blue

    $SEARCH_CHOICE = Read-Host "Please select web search provider (1 for doubao, 2 for tavily, press Enter to skip)"

    $WEB_SEARCH_PROVIDER = ""
    $DOUBAO_SEARCH_API_KEY = ""
    $TAVILY_API_KEY = ""

    if ($SEARCH_CHOICE -eq "1" -or $SEARCH_CHOICE -eq "doubao") {
        $WEB_SEARCH_PROVIDER = "doubao"
        Write-Host "Get your Doubao API Key from Volcengine Console" -ForegroundColor Blue
        $DOUBAO_SEARCH_API_KEY = Read-Host "Please enter your DOUBAO_SEARCH_API_KEY"
    } elseif ($SEARCH_CHOICE -eq "2" -or $SEARCH_CHOICE -eq "tavily") {
        $WEB_SEARCH_PROVIDER = "tavily"
        Write-Host "Get your Tavily API key from: https://app.tavily.com/" -ForegroundColor Blue
        $TAVILY_API_KEY = Read-Host "Please enter your TAVILY_API_KEY"
    }

    Write-Host ""
    Write-Host "JWT security settings" -ForegroundColor Yellow
    $JWT_SECRET_KEY = Read-SecuritySecret "JWT_SECRET_KEY"
    $API_KEY_DERIVATION_SECRET = Read-SecuritySecret "API_KEY_DERIVATION_SECRET" @($JWT_SECRET_KEY)

    $YUXI_INSTANCE_ID = Read-Host "Please enter your YUXI_INSTANCE_ID (press Enter to auto-generate)"
    if ([string]::IsNullOrEmpty($YUXI_INSTANCE_ID)) {
        $YUXI_INSTANCE_ID = "instance-$(New-RandomHex 8)"
        Write-Host "Generated YUXI_INSTANCE_ID and saved it to .env." -ForegroundColor Green
    }

    $SANDBOX_PROVISIONER_TOKEN = Read-SecuritySecret "SANDBOX_PROVISIONER_TOKEN" @(
        $JWT_SECRET_KEY,
        $API_KEY_DERIVATION_SECRET
    )

    # Create .env file
    $envContent = @"
# SiliconFlow API Key (required)
SILICONFLOW_API_KEY=$apiKey

# Web Search Provider settings
"@

    if (-not [string]::IsNullOrEmpty($WEB_SEARCH_PROVIDER)) {
        $envContent += "`nWEB_SEARCH_PROVIDER=$WEB_SEARCH_PROVIDER"
    }
    if (-not [string]::IsNullOrEmpty($DOUBAO_SEARCH_API_KEY)) {
        $envContent += "`nDOUBAO_SEARCH_API_KEY=$DOUBAO_SEARCH_API_KEY"
    }
    if (-not [string]::IsNullOrEmpty($TAVILY_API_KEY)) {
        $envContent += "`nTAVILY_API_KEY=$TAVILY_API_KEY"
    }

    $envContent += @"

# JWT security settings
JWT_SECRET_KEY=$JWT_SECRET_KEY
API_KEY_DERIVATION_SECRET=$API_KEY_DERIVATION_SECRET
YUXI_INSTANCE_ID=$YUXI_INSTANCE_ID
SANDBOX_PROVISIONER_TOKEN=$SANDBOX_PROVISIONER_TOKEN
"@

    $envContent | Out-File -FilePath ".env" -Encoding UTF8
    Assert-SecuritySecrets
    Write-Host "✅ .env file created successfully!" -ForegroundColor Green

    # Clear the variables from memory
    Remove-Variable -Name "apiKey" -ErrorAction SilentlyContinue
    Remove-Variable -Name "WEB_SEARCH_PROVIDER" -ErrorAction SilentlyContinue
    Remove-Variable -Name "DOUBAO_SEARCH_API_KEY" -ErrorAction SilentlyContinue
    Remove-Variable -Name "TAVILY_API_KEY" -ErrorAction SilentlyContinue
    Remove-Variable -Name "JWT_SECRET_KEY" -ErrorAction SilentlyContinue
    Remove-Variable -Name "API_KEY_DERIVATION_SECRET" -ErrorAction SilentlyContinue
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
    "neo4j:5.26.29",
    "ghcr.io/astral-sh/uv:0.12.6",
    "nginx:alpine",
    "quay.io/coreos/etcd:v3.5.5",
    "postgres:16",
    "redis:7.4.10-alpine"
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

# MinIO 官方镜像已全面下架，改为从仓库内 Dockerfile 构建
Write-Host "🔄 Building minio image..." -ForegroundColor Yellow
docker compose build minio
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed to build minio image" -ForegroundColor Red
    exit 1
}

$sandboxImage = "enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:1.11.0"
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
