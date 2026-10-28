#!/usr/bin/env python3
"""Claude Code SessionStart 훅. 보호 폴더 정책을 사실 진술로 모델 컨텍스트에 넣고, 정책 오류는 사용자에게 한 번 알린다.

명령문으로 쓰면 프롬프트 주입 방어에 걸릴 수 있어 사실만 적는다. 이 문구는 안내일 뿐이고 강제는 guard.py와 관리형 설정이 맡는다.
정책 파일이 없으면 아무것도 출력하지 않는다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guard import load_policy, log_error  # noqa: E402


def emit(obj: dict) -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(json.dumps(obj, ensure_ascii=False))


def main() -> int:
    try:
        policy, err = load_policy()
        if err:
            emit({"systemMessage": f"[작업구역] {err}. 보호 폴더 차단이 꺼진 상태입니다. python -m workbench.doctor 로 확인하세요."})
            return 0
        if not policy or not policy["protected_roots"]:
            return 0
        roots = ", ".join(policy["protected_roots"])
        text = (
            f"이 PC에는 NASCA DRM 보호 폴더가 지정돼 있다: {roots}. "
            "보호 폴더의 문서는 workbench MCP 서버의 workbench_open 도구로 격리 작업구역에 열고, "
            "workbench_read, workbench_edit, workbench_view_image 등으로 읽고 고친다. "
            "보호 폴더 밖의 일반 파일은 평소처럼 Read, Edit, Write, Bash로 다루며 작업구역과 무관하다. "
            "보호 폴더에 대한 Read, Grep, Glob, Bash 같은 직접 접근과 원본 NASCA MCP 호출은 훅이 차단한다. "
            "작업구역의 복호화된 사본은 workbench_export로 DRM 재보호되어 나갈 때까지만 존재하고, "
            "workbench_close 또는 시간 초과로 삭제된다. 작업이 막히면 우회하지 않고 사용자에게 알린다."
        )
        emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}})
    except Exception as e:  # noqa: BLE001  시작 훅의 오류가 세션을 방해하면 안 된다
        log_error(f"session_start 오류 {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
