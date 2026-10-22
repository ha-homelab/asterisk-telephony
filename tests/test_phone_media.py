"""Pure radio catalog and identity validation, without HA or network access."""
import copy
import unittest

from test_phone_controls import model, snapshot


def media_snapshot():
    data = snapshot()
    data["dial_allowlist"] += ["708", "709"]
    data["services"] = [
        {"number": "708", "label": "Toddler music", "kind": "radio"},
        {"number": "709", "label": "Lullabies", "kind": "radio"},
        {"number": "630", "label": "Voice assistant", "kind": "assistant"},
    ]
    phone = data["phones"][0]
    phone["capabilities"] = ["media"]
    phone["media"] = {"state": "idle", "source": None, "line": None,
                      "channel": None, "uniqueid": None, "reason": None}
    return data


def playing(data, *, source="708", line="202", uniqueid="123.4", state="playing"):
    phone = data["phones"][0]
    channel = f"PJSIP/{line}-00000001"
    phone["channels"] = [{"name": channel, "uniqueid": uniqueid, "line": line, "state": "Up"}]
    phone["media"] = {"state": state, "source": source, "line": line,
                      "channel": channel, "uniqueid": uniqueid, "reason": None}
    return phone["media"]


class MediaModelTests(unittest.TestCase):
    def test_old_gateway_without_media_remains_valid(self):
        self.assertEqual(model.inventory(snapshot()), snapshot())

    def test_idle_and_owned_secondary_line_are_valid(self):
        data = media_snapshot()
        self.assertIs(model.inventory(data), data)
        playing(data)
        self.assertIs(model.inventory(data), data)

    def test_every_blocked_reason_has_no_active_identity(self):
        for reason in model.MEDIA_REASONS:
            for state in ("blocked", "unknown"):
                data = media_snapshot()
                data["phones"][0]["media"].update(state=state, reason=reason)
                self.assertIs(model.inventory(data), data)

    def test_rejects_malformed_or_foreign_media_evidence(self):
        cases = [
            {"source": "630"}, {"source": "999"}, {"line": "999"},
            {"channel": "PJSIP/201-00000001"}, {"uniqueid": "999.1"},
            {"state": "paused"}, {"reason": "guessed"}, {"extra": "field"},
        ]
        for change in cases:
            with self.subTest(change=change):
                data = media_snapshot()
                playing(data).update(change)
                with self.assertRaises(ValueError):
                    model.inventory(data)
        for reason in ("private caller details", [], None):
            data = media_snapshot()
            data["phones"][0]["media"].update(state="blocked", reason=reason)
            with self.assertRaises(ValueError):
                model.inventory(data)

    def test_rejects_call_ambiguity_or_disconnected_playback(self):
        data = media_snapshot()
        playing(data)
        data["phones"][0]["channels"][0]["state"] = "Ringing"
        with self.assertRaises(ValueError):
            model.inventory(data)
        data["phones"][0]["media"]["state"] = "buffering"
        self.assertIs(model.inventory(data), data)
        extra = copy.deepcopy(data["phones"][0]["channels"][0])
        extra.update(name="PJSIP/201-00000002", line="201", uniqueid="123.5")
        data["phones"][0]["channels"].append(extra)
        with self.assertRaises(ValueError):
            model.inventory(data)

    def test_idle_cannot_retain_a_source_or_identity(self):
        for key in model.MEDIA_IDENTITY:
            data = media_snapshot()
            data["phones"][0]["media"][key] = "708"
            with self.assertRaises(ValueError):
                model.inventory(data)

    def test_sources_are_only_safe_unambiguous_advertised_radio(self):
        data = media_snapshot()
        data["dial_allowlist"] += ["201", "209", "710", "711", "712"]
        data["softphones"] = [{"extension": "209"}]
        data["services"] += [
            {"number": "201", "kind": "radio", "label": "Physical line"},
            {"number": "209", "kind": "radio", "label": "Softphone"},
            {"number": "710", "kind": "radio", "label": "Bad\nlabel"},
            {"number": "999", "kind": "radio", "label": "Not allowed"},
            {"number": "711", "kind": "radio", "label": "Duplicate one"},
            {"number": "711", "kind": "radio", "label": "Duplicate two"},
            {"number": "712", "kind": "diagnostic", "label": "Test signal"},
        ]
        self.assertEqual(model.radio_sources(data), {"708": "Toddler music", "709": "Lullabies"})


if __name__ == "__main__":
    unittest.main()
