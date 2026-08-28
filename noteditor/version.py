"""앱이 스스로 "나는 어느 빌드인가"를 말할 수 있게 버전 하나를 정한다.

버전 번호가 소스와 배포 태그 두 군데에 따로 적혀 있으면 반드시 어긋난다. 실제로 한 번
어긋나서, 배포가 반영됐는지를 버전 번호로 확인하지 못하고 다른 신호를 찾아야 했다.
그래서 **깃 태그를 유일한 출처**로 두고 나머지는 거기서 파생시킨다.

빌드된 앱 안에는 깃 저장소가 없으므로, 출처를 순서대로 훑는다:

1. ``NOTEDITOR_VERSION`` 환경변수 — 어디서든 강제로 지정할 때
2. ``noteditor/_version.py`` — 배포 빌드 때 태그에서 새겨 넣는 파일 (버전 관리 대상 아님)
3. ``git describe`` — 개발 중 체크아웃. 태그 이후 커밋 수까지 붙어 나온다
4. 배포 플랫폼이 알려주는 커밋 해시
5. 아무것도 없으면 ``0.0.0+unknown`` — 모르면 모른다고 말한다

거짓 버전보다 ``unknown`` 이 낫다. 아는 척하면 다음에 또 같은 자리에서 속는다.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

UNKNOWN_VERSION = "0.0.0+unknown"

_TAG = re.compile(r"^v?(?P<version>\d+(?:\.\d+)*)$")
_DESCRIBE = re.compile(
    r"^v?(?P<version>\d+(?:\.\d+)*)(?:-(?P<distance>\d+)-g(?P<commit>[0-9a-f]+))?$"
)


def normalize_tag(tag: str) -> str | None:
    """``v0.5.0`` 처럼 생긴 태그만 버전으로 받고 앞의 ``v`` 를 뗀다. 아니면 ``None``."""
    match = _TAG.match((tag or "").strip())
    return match.group("version") if match else None


def version_from_describe(described: str) -> str:
    """``git describe`` 출력 한 줄을 버전 문자열로 바꾼다.

    ``v0.5.0`` → ``0.5.0`` / ``v0.5.0-3-gbf90fcf`` → ``0.5.0+3.gbf90fcf`` 처럼,
    태그 이후 몇 커밋인지가 그대로 남아야 "지금 도는 게 태그 그 자체인지"를 구분할 수 있다.
    """
    text = (described or "").strip()
    dirty = text.endswith("-dirty")
    if dirty:
        text = text[: -len("-dirty")]
    local: list[str] = []
    match = _DESCRIBE.match(text)
    if match is None:
        # 태그가 하나도 없으면 ``--always`` 가 커밋 해시만 준다.
        commit = re.sub(r"[^0-9a-z]", "", text.lower())
        version = "0.0.0"
        local.append(commit or "unknown")
    else:
        version = match.group("version")
        if match.group("distance"):
            local.append(f"{match.group('distance')}.g{match.group('commit')}")
    if dirty:
        local.append("dirty")
    return version + ("+" + ".".join(local) if local else "")


def describe(root: Path | None = None) -> str | None:
    """체크아웃이면 ``git describe`` 결과를, 아니면 ``None`` 을 준다."""
    root = root or Path(__file__).resolve().parent.parent
    try:
        completed = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _stamped_version() -> str | None:
    try:
        from ._version import __version__ as stamped
    except ImportError:
        return None
    return stamped.strip() or None


def resolve_version() -> str:
    """모듈 문서에 적은 순서대로 출처를 훑어 버전 하나를 정한다."""
    override = os.environ.get("NOTEDITOR_VERSION", "").strip()
    if override:
        return override
    stamped = _stamped_version()
    if stamped:
        return stamped
    described = describe()
    if described:
        return version_from_describe(described)
    # Render 같은 배포 플랫폼은 깃 없이 커밋 해시만 환경변수로 알려준다.
    commit = os.environ.get("RENDER_GIT_COMMIT", "").strip()
    if commit:
        return f"0.0.0+{commit[:7]}"
    return UNKNOWN_VERSION
