from __future__ import annotations

import argparse
import traceback

from .app import configure_logging, run


def main() -> None:
    parser = argparse.ArgumentParser(description="NotEditor 데스크톱 앱을 실행합니다.")
    parser.add_argument("--debug", action="store_true", help="개발자 도구와 디버그 로그를 켭니다.")
    parser.add_argument(
        "--open-plan",
        metavar="HANDOFF_JSON",
        help="summary.ai 합치기 계획을 열어 원본·쪽 선택·저장 경로를 적용합니다.",
    )
    args = parser.parse_args()
    log_path = configure_logging()
    try:
        run(debug=args.debug, open_plan=args.open_plan)
    except Exception as exc:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(traceback.format_exc())
        try:
            from tkinter import messagebox

            messagebox.showerror(
                "NotEditor",
                "앱을 시작하지 못했습니다. 서버를 따로 실행할 필요는 없습니다.\n\n"
                f"오류: {exc}\n\n진단 기록: {log_path}",
            )
        except Exception:
            pass
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
