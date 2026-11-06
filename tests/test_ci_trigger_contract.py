"""Keep one PR validation path while retaining main and tag qualification."""

from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class CITriggerContractTests(unittest.TestCase):
    def test_contracts_validate_prs_main_and_all_tags_without_path_filters(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text())
        # PyYAML treats the YAML 1.1 word "on" as a boolean mapping key.
        events = workflow.get("on", workflow.get(True))
        self.assertEqual(set(events), {"push", "pull_request"})
        self.assertIsNone(events["pull_request"])
        self.assertEqual(events["push"], {"branches": ["main"], "tags": ["**"]})


if __name__ == "__main__":
    unittest.main()
