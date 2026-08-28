"""배포 빌드 직전에 태그에서 읽은 버전을 ``noteditor/_version.py`` 로 새긴다.

빌드된 앱 안에는 깃 저장소가 없어서 ``git describe`` 를 쓸 수 없다. 그래서 빌드하는
쪽에서 한 번 확정한 값을 파일로 남겨 둔다. 확정한 버전을 표준 출력으로도 내보내므로,
설치 파일 빌드에 그대로 넘겨 쓰면 앱과 설치 파일이 같은 번호를 갖는다.

사용: ``python -m noteditor.stamp_version [태그]``
"""
from __future__ import annotations

import sys
from pathlib import Path

from .version import UNKNOWN_VERSION, describe, normalize_tag, version_from_describe

TEMPLATE = '''"""빌드할 때 생성되는 파일. 직접 고치지 말 것 — 버전 관리 대상이 아니다."""

__version__ = "{version}"
'''


def resolve(tag: str | None) -> str:
    """태그가 버전 모양이면 그것을, 아니면 체크아웃에서 알아낸 값을 쓴다."""
    normalized = normalize_tag(tag or "")
    if normalized:
        return normalized
    described = describe()
    return version_from_describe(described) if described else UNKNOWN_VERSION


def stamp(version: str, package_dir: Path | None = None) -> Path:
    target = (package_dir or Path(__file__).resolve().parent) / "_version.py"
    target.write_text(TEMPLATE.format(version=version), encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> None:
    arguments = sys.argv[1:] if argv is None else argv
    version = resolve(arguments[0] if arguments else None)
    stamp(version)
    print(version)


if __name__ == "__main__":
    main()
