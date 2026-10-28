<#
.SYNOPSIS
nasca-mcp(작업구역) 설치 스크립트. Windows 10/11 Pro 이상, PowerShell 5.1 이상.

.DESCRIPTION
이 스크립트는 시스템 전체의 파일 읽기와 쓰기에 관여하지 않는다. 드라이버, 서비스, 시작 프로그램, 예약 작업, 레지스트리,
PATH, 호스트 방화벽, NASCA 설정을 바꾸지 않는다. 만드는 것은 사용자 폴더 아래의 두 폴더(가상환경, 설정)뿐이며,
MCP 서버는 AI 클라이언트가 띄워야 실행된다. Windows 샌드박스 기능 켜기는 -EnableSandboxFeature를 줄 때만 한다.

1. 점검: Windows, 파이썬 3.11 이상, Windows 샌드박스 기능
2. 가상환경을 만들고 이 저장소를 설치
3. 설정 파일(config.json, policy.json) 작성: %LOCALAPPDATA%\workbench
4. 샌드박스 런타임 빌드(읽기 전용 파이썬 + 문서 라이브러리 + 에이전트)
5. 선택: Claude Code 플러그인 또는 Codex에 등록
6. 점검 실행(doctor)

여러 번 실행해도 된다. 이미 있는 설정 파일은 -Force가 없으면 유지한다.

.PARAMETER NascaMcpConfig
사내 NASCA MCP 설정 JSON 경로. 없으면 모의 NASCA(보안 기능 없음)로 설치한다.
.PARAMETER ProtectedRoot
AI가 직접 열지 못하게 막을 DRM 보호 폴더. 여러 개 가능.
.PARAMETER OpenRoot
workbench_open으로 열 수 있는 폴더. 비우면 제한 없음.
.PARAMETER ExportRoot
workbench_export로 내보낼 수 있는 폴더. 비우면 제한 없음.
.PARAMETER BlockedMcpServer
AI가 직접 호출하지 못하게 막을 원본 NASCA MCP 서버 이름. 기본 nasca.
.PARAMETER PipIndex
사내 PyPI 미러 주소(예: https://사내미러/simple).
.PARAMETER PythonEmbedZip
인터넷 없이 쓸 내장 파이썬 zip(python-3.12.x-embed-amd64.zip) 경로.
.PARAMETER EnableSandboxFeature
Windows 샌드박스 기능을 켠다. 관리자 PowerShell이 필요하고 재부팅해야 한다.
.PARAMETER SkipRuntime
샌드박스 런타임 빌드를 건너뛴다.
.PARAMETER RegisterClaude
Claude Code 플러그인으로 등록한다(claude 명령 필요).
.PARAMETER ClaudeScope
플러그인을 켤 범위. user(이 PC의 모든 Claude Code), project(현재 폴더의 프로젝트만), local(현재 폴더, 공유 안 함). 기본 user.
.PARAMETER RegisterCodex
Codex에 MCP 서버로 등록한다(codex 명령 필요).
.PARAMETER Selftest
끝에서 샌드박스를 실제로 띄워 임시 문서를 열고 읽고 닫아 본다(수십 초에서 수 분).
.PARAMETER Force
이미 있는 설정 파일을 덮어쓴다.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\install.ps1 -RegisterClaude

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\install.ps1 -NascaMcpConfig C:\tools\nasca-mcp.json `
  -ProtectedRoot 'D:\NASCA\보안문서' -OpenRoot 'D:\NASCA\보안문서' -ExportRoot 'D:\NASCA\결과' `
  -RegisterClaude -Selftest
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'nasca-mcp'),
    [string]$NascaMcpConfig = '',
    [string[]]$ProtectedRoot = @(),
    [string[]]$OpenRoot = @(),
    [string[]]$ExportRoot = @(),
    [string[]]$BlockedMcpServer = @('nasca'),
    [string]$PipIndex = '',
    [string]$PythonEmbedZip = '',
    [switch]$EnableSandboxFeature,
    [switch]$SkipRuntime,
    [switch]$RegisterClaude,
    [ValidateSet('user', 'project', 'local')][string]$ClaudeScope = 'user',
    [switch]$RegisterCodex,
    [switch]$Selftest,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$RepoRoot   = $PSScriptRoot
$ConfigDir  = Join-Path $env:LOCALAPPDATA 'workbench'
$RuntimeDir = Join-Path $ConfigDir 'runtime'
$Venv       = Join-Path $InstallDir 'venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$Warnings   = New-Object System.Collections.Generic.List[string]

function Step([string]$msg) { Write-Host ''; Write-Host "== $msg" -ForegroundColor Cyan }
function Info([string]$msg) { Write-Host "   $msg" }
function Warn([string]$msg) { $Warnings.Add($msg); Write-Host "   [경고] $msg" -ForegroundColor Yellow }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# 3.11 이상인 파이썬을 찾는다. Microsoft Store의 python 껍데기(실행하면 스토어가 열림)는 건너뛴다.
function Find-Python {
    $candidates = @(
        @{ Name = 'py';      Args = @('-3') },
        @{ Name = 'python';  Args = @() },
        @{ Name = 'python3'; Args = @() }
    )
    foreach ($c in $candidates) {
        $cmd = Get-Command $c.Name -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $ver = & $cmd.Source @($c.Args) -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $ver) { continue }
            $parts = ([string]$ver).Trim().Split('.')
            $major = [int]$parts[0]; $minor = [int]$parts[1]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                return @{ Exe = $cmd.Source; Args = @($c.Args); Version = "$major.$minor" }
            }
        } catch { continue }
    }
    return $null
}

function Write-JsonFile([string]$path, $obj) {
    $json = ConvertTo-Json -InputObject $obj -Depth 6
    [System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
}

function Save-Config([string]$path, $obj, [string]$label) {
    if ((Test-Path $path) -and -not $Force) {
        Warn "$label 이미 있어 유지합니다: $path (바꾸려면 -Force)"
        return
    }
    Write-JsonFile $path $obj
    Info "$label 작성: $path"
}

# ---------------------------------------------------------------- 1. 점검
Step '1/6 환경 점검'
if ($env:OS -ne 'Windows_NT') { throw 'Windows에서만 설치할 수 있습니다. 리눅스와 맥에서는 개발용 로컬 백엔드만 쓸 수 있습니다(README의 개발 절). ' }
Info "저장소: $RepoRoot"

$py = Find-Python
if (-not $py) {
    throw '파이썬 3.11 이상을 찾지 못했습니다. https://www.python.org/downloads/ 에서 설치하고(PATH 추가 선택) 새 PowerShell에서 다시 실행하세요.'
}
Info "파이썬 $($py.Version): $($py.Exe)"

# Claude Code 훅은 python 명령으로 실행된다. 없으면 훅 오류 알림이 도구 호출마다 뜬다(작업은 막히지 않는다).
$hookPy = Get-Command python -ErrorAction SilentlyContinue
$hookOk = $false
if ($hookPy) {
    try { & $hookPy.Source -c "print(1)" 2>$null | Out-Null; $hookOk = ($LASTEXITCODE -eq 0) } catch { $hookOk = $false }
}
if (-not $hookOk) {
    Warn 'python 명령이 PATH에 없거나 Microsoft Store 껍데기입니다. Claude Code 훅(접근 통제)이 실행되지 않습니다. python.org에서 설치할 때 "Add python.exe to PATH"를 선택하세요.'
}

$sandboxExe = Join-Path $env:WINDIR 'System32\WindowsSandbox.exe'
$needReboot = $false
if (Test-Path $sandboxExe) {
    Info 'Windows 샌드박스: 사용 가능'
}
elseif ($EnableSandboxFeature) {
    if (-not (Test-Admin)) { throw '-EnableSandboxFeature는 관리자 권한 PowerShell에서 실행해야 합니다.' }
    Enable-WindowsOptionalFeature -Online -FeatureName 'Containers-DisposableClientVM' -All -NoRestart | Out-Null
    $needReboot = $true
    Warn 'Windows 샌드박스 기능을 켰습니다. 설치를 마친 뒤 재부팅하고, 재부팅 후 점검(doctor)을 다시 실행하세요.'
}
else {
    Warn 'Windows 샌드박스가 꺼져 있습니다(Pro, Enterprise, Education 에디션만 가능). 관리자 PowerShell에서 -EnableSandboxFeature로 켜거나 Windows 기능 추가/제거에서 켜세요. 사내 정책이 막고 있을 수 있습니다.'
}

foreach ($r in @($ProtectedRoot) + @($OpenRoot) + @($ExportRoot)) {
    if ($r -and -not (Test-Path $r)) { Warn "폴더가 아직 없습니다: $r" }
}
if ($NascaMcpConfig -and -not (Test-Path $NascaMcpConfig)) { throw "NASCA MCP 설정 파일이 없습니다: $NascaMcpConfig" }

# ---------------------------------------------------------------- 2. 가상환경과 설치
Step '2/6 가상환경과 패키지 설치'
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
if (-not (Test-Path $VenvPython)) {
    & $py.Exe @($py.Args) -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw '가상환경을 만들지 못했습니다.' }
    Info "가상환경: $Venv"
}
else {
    Info "가상환경 재사용: $Venv"
}
$pipArgs = @('-m', 'pip', 'install', '--upgrade', '--disable-pip-version-check')
if ($PipIndex) { $pipArgs += @('--index-url', $PipIndex) }
& $VenvPython @pipArgs $RepoRoot
if ($LASTEXITCODE -ne 0) {
    throw 'pip install에 실패했습니다. 사내 PyPI 미러를 쓰는 환경이면 -PipIndex를 지정하세요.'
}
Info '패키지 설치 완료'

# ---------------------------------------------------------------- 3. 설정 파일
Step '3/6 설정 파일'
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
# 열 수 있는 폴더를 따로 주지 않으면 보호 폴더로 한정한다. 그래야 일반 파일은 작업구역을 거치지 않고 평소 도구로 다뤄진다.
if (@($OpenRoot | Where-Object { $_ }).Count -eq 0 -and @($ProtectedRoot | Where-Object { $_ }).Count -gt 0) {
    $OpenRoot = @($ProtectedRoot)
    Info '열 수 있는 폴더(OpenRoot)를 보호 폴더와 같게 설정합니다.'
}
$cfg = [ordered]@{
    backend     = 'winsandbox'
    nasca       = $(if ($NascaMcpConfig) { 'mcp' } else { 'mock' })
    runtime_dir = $RuntimeDir
    open_roots  = @($OpenRoot | Where-Object { $_ })
    export_roots = @($ExportRoot | Where-Object { $_ })
}
if ($NascaMcpConfig) { $cfg['nasca_mcp_config'] = (Resolve-Path $NascaMcpConfig).Path }
Save-Config (Join-Path $ConfigDir 'config.json') $cfg '설정(config.json)'

$policy = [ordered]@{
    protected_roots     = @($ProtectedRoot | Where-Object { $_ })
    blocked_mcp_servers = @($BlockedMcpServer | Where-Object { $_ })
}
Save-Config (Join-Path $ConfigDir 'policy.json') $policy '접근 통제 정책(policy.json)'

if (-not $NascaMcpConfig) {
    Warn 'NASCA MCP 설정이 없어 모의 NASCA로 설치했습니다. 실제 DRM은 적용되지 않습니다. -NascaMcpConfig를 주고 -Force로 다시 설치하세요.'
}
if (@($ProtectedRoot | Where-Object { $_ }).Count -eq 0) {
    Warn '보호 폴더(-ProtectedRoot)가 비어 있어 직접 접근 차단 훅이 아무것도 막지 않습니다.'
}
if (@($OpenRoot | Where-Object { $_ }).Count -eq 0) { Warn '열 수 있는 폴더(-OpenRoot)에 제한이 없습니다.' }
if (@($ExportRoot | Where-Object { $_ }).Count -eq 0) { Warn '내보낼 수 있는 폴더(-ExportRoot)에 제한이 없습니다.' }

# ---------------------------------------------------------------- 4. 샌드박스 런타임
Step '4/6 샌드박스 런타임'
if ($SkipRuntime) {
    Warn '-SkipRuntime: 런타임을 만들지 않았습니다. 나중에 runtime\build_runtime.ps1을 실행하세요.'
}
else {
    $buildArgs = @{ Out = $RuntimeDir; HostPython = $VenvPython }
    if ($PipIndex) { $buildArgs['PipIndex'] = $PipIndex }
    if ($PythonEmbedZip) { $buildArgs['EmbedZip'] = $PythonEmbedZip }
    & (Join-Path $RepoRoot 'runtime\build_runtime.ps1') @buildArgs
}

# ---------------------------------------------------------------- 5. 등록
Step '5/6 클라이언트 등록'
if ($RegisterClaude) {
    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $claude) {
        Warn ("claude 명령을 찾지 못했습니다. Claude Code를 설치한 뒤 직접 실행하세요: claude plugin marketplace add `"$RepoRoot`" ; claude plugin install workbench@nasca-mcp")
    }
    else {
        & $claude.Source plugin marketplace add $RepoRoot --scope $ClaudeScope
        if ($LASTEXITCODE -ne 0) { Warn '마켓플레이스 등록이 0이 아닌 코드로 끝났습니다. 이미 등록된 경우일 수 있어 설치를 계속합니다.' }
        & $claude.Source plugin install 'workbench@nasca-mcp' --scope $ClaudeScope
        if ($LASTEXITCODE -ne 0) { Warn 'Claude Code 플러그인 설치에 실패했습니다. claude plugin validate 로 확인하세요.' }
        else { Info 'Claude Code 플러그인 설치 완료. 이 저장소 폴더를 지우면 플러그인이 동작하지 않습니다.' }
    }
}
if ($RegisterCodex) {
    $codex = Get-Command codex -ErrorAction SilentlyContinue
    if (-not $codex) {
        Warn 'codex 명령을 찾지 못했습니다. enforcement\codex\config.toml.example를 ~/.codex/config.toml에 합치세요.'
    }
    else {
        & $codex.Source mcp add workbench -- $VenvPython -m workbench.mcp_server
        if ($LASTEXITCODE -ne 0) { Warn 'Codex 등록에 실패했습니다.' }
        else {
            Info 'Codex 등록 완료.'
            Warn 'Codex 기본 도구 제한 시간은 60초라 첫 workbench_open(샌드박스 기동)에서 시간 초과가 납니다. ~/.codex/config.toml의 [mcp_servers.workbench]에 tool_timeout_sec = 400 을 추가하세요.'
        }
    }
}
if (-not $RegisterClaude -and -not $RegisterCodex) {
    Info '등록을 건너뛰었습니다. -RegisterClaude 또는 -RegisterCodex를 주거나 README의 "클라이언트 연결"을 보세요.'
}

# ---------------------------------------------------------------- 6. 점검
Step '6/6 점검(doctor)'
$doctorArgs = @('-m', 'workbench.doctor')
if ($Selftest) { $doctorArgs += '--selftest' }
& $VenvPython @doctorArgs
$doctorExit = $LASTEXITCODE

Write-Host ''
Write-Host '================ 요약 ================' -ForegroundColor Cyan
Info "가상환경: $Venv"
Info "설정 폴더: $ConfigDir (config.json, policy.json, runtime, audit)"
Info '시스템 영향: 위 두 폴더만 만들었습니다. 서비스, 시작 프로그램, 레지스트리, PATH, 호스트 방화벽, NASCA 설정은 바꾸지 않았고,'
Info '            MCP 서버는 AI 클라이언트가 띄울 때만 실행됩니다. 일반 프로그램의 파일 읽기와 쓰기는 그대로입니다.'
if ($Warnings.Count -gt 0) {
    Write-Host "경고 $($Warnings.Count)건:" -ForegroundColor Yellow
    foreach ($w in $Warnings) { Write-Host "  - $w" -ForegroundColor Yellow }
}
if ($needReboot) { Write-Host '재부팅이 필요합니다(Windows 샌드박스 기능). 재부팅 후 python -m workbench.doctor --selftest 로 확인하세요.' -ForegroundColor Yellow }
if ($doctorExit -ne 0) {
    Write-Host '점검에서 오류가 났습니다. 위 [오류] 줄을 먼저 해결하세요. docs\windows-first-run.md에 막힐 때의 확인 지점이 있습니다.' -ForegroundColor Red
    exit $doctorExit
}
Write-Host '설치를 마쳤습니다. Claude Code를 다시 시작하고 /plugin 에서 workbench가 켜져 있는지 확인하세요.' -ForegroundColor Green
