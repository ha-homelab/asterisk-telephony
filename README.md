# Asterisk telephony for Home Assistant

Наблюдение за SIP-телефонами, вызов пары аппаратов, завершение текущего разговора,
безопасный тест DTMF и подключение Assist через номер **630**.

Используется существующая интеграция
[TECH7Fox/asterisk-hass-integration](https://github.com/TECH7Fox/asterisk-hass-integration),
версия **1.0.5**. Этот репозиторий добавляет проверяемую установку, локальный
генератор конфигурации, примеры, документацию и SVG. Исходники upstream здесь
не распространяются.

![Архитектура](docs/diagrams/architecture.svg)

## Что готово

- Установщик проверяет закреплённый ZIP по SHA-256 и по умолчанию только готовит
  staging-каталог. Он не перезапускает HA и не меняет кластер.
- Генератор создаёт HA package, отдельный privacy package и dashboard по
  **реальным entity ID**, введённым из вашей HA. Пароль AMI ему не нужен.
- Скрипт вызова сначала звонит **201**, а после ответа соединяет его с **202**.
  Hangup читает фактический атрибут `Channel` и проверяет подключённый разговор.
- Пример DTMF показывает только уведомление внутри HA и по умолчанию выключен.
- Для Assist описан отдельный SIP/аудиотракт. **Работа AMI не доказывает работу
  Assist**: нужен G.711↔Opus transcoder и проверка живого звонка.

Проверки репозитория выполняются локально и в GitHub Actions на `ubuntu-24.04`,
без секретов, звонков и deployment. 17 октября 2026 интеграции Asterisk и VoIP
подключены к работающему HA 2026.10.0; реальная AMI-связь проверена, создана
панель состояния «Телефония». [Протокол серверной установки](https://github.com/4alvit/cisco-home-telephony/blob/main/docs/server-validation.md).
Физические телефоны, их управление и звук ещё требуют приёмки.
[Матрица проверки](docs/acceptance.md).
[Результаты выполненных тестов](docs/runtime-verification.md) содержат ссылки
на успешные Actions обоих репозиториев.

## Начало работы

1. Прочитайте [требования и контракт PBX](docs/pbx-contract.md).
2. Выберите [закреплённую установку](docs/installation.md) или
   [HACS](docs/hacs.md). Для HA Container в k3s есть
   [отдельная инструкция](docs/ha-container-k3s.md).
3. Добавьте интеграцию Asterisk через UI HA, затем скопируйте entity ID каждого
   телефона из Settings → Devices & services → Entities.
4. Подготовьте локальное описание. Заглушки намеренно отклоняются:

```bash
umask 077
mkdir -p .local
cp examples/site.example.json .local/site.json
# Заполните реальные entity ID в .local/site.json.
python3 scripts/render.py --input .local/site.json --output .local/rendered
```

5. Установите три полученных YAML-файла по
   [инструкции package/dashboard](docs/configuration.md), выполните config check,
   затем тесты приёмки. Нужны Python 3.10+ и стандартная библиотека Python.

## Навигация

- [Установка с SHA-256 и откат](docs/installation.md)
- [HACS без второго установщика](docs/hacs.md)
- [HA Container, PVC и host networking в k3s](docs/ha-container-k3s.md)
- [AMI, права, Assist 630 и контракт PBX](docs/pbx-contract.md)
- [Сущности, команды, DTMF и dashboard](docs/configuration.md)
- [Конфиденциальность и границы доступа](docs/privacy.md)
- [Эксплуатация и диагностика](docs/operations.md)
- [Приёмка](docs/acceptance.md)
- [Проверка настоящего HA runtime с моделируемым AMI](docs/runtime-verification.md)
- [Первичные источники и закреплённые версии](docs/sources.md)

SVG редактируются как текст или в SVG-редакторе. Пересборка:
`python3 scripts/build_diagrams.py`. Примеры `*.example.yaml` содержат **явно
синтетические** `example_*` ID и предназначены для чтения, не для установки.

## Разработка

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
python3 scripts/build_diagrams.py --check
```

Не добавляйте в public Git локальные entity maps, IP-адреса дома, имена жильцов,
AMI-пароли, `.storage`, backup HA, файлы записей разговоров или данные покупок.

## Управление репозиторием

Публичный [ha-homelab/asterisk-telephony](https://github.com/ha-homelab/asterisk-telephony)
создан через каноническую Terraform-конфигурацию
[terraform-github-ha-homelab](https://github.com/4alvit/terraform-github-ha-homelab/tree/687eb21afa585b535ab619f159d0bb1707f620b5),
изменение [PR #53](https://github.com/4alvit/terraform-github-ha-homelab/pull/53).
Настройки GitHub-репозитория изменяются через Terraform, содержимое и документация
обновляются обычными commits/PR. Скрипты этого репозитория не создают GitHub-ресурсы.
