# Проверка совместимости с Home Assistant 2026.10.0 и 2026.10.2

16 октября 2026 года в одноразовом Python 3.14.7 virtualenv проверен неизменённый
upstream TECH7Fox `v1.0.5` / commit
`65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e` с настоящими пакетами
`homeassistant==2026.10.2`, `asterisk-ami==0.1.7` и `voluptuous==0.15.2`.
Использованы исходники из архива, проверенного по [upstream.lock.json](../upstream.lock.json).
Все четыре теста [test_upstream_runtime.py](../tests/test_upstream_runtime.py) прошли.

17 октября тот же набор прошёл с `homeassistant==2026.10.0` перед установкой
на HA этой версии. CI проверяет обе версии в отдельных изолированных окружениях.
Для повторения с 2026.10.0 используйте `requirements-runtime-2026.10.0.txt`
вместо `requirements-runtime.txt` в командах ниже.

Тот же набор прошёл на Linux amd64 в
[GitHub Actions](https://github.com/ha-homelab/asterisk-telephony/actions/runs/39005310862):
26 проверок генератора/установщика и отдельно четыре проверки HA runtime.
Проверка [Asterisk-контейнера](https://github.com/4alvit/cisco-home-telephony/actions/runs/39005213169)
также прошла: 20 unit-тестов, сборка образов и изолированный smoke с настоящими
AMI, G.711/Opus translation, маршрутом Assist и TFTP.

Проверены следующие границы API:

- Форма настройки upstream сериализуется текущим HA/Probatio; содержит host,
  port, username, password. Синтетический AMI login создаёт config entry.
- Setup корректно обрабатывает отсутствие chan_sip и обнаружение PJSIP endpoint.
  Настоящий HA service registry вызывает `asterisk.send_action` и передаёт
  Originate/Hangup исходной интеграции с ожидаемыми параметрами.
- Шесть настоящих классов sensors инициализируются, запрашивают CoreSettings,
  обрабатывают unavailable/ringing и повторяющиеся DTMF 1/1/#. Событие другого
  ConnectedLineNum не изменяет соответствующий DTMF sensor.
- Сгенерированный YAML проходит настоящие схемы HA для scripts, automation и
  state trigger; последовательности действий проходят async validation HA.

AMI client заменён управляемым fake. Forwarding платформ перехвачен; регистрация
service, форма config flow и классы sensors настоящие. Поэтому результат означает
проверенную совместимость этих API, а не полный запуск Home Assistant, сетевую
доставку AMI, настройку реального HA или успешный разговор с Cisco.

## Повторить отдельно от работающего HA

Нужен Python не ниже 3.14.2. Из корня этого репозитория создайте свежий staging
каталог и отдельное окружение. Не устанавливайте тестовые dependencies в runtime
домашнего Home Assistant.

```bash
python3 scripts/stage_integration.py --stage .local/runtime-upstream
uv venv --python 3.14 .local/runtime-venv
uv pip install --python .local/runtime-venv/bin/python -r requirements-runtime.txt
ASTERISK_UPSTREAM_ROOT="$PWD/.local/runtime-upstream" \
  .local/runtime-venv/bin/python -m unittest discover -s tests -p test_upstream_runtime.py -v
```

Инсталлятор откажется перезаписывать готовый staging каталог; для следующего
прогона можно использовать уже проверенный каталог или выбрать новый путь.
Обычный `python3 -m unittest discover -s tests -v` без этого окружения пропускает
четыре runtime-теста и выполняет основной набор тестов стандартной библиотеки.

## Что требует приёмки дома

После установки проверяют AMI reconnect, обновление состояний при регистрации и
отключении телефона, входящий/исходящий звонок, hold/hangup и реальные направления
DTMF. `ConnectedLineNum` в upstream определяет датчик, поэтому его привязку к
нажимающему клавишу аппарату подтверждают звонком между двумя телефонами.

Для Assist отдельно проверяют путь Cisco G.711 → Asterisk Opus → HA VoIP, Allow
calls, STT/TTS pipeline, звук в обе стороны и освобождение каналов после отбоя.
API-тесты этого файла не заменяют такую приёмку.

Исходные источники:
[TECH7Fox integration](https://github.com/TECH7Fox/asterisk-hass-integration/tree/65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e),
[HA 2026.10.2](https://github.com/home-assistant/core/tree/2026.10.2),
[официальная VoIP integration](https://www.home-assistant.io/integrations/voip/).
