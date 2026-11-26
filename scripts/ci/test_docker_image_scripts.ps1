# 使用本地 Docker 替身验证镜像标签、归档及失败退出码。
$ErrorActionPreference = "Stop"
$pullScript = (Resolve-Path (Join-Path $PSScriptRoot "../pull_image.ps1")).Path
$saveScript = (Resolve-Path (Join-Path $PSScriptRoot "../../docker/save_docker_images.ps1")).Path
$caseDirectory = Join-Path ([IO.Path]::GetTempPath()) ("yuxi-docker-images-" + [guid]::NewGuid().ToString("N"))
$dockerDirectory = New-Item -ItemType Directory -Path (Join-Path $caseDirectory "docker") -Force
$copiedSaveScript = Join-Path $dockerDirectory.FullName "save_docker_images.ps1"
Copy-Item -LiteralPath $saveScript -Destination $copiedSaveScript
$state = @{}

function docker {
    $global:LASTEXITCODE = 0
    switch ($args[0]) {
        "pull" {
            if ($args[1] -like "m.daocloud.io/*") {
                if ($state.FailMirror) { $global:LASTEXITCODE = 21 }
                else { Set-Content -Path (Join-Path $caseDirectory "mirror") -Value "fixture image" }
            } elseif ($state.FailOrigin) {
                $global:LASTEXITCODE = 22
            } elseif ($args[1] -eq "yuxi-audit-test:1") {
                Set-Content -Path (Join-Path $caseDirectory "original") -Value "fixture image"
            }
        }
        "tag" {
            if ($state.FailTag) { $global:LASTEXITCODE = 23 }
            else { Copy-Item (Join-Path $caseDirectory "mirror") (Join-Path $caseDirectory "original") }
        }
        "rmi" { Remove-Item (Join-Path $caseDirectory "mirror") -ErrorAction SilentlyContinue }
        "compose" {
            if ($args -contains "config") {
                switch ($args[-1]) {
                    "minio" { "fixture-minio:RELEASE.fixture" }
                    "storage-init" { "fixture-storage-init:1" }
                    "sandbox-image" { "fixture-sandbox:1" }
                }
            }
        }
        "save" {
            if ($state.FailSave) { $global:LASTEXITCODE = 24 }
            else {
                $imageTags = $args[1..($args.Length - 3)]
                Set-Content -Path $args[-1] -Value $imageTags
            }
        }
    }
}

try {
    $original = Join-Path $caseDirectory "original"
    $mirror = Join-Path $caseDirectory "mirror"
    foreach ($scenario in @(
        @{ Mirror = $false; Origin = $false; Tag = $false; Status = 0 },
        @{ Mirror = $true; Origin = $false; Tag = $false; Status = 0 },
        @{ Mirror = $true; Origin = $true; Tag = $false; Status = 22 },
        @{ Mirror = $false; Origin = $false; Tag = $true; Status = 23 }
    )) {
        $state.FailMirror = $scenario.Mirror
        $state.FailOrigin = $scenario.Origin
        $state.FailTag = $scenario.Tag
        $output = & $pullScript "yuxi-audit-test:1" *>&1
        if ($LASTEXITCODE -ne $scenario.Status) {
            throw "Image pull returned $LASTEXITCODE instead of $($scenario.Status).`n$($output -join "`n")"
        }
        if ($scenario.Status -eq 0) {
            if ((Get-Content -LiteralPath $original) -ne "fixture image" -or (Test-Path -LiteralPath $mirror)) {
                throw "Image pull did not publish the original tag."
            }
        } elseif (Test-Path -LiteralPath $original) {
            throw "A failed image pull published the original tag."
        }
        Remove-Item -LiteralPath $original, $mirror -ErrorAction SilentlyContinue
    }

    $state.FailOrigin = $true
    $state.FailSave = $false
    Push-Location $caseDirectory
    try {
        & $copiedSaveScript *> $null
        $archive = Get-ChildItem -Path "docker_images_backup" -Filter "*.tar" -File
        if ($LASTEXITCODE -ne 0 -or -not $archive) {
            throw "Image save did not write an archive from local images after failed pulls."
        }
        $archiveTags = Get-Content -LiteralPath $archive.FullName
        foreach ($tag in @("alpine:3.20", "fixture-minio:RELEASE.fixture", "fixture-storage-init:1", "fixture-sandbox:1")) {
            if ($archiveTags -notcontains $tag) { throw "Image archive omitted $tag." }
        }
        $state.FailSave = $true
        $output = & $copiedSaveScript *>&1
        if ($LASTEXITCODE -ne 24 -or $output -match "docker load -i") {
            throw "Image save reported success despite a failed save and a pre-existing archive."
        }
    } finally {
        Pop-Location
    }
} finally {
    Remove-Item -LiteralPath $caseDirectory -Recurse -Force
}
