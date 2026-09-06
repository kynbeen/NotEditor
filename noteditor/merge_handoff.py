"""summary.ai ↔ NotEditor merge/review handoff contracts (versions 1, 2 and 3).

Version 3 splits the review outcome into two independent axes. ``decision`` still
says **how the file is swapped** (``refresh`` / ``merge`` / ``skip``); the new
``change`` says **what actually changed**, which is what decides how much of
summary.ai's eight-step pipeline has to run again:

===============  ==========================================================
``change``       what summary.ai does
===============  ==========================================================
``none``         nothing — the reviewer confirmed the pages look the same
``questions``    re-reads only ``changed_pages`` of the exam PDF and re-uploads
``content``      re-runs from step 1 but keeps the question steps
``both``         re-runs everything (this is also how version 2 is read)
===============  ==========================================================

``changed_pages`` are 1-based page numbers **in the current collection file**.
The review screen already computes them, so summary.ai never recomputes the
comparison — that would risk disagreeing with what the reviewer saw.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .engine import PageRef, PdfComposerError, SourceDocument
from .ranges import format_page_ranges


LEGACY_CONTRACT_VERSION = 1
REVIEW_CONTRACT_VERSION = 2      # collection-started merges and source page review
CONTRACT_VERSION = 3             # review outcome split into decision + change
SUPPORTED_CONTRACT_VERSIONS = (
    LEGACY_CONTRACT_VERSION, REVIEW_CONTRACT_VERSION, CONTRACT_VERSION,
)
SIDECAR_SUFFIX = ".merge.json"

DECISIONS = ("refresh", "merge", "skip")
CHANGES = ("none", "questions", "content", "both")


@dataclass(frozen=True)
class MergePlanPart:
    path: Path
    pages: str


@dataclass(frozen=True)
class RangeHint:
    """족첵에서 이 강의 몫을 자동으로 짚어 보라는 힌트.

    ``lecture`` 는 이 강의의 강의록, ``others`` 는 같은 족첵을 나눠 쓰는 이웃 강의들의
    강의록이다. 이웃을 함께 넣어야 경계가 선다 — :mod:`noteditor.exam_range` 참고.
    """

    lecture: Path
    others: tuple[Path, ...] = ()


@dataclass(frozen=True)
class MergePlan:
    version: int
    mode: str
    title: str
    output_path: Path
    parts: tuple[MergePlanPart, ...]
    input_root: Path | None = None
    reference_path: Path | None = None
    origin: str | None = None
    decision_path: Path | None = None
    range_hint: RangeHint | None = None


def paths_refer_to_same_file(left: Path, right: Path) -> bool:
    left = left.expanduser().resolve()
    right = right.expanduser().resolve()
    try:
        return left.samefile(right)
    except OSError:
        return os.path.normcase(str(left)) == os.path.normcase(str(right))


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
        return True
    except (OSError, ValueError):
        return False


def _absolute_pdf(value: object, label: str, *, must_exist: bool = False) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise PdfComposerError(f"{label}가 없습니다.")
    path = Path(value).expanduser()
    if not path.is_absolute() or path.suffix.lower() != ".pdf":
        raise PdfComposerError(f"{label}는 절대경로인 PDF 파일이어야 합니다.")
    path = path.resolve()
    if must_exist and not path.is_file():
        raise PdfComposerError(f"{label} 파일을 찾을 수 없습니다: {path}")
    return path


def _absolute_json(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise PdfComposerError(f"{label}가 없습니다.")
    path = Path(value).expanduser()
    if not path.is_absolute() or path.suffix.lower() != ".json":
        raise PdfComposerError(f"{label}는 절대경로인 JSON 파일이어야 합니다.")
    return path.resolve()


def _range_hint(value: object) -> RangeHint | None:
    """범위 힌트는 **있으면 좋은 것**이라 조용히 없는 셈 칠 수 있다.

    강의록을 못 읽는다고 합치기 자체를 막지 않는다 — 사용자는 늘 손으로 고를 수 있고,
    자동 제안이 안 되는 것과 창이 안 열리는 것은 무게가 전혀 다르다.
    """
    if not isinstance(value, dict):
        return None
    raw_lecture = value.get("lecture_path")
    if not isinstance(raw_lecture, str) or not raw_lecture.strip():
        return None
    lecture = Path(raw_lecture).expanduser()
    if not lecture.is_absolute() or not lecture.is_file():
        return None
    others: list[Path] = []
    for raw in value.get("other_lecture_paths") or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        candidate = Path(raw).expanduser()
        if candidate.is_absolute() and candidate.is_file():
            others.append(candidate.resolve())
    return RangeHint(lecture.resolve(), tuple(others))


def load_merge_plan(path: str | Path) -> MergePlan:
    plan_path = Path(path).expanduser().resolve()
    if not plan_path.is_file():
        raise PdfComposerError(f"합치기 계획 파일을 찾을 수 없습니다: {plan_path}")
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PdfComposerError(f"합치기 계획 JSON을 읽을 수 없습니다: {exc}") from exc
    if not isinstance(payload, dict):
        raise PdfComposerError("합치기 계획은 JSON 객체여야 합니다.")
    version = payload.get("version")
    if version not in SUPPORTED_CONTRACT_VERSIONS:
        raise PdfComposerError(
            f"지원하지 않는 합치기 계획 판입니다: {payload.get('version')!r} "
            f"(지원: {', '.join(map(str, SUPPORTED_CONTRACT_VERSIONS))})"
        )

    output = _absolute_pdf(payload.get("output_path"), "합치기 계획의 output_path")
    mode = "merge" if version == LEGACY_CONTRACT_VERSION else payload.get("mode")
    if mode not in ("merge", "review"):
        raise PdfComposerError("합치기 계획의 mode는 merge 또는 review여야 합니다.")

    input_root: Path | None = None
    reference_path: Path | None = None
    origin: str | None = None
    decision_path: Path | None = None
    # 판 2 에서 들어온 칸들은 판 3 에도 그대로 있다. `== CONTRACT_VERSION` 으로 물으면
    # 판을 올릴 때마다 옛 판이 조용히 이 블록을 건너뛰어 input_root 검사가 사라진다.
    if version >= REVIEW_CONTRACT_VERSION:
        raw_root = payload.get("input_root")
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise PdfComposerError("판 2 계획에 input_root가 없습니다.")
        input_root = Path(raw_root).expanduser()
        if not input_root.is_absolute() or not input_root.is_dir():
            raise PdfComposerError("input_root는 존재하는 절대경로 디렉터리여야 합니다.")
        input_root = input_root.resolve()
        if mode == "review":
            reference_path = _absolute_pdf(
                payload.get("reference_path"), "비교 계획의 reference_path", must_exist=True
            )
            origin = payload.get("origin")
            if origin not in ("selected", "merged"):
                raise PdfComposerError("비교 계획의 origin은 selected 또는 merged여야 합니다.")
            decision_path = _absolute_json(
                payload.get("decision_path"), "비교 계획의 decision_path"
            )

    range_hint = _range_hint(payload.get("range_hint")) if version >= CONTRACT_VERSION else None

    raw_parts = payload.get("parts")
    if not isinstance(raw_parts, list):
        raise PdfComposerError("합치기 계획의 parts는 목록이어야 합니다.")
    if (version == LEGACY_CONTRACT_VERSION or mode == "review") and not raw_parts:
        raise PdfComposerError("합치기 계획에 원본 PDF가 없습니다.")
    parts: list[MergePlanPart] = []
    for index, raw_part in enumerate(raw_parts, start=1):
        if not isinstance(raw_part, dict):
            raise PdfComposerError(f"합치기 계획의 {index}번째 parts 항목이 올바르지 않습니다.")
        raw_path = raw_part.get("path")
        raw_pages = raw_part.get("pages", "")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise PdfComposerError(f"합치기 계획의 {index}번째 PDF 경로가 없습니다.")
        if not isinstance(raw_pages, str):
            raise PdfComposerError(f"합치기 계획의 {index}번째 pages는 문자열이어야 합니다.")
        source = _absolute_pdf(
            raw_path, f"합치기 계획의 {index}번째 원본", must_exist=True
        )
        if input_root is not None and not path_is_within(source, input_root):
            raise PdfComposerError(
                f"합치기 계획의 {index}번째 원본이 수집함 밖에 있습니다: {source}"
            )
        if paths_refer_to_same_file(source, output):
            raise PdfComposerError("합치기 결과가 원본 PDF를 덮어쓸 수 없습니다.")
        parts.append(MergePlanPart(source, raw_pages.strip()))

    title = payload.get("title", "")
    if title is None:
        title = ""
    if not isinstance(title, str):
        raise PdfComposerError("합치기 계획의 title은 문자열이어야 합니다.")
    return MergePlan(
        version=version,
        mode=mode,
        title=title.strip(),
        output_path=output,
        parts=tuple(parts),
        input_root=input_root,
        reference_path=reference_path,
        origin=origin,
        decision_path=decision_path,
        range_hint=range_hint,
    )


def sidecar_path(output: str | Path) -> Path:
    output_path = Path(output).expanduser().resolve()
    return output_path.with_name(output_path.name + SIDECAR_SUFFIX)


def parts_from_order(
    order: Iterable[dict], sources: Iterable[SourceDocument]
) -> list[dict[str, str]]:
    """Convert the actual UI order without silently changing its meaning.

    Contract v1 stores one compact, ascending range per source block. A source
    appearing in multiple blocks or pages reordered inside a block cannot be
    represented by that schema and must be rejected instead of writing a lie.
    """
    source_by_id = {source.id: source for source in sources}
    refs = [PageRef.from_value(item) for item in order]
    if not refs:
        raise PdfComposerError("선택된 페이지가 없습니다.")

    blocks: list[tuple[SourceDocument, list[int]]] = []
    seen_sources: set[str] = set()
    for ref in refs:
        source = source_by_id.get(ref.document_id)
        if source is None:
            raise PdfComposerError("합치기 계획에 없는 PDF가 결과 순서에 들어 있습니다.")
        if not 0 <= ref.page_index < source.page_count:
            raise PdfComposerError(
                f"{source.name}의 페이지 범위를 벗어났습니다: {ref.page_index + 1}"
            )
        if blocks and blocks[-1][0].id == source.id:
            blocks[-1][1].append(ref.page_index)
            continue
        if source.id in seen_sources:
            raise PdfComposerError(
                "현재 결과 순서는 합치기 인계 규격에 손실 없이 기록할 수 없습니다. "
                f"{source.name} 쪽을 한 구간으로 모은 뒤 다시 저장하세요."
            )
        seen_sources.add(source.id)
        blocks.append((source, [ref.page_index]))

    parts: list[dict[str, str]] = []
    for source, indices in blocks:
        if indices != sorted(set(indices)):
            raise PdfComposerError(
                "현재 결과 순서는 합치기 인계 규격에 손실 없이 기록할 수 없습니다. "
                f"{source.name}의 쪽을 원래 쪽 순서로 정렬한 뒤 다시 저장하세요."
            )
        parts.append({"path": str(source.path.resolve()), "pages": format_page_ranges(indices)})
    return parts


def write_sidecar(
    output: str | Path,
    *,
    parts: list[dict[str, str]],
    noteditor_version: str,
    version: int = CONTRACT_VERSION,
) -> Path:
    output_path = Path(output).expanduser().resolve()
    if not output_path.is_file():
        raise PdfComposerError("결과 PDF가 저장되기 전에는 합치기 사이드카를 쓸 수 없습니다.")
    target = sidecar_path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": version,
        "noteditor_version": str(noteditor_version),
        "output": str(output_path),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "parts": parts,
    }
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}-", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return target


def normalize_changed_pages(value: object) -> list[int]:
    """1-based page numbers, sorted and de-duplicated. Junk entries are dropped.

    A bad entry must not sink the whole decision: the page list only narrows the
    work, and an empty list makes summary.ai fall back to reading the whole file.
    """
    if not isinstance(value, (list, tuple, set)):
        return []
    pages = set()
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number >= 1:
            pages.add(number)
    return sorted(pages)


def write_decision(plan: MergePlan, decision: str, change: str = "both",
                   changed_pages: object = None) -> Path:
    if plan.mode != "review" or plan.decision_path is None or plan.origin is None:
        raise PdfComposerError("비교 계획이 아니어서 갱신 결정을 기록할 수 없습니다.")
    allowed = {"selected": {"refresh", "skip"}, "merged": {"merge", "skip"}}
    if decision not in allowed[plan.origin]:
        raise PdfComposerError(
            f"{plan.origin} 자료에서 허용하지 않는 갱신 결정입니다: {decision}"
        )
    if change not in CHANGES:
        raise PdfComposerError(f"알 수 없는 변경 종류입니다: {change}")
    # `넘어가기` 와 `변화 없음` 은 같은 것을 말한다. 어긋난 조합을 쓰면 읽는 쪽이 어느 쪽이
    # 진짜였는지 알 수 없어 결정 파일을 통째로 버린다.
    if (decision == "skip") != (change == "none"):
        raise PdfComposerError(
            f"`{decision}` 과 `{change}` 는 함께 기록할 수 없습니다."
        )
    if decision == "merge":
        if not plan.output_path.is_file() or not sidecar_path(plan.output_path).is_file():
            raise PdfComposerError("합치기 결과 PDF와 사이드카가 완성되지 않았습니다.")

    target = plan.decision_path
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CONTRACT_VERSION,
        "decision": decision,
        "change": change,
        "changed_pages": normalize_changed_pages(changed_pages),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}-", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return target
