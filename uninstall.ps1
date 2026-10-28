<#
.SYNOPSIS
nasca-mcp 제거 스크립트. 기본은 가상환경과 클라이언트 등록만 지우고 설정과 감사 로그는 남긴다.

.PARAMETER RemoveRuntime
샌드박스 런타임(%LOCALAPPDATA%\workbench\runtime)을 지운다.
.PARAMETER RemoveConfig
config.json과 policy.json을 지운다.
.PARAMETER RemoveAudit
감사 로그(audit)를 지운다. 기록이 사라지므로 보안팀 보존 규정을 먼저 확인한다.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'nasca-mcp'),
    [switch]$RemoveRuntime,
    [switch]$RemoveConfig,
    [switch]$RemoveAudit
)

$ErrorActionPreference = 'Continue'
$ConfigDir = Join-Path $env:LOCALAPPDATA 'workbench'

function Info([string]$msg) { Write-Host "   $msg" }

Write-Host '== Claude Code 등록 해제' -ForegroundColor Cyan
$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) {
    & $claude.Source plugin uninstall 'workbench@nasca-mcp'
    & $claude.Source plugin marketplace remove 'nasca-mcp'
}
else {
    Info 'claude 명령이 없어 건너뜁니다.'
}
Info 'Codex에 등록했다면 ~/.codex/config.toml에서 [mcp_servers.workbench]를 직접 지우세요.'

Write-Host '== 파일 정리' -ForegroundColor Cyan
if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir; Info "삭제: $InstallDir" }
if ($RemoveRuntime -and (Test-Path (Join-Path $ConfigDir 'runtime'))) {
    Remove-Item -Recurse -Force (Join-Path $ConfigDir 'runtime'); Info '삭제: 샌드박스 런타임'
}
if ($RemoveConfig) {
    foreach ($f in 'config.json', 'policy.json') {
        $p = Join-Path $ConfigDir $f
        if (Test-Path $p) { Remove-Item -Force $p; Info "삭제: $p" }
    }
}
if ($RemoveAudit -and (Test-Path (Join-Path $ConfigDir 'audit'))) {
    Remove-Item -Recurse -Force (Join-Path $ConfigDir 'audit'); Info '삭제: 감사 로그'
}
else {
    Info "감사 로그는 유지했습니다: $(Join-Path $ConfigDir 'audit')"
}
Write-Host '제거를 마쳤습니다.' -ForegroundColor Green
