from __future__ import annotations

import argparse

from .app import run


def main() -> None:
    parser = argparse.ArgumentParser(description="필요한 PDF 쪽을 골라 하나로 조합합니다.")
    parser.add_argument("--debug", action="store_true", help="개발자 도구와 디버그 로그를 켭니다.")
    args = parser.parse_args()
    run(debug=args.debug)


if __name__ == "__main__":
    main()
