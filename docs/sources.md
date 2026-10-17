# Источники и границы проверки

Проверка исходников: 16 октября 2026 года. Наличие функций в исходниках не
означает, что они испытаны на конкретной домашней инсталляции.

## Upstream Asterisk integration

- [Репозиторий](https://github.com/TECH7Fox/asterisk-hass-integration).
- [Закреплённый commit](https://github.com/TECH7Fox/asterisk-hass-integration/tree/65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e), manifest version 1.0.5.
- [Action schema](https://github.com/TECH7Fox/asterisk-hass-integration/blob/65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e/custom_components/asterisk/services.yaml): `asterisk.send_action`, поля `action` и `parameters`.
- [Setup/service implementation](https://github.com/TECH7Fox/asterisk-hass-integration/blob/65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e/custom_components/asterisk/__init__.py): discovery при setup, глобальный action, AMI TCP.
- [Sensors](https://github.com/TECH7Fox/asterisk-hass-integration/blob/65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e/custom_components/asterisk/sensor.py): Channel attributes и timestamp DTMF sensors.
- [Binary sensor](https://github.com/TECH7Fox/asterisk-hass-integration/blob/65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e/custom_components/asterisk/binary_sensor.py): Registered выводится из device state.
- [Lock](../upstream.lock.json): commit, URL, размер архива и SHA-256.

У upstream не обнаружена явная лицензия для копирования исходников в этот
репозиторий. Здесь находятся собственные скрипты/документы и ссылки; installer
получает выбранный пользователем upstream непосредственно из его репозитория.
Наличие ссылки или публичного исходника не следует трактовать как перелицензирование.

## Home Assistant и HACS

- [Packages](https://www.home-assistant.io/docs/configuration/packages/): включение и правила объединения YAML.
- [Dashboard actions](https://www.home-assistant.io/dashboards/actions/): `perform-action`, `perform_action` и confirmation.
- [Recorder](https://www.home-assistant.io/integrations/recorder/): исключение entities и история.
- [Voice over IP](https://www.home-assistant.io/integrations/voip/): отдельная интеграция, настройка SIP-порта.
- [HA 2026.10.2 VoIP source](https://github.com/home-assistant/core/tree/2026.10.2/homeassistant/components/voip): версия для проверки media-контракта.
- [HACS custom repositories](https://www.hacs.xyz/docs/faq/custom_repositories/): установка внешней интеграции.

## Asterisk

- [AMI Originate](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Actions/Originate/).
- [AMI Hangup](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Actions/Hangup/).
- [AMI configuration](https://docs.asterisk.org/Configuration/Interfaces/Asterisk-Manager-Interface-AMI/).

Права AMI дополнительно сверяются с версией вашего Asterisk. Discovery выбранного
upstream требует `system`; такой account нельзя характеризовать как read-only.
