# Windows 一键启动，对应 start.sh。
# 在交付包根目录执行: powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Invoke-Checked {
    # 不用高级函数。否则 docker load -i、-d 会被当成 -InformationAction、-Debug。
    $exe = [string]$args[0]
    if (-not $exe) { throw "未指定命令" }
    if ($args.Count -gt 1) {
        & $exe @($args[1..($args.Count - 1)])
    } else {
        & $exe
    }
    if ($LASTEXITCODE -ne 0) {
        throw "命令失败（退出码 $LASTEXITCODE）: $($args -join ' ')"
    }
}

function Get-Sha256Text {
    param([string]$Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLower()
    } finally {
        $sha.Dispose()
    }
}

function Read-MachineIdText {
    param([string]$Text)
    if (-not $Text) { return $null }
    $clean = ($Text -replace "`0", "" -replace "\s", "")
    if ($clean -match "([0-9a-fA-F]{32})") {
        return $Matches[1].ToLower()
    }
    return $null
}

function Get-HostMachineId {
    foreach ($path in @(
        "\\wsl$\docker-desktop\etc\machine-id",
        "\\wsl.localhost\docker-desktop\etc\machine-id"
    )) {
        try {
            if (Test-Path -LiteralPath $path) {
                $id = Read-MachineIdText (Get-Content -LiteralPath $path -Raw)
                if ($id) { return $id }
            }
        } catch {
        }
    }
    $commands = @(
        @("wsl.exe", "-d", "docker-desktop", "--", "cat", "/etc/machine-id"),
        @("wsl.exe", "-d", "docker-desktop", "-e", "cat", "/etc/machine-id"),
        @("wsl.exe", "--", "cat", "/etc/machine-id")
    )
    foreach ($cmd in $commands) {
        try {
            $raw = & $cmd[0] @($cmd[1..($cmd.Length - 1)]) 2>$null | Out-String
            $id = Read-MachineIdText $raw
            if ($id) { return $id }
        } catch {
        }
    }
    return $null
}

function Get-HostMachineIdFromDocker {
    foreach ($image in @("postgres:16-alpine", "rabbitmq:3.13-management-alpine")) {
        & docker image inspect $image 1>$null 2>$null
        if ($LASTEXITCODE -ne 0) { continue }
        $prev = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $raw = & docker run --rm -v /etc/machine-id:/host/machine-id:ro --entrypoint cat $image /host/machine-id 2>$null | Out-String
            if ($LASTEXITCODE -eq 0) {
                $id = Read-MachineIdText $raw
                if ($id) { return $id }
            }
        } catch {
        } finally {
            $ErrorActionPreference = $prev
        }
    }
    return $null
}

$script:FingerprintShown = $false

function Show-MachineFingerprint {
    param([string]$MachineId)
    if (-not $MachineId) { return $false }
    $fp = Get-Sha256Text $MachineId
    Write-Host ">>> 机器指纹 machine_id=$fp"
    Write-Host ">>> 签发绑定许可时把该指纹提供给供应商。该许可不能在其他机器运行。"
    $script:FingerprintShown = $true
    return $true
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未找到 docker。请先安装并启动 Docker Desktop。"
}
Invoke-Checked docker compose version | Out-Null

Show-MachineFingerprint (Get-HostMachineId) | Out-Null

Write-Host ">>> 准备 data 目录"
foreach ($dir in @("data\.license", "data\.worker", "data\input", "data\output")) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}
Write-Host ">>> Windows 上无法 chown 到 uid 10001。Docker Desktop 挂载的 data 目录需允许容器写入。"

$required = @(
    "worker-image.tar",
    "backend-image.tar",
    "frontend-image.tar",
    "postgres-image.tar",
    "rabbitmq-image.tar"
)
if (Test-Path -LiteralPath "images\load-images.sh") {
    $missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path "images" $_)) })
    if ($missing.Count -gt 0) {
        throw "缺少镜像文件: $($missing -join ', ')"
    }
    foreach ($name in $required) {
        $tar = Join-Path "images" $name
        Write-Host ">>> docker load -i $name"
        Invoke-Checked docker load -i $tar
        $archFile = "$tar.arch"
        if (Test-Path -LiteralPath $archFile) {
            $want = (Get-Content -LiteralPath $archFile -Raw).Trim()
            $ref = switch ($name) {
                "worker-image.tar" { docker images --format "{{.Repository}}:{{.Tag}}" | Where-Object { $_ -like "spectrum-mosaic-worker:*" } | Select-Object -First 1 }
                "backend-image.tar" { docker images --format "{{.Repository}}:{{.Tag}}" | Where-Object { $_ -like "spectrum-mosaic-backend:*" } | Select-Object -First 1 }
                "frontend-image.tar" { docker images --format "{{.Repository}}:{{.Tag}}" | Where-Object { $_ -like "spectrum-mosaic-frontend:*" } | Select-Object -First 1 }
                "postgres-image.tar" { "postgres:16-alpine" }
                "rabbitmq-image.tar" { "rabbitmq:3.13-management-alpine" }
            }
            if ($ref) {
                $got = (docker image inspect --format "{{.Architecture}}" $ref).Trim()
                Write-Host ">>> arch check $ref got=$got want=$want"
                if ($got -ne $want) {
                    throw "$name 加载后架构为 $got，期望 $want。请换正确架构的交付包。"
                }
            }
        }
    }
} else {
    Write-Host ">>> 未找到 images，跳过镜像导入（请确认本地已有所需镜像）"
}

if (-not $script:FingerprintShown) {
    if (-not (Show-MachineFingerprint (Get-HostMachineIdFromDocker))) {
        Write-Warning "读不到 Docker 虚拟机里的 /etc/machine-id，无法计算机器指纹。确认 Docker Desktop 已在运行后再执行本脚本。"
    }
}

if (-not (Test-Path -LiteralPath ".env")) {
    if (-not (Test-Path -LiteralPath ".env.example")) {
        throw "缺少 .env 与 .env.example"
    }
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host ">>> 已从 .env.example 生成 .env"
}

$lic = @(Get-ChildItem -LiteralPath "licenses" -Filter "*.lic" -ErrorAction SilentlyContinue)
if ($lic.Count -eq 0) {
    Write-Warning "licenses\ 下未发现 .lic 文件，worker 可能无法启动"
}

Write-Host ">>> docker compose up -d"
Invoke-Checked docker compose up -d
docker compose ps

$frontendPort = "9080"
if (Test-Path -LiteralPath ".env") {
    $line = Get-Content -LiteralPath ".env" | Where-Object { $_ -match "^FRONTEND_PORT=" } | Select-Object -First 1
    if ($line) {
        $val = ($line -replace "^FRONTEND_PORT=", "").Trim().Trim('"').Trim("'")
        if ($val) { $frontendPort = $val }
    }
}
Write-Host ">>> 启动完成。页面: http://localhost:${frontendPort}/"
Write-Host ">>> 查看日志: docker compose logs -f"
