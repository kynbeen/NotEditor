from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf

from pdf_page_composer.page_match import (
    PageMatchError,
    distance,
    fingerprints,
    match_fingerprints,
    match_pages,
)

WIDTH = 640.0
HEIGHT = 480.0


def draw_pattern(page, seed: int) -> None:
    """쪽마다 확실히 달라 보이는 무늬. 4×4 칸을 seed 의 비트로 채운다."""
    cell_width = (WIDTH - 120) / 4
    cell_height = (HEIGHT - 120) / 4
    for index in range(16):
        if not (seed >> index) & 1:
            continue
        column, row = index % 4, index // 4
        rect = pymupdf.Rect(
            60 + column * cell_width, 60 + row * cell_height,
            60 + (column + 1) * cell_width - 8, 60 + (row + 1) * cell_height - 8,
        )
        page.draw_rect(rect, color=(0, 0, 0), fill=(0.1, 0.1, 0.1))


def make_document(path: Path, seeds: list[int | None], *, scale: float = 1.0,
                  pad: float = 0.0, page_scale: float = 1.0) -> Path:
    """``seeds`` 순서대로 쪽을 만든다. ``None`` 은 빈 쪽."""
    document = pymupdf.open()
    for seed in seeds:
        page = document.new_page(width=WIDTH * page_scale, height=HEIGHT * page_scale)
        if seed is None:
            continue
        if scale == 1.0 and pad == 0.0:
            draw_pattern(page, seed)
            continue
        # 같은 무늬를 축소·이동해 그린다 (재조판된 판본 흉내).
        with pymupdf.open() as scratch:
            origin = scratch.new_page(width=WIDTH, height=HEIGHT)
            draw_pattern(origin, seed)
            page.show_pdf_page(
                pymupdf.Rect(pad, pad, pad + WIDTH * scale, pad + HEIGHT * scale), scratch, 0
            )
    document.save(path)
    document.close()
    return path


# 4×4 비트 무늬 12개. 서로 충분히 다르게 고른 값이다.
SEEDS = [0x8001, 0x0FF0, 0x1248, 0x8421, 0xF00F, 0x3C3C,
         0x0180, 0x7E00, 0x1111, 0xAAAA, 0xC300, 0x00FF]


class MatchPagesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.origin = make_document(self.root / "origin.pdf", SEEDS)

    def tearDown(self):
        self.folder.cleanup()

    def match(self, variant: Path):
        with pymupdf.open(self.origin) as source, pymupdf.open(variant) as target:
            return match_pages(source, target)

    def assert_monotonic(self, result):
        matched = result.matched_pairs
        sources = [pair.source_index for pair in matched]
        targets = [pair.target_index for pair in matched]
        self.assertEqual(sources, sorted(sources))
        self.assertEqual(targets, sorted(targets))

    def test_identical_documents_match_one_to_one(self):
        result = self.match(self.origin)
        self.assertEqual(len(result.matched_pairs), len(SEEDS))
        self.assertEqual(result.source_only, ())
        self.assertEqual(result.target_only, ())
        self.assertEqual(result.source_to_target(), {i: i for i in range(len(SEEDS))})

    def test_inserted_pages_become_target_only_gaps(self):
        variant = make_document(
            self.root / "inserted.pdf",
            SEEDS[:3] + [0x0660] + SEEDS[3:8] + [0x9009] + SEEDS[8:],
        )
        result = self.match(variant)
        self.assert_monotonic(result)
        self.assertEqual(result.target_only, (3, 9))
        self.assertEqual(result.source_only, ())
        self.assertEqual(len(result.matched_pairs), len(SEEDS))

    def test_deleted_pages_become_source_only_gaps(self):
        kept = [seed for index, seed in enumerate(SEEDS) if index not in {2, 7}]
        variant = make_document(self.root / "deleted.pdf", kept)
        result = self.match(variant)
        self.assert_monotonic(result)
        self.assertEqual(result.source_only, (2, 7))
        self.assertEqual(result.target_only, ())

    def test_insertion_and_deletion_together(self):
        kept = [seed for index, seed in enumerate(SEEDS) if index != 4]
        variant = make_document(self.root / "mixed.pdf", kept[:6] + [0x0660] + kept[6:])
        result = self.match(variant)
        self.assert_monotonic(result)
        self.assertEqual(result.source_only, (4,))
        self.assertEqual(result.target_only, (6,))
        self.assertEqual(len(result.matched_pairs), len(SEEDS) - 1)

    def test_rescaled_and_remargined_target_still_matches(self):
        variant = make_document(
            self.root / "relaid.pdf", SEEDS, scale=0.75, pad=40.0, page_scale=1.2
        )
        result = self.match(variant)
        self.assertEqual(result.source_to_target(), {i: i for i in range(len(SEEDS))})

    def test_duplicate_page_is_resolved_by_order_not_by_looks(self):
        # 5쪽과 똑같은 쪽을 뒤쪽에 끼워 넣는다. 생김새만 보면 어느 쪽이든 붙을 수 있다.
        variant = make_document(
            self.root / "duplicate.pdf", SEEDS[:9] + [SEEDS[4]] + SEEDS[9:]
        )
        result = self.match(variant)
        self.assert_monotonic(result)
        self.assertEqual(result.source_to_target()[4], 4)
        self.assertEqual(result.target_only, (9,))
        # 똑같은 쪽이 둘이므로 사람이 확인하도록 표시되어야 한다.
        self.assertTrue(any(pair.source_index == 4 for pair in result.uncertain))

    def test_blank_pages_pair_with_blank_pages(self):
        origin = make_document(self.root / "blanks.pdf", [SEEDS[0], None, SEEDS[1]])
        variant = make_document(self.root / "blanks2.pdf", [SEEDS[0], None, SEEDS[1]])
        with pymupdf.open(origin) as source, pymupdf.open(variant) as target:
            result = match_pages(source, target)
        self.assertEqual(result.source_to_target(), {0: 0, 1: 1, 2: 2})

    def test_summary_is_json_friendly(self):
        variant = make_document(self.root / "inserted2.pdf", SEEDS + [0x0660])
        payload = self.match(variant).as_dict()
        self.assertEqual(payload["matched_count"], len(SEEDS))
        self.assertEqual(payload["target_only"], [len(SEEDS)])
        for pair in payload["pairs"]:
            for key in ("distance", "margin"):
                value = pair[key]
                self.assertTrue(value is None or isinstance(value, float), (key, value))
                if isinstance(value, float):
                    self.assertEqual(value, value)  # NaN 아님
                    self.assertNotEqual(abs(value), float("inf"))


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_same_page_at_different_scales_is_close(self):
        origin = make_document(self.root / "a.pdf", SEEDS[:4])
        scaled = make_document(self.root / "b.pdf", SEEDS[:4], scale=0.6, pad=80.0)
        with pymupdf.open(origin) as left, pymupdf.open(scaled) as right:
            first, second = fingerprints(left), fingerprints(right)
        for index in range(4):
            same = distance(first[index], second[index])
            others = [distance(first[index], second[j]) for j in range(4) if j != index]
            self.assertLess(same, 0.35, index)
            self.assertLess(same, min(others), index)

    def test_refuses_documents_that_are_too_large_to_compare(self):
        with pymupdf.open() as document:
            document.new_page(width=WIDTH, height=HEIGHT)
            prints = fingerprints(document)
        many = prints * 700
        with self.assertRaises(PageMatchError):
            match_fingerprints(many, many)


if __name__ == "__main__":
    unittest.main()
