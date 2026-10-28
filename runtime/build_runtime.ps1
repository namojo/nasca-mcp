<#
Windows Sandbox에 읽기 전용으로 붙일 런타임을 만든다. 호스트에서 한 번만 실행한다.
  powershell -ExecutionPolicy Bypass -File runtime\build_runtime.ps1
  powershell -ExecutionPolicy Bypass -File runtime\build_runtime.ps1 -PipIndex https://사내미러/simple
  (install.ps1이 이 스크립트를 대신 실행해 준다)

결과 폴더(기본 %LOCALAPPDATA%\workbench\runtime):
  python\          내장 파이썬(설치 없이 실행됨)
  site-packages\   python-docx, openpyxl, python-pptx, pillow와 workbench 에이전트
  start.ps1        샌드박스 로그온 스크립트
#>
param(
    [string]$Out = (Join-Path $env:LOCALAPPDATA 'workbench\runtime'),
    [string]$PythonVersion = '3.12.8',
    [string]$PipIndex = '',
    [string]$HostPython = 'python',  # 라이브러리를 내려받을 때 쓰는 파이썬(pip가 있는 것)
    [string]$EmbedZip = ''           # 인터넷 없이 쓸 내장 파이썬 zip 경로(없으면 python.org에서 내려받음)
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$py   = Join-Path $Out 'python'
$sp   = Join-Path $Out 'site-packages'
New-Item -ItemType Directory -Force -Path $py, $sp | Out-Null

# 1) 내장 파이썬
$zip = if ($EmbedZip) { $EmbedZip } else { Join-Path $env:TEMP "python-$PythonVersion-embed-amd64.zip" }
if (-not (Test-Path $zip)) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12   # Windows PowerShell 5.1 기본값은 TLS 1.2가 꺼져 있을 수 있다
    Invoke-WebRequest "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" -OutFile $zip
}
Expand-Archive -Path $zip -DestinationPath $py -Force
$ver = ($PythonVersion.Split('.')[0..1]) -join ''
Set-Content -Path (Join-Path $py "python$ver._pth") -Value "python$ver.zip`n.`n..\site-packages" -Encoding ascii

# 2) 라이브러리(내장 파이썬과 맞는 wheel만)
$pipArgs = @('-m', 'pip', 'install', '--target', $sp, '--only-binary=:all:', '--platform', 'win_amd64',
             '--python-version', $ver, '--implementation', 'cp', '--upgrade',
             'python-docx', 'openpyxl', 'python-pptx', 'pillow')
if ($PipIndex) { $pipArgs += @('--index-url', $PipIndex) }
& $HostPython @pipArgs
if ($LASTEXITCODE -ne 0) { throw 'pip install 실패. 사내 미러를 쓰려면 -PipIndex를 지정하세요.' }

# 3) 에이전트 코드(호스트 중개 계층 코드는 넣지 않는다)
$dst = Join-Path $sp 'workbench'
New-Item -ItemType Directory -Force -Path (Join-Path $dst 'agent') | Out-Null
Copy-Item (Join-Path $repo 'src\workbench\__init__.py'), (Join-Path $repo 'src\workbench\errors.py') $dst -Force
Copy-Item (Join-Path $repo 'src\workbench\agent\*.py') (Join-Path $dst 'agent') -Force
Copy-Item (Join-Path $PSScriptRoot 'start.ps1') $Out -Force

# 4) 확인
& (Join-Path $py 'python.exe') -c "import workbench.agent.server; print('런타임 확인 완료')"
if ($LASTEXITCODE -ne 0) { throw '런타임 import 확인 실패' }
Write-Host "완료: $Out"
