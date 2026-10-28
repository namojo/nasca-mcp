"""PowerShell 스크립트는 이 환경(리눅스)에서 실행할 수 없어, 흔한 실수를 정적으로 막는다."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ["install.ps1", "uninstall.ps1", "runtime/build_runtime.ps1", "runtime/start.ps1"]


def strip_noise(text: str) -> str:
    """블록 주석, 줄 주석, 문자열 내용을 지워 괄호 수만 센다."""
    text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    out = []
    for line in text.splitlines():
        line = re.sub(r"'[^']*'", "''", line)
        line = re.sub(r'"(?:`.|[^"`])*"', '""', line)
        line = re.sub(r"#.*$", "", line)
        out.append(line)
    return "\n".join(out)


@pytest.mark.parametrize("rel", SCRIPTS)
def test_utf8_bom_so_windows_powershell_reads_korean(rel):
    assert (ROOT / rel).read_bytes()[:3] == b"\xef\xbb\xbf", "BOM이 없으면 한국어 Windows에서 문자열이 깨진다"


@pytest.mark.parametrize("rel", SCRIPTS)
def test_brackets_balance(rel):
    code = strip_noise((ROOT / rel).read_text(encoding="utf-8-sig"))
    for a, b in ("{}", "()", "[]"):
        assert code.count(a) == code.count(b), f"{rel}: {a}{b} 수가 맞지 않다"


@pytest.mark.parametrize("rel", SCRIPTS)
def test_no_command_argument_concatenation(rel):
    """`Warn '문자' + $x`는 PowerShell에서 +가 별도 인자가 된다. 괄호로 묶어야 한다."""
    text = (ROOT / rel).read_text(encoding="utf-8-sig")
    bad = re.findall(r"^\s*(?:Warn|Info|Step|throw)\s+['\"][^\n]*['\"]\s*\+", text, flags=re.M)
    assert not bad, bad


@pytest.mark.parametrize("rel", SCRIPTS)
def test_does_not_assign_automatic_variables(rel):
    text = (ROOT / rel).read_text(encoding="utf-8-sig")
    assert not re.search(r"^\s*\$(args|input|PSScriptRoot|MyInvocation)\s*[+]?=", text, flags=re.M | re.I)


def test_install_script_wires_to_existing_files_and_parameters():
    install = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")
    build = (ROOT / "runtime/build_runtime.ps1").read_text(encoding="utf-8-sig")
    assert (ROOT / "runtime/build_runtime.ps1").is_file() and "runtime\\build_runtime.ps1" in install
    # install.ps1이 build_runtime.ps1에 넘기는 인자가 실제로 선언돼 있어야 한다
    for key in re.findall(r"\$buildArgs\['(\w+)'\]|\$buildArgs = @\{ ([^}]+) \}", install):
        pass
    passed = set(re.findall(r"\b(Out|HostPython|PipIndex|EmbedZip)\b(?=\s*=|'\])", install))
    declared = set(re.findall(r"\[string\]\$(\w+)", build.split(")")[0] if False else build))
    assert {"Out", "HostPython", "PipIndex", "EmbedZip"} <= declared, declared
    assert passed <= declared


def test_commands_match_the_claude_cli_syntax_we_verified():
    install = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")
    assert "plugin marketplace add" in install and "plugin install 'workbench@nasca-mcp'" in install
    assert "mcp add workbench --" in install
    market = (ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
    assert '"name": "nasca-mcp"' in market
    un = (ROOT / "uninstall.ps1").read_text(encoding="utf-8-sig")
    assert "plugin uninstall 'workbench@nasca-mcp'" in un and "plugin marketplace remove 'nasca-mcp'" in un


SYSTEM_WIDE = [
    r"New-Service", r"Set-Service", r"\bsc\.exe\b", r"Register-ScheduledTask", r"\bschtasks\b", r"HKLM:", r"HKCU:",
    r"New-ItemProperty", r"Set-ItemProperty", r"SetEnvironmentVariable", r"\bsetx\b", r"\breg\s+add\b",
    r"Set-NetFirewall", r"New-NetFirewallRule", r"netsh", r"Startup", r"\\Run\b", r"fltmc", r"sc create",
    r"Set-ExecutionPolicy", r"Add-MpPreference", r"Set-MpPreference", r"bcdedit", r"(?-i:\bDisable-[A-Z]\w+)", r"Stop-Service",
]


@pytest.mark.parametrize("rel", ["install.ps1", "uninstall.ps1", "runtime/build_runtime.ps1"])
def test_host_scripts_make_no_system_wide_changes(rel):
    """설치와 제거, 런타임 빌드는 서비스, 시작 프로그램, 레지스트리, PATH, 호스트 방화벽을 건드리지 않는다."""
    raw = (ROOT / rel).read_text(encoding="utf-8-sig")
    code = re.sub(r"<#.*?#>", "", raw, flags=re.S)
    code = "\n".join(re.sub(r"(?<!['\"])#.*$", "", l) for l in code.splitlines())
    hits = [pat for pat in SYSTEM_WIDE if re.search(pat, code, flags=re.I)]
    assert not hits, f"{rel}: 시스템 전체에 영향을 주는 명령 {hits}"


def test_only_the_guest_script_touches_a_firewall_and_it_is_not_run_on_the_host():
    """방화벽 잠금은 샌드박스 안(start.ps1)에서만 일어난다. 호스트 스크립트는 start.ps1을 실행하지 않고 복사만 한다."""
    start = (ROOT / "runtime/start.ps1").read_text(encoding="utf-8-sig")
    assert "Set-NetFirewallProfile" in start
    for rel in ("install.ps1", "uninstall.ps1"):
        assert "start.ps1" not in (ROOT / rel).read_text(encoding="utf-8-sig")
    from workbench.broker.backends.winsandbox import build_wsb
    assert "start.ps1" in build_wsb("a", "b"), "start.ps1은 .wsb의 LogonCommand(게스트 안)로만 실행된다"


def test_sandbox_feature_is_only_enabled_when_explicitly_asked():
    text = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")
    i = text.index("Enable-WindowsOptionalFeature")
    assert "elseif ($EnableSandboxFeature)" in text[:i] and text.count("Enable-WindowsOptionalFeature") == 1


def test_install_scopes_and_defaults_protect_ordinary_files():
    text = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")
    assert "ValidateSet('user', 'project', 'local')" in text and "--scope $ClaudeScope" in text
    assert "$OpenRoot = @($ProtectedRoot)" in text
