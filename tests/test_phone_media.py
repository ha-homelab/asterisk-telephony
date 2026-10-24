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


def full_media_snapshot(*, active=False):
    data = media_snapshot()
    data["phones"][0]["player"] = {
        "v": 1, "state": "playing" if active else "idle", "reason": None,
        "session_id": "12345678-1234-4234-8234-123456789abc" if active else None,
        "line": "202" if active else None,
        "source_id": "https://media.example.test/song.mp3" if active else None,
        "title": "A song" if active else None, "artist": None, "album": None,
        "position": 12.5 if active else 0, "position_updated_at": 1700000000.0,
        "duration": 120.0 if active else None, "seekable": active,
        "volume_level": 0.5, "is_volume_muted": False,
        "queue_index": 0 if active else None, "queue_length": 1 if active else 0,
        "capabilities": sorted(model.PLAYER_CAPABILITIES),
    }
    return data


class ManagedMediaModelTests(unittest.TestCase):
    def test_optional_player_coexists_with_legacy_media(self):
        for active in (False, True):
            data = full_media_snapshot(active=active)
            self.assertIs(model.inventory(data), data)
        data = full_media_snapshot()
        playing(data)
        self.assertIs(model.inventory(data), data)

    def test_malformed_player_observations_fail_closed(self):
        for change in ({"v": 2}, {"v": True}, {"session_id": "not-a-session"},
                       {"line": "999"}, {"position": float("nan")}, {"duration": float("inf")},
                       {"volume_level": 1.1}, {"is_volume_muted": "true"},
                       {"queue_index": 4}, {"seekable": 1}, {"title": "bad\nvalue"}):
            data = full_media_snapshot(active=True)
            data["phones"][0]["player"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                model.inventory(data)

    def test_url_validation_allows_signed_http_not_credentials_or_protocols(self):
        for url in ("https://media.example.test/song.mp3?token=example", "http://192.0.2.10:8097/stream", "708"):
            self.assertEqual(model.player_source(url, {"708": "Music"}), url)
        for url in ("file:///etc/passwd", "ftp://example.test/a", "999", "https://user:pass@example.test/a",
                    "https://example.test/a#fragment", "https://example.test/a\nheader", "https://example.test:bad/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                model.player_source(url, {})

    def test_transport_cannot_target_replacement_session_or_selected_sibling_line(self):
        phone = full_media_snapshot(active=True)["phones"][0]
        session = phone["player"]["session_id"]
        expected = {"session_id": session, "line": "202"}
        self.assertEqual(model.player_action(phone, "player_pause", expected, {}, "201"), expected)
        for invalid in ({"session_id": None}, {"session_id": session, "line": "201"}):
            with self.assertRaises(ValueError):
                model.player_action(phone, "player_stop", invalid, {}, "201")

    def test_idle_volume_preferences_and_seek_constraints(self):
        phone = full_media_snapshot()["phones"][0]
        self.assertEqual(model.player_action(phone, "player_volume", {"volume_level": .3}, {}, "201"),
                         {"session_id": None, "line": "201", "volume_level": .3})
        with self.assertRaises(ValueError):
            model.player_action(phone, "player_pause", {}, {}, "201")
        phone = full_media_snapshot(active=True)["phones"][0]
        for position in (-1, float("nan"), 121, True):
            with self.assertRaises(ValueError):
                model.player_action(phone, "player_seek", {"session_id": phone["player"]["session_id"], "position": position}, {}, "201")
        phone["player"]["seekable"] = False
        with self.assertRaises(ValueError):
            model.player_action(phone, "player_seek", {"session_id": phone["player"]["session_id"], "position": 10}, {}, "201")


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
