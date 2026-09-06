"""Quality review applies to confident matches and every interactive export path."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from noteditor.alignment import Alignment
from noteditor.app import ComposerApi
from noteditor.page_match import MatchResult, PagePair
from noteditor.transfer_plan import TransferInspection
from noteditor.web import HandwritingExportRequest, _export_handwriting

POINTS_PER_MM = 72 / 25.4


def fit(residual_mm=30.2, clipped_mm=.28, aspect_scale=1):
    return Alignment(1, 0, 0, 2, residual_mm * POINTS_PER_MM,
                     100 * POINTS_PER_MM, aspect_scale, 0, clipped_mm * POINTS_PER_MM)


def inspection(alignment):
    return TransferInspection('old.sdocx', 'new.pdf', 2, 2, 2, 'old.pdf', 100,
                              source_page_count=2, mode='aligned', alignment=alignment,
                              match=MatchResult((PagePair(0, 0, .01, .9), PagePair(1, 1, .01, .9))))


class AlignmentQualityTests(unittest.TestCase):
    def test_thresholds_use_unrounded_measurements(self):
        for residual, clipped, aspect, required in (
            (30.2, .28, 1, False), (50, 4.999, 1, False),
            (50.0001, 0, 1, True), (0, 5, 1, True), (0, 0, 2, True),
        ):
            with self.subTest(residual=residual, clipped=clipped, aspect=aspect):
                value = fit(residual, clipped, aspect)
                self.assertEqual(value.requires_confirmation, required)
                self.assertEqual(value.as_dict()['requires_confirmation'], required)

    def test_confident_pairs_need_review_only_when_alignment_quality_is_low(self):
        safe = inspection(fit())
        poor = replace(safe, alignment=fit(61))
        self.assertEqual(safe.as_dict()['plan']['unconfirmed_count'], 0)
        self.assertEqual(poor.as_dict()['plan']['unconfirmed_count'], 2)
        self.assertTrue(all(s['needs_confirmation'] for s in poor.as_dict()['plan']['slots']))
        self.assertTrue(all(p.confident for p in poor.match.pairs))

    def test_absent_alignment_does_not_require_quality_review(self):
        self.assertEqual(inspection(None).as_dict()['plan']['unconfirmed_count'], 0)

    def test_desktop_and_web_exports_require_explicit_review(self):
        with tempfile.TemporaryDirectory() as directory:
            api = ComposerApi()
            self.addCleanup(api._close)
            api._handwriting_source = Path(directory) / 'old.sdocx'
            api._handwriting_target = Path(directory) / 'new.pdf'
            output = Path(directory) / 'result.sdocx'
            poor = inspection(fit(61))
            rows = poor.as_dict()['plan']['slots']
            with patch.object(api, '_inspection', return_value=poor):
                for web in (False, True):
                    def export(plan, approved=False):
                        if web:
                            return _export_handwriting(api, HandwritingExportRequest(
                                page_plan=plan, allow_unconfirmed=approved), output)
                        response = api.transfer_handwriting_to_path(str(output), plan, approved)
                        if not response['ok']:
                            raise ValueError(response['error'])
                        return response['result']

                    with self.subTest(web=web), patch(
                        'noteditor.web.transfer_handwriting' if web else 'noteditor.app.transfer_handwriting',
                        return_value={'path': str(output)},
                    ) as transfer:
                        for plan in (None, rows):
                            with self.assertRaisesRegex(ValueError, '확인'):
                                export(plan)
                        transfer.assert_not_called()
                        export(rows, True)
                        export([dict(row, confirmed=True) for row in rows])
                        self.assertEqual(transfer.call_count, 2)
                        if not web:
                            with self.assertRaisesRegex(ValueError, '확인'):
                                export([0, 1])


if __name__ == '__main__':
    unittest.main()
