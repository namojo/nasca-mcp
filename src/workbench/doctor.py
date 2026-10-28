"""첫 실행 점검. 설정, 백엔드, NASCA 어댑터, 접근 통제 정책을 확인하고 선택적으로 전체 흐름을 한 번 돌려 본다.

  python -m workbench.doctor                  설정과 환경 점검
  python -m workbench.doctor --probe-nasca    NASCA MCP를 띄워 도구 이름과 인자를 보여 줌(설정 맞추기용)
  python -m workbench.doctor --selftest       백엔드를 실제로 띄워 임시 문서를 열고 읽고 닫아 본다(NASCA는 쓰지 않음)
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import tempfile
import time
from pathlib import Path

from .config import Settings, machine_config_path
from .errors import WorkbenchError

OK, WARN, ERR = "[OK]  ", "[경고]", "[오류]"


def _lines(settings: Settings, args) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    add = lambda lvl, msg: out.append((lvl, msg))  # noqa: E731

    add(OK if sys.version_info >= (3, 11) else ERR, f"파이썬 {sys.version.split()[0]} (3.11 이상 필요)")
    for mod, pkg in (("mcp", "mcp"), ("docx", "python-docx"), ("openpyxl", "openpyxl"), ("pptx", "python-pptx"),
                     ("PIL", "pillow")):
        try:
            importlib.import_module(mod)
            add(OK, f"라이브러리 {pkg}")
        except ImportError:
            add(ERR, f"라이브러리 {pkg}가 없습니다. pip install {pkg}")

    import shutil

    if shutil.which("python") or shutil.which("python3"):
        add(OK, "훅 실행용 python 명령 확인")
    else:
        add(WARN, "python 명령이 PATH에 없습니다. Claude Code 훅(접근 통제)이 실행되지 않습니다.")

    mpath = machine_config_path()
    add(OK, f"기계 설정 {'적용: ' + str(mpath) if mpath.is_file() else '없음(' + str(mpath) + ')'}")
    add(OK, f"설정: 백엔드={settings.backend}, NASCA={settings.nasca}, 유휴 {settings.idle_ttl_s:.0f}초, "
            f"최대 {settings.max_ttl_s:.0f}초")
    add(OK if settings.open_roots else WARN,
        "열 수 있는 폴더: " + (", ".join(map(str, settings.open_roots)) or "제한 없음(open_roots를 지정하세요)"))
    add(OK if settings.export_roots else WARN,
        "내보낼 수 있는 폴더: " + (", ".join(map(str, settings.export_roots)) or "제한 없음(export_roots를 지정하세요)"))

    if settings.backend == "local":
        add(WARN, "백엔드가 local입니다. 격리가 없는 개발용이며 운영에서는 winsandbox를 쓰세요.")
    elif settings.backend == "winsandbox":
        from .broker.backends.winsandbox import check_environment
        for lvl, msg in check_environment(settings.runtime_dir):
            add({"ok": OK, "warn": WARN, "error": ERR}[lvl], msg)
    else:
        add(ERR, f"알 수 없는 백엔드: {settings.backend}")

    if settings.nasca == "mock":
        add(WARN, "NASCA 어댑터가 mock입니다. 실제 DRM이 적용되지 않습니다. nasca=mcp와 nasca_mcp_config를 설정하세요.")
    elif settings.nasca == "mcp":
        cfg = settings.nasca_mcp_config
        if not cfg or not cfg.is_file():
            add(ERR, f"NASCA MCP 설정 파일이 없습니다: {cfg}")
        else:
            try:
                conf = json.loads(cfg.read_text(encoding="utf-8-sig"))
                missing = [k for k in ("command", "decrypt", "protect") if k not in conf]
                add(ERR if missing else OK, f"NASCA MCP 설정 {cfg}" + (f" (없는 키: {missing})" if missing else ""))
                if args.probe_nasca and not missing:
                    from .nasca.mcp_bridge import McpNasca
                    a = McpNasca(conf)
                    try:
                        tools = a.list_tools()
                        names = {t["name"] for t in tools}
                        for t in tools:
                            add(OK, f"NASCA MCP 도구 {t['name']}({', '.join(t['args'])}) {t['description']}")
                        for role in ("decrypt", "protect"):
                            want = conf[role]["tool"]
                            add(OK if want in names else ERR, f"설정의 {role}.tool '{want}'"
                                + ("" if want in names else "이(가) 서버에 없습니다. 위 도구 이름으로 고치세요."))
                    except WorkbenchError as e:
                        add(ERR, str(e))
                    finally:
                        a.close()
            except (OSError, ValueError) as e:
                add(ERR, f"NASCA MCP 설정을 읽을 수 없습니다({type(e).__name__}).")
    else:
        add(ERR, f"알 수 없는 NASCA 어댑터: {settings.nasca}")

    guard_dir = Path(__file__).resolve().parents[2] / "enforcement" / "hooks"
    if guard_dir.is_dir():
        sys.path.insert(0, str(guard_dir))
        try:
            import guard  # type: ignore

            policy, err = guard.load_policy()
            if err:
                add(ERR, f"접근 통제 정책 오류: {err}")
            elif policy is None:
                add(WARN, "접근 통제 정책(policy.json)이 없습니다. 훅이 아무것도 막지 않습니다.")
            else:
                add(OK, f"접근 통제 정책: 보호 폴더 {len(policy['protected_roots'])}개, 차단 MCP {policy['blocked_mcp_servers']}")
        finally:
            sys.path.remove(str(guard_dir))
    return out


def selftest(settings: Settings) -> list[tuple[str, str]]:
    """백엔드를 실제로 띄워 임시 문서로 열기, 읽기, 수정, 닫기를 한 번 돌린다. NASCA는 쓰지 않는다."""
    from docx import Document

    from .broker.manager import Manager
    from .nasca.mock import MockNasca

    out: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "selftest.docx"
        d = Document()
        d.add_paragraph("작업구역 자체 점검 문서")
        d.save(str(src))
        scoped = Settings.load()
        scoped.open_roots, scoped.export_roots = [], []
        m = Manager(scoped, MockNasca())
        t0 = time.monotonic()
        try:
            m.open(str(src))
            out.append((OK, f"세션 시작과 문서 열기 {time.monotonic() - t0:.1f}초, 격리: {m.session.backend.isolation}"))
            text = m.read("selftest.docx")["text"]
            out.append((OK if "자체 점검" in text else ERR, "격리 환경 안에서 문서 읽기"))
            m.edit("selftest.docx", [{"op": "replace_text", "find": "자체", "replace": "셀프"}])
            out.append((OK, "격리 환경 안에서 문서 수정"))
            m.close(discard=True)
            out.append((OK, "닫기와 평문 삭제"))
        except WorkbenchError as e:
            out.append((ERR, f"{e}"))
        finally:
            m.shutdown()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="workbench-doctor")
    ap.add_argument("--probe-nasca", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    try:
        settings = Settings.load()
    except WorkbenchError as e:
        print(f"{ERR} 설정을 읽을 수 없습니다: {e}")
        return 1
    results = _lines(settings, args)
    if args.selftest:
        results += selftest(settings)
    for lvl, msg in results:
        print(f"{lvl} {msg}")
    errors = sum(1 for lvl, _ in results if lvl == ERR)
    print(f"\n오류 {errors}개, 경고 {sum(1 for lvl, _ in results if lvl == WARN)}개")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
