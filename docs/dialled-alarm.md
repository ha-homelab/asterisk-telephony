# Toggle a saved alarm from a handset

Cisco Phone Control **0.4.0** adds the administrator-only `toggle_saved_alarm` operation to the existing `cisco_phone_control.schedule` service. With the matching Asterisk phone-service configuration, a Cisco user dials **641** to enable or disable that physical handset's saved recurring alarm and hear a short spoken result.

The alarm must first be saved with a time and weekdays, for example through the CLI's `alarm set` command or the existing `alarm_set` service. Explicitly editing Alarm time or a weekday entity also saves the displayed schedule without enabling it. The first such edit captures the selected owner line; later edits preserve that owner. This operation does not create a default alarm. If `alarm_configured` is false, it returns `operation_result: not_configured` and leaves alarm settings unchanged.

A caller on either line of the same Cisco may toggle its saved alarm. Its owner line, time and weekdays remain unchanged. Disabling also attempts to stop an active alert only when its kind is `alarm` and its line and ID match the saved alarm. An unrelated timer is not stopped. Existing scheduler behavior, including missed-occurrence handling and ringing until answer/stop, remains unchanged.

## API contract

Send the following to the existing authenticated service endpoint, `POST /api/services/cisco_phone_control/schedule?return_response`:

```json
{
  "entry_id": "REPLACE_WITH_CISCO_PHONE_CONTROL_ENTRY_ID",
  "phone": "201",
  "line": "202",
  "operation": "toggle_saved_alarm",
  "request_id": "one-stable-id-for-this-operation"
}
```

The example numbers are synthetic. `phone` is the primary handset ID; `line` must belong to it. A fresh gateway snapshot is required for a new toggle. Repeating the same request ID and inputs cannot flip the alarm again. The request is recorded before external actions; ambiguous results return `outcome_unknown` and are not replayed. A repeated completed no-alarm request retains `not_configured`, even if a later independent request saved an alarm.

The `service_response` body uses the existing schema: `schema_version`, `phone`, `line`, `gateway_fresh`, `alarm`, `timer`, `alert`, `status`, and `operation_result`. The `alarm` object contains `configured`, `enabled`, `line`, `time`, and `weekdays`. Confirm both `gateway_fresh` and `operation_result`; a successful HTTP request alone does not prove that a stop operation completed.

This is an administrator service using HA's existing authentication. No new HTTP endpoint, webhook secret or anonymous action has been added. The Asterisk bridge reads a private HA administrator token from its generated configuration, identifies the caller from the authenticated PJSIP channel rather than caller ID, and makes only one bounded request. A new phone call is a new operation; after a timeout, inspect the alarm before redialling.

## PBX setup and voice

Follow the matching [Asterisk service setup](https://github.com/4alvit/cisco-home-telephony/blob/main/docs/phone-services.md) for image/configuration rollout, private credentials, generated inventory ownership, and Baya prompt preparation. Spoken confirmations are pre-generated from the selected HA TTS entity into four short private WAV files. This keeps speech generation off the call's critical path and does not modify Assist pipelines.

Upgrade this integration before enabling the Asterisk service. Keep local `.storage` backups private. The optional `result` field in persisted schedule request records was introduced in 0.4.0; when rolling back only the PBX, retain this integration version. A full HA rollback requires a compatible reviewed storage backup rather than editing the live journal.

## Verification

The runtime tests use real Home Assistant with a fake local gateway and place no phone calls. They verify that an unconfigured alarm stays unconfigured, replay is safe across storage reload, both lines use the same saved alarm, concurrent duplicate requests toggle once, disabling targets only the saved alarm, and storage/stop failures cannot cause retries.

For live acceptance, create a disabled future alarm, dial 641 from a sibling line, check that the saved schedule now reads enabled with the same owner/time/weekdays, and dial again to disable it. Separately test an unconfigured handset; it must announce that no alarm exists and stay unconfigured. Perform audible tests only when appropriate for the household.
