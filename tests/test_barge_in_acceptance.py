import unittest

from talos.voice.diagnostics.barge_in_acceptance import (
    AcceptanceCaseResult,
    summarize_results,
)


class AcceptanceSummaryTests(unittest.TestCase):
    def test_reports_recall_and_false_candidates(self):
        results = [
            AcceptanceCaseResult("near", "near_end", True, True, True, 0.9, 1),
            AcceptanceCaseResult("double", "double_talk", True, False, False, 0.4, 0),
            AcceptanceCaseResult("far", "far_end", False, False, True, 0.1, 0),
            AcceptanceCaseResult("noise", "noise", False, True, False, 0.8, 1),
        ]
        summary = summarize_results(results)
        self.assertEqual(summary["interruption_recall"], 0.5)
        self.assertEqual(summary["false_candidate_count"], 1)
        self.assertEqual(summary["passed"], 2)
        self.assertEqual(summary["failed"], 2)


if __name__ == "__main__":
    unittest.main()
