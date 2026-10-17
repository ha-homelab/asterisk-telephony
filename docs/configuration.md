# Package, dashboard, DTMF

## Реальные entity ID

Upstream создаёт Registered, State, Connected Line, DTMF Sent и DTMF Received
для найденных SIP/PJSIP endpoint. Названия могут зависеть от entity registry,
переименований и конфликтов. Не выводите entity ID из номера по шаблону.

Откройте Devices & services → Asterisk → устройство телефона → Entities и
скопируйте фактические ID. У Registered домен `binary_sensor`, у остальных
`sensor`. Заполните все пять полей каждого телефона в `.local/site.json`.
На первой итерации достаточно двух аппаратов; остальные добавляются аналогично.

Значение Registered upstream получает из device state, а не из полного аудита
SIP ContactStatus. State описывает состояние endpoint. Connected Line показывает
последнее событие одного канала, не полный список одновременных вызовов.
Проверку регистрации и всех каналов выполняйте также на Asterisk.

```bash
python3 scripts/render.py --input .local/site.json --output .local/rendered
```

Генератор не выводит mapping или AMI secret. Он отклоняет неизвестные поля,
заглушки, неподходящие домены, повторяющиеся ID и небезопасные context/extension.
Каждый запуск используйте свежий output-каталог: файлы не перезаписываются.
Это синтаксические проверки, а наличие сущностей подтверждает пользователь в HA.

## Подключение файлов

Скопируйте `asterisk.yaml` и `asterisk_privacy.yaml` в `/config/packages/`.
Не копируйте dashboard в packages. В **существующую** секцию `homeassistant`
в `configuration.yaml` добавьте packages, не создавая второй ключ:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Если packages уже подключены другим способом, адаптируйте путь к этой схеме.
[Официальные правила объединения packages](https://www.home-assistant.io/docs/configuration/packages/).

Скопируйте `asterisk_dashboard.yaml` в `/config/dashboards/`. Для YAML dashboard
добавьте в существующий `lovelace`:

```yaml
lovelace:
  dashboards:
    asterisk-telephony:
      mode: yaml
      title: Telephony
      icon: mdi:phone
      show_in_sidebar: true
      filename: dashboards/asterisk_dashboard.yaml
```

Либо создайте отдельный dashboard в UI и перенесите содержимое файла через
Raw configuration editor. Не заменяйте основной dashboard целиком.
Затем config check и контролируемый restart/reload. Наши скрипты имеют префикс
`asterisk_`; проверьте отсутствие таких же ключей в других packages.

## Управление звонками

`script.asterisk_call_pair` использует `asterisk.send_action` с полями
`action: Originate`, `parameters: {Channel, Context, Exten, Priority, CallerID,
Timeout, Async}`. Разрешение PBX `allow_originate:true` обязательно.
`script.asterisk_call_assist` аналогично сначала звонит выбранному аппарату,
затем соединяет его с 630. Если Assist ещё не прошёл приёмку, кнопку не используйте.

`script.asterisk_hangup_<номер>` каждый раз читает **актуальный** `Channel`
из указанного Connected Line sensor. Условие требует канал `PJSIP/<номер>-…`,
состояние `Up` и непустую Connected Line. Idle/stale/чужой канал не отправляется.
Кнопка предназначена для соединённого разговора, а не для всех стадий ringing.
Если upstream сообщает канал противоположной стороны, защита откажет в действии:
используйте правильный sensor после проверки атрибутов. Никогда не сохраняйте
суффикс канала вроде `-0000001a` как постоянную настройку.

Dashboard требует подтверждение перед звонком и Hangup. Это защита от случайного
нажатия, а не механизм разграничения AMI-полномочий.

## DTMF

У upstream нет отдельного HA event-bus события DTMF. Датчик DTMF имеет timestamp
в state и `Digit`, `Channel`, `ConnectedLineNum`, `Context` в attributes.
Направления Sent/Received описываются со стороны Asterisk и фильтруются по
ConnectedLineNum, поэтому название sensor не гарантирует, что это клавиатура
того же физического аппарата. Проверьте оба направления тестовым звонком.

Наш пример использует state trigger, изменение timestamp, выбранную `Digit`
и context. По умолчанию `dtmf.enabled:false`, а automation `initial_state:false`.
Выберите фактический sensor после теста, затем осознанно включите automation.
Результат — `persistent_notification.create`, без замков, сигнализации,
дверей, внешних сообщений или платежей. Не вводите DTMF-секреты в тесте.

Отдельный privacy package исключает call/DTMF entities из Recorder. Dashboard
показывает время последнего DTMF, а не вводимые цифры. При диагностике атрибут
`Digit` можно посмотреть в Developer tools → States.
