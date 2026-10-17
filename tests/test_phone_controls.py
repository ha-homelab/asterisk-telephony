"""Pure guard/scheduling tests; no Home Assistant or live network needed."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "custom_components/cisco_phone_control" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


model = module("model")
schedule = module("schedule")


def snapshot():
    return {"ami_fresh": True, "dial_allowlist": ["600", "630", "640"], "ring_allowlist": ["640"], "phones": [
        {"id": "201", "lines": ["201", "202"], "online": True, "dnd": False,
         "autoanswer": False, "dnd_pending": False, "channels": [], "alert": None}]}


class InventoryTests(unittest.TestCase):
    def test_alert_requires_exact_identity_kind_line_and_known_state(self):
        value = snapshot()
        alert = {"id": "a" * 32, "kind": "timer", "line": "201", "state": "unknown", "reason": None}
        value["phones"][0]["alert"] = alert
        self.assertEqual(model.inventory(value), value)
        for key, bad in (("id", "unsafe/id"), ("line", "999"), ("kind", "radio"), ("state", "maybe"), ("reason", "guessed answer")):
            with self.subTest(key=key):
                value["phones"][0]["alert"] = {**alert, key: bad}
                with self.assertRaises(ValueError): model.inventory(value)

    def test_automated_answer_refuses_active_alert(self):
        phone = snapshot()["phones"][0]
        phone["channels"] = [{"name": "PJSIP/201-00000001", "line": "201", "state": "Ringing", "uniqueid": "1.2"}]
        phone["alert"] = {"id": "a" * 32, "kind": "alarm", "line": "201", "state": "ringing", "reason": None}
        with self.assertRaises(ValueError): model.owned_channel(phone, "201", answer=True)
        self.assertEqual(model.owned_channel(phone, "201")["uniqueid"], "1.2")

    def test_inventory_and_unique_physical_ownership(self):
        good = snapshot()
        self.assertEqual(model.inventory(good), good)
        other = copy.deepcopy(good["phones"][0])
        other["id"] = "202"
        good["phones"].append(other)
        with self.assertRaises(ValueError):
            model.inventory(good)

    def test_reject_stale_foreign_missing_channel_identity(self):
        for change in ("stale", "foreign", "missing"):
            value = snapshot()
            if change == "stale":
                value["ami_fresh"] = False
            else:
                value["phones"][0]["channels"] = [{"line": "201", "name": "PJSIP/999-00000001" if change == "foreign" else "PJSIP/201-00000001", "uniqueid": "1.2" if change == "foreign" else None, "state": "Up"}]
            with self.assertRaises(ValueError):
                model.inventory(value)

    def test_only_called_ringing_is_answerable(self):
        phone = snapshot()["phones"][0]
        phone["channels"] = [{"name": "PJSIP/201-00000001", "line": "201", "state": "Ring", "uniqueid": "1.2"}]
        with self.assertRaises(ValueError):
            model.owned_channel(phone, "201", answer=True)
        phone["channels"][0]["state"] = "Ringing"
        self.assertEqual(model.owned_channel(phone, "201", answer=True)["uniqueid"], "1.2")
        with self.assertRaises(ValueError):
            model.owned_channel(phone, "202")
        phone["channels"] *= 2
        with self.assertRaises(ValueError):
            model.owned_channel(phone, "201")

    def test_url_never_embeds_credentials_or_paths(self):
        self.assertEqual(model.gateway_url("http://127.0.0.1:18084/"), "http://127.0.0.1:18084")
        for value in ("file:///etc/passwd", "https://user:pass@example.test", "http://example.test/path", "http://example.test/?secret=yes"):
            with self.assertRaises(ValueError):
                model.gateway_url(value)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 19, 7, 0, 1, tzinfo=timezone.utc)
        self.settings = schedule.defaults("201")
        self.start = self.now.timestamp() - 3600

    def test_alarm_claim_once_and_persists_across_restart(self):
        self.settings["alarm_enabled"] = True
        first = schedule.pending(self.settings, "201", self.now, self.start)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0][0], "alarm")
        restored = schedule.validate_settings({"201": copy.deepcopy(self.settings)})["201"]
        self.assertEqual(schedule.pending(restored, "201", self.now + timedelta(seconds=1), self.start), [])

    def test_weekend_and_missed_alarm_do_not_call(self):
        self.settings["alarm_enabled"] = True
        self.assertEqual(schedule.pending(self.settings, "201", self.now - timedelta(days=1), self.start - 86400), [])
        self.assertEqual(schedule.pending(self.settings, "201", self.now + timedelta(seconds=30), self.start), [])
        self.assertIn("missed", self.settings["status"])

    def test_dst_fall_back_does_not_ring_twice(self):
        self.settings.update(alarm_enabled=True, alarm_time="01:30", weekdays=[6])
        first = datetime(2026, 11, 1, 1, 30, 1, tzinfo=ZoneInfo("America/Los_Angeles"), fold=0)
        second = first.replace(fold=1)
        self.assertEqual(len(schedule.pending(self.settings, "201", first, first.timestamp() - 3600)), 1)
        self.assertEqual(schedule.pending(self.settings, "201", second, first.timestamp() - 3600), [])

    def test_timer_freezes_line_and_skips_expired_during_downtime(self):
        schedule.start_timer(self.settings, self.now)
        self.settings["line"] = "202"
        self.settings["timer_destination"] = "701"  # Legacy storage is readable, but no audio is requested.
        due = self.now + timedelta(minutes=5)
        first = schedule.pending(self.settings, "201", due, self.start)
        self.assertEqual(first[0][0], "timer")
        self.assertEqual(first[0][2], "201")
        self.assertEqual(schedule.pending(self.settings, "201", due, self.start), [])
        schedule.start_timer(self.settings, self.now)
        self.assertEqual(schedule.pending(self.settings, "201", due + timedelta(seconds=1), due.timestamp() + 1), [])
        self.assertIn("missed", self.settings["status"])

    def test_disabled_default_and_corrupt_storage(self):
        self.assertFalse(self.settings["alarm_enabled"])
        for key, value in (("alarm_enabled", "true"), ("timer_deadline", float("nan")), ("alarm_time", "99:99"), ("weekdays", [8])):
            invalid = {**self.settings, key: value}
            with self.assertRaises(ValueError):
                schedule.validate_settings({"201": invalid})


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "phone-images"
        self.root.mkdir()
        self.good = self.root / "test.png"
        self.good.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")

    def tearDown(self):
        self.temp.cleanup()

    def test_only_explicit_local_images(self):
        self.assertTrue(model.read_image("test.png", [self.root]).startswith(b"\x89PNG"))
        for path in (str(self.base / "secrets.yaml"), "../phone-images/test.png", "https://example.test/test.png"):
            with self.assertRaises((ValueError, OSError)):
                model.read_image(path, [self.root])

    def test_symlink_file_and_directory_rejected(self):
        (self.root / "linked.png").symlink_to(self.good)
        (self.root / "linked-dir").symlink_to(self.root, target_is_directory=True)
        for path in ("linked.png", "linked-dir/test.png"):
            with self.assertRaises(OSError):
                model.read_image(path, [self.root])

    def test_size_and_magic_rejected(self):
        self.good.write_bytes(b"not an image")
        with self.assertRaises(ValueError):
            model.read_image("test.png", [self.root])
        self.good.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(model.MAX_IMAGE))
        with self.assertRaises(ValueError):
            model.read_image("test.png", [self.root])
