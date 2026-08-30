"""사용자가 검토·재정렬할 수 있는 필기 쪽 대응 계획.

자동 매칭은 정확도를 위해 원본·대상 순서를 보존하지만, 사용자가 경고를 확인한 뒤에는 대상 PDF
쪽을 다른 행으로 옮길 수 있다. 이 모듈은 UI가 보낸 값을 신뢰하지 않고 모든 원본·대상 쪽이 정확히
한 번씩 들어 있는지 다시 검증하며, 화면과 두 필기 형식이 같은 행 계약을 쓰게 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from .page_match import MatchResult, PageMatchError, PagePair


class PagePlanError(PageMatchError):
    pass


@dataclass(frozen=True)
class PlanSlot:
    source_index: int | None
    target_index: int | None
    confirmed: bool
    manual: bool = False
    distance: float | None = None
    margin: float | None = None

    @property
    def kind(self) -> str:
        if self.source_index is None:
            return "target_only"
        if self.target_index is None:
            return "source_only"
        return "matched"

    @property
    def needs_confirmation(self) -> bool:
        return not self.confirmed

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_index": self.source_index,
            "target_index": self.target_index,
            "kind": self.kind,
            "confirmed": self.confirmed,
            "needs_confirmation": self.needs_confirmation,
            "manual": self.manual,
            "distance": None if self.distance is None else round(self.distance, 4),
            "margin": None if self.margin is None else round(self.margin, 4),
        }


@dataclass(frozen=True)
class PlanImpact:
    target_pages: tuple[int, ...]
    source_pages: tuple[int, ...]

    @property
    def relationship_count(self) -> int:
        return len(set(self.target_pages) | set(self.source_pages))

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_pages": list(self.target_pages),
            "source_pages": list(self.source_pages),
            "relationship_count": self.relationship_count,
        }


@dataclass(frozen=True)
class PagePlan:
    source_count: int
    target_count: int
    slots: tuple[PlanSlot, ...]

    @classmethod
    def from_match(cls, result: MatchResult, source_count: int, target_count: int) -> "PagePlan":
        slots = tuple(
            PlanSlot(
                pair.source_index,
                pair.target_index,
                confirmed=pair.matched and pair.confident,
                distance=pair.distance,
                margin=pair.margin,
            )
            for pair in result.pairs
        )
        plan = cls(source_count, target_count, slots)
        plan._validate_complete()
        return plan

    @classmethod
    def from_payload(
        cls,
        source_count: int,
        target_count: int,
        payload: Iterable[dict[str, Any]],
        original: MatchResult | None = None,
    ) -> "PagePlan":
        original_pairs = {
            (pair.source_index, pair.target_index): (pair.distance, pair.margin)
            for pair in (original.pairs if original else ())
        }
        original_target_source = {
            pair.target_index: pair.source_index
            for pair in (original.pairs if original else ())
            if pair.target_index is not None
        }
        values = list(payload)
        slots: list[PlanSlot] = []
        target_order: list[int] = []
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                raise PagePlanError(f"{index + 1}번째 쪽 대응 항목이 올바르지 않습니다.")
            source = _optional_index(value.get("source_index"), "원본", index)
            target = _optional_index(value.get("target_index"), "대상", index)
            if source is None and target is None:
                raise PagePlanError("양쪽이 모두 빈 쪽 대응 행은 저장할 수 없습니다.")
            confirmed = value.get("confirmed", False)
            if not isinstance(confirmed, bool):
                raise PagePlanError(f"{index + 1}번째 행의 확인 상태가 올바르지 않습니다.")
            if target is not None:
                target_order.append(target)
            distance, margin = original_pairs.get((source, target), (None, None))
            manual = (
                original is not None
                and (
                    (target is not None and original_target_source.get(target) != source)
                    or (source, target) not in original_pairs
                )
            )
            slots.append(PlanSlot(source, target, confirmed, manual, distance, margin))

        if original is not None and target_order != list(range(target_count)):
            changed_order = {
                target for position, target in enumerate(target_order) if target != position
            }
            slots = [
                replace(slot, manual=True)
                if slot.target_index in changed_order
                else slot
                for slot in slots
            ]

        plan = cls(source_count, target_count, tuple(slots))
        plan._validate_complete()
        return plan

    def _validate_complete(self) -> None:
        if self.source_count < 0 or self.target_count < 0:
            raise PagePlanError("쪽 수가 올바르지 않습니다.")
        sources = [slot.source_index for slot in self.slots if slot.source_index is not None]
        targets = [slot.target_index for slot in self.slots if slot.target_index is not None]
        _validate_indices(sources, self.source_count, "원본")
        _validate_indices(targets, self.target_count, "대상")
        if any(slot.source_index is None and slot.target_index is None for slot in self.slots):
            raise PagePlanError("양쪽이 모두 빈 쪽 대응 행은 저장할 수 없습니다.")

    @property
    def unconfirmed(self) -> tuple[int, ...]:
        return tuple(index for index, slot in enumerate(self.slots) if slot.needs_confirmation)

    @property
    def unconfirmed_labels(self) -> tuple[str, ...]:
        labels = []
        for index in self.unconfirmed:
            slot = self.slots[index]
            sides = []
            if slot.source_index is not None:
                sides.append(f"원본 {slot.source_index + 1}쪽")
            if slot.target_index is not None:
                sides.append(f"새 PDF {slot.target_index + 1}쪽")
            labels.append(" ↔ ".join(sides))
        return tuple(labels)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_count": self.source_count,
            "target_count": self.target_count,
            "slots": [slot.as_dict() for slot in self.slots],
            "unconfirmed_count": len(self.unconfirmed),
        }

    def to_match_result(self) -> MatchResult:
        return MatchResult(tuple(
            PagePair(slot.source_index, slot.target_index, slot.distance, slot.margin)
            for slot in self.slots
        ))

    def move_target(self, from_slot: int, to_slot: int) -> tuple["PagePlan", PlanImpact]:
        if not 0 <= from_slot < len(self.slots) or not 0 <= to_slot < len(self.slots):
            raise PagePlanError("드래그한 쪽의 위치가 범위를 벗어났습니다.")
        if self.slots[from_slot].target_index is None:
            raise PagePlanError("대상 PDF 쪽이 없는 행은 드래그할 수 없습니다.")
        if from_slot == to_slot:
            return self, PlanImpact((), ())

        before_target_order = [
            slot.target_index for slot in self.slots if slot.target_index is not None
        ]
        before_target_source = {
            slot.target_index: slot.source_index
            for slot in self.slots
            if slot.target_index is not None
        }
        before_source_target = {
            slot.source_index: slot.target_index
            for slot in self.slots
            if slot.source_index is not None
        }
        targets = [slot.target_index for slot in self.slots]
        moved = targets.pop(from_slot)
        targets.insert(to_slot, moved)

        rebuilt: list[PlanSlot] = []
        prior_by_pair = {
            (slot.source_index, slot.target_index): slot for slot in self.slots
        }
        for prior, target in zip(self.slots, targets):
            source = prior.source_index
            if source is None and target is None:
                continue
            same = prior_by_pair.get((source, target))
            rebuilt.append(PlanSlot(
                source,
                target,
                confirmed=same.confirmed if same is not None else False,
                manual=(same.manual if same is not None else True),
                distance=same.distance if same is not None else None,
                margin=same.margin if same is not None else None,
            ))

        after_target_order = [slot.target_index for slot in rebuilt if slot.target_index is not None]
        after_target_source = {
            slot.target_index: slot.source_index
            for slot in rebuilt
            if slot.target_index is not None
        }
        after_source_target = {
            slot.source_index: slot.target_index
            for slot in rebuilt
            if slot.source_index is not None
        }
        changed_targets = tuple(sorted(
            target
            for target in range(self.target_count)
            if before_target_source.get(target) != after_target_source.get(target)
            or before_target_order.index(target) != after_target_order.index(target)
        ))
        changed_sources = tuple(sorted(
            source
            for source in range(self.source_count)
            if before_source_target.get(source) != after_source_target.get(source)
        ))
        plan = PagePlan(self.source_count, self.target_count, tuple(rebuilt))
        plan._validate_complete()
        return plan, PlanImpact(changed_targets, changed_sources)


def _optional_index(value: Any, label: str, row: int) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise PagePlanError(f"{row + 1}번째 행의 {label} 쪽 번호는 정수여야 합니다.")
    return value


def _validate_indices(indices: list[int], count: int, label: str) -> None:
    if any(index < 0 or index >= count for index in indices):
        raise PagePlanError(f"{label} 쪽 번호가 범위를 벗어났습니다.")
    expected = list(range(count))
    if sorted(indices) != expected:
        missing = sorted(set(expected) - set(indices))
        duplicate = sorted(index for index in set(indices) if indices.count(index) > 1)
        details = []
        if missing:
            details.append("누락 " + ", ".join(str(index + 1) for index in missing[:8]))
        if duplicate:
            details.append("중복 " + ", ".join(str(index + 1) for index in duplicate[:8]))
        raise PagePlanError(f"{label} 쪽은 모두 한 번씩 포함해야 합니다 ({'; '.join(details)}).")


__all__ = ["PagePlan", "PagePlanError", "PlanImpact", "PlanSlot"]
