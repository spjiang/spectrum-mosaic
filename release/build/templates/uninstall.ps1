# Windows 卸载：只删除本交付包的容器和镜像。不删除 data、pgdata、licenses、.env。
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未找到 docker。请先启动 Docker Desktop。"
}
if (-not (Test-Path -LiteralPath "docker-compose.yml")) {
    throw "当前目录没有 docker-compose.yml。请在交付包根目录执行。"
}

Write-Host ">>> 停止并删除本交付包的容器和镜像"
Write-Host ">>> 保留 data、pgdata、licenses、.env，不删除用户数据"

$composeArgs = @("-f", "docker-compose.yml")
if (Test-Path -LiteralPath "docker-compose.gpu.yml") {
    $composeArgs += @("-f", "docker-compose.gpu.yml")
}
# 禁止追加 -v 或 --volumes，否则会清掉命名数据卷。
& docker compose @composeArgs down --rmi all --remove-orphans
if ($LASTEXITCODE -ne 0) {
    throw "卸载失败（退出码 $LASTEXITCODE）"
}

Write-Host ">>> 卸载完成。以下内容未删除："
Write-Host ">>>   data\      航片和成果"
Write-Host ">>>   pgdata\    数据库"
Write-Host ">>>   licenses\  许可文件"
Write-Host ">>>   .env       本机配置"
Write-Host ">>> 再次使用时双击 start.cmd。镜像已删除时会重新导入，并沿用上述数据。"
