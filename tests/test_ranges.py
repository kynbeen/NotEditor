import unittest

from pdf_page_composer.ranges import PageRangeError, format_page_ranges, parse_page_ranges


class PageRangeTests(unittest.TestCase):
    def test_parses_ranges_in_typed_order_without_duplicates(self):
        self.assertEqual(parse_page_ranges("3, 1-2, 2, 5", 5), [2, 0, 1, 4])

    def test_blank_means_no_pages(self):
        self.assertEqual(parse_page_ranges("  ", 8), [])

    def test_rejects_invalid_or_out_of_bounds_ranges(self):
        for value in ("0", "4-2", "1,,2", "nine", "6"):
            with self.subTest(value=value), self.assertRaises(PageRangeError):
                parse_page_ranges(value, 5)

    def test_formats_compact_ranges(self):
        self.assertEqual(format_page_ranges([4, 0, 1, 2, 7]), "1-3, 5, 8")


if __name__ == "__main__":
    unittest.main()
