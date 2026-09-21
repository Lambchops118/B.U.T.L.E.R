from __future__ import annotations

import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.request_classifier import classify_request


class RequestClassifierTests(unittest.TestCase):
    def test_explicit_mode_overrides_rules(self) -> None:
        decision = classify_request("what is a PCB?", requested_mode="background")

        self.assertEqual(decision.mode, "background")

    def test_simple_explanatory_kicad_question_stays_foreground(self) -> None:
        decision = classify_request("What is KiCad?")

        self.assertEqual(decision.mode, "foreground")

    def test_kicad_work_becomes_background(self) -> None:
        decision = classify_request("Create a KiCad schematic and route the PCB.")

        self.assertEqual(decision.mode, "background")

    def test_bare_kicad_mentions_do_not_become_background_jobs(self) -> None:
        decision = classify_request("kicad kicad kicad")

        self.assertEqual(decision.mode, "foreground")

    def test_need_work_done_on_kicad_project_becomes_background(self) -> None:
        decision = classify_request("i need work done on a kicad project")

        self.assertEqual(decision.mode, "background")

    def test_multi_step_work_becomes_background(self) -> None:
        decision = classify_request("Research the repo and generate a migration plan.")

        self.assertEqual(decision.mode, "background")

    def test_background_status_question_uses_status_mode(self) -> None:
        decision = classify_request("any updates on that background process")

        self.assertEqual(decision.mode, "status")

    def test_background_definition_stays_foreground(self) -> None:
        decision = classify_request("define the word background")

        self.assertEqual(decision.mode, "foreground")

    def test_background_policy_question_stays_foreground(self) -> None:
        decision = classify_request(
            "what jobs do you schedule in the background and which do you schedule in the foreground"
        )

        self.assertEqual(decision.mode, "foreground")

    def test_explicit_background_work_request_becomes_background(self) -> None:
        decision = classify_request("run this in the background")

        self.assertEqual(decision.mode, "background")

    def test_kicad_loading_question_uses_status_mode(self) -> None:
        decision = classify_request("is kicad stuck loading or something")

        self.assertEqual(decision.mode, "status")

    def test_job_work_request_does_not_become_status(self) -> None:
        decision = classify_request("create a scheduled job for watering plants")

        self.assertNotEqual(decision.mode, "status")


if __name__ == "__main__":
    unittest.main()
