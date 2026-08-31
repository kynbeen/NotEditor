"""summary.ai ↔ NotEditor merge handoff contract (version 1)."""
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


CONTRACT_VERSION = 1
SIDECAR_SUFFIX = ".merge.json"


@dataclass(frozen=True)
class MergePlanPart:
    path: Path
    pages: str


@dataclass(frozen=True)
class MergePlan:
    title: str
    output_path: Path
    parts: tuple[MergePlanPart, ...]


def paths_refer_to_same_file(left: Path, right: Path) -> bool:
    left = left.expanduser().resolve()
    right = right.expanduser().resolve()
    try:
        return left.samefile(right)
    except OSError:
        return os.path.normcase(str(left)) == os.path.normcase(str(right))


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
    if payload.get("version") != CONTRACT_VERSION:
        raise PdfComposerError(
            f"지원하지 않는 합치기 계획 판입니다: {payload.get('version')!r} "
            f"(지원: {CONTRACT_VERSION})"
        )

    raw_output = payload.get("output_path")
    if not isinstance(raw_output, str) or not raw_output.strip():
        raise PdfComposerError("합치기 계획에 output_path가 없습니다.")
    output = Path(raw_output).expanduser()
    if not output.is_absolute() or output.suffix.lower() != ".pdf":
        raise PdfComposerError("output_path는 절대경로인 PDF 파일이어야 합니다.")
    output = output.resolve()

    raw_parts = payload.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
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
        source = Path(raw_path).expanduser()
        if not source.is_absolute() or source.suffix.lower() != ".pdf" or not source.is_file():
            raise PdfComposerError(
                f"합치기 계획의 {index}번째 원본은 존재하는 절대경로 PDF여야 합니다: {source}"
            )
        source = source.resolve()
        if paths_refer_to_same_file(source, output):
            raise PdfComposerError("합치기 결과가 원본 PDF를 덮어쓸 수 없습니다.")
        parts.append(MergePlanPart(source, raw_pages.strip()))

    title = payload.get("title", "")
    if title is None:
        title = ""
    if not isinstance(title, str):
        raise PdfComposerError("합치기 계획의 title은 문자열이어야 합니다.")
    return MergePlan(title.strip(), output, tuple(parts))


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
) -> Path:
    output_path = Path(output).expanduser().resolve()
    if not output_path.is_file():
        raise PdfComposerError("결과 PDF가 저장되기 전에는 합치기 사이드카를 쓸 수 없습니다.")
    target = sidecar_path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CONTRACT_VERSION,
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
