"""Cisco handset controls, separate from the existing Asterisk integration."""
DOMAIN = "cisco_phone_control"
PLATFORMS = ["button", "switch", "select", "text", "time", "number", "sensor"]
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
NUMBER_LABELS = {
    "600": "Echo test", "601": "Milliwatt test tone", "620": "Ring group", "630": "Home Assistant",
    "640": "Alarm tone", "701": "Matryoshka", "702": "Echo radio", "703": "BOB rock",
    "704": "BOB metal", "705": "BOB gothic", "706": "PopTron", "707": "Radyo Viva",
}
