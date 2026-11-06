"""Offline contract tests for the published OpenAI Responses usage shape."""
import copy
import unittest

from ai_lab.contracts import LabError
from ai_lab.phase2_policy import normalize_usage, usage_cost, MODEL


def published_usage():
    return {
        "input_tokens": 8438,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens": 398,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 8836,
    }


class PublishedResponsesUsageTests(unittest.TestCase):
    def test_current_documented_usage_accepts_cache_write_tokens(self):
        usage = published_usage()
        self.assertEqual(normalize_usage(usage), {
            "input_tokens": 8438, "output_tokens": 398, "total_tokens": 8836
        })
        self.assertEqual(usage_cost(MODEL, usage), 8438 * 2 + 398 * 8)

    def test_optional_ttl_breakdown_is_non_billable_metadata(self):
        usage = published_usage()
        usage["input_tokens_details"]["cache_write_tokens"] = 100
        usage["input_tokens_details"]["cache_write_tokens_details"] = {
            "ttl_12h_tokens": 100, "ttl_30m_tokens": 0
        }
        self.assertEqual(normalize_usage(usage)["input_tokens"], 8438)
        self.assertEqual(usage_cost(MODEL, usage), 8438 * 2 + 398 * 8)

    def test_unexpected_or_invalid_nested_metadata_rejected(self):
        base = published_usage()
        mutations = [
            ("input_tokens_details", {"cached_tokens": 0, "unknown": 1}),
            ("input_tokens_details", {"cached_tokens": True}),
            ("input_tokens_details", {"cached_tokens": 0, "cache_write_tokens": -1}),
            ("input_tokens_details", {"cached_tokens": 0, "cache_write_tokens": 9000}),
            ("input_tokens_details", {"cached_tokens": 0, "cache_write_tokens_details": {"unknown": 1}}),
            ("input_tokens_details", {"cached_tokens": 0, "cache_write_tokens_details": {"ttl_30m_tokens": True}}),
            ("output_tokens_details", {"reasoning_tokens": False}),
            ("output_tokens_details", {"reasoning_tokens": 399}),
            ("output_tokens_details", {"reasoning_tokens": 0, "unknown": 1}),
        ]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                sample = copy.deepcopy(base)
                sample[field] = value
                with self.assertRaisesRegex(LabError, "^PHASE2_USAGE_INVALID$"):
                    normalize_usage(sample)

    def test_parent_usage_accounting_stays_strict(self):
        for key, value in (("input_tokens", True), ("output_tokens", -1),
                           ("total_tokens", 8835), ("total_tokens", 2 ** 50)):
            with self.subTest(key=key, value=value):
                usage = published_usage()
                usage[key] = value
                with self.assertRaises(LabError):
                    normalize_usage(usage)


if __name__ == "__main__":
    unittest.main()
