"""Actual registry mapping, preservation and refusal tests for the UI renderer."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("render_control_dashboard", Path(__file__).resolve().parents[1] / "scripts/render_control_dashboard.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def registry():
    return [{"config_entry_id": "synthetic", "platform": module.DOMAIN, "unique_id": f"synthetic:201:{key}",
             "entity_id": f"{domain}.renamed_{key}", "disabled_by": None} for key, domain in module.KEY_DOMAINS.items()]


class ControlsDashboardTests(unittest.TestCase):
    def test_preserves_existing_tabs_and_uses_renamed_actual_ids(self):
        baseline = {"title": "Existing", "views": [{"path": "old", "cards": [{"type": "markdown", "content": "Keep me"}]}]}
        original = copy.deepcopy(baseline)
        mapped = module.entity_map(registry(), "synthetic")
        result = module.render(mapped, baseline)
        self.assertEqual(baseline, original)
        self.assertEqual(result["dashboard.json"]["views"][0], original["views"][0])
        self.assertEqual(mapped["201"]["hangup"], "button.renamed_hangup")
        privacy = result["cisco_phone_control_privacy.yaml"]["recorder"]["exclude"]["entities"]
        self.assertEqual(privacy, ["text.renamed_title", "text.renamed_message", "text.renamed_image_path", "sensor.renamed_timer_remaining"])

    def test_incomplete_disabled_foreign_and_duplicate_identity_fail_closed(self):
        for case in ("missing", "disabled", "foreign", "duplicate"):
            records = registry()
            if case == "missing": records.pop()
            elif case == "disabled": records[0]["disabled_by"] = "user"
            elif case == "foreign": records[0]["platform"] = "unrelated"
            else: records.append(copy.deepcopy(records[0]))
            with self.assertRaises(ValueError): module.entity_map(records, "synthetic")

    def test_does_not_overwrite_an_existing_handset_tab(self):
        with self.assertRaises(ValueError):
            module.render(module.entity_map(registry(), "synthetic"), {"views": [{"path": "controls-201"}]})
