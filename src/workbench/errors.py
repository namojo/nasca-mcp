"""작업구역 공통 예외.

메시지는 모델이 그대로 읽는다. 무엇이 잘못됐는지와 다음에 할 일을 함께 적는다.
"""

from __future__ import annotations


class WorkbenchError(Exception):
    """예상한 실패. 메시지를 모델에게 그대로 보여 줘도 안전하다."""

    def __init__(self, message: str, *, code: str = "error"):
        super().__init__(message)
        self.code = code
