"""모델에게 작업구역의 규칙과 현재 상태를 알리는 문구.

규칙을 말로 전하는 것은 부탁일 뿐이고, 실제 강제는 격리 환경·도구 표면·훅이 맡는다.
그래도 모델이 규칙을 알아야 불필요한 우회 시도와 오류 재시도가 줄어든다.
MCP 서버 지침은 Claude Code에서 4,096자가 넘으면 잘리므로 짧게 유지한다.
"""

from __future__ import annotations

RULES = [
    "작업구역 안의 문서는 복호화된 평문이다. 원문을 작업구역 밖으로 옮기지 않고, 답변에는 필요한 만큼만 인용한다.",
    "보호 문서는 workbench_open으로만 연다. Read, Bash, 다른 MCP 등 어떤 경로로도 직접 열지 않는다.",
    "읽기와 수정은 workbench_* 도구로만 한다. 수정은 작업구역 안의 사본에만 적용되고 원본 보호 파일은 그대로다.",
    "결과는 workbench_export로만 꺼낸다. 내보낼 때 DRM으로 다시 보호된다.",
    "내보내지 않은 수정은 세션이 끝나면 사라진다. 세션은 유휴 시간 또는 최대 수명이 지나면 자동 종료된다.",
    "작업이 끝나면 workbench_close를 부른다. 차단 응답이 오면 우회하지 말고 사용자에게 알린다.",
    "작업구역은 workbench_open으로 연 문서와 workbench_create로 만든 문서만 다룬다. 보호 폴더 밖의 일반 파일은 평소처럼 "
    "Read, Edit, Write, Bash로 다루며, workbench_*는 호스트 파일을 읽거나 지우거나 덮어쓰지 않는다(열 때 읽고, 내보낼 때만 쓴다).",
]

SERVER_INSTRUCTIONS = """\
이 서버는 NASCA DRM으로 보호된 사내 문서를 격리된 작업구역(샌드박스)에서 읽고 쓰는 유일한 경로다.

작업 흐름: workbench_open(복호화해 작업구역에 넣음) → workbench_outline/read/view_image(읽기) → \
workbench_edit/create(수정) → workbench_export(DRM 재보호해 꺼냄) → workbench_close(평문 삭제).
한 번 연 문서는 닫을 때까지 복호화된 상태로 남으므로 같은 문서를 다시 열 필요가 없다. 읽을 때는 outline으로 구조를 \
본 뒤 필요한 구간만 read하면 토큰이 적게 든다. 그림은 view_image로 직접 본다.

규칙:
""" + "\n".join(f"{i}. {r}" for i, r in enumerate(RULES, start=1)) + """

도구 결과 끝의 [작업구역] 줄은 현재 상태다. 남은 시간과 미반출 수정 개수를 확인한다."""

assert len(SERVER_INSTRUCTIONS) < 4096


def _mins(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 3600}시간 {s % 3600 // 60}분" if s >= 3600 else f"{s // 60}분"


def footer(status: dict) -> str:
    if not status.get("active"):
        return "[작업구역] 열린 세션 없음"
    dirty = sum(1 for f in status["files"] if f["dirty"])
    return (f"[작업구역] 파일 {len(status['files'])}개, 미반출 수정 {dirty}개, "
            f"유휴 종료까지 {_mins(status['idle_remaining_s'])}, 최대 {_mins(status['lifetime_remaining_s'])} 남음")


def render(status: dict) -> str:
    if not status.get("active"):
        end = status.get("last_end")
        why = f"\n직전 세션은 {end['reason']}로 종료됐고 평문은 삭제됐다." if end else ""
        return "열려 있는 작업구역이 없다. workbench_open으로 문서를 열면 새 작업구역이 시작된다." + why
    lines = [
        f"세션 {status['session']} | 격리: {status['isolation']} | NASCA 어댑터: {status['nasca']}",
        f"남은 시간: 유휴 {_mins(status['idle_remaining_s'])}, 최대 {_mins(status['lifetime_remaining_s'])}",
        f"파일 {len(status['files'])}개:",
    ]
    for f in status["files"]:
        flags = []
        if f["dirty"]:
            flags.append("수정됨·미반출")
        if f.get("derived"):
            flags.append("문서에서 꺼낸 그림")
        lines.append(f"  - {f['name']} ({f['kind']}, {f['size']:,}바이트{', ' + ', '.join(flags) if flags else ''})")
    c = status["counters"]
    lines.append(f"카운터: 복호화 {c['decrypts']}회, 재보호 {c['protects']}회, 읽기 {c['reads']}회, 수정 {c['edits']}회")
    lines.append("규칙:")
    lines.extend(f"  {i}. {r}" for i, r in enumerate(RULES, start=1))
    return "\n".join(lines)
