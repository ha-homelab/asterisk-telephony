"""Physical volume contracts and real HA dispatch; no phone/network access."""
import copy
import unittest
from unittest.mock import AsyncMock

from test_phone_controls import module, snapshot
import test_phone_controls_runtime as runtime

volume = module("hardware_volume")


def phone():
    value = snapshot()["phones"][0]
    value["capabilities"] = ["hardware_volume"]
    return value


def call(line="201", uid="12.3"):
    return {"line": line, "name": f"PJSIP/{line}-00000002", "uniqueid": uid, "state": "Up"}


def ack(values):
    return {"context": values["context"], "direction": values["direction"],
            "requested_steps": values["steps"], "acknowledged_steps": values["steps"],
            "level_known": False, "volume_level": None, "audio_output": "contextual",
            "physical_context_verified": False}


class HardwareVolumeContractTests(unittest.TestCase):
    def test_ringer_payload_has_no_invented_level_or_output(self):
        self.assertEqual(volume.request(phone(), "202", {"context": "ringer", "direction": "down"}),
                         {"line": "202", "context": "ringer", "direction": "down", "steps": 1})

    def test_steps_reject_boolean_fraction_and_out_of_range(self):
        for invalid in (True, False, 0, 6, 1.0, "2", None):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "integer"):
                volume.request(phone(), "201", {"context": "ringer", "direction": "up", "steps": invalid})

    def test_call_binds_one_selected_line(self):
        value = phone(); value["channels"] = [call("202")]
        result = volume.request(value, "202", {"context": "call", "direction": "up", "steps": 5})
        self.assertEqual((result["line"], result["channel"], result["uniqueid"]),
                         ("202", "PJSIP/202-00000002", "12.3"))
        with self.assertRaisesRegex(ValueError, "Select the line"):
            volume.request(value, "201", {"context": "call", "direction": "up"})

    def test_busy_ringer_and_ambiguous_or_ringing_call_refused(self):
        for channels in ([call()], [call(), call("202")]):
            value = phone(); value["channels"] = channels
            with self.assertRaisesRegex(ValueError, "idle"):
                volume.request(value, "201", {"context": "ringer", "direction": "up"})
        for channels in ([], [call(), call("202")], [{**call(), "state": "Ringing"}]):
            value = phone(); value["channels"] = channels
            with self.assertRaisesRegex(ValueError, "single connected"):
                volume.request(value, "201", {"context": "call", "direction": "up"})

    def test_offline_missing_capability_and_extra_inputs_refused(self):
        values = {"context": "ringer", "direction": "up"}
        for change in ({"online": False}, {"capabilities": []}, {"capabilities": "hardware_volume"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                volume.request({**phone(), **change}, "201", values)
        for key in ("channel", "uniqueid", "volume_level", "audio_output"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Use only"):
                volume.request(phone(), "201", {**values, key: "invented"})

    def test_acknowledgement_requires_complete_typed_unknown_level_contract(self):
        requested = {"context": "call", "direction": "up", "steps": 1}
        accepted = ack(requested)
        self.assertEqual(volume.acknowledgement(accepted, requested), accepted)
        changes = ({"requested_steps": True}, {"acknowledged_steps": True},
                   {"requested_steps": 1.0}, {"acknowledged_steps": 0},
                   {"acknowledged_steps": 2}, {"level_known": 0},
                   {"physical_context_verified": 0}, {"level_known": True},
                   {"volume_level": 0.5}, {"audio_output": "speaker"},
                   {"context": "ringer"}, {"direction": "down"})
        for change in changes:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "outcome unknown"):
                volume.acknowledgement({**accepted, **change}, requested)
        for key in accepted:
            missing = dict(accepted); missing.pop(key)
            with self.subTest(missing=key), self.assertRaisesRegex(ValueError, "outcome unknown"):
                volume.acknowledgement(missing, requested)


@unittest.skipUnless(runtime.READY, "requires optional pinned Home Assistant runtime")
class HardwareVolumeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.PhoneRuntimeTests.asyncSetUp(self)
        self.data["phones"][0]["capabilities"] = ["hardware_volume"]
        self.c.async_set_updated_data(copy.deepcopy(self.data))
        self.c.async_request_refresh = AsyncMock()
        self.client.action.side_effect = lambda primary, action, values, action_id: ack(values)

    asyncTearDown = runtime.PhoneRuntimeTests.asyncTearDown

    async def test_four_buttons_dispatch_one_step_on_selected_line(self):
        from custom_components.cisco_phone_control.button import HARDWARE_VOLUME, PhoneButton
        self.c.settings["201"]["line"] = "202"
        for key, (label, context, direction) in HARDWARE_VOLUME.items():
            self.data["phones"][0]["channels"] = [call("202")] if context == "call" else []
            button = PhoneButton(self.c, "201", key, label)
            self.assertTrue(button.available)
            self.client.action.reset_mock()
            await button.async_press()
            args = self.client.action.await_args.args
            self.assertEqual(args[:2], ("201", "hardware_volume"))
            self.assertEqual({k: args[2][k] for k in ("line", "context", "direction", "steps")},
                             {"line": "202", "context": context, "direction": direction, "steps": 1})
            self.client.action.assert_awaited_once()

    async def test_fresh_channel_identity_replaces_stale_cache(self):
        self.c.data["phones"][0]["channels"] = [call(uid="1.1")]
        self.data["phones"][0]["channels"] = [call(uid="2.2")]
        await self.c.perform("201", "hardware_volume", {"context": "call", "direction": "down"})
        self.assertEqual(self.client.action.await_args.args[2]["uniqueid"], "2.2")

    async def test_fresh_capability_offline_or_call_loss_never_dispatches(self):
        from homeassistant.exceptions import HomeAssistantError
        self.c.data["phones"][0]["channels"] = [call()]
        for change in ({"capabilities": []}, {"online": False}, {"channels": []},
                       {"channels": [call("202")]}, {"channels": [call(), call("202")]}):
            self.data["phones"][0] = {**phone(), "channels": [call()], **change}
            with self.subTest(change=change), self.assertRaises(HomeAssistantError):
                await self.c.perform("201", "hardware_volume", {"context": "call", "direction": "up"})
        self.client.action.assert_not_awaited()

    async def test_snapshot_failure_never_uses_cached_good_call(self):
        from homeassistant.exceptions import HomeAssistantError
        from custom_components.cisco_phone_control.api import GatewayError
        self.c.data["phones"][0]["channels"] = [call()]
        self.client.snapshot.side_effect = GatewayError("unavailable")
        with self.assertRaises(HomeAssistantError):
            await self.c.perform("201", "hardware_volume", {"context": "call", "direction": "up"})
        self.client.action.assert_not_awaited()

    async def test_partial_ack_or_gateway_failure_is_not_retried(self):
        from homeassistant.exceptions import HomeAssistantError
        from custom_components.cisco_phone_control.api import GatewayError
        values = {"context": "ringer", "direction": "up", "steps": 2}
        self.client.action.side_effect = lambda *args: {**ack(values), "acknowledged_steps": 1}
        with self.assertRaisesRegex(HomeAssistantError, "outcome unknown"):
            await self.c.perform("201", "hardware_volume", values)
        self.client.action.assert_awaited_once()
        self.client.action.reset_mock()
        self.client.action.side_effect = GatewayError("outcome unknown", "synthetic")
        with self.assertRaisesRegex(HomeAssistantError, "do not retry"):
            await self.c.perform("201", "hardware_volume", values)
        self.client.action.assert_awaited_once()

    async def test_old_gateway_and_offline_buttons_unavailable(self):
        from custom_components.cisco_phone_control.button import HARDWARE_VOLUME, PhoneButton
        for change in ({"capabilities": []}, {"online": False}):
            self.c.data["phones"][0].update(change)
            for key, (label, _, _) in HARDWARE_VOLUME.items():
                self.assertFalse(PhoneButton(self.c, "201", key, label).available)

    async def test_service_schema_rejects_coerced_steps_and_output_picker(self):
        import voluptuous as vol
        from custom_components.cisco_phone_control import HARDWARE_VOLUME_SCHEMA
        data = {"entry_id": "test", "phone": "201", "line": "202", "context": "call", "direction": "up"}
        self.assertEqual(HARDWARE_VOLUME_SCHEMA(data)["steps"], 1)
        for value in (True, 1.0, "1", 6):
            with self.subTest(value=value), self.assertRaises(vol.Invalid):
                HARDWARE_VOLUME_SCHEMA({**data, "steps": value})
        with self.assertRaises(vol.Invalid):
            HARDWARE_VOLUME_SCHEMA({**data, "audio_output": "speaker"})


if __name__ == "__main__":
    unittest.main()
