# Windows Sandbox 안에서 로그온 직후 실행된다. 방화벽을 잠그고 작업구역 에이전트를 연다.
# 실패하면 호스트가 읽을 수 있도록 C:\wbcontrol\guest.log에 이유를 남긴다. 문서 내용은 기록하지 않는다.
$ErrorActionPreference = 'Stop'
$ctl = 'C:\wbcontrol'
$rt  = 'C:\wbruntime'
try {
    $cfg = Get-Content "$ctl\session.json" -Raw | ConvertFrom-Json

    # 1) 네트워크 잠금: 호스트 게이트웨이에서 오는 에이전트 포트 연결만 받고, 나가는 연결은 전부 막는다.
    $gw = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Sort-Object RouteMetric | Select-Object -First 1).NextHop
    Set-NetFirewallProfile -Profile Domain, Public, Private -Enabled True `
        -DefaultInboundAction Block -DefaultOutboundAction Block
    New-NetFirewallRule -DisplayName 'workbench-agent-in' -Direction Inbound -Protocol TCP `
        -LocalPort $cfg.port -RemoteAddress $gw -Action Allow | Out-Null

    # 2) 에이전트 시작(읽기 전용 런타임에서). 작업 파일은 게스트 로컬 디스크에만 놓인다.
    New-Item -ItemType Directory -Force -Path 'C:\wbwork' | Out-Null
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & "$rt\python\python.exe" -m workbench.agent `
        --root 'C:\wbwork' --host 0.0.0.0 --port $cfg.port `
        --token-file "$ctl\token.txt" --handshake-file "$ctl\agent.json" `
        --lease-s $cfg.lease_s --max-ttl-s $cfg.max_ttl_s --vm-shutdown
}
catch {
    "$(Get-Date -Format s) start.ps1 실패: $($_.Exception.Message)" | Out-File -FilePath "$ctl\guest.log" -Append -Encoding utf8
    Start-Sleep -Seconds 5
}
