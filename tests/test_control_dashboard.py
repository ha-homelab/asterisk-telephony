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

    def test_optional_hands_free_pair_accepts_mixed_capability_and_renders_read_only_card(self):
        records = registry()
        old = [dict(item, unique_id=item["unique_id"].replace(":201:", ":203:"),
                    entity_id=item["entity_id"] + "_other") for item in records
               if item["unique_id"].rsplit(":", 1)[1] in module.REQUIRED_KEYS]
        mapping = module.entity_map(records + old, "synthetic")
        views = module.render(mapping)["dashboard.json"]["views"]
        self.assertEqual(len(views[0]["cards"]), 5)
        self.assertEqual(len(views[1]["cards"]), 4)
        card = views[0]["cards"][-1]["cards"][0]
        self.assertEqual(card["entities"], [{"entity": "binary_sensor.renamed_hands_free_enabled"},
                                          {"entity": "sensor.renamed_voice_status"}])
        self.assertFalse(card["show_header_toggle"])

    def test_partial_hands_free_pair_is_rejected_without_requiring_it_on_old_gateway(self):
        original = registry()
        old = [record for record in original if record["unique_id"].rsplit(":", 1)[1] in module.REQUIRED_KEYS]
        self.assertEqual(len(module.entity_map(old, "synthetic")["201"]), 29)
        with self.assertRaises(ValueError):
            module.entity_map(old + [record for record in original if record["unique_id"].endswith(":voice_status")], "synthetic")
