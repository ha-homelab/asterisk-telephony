# Установка через HACS

HACS и закреплённый installer — альтернативные способы размещения одного
компонента. Выберите один. Этот companion-репозиторий **не является HACS
интеграцией** и не должен добавляться в HACS как Integration.

1. Установите и настройте HACS по [официальной инструкции](https://www.hacs.xyz/docs/use/).
2. В HACS откройте меню с тремя точками → Custom repositories.
3. Добавьте `https://github.com/TECH7Fox/asterisk-hass-integration`, тип
   **Integration**. Это [поддерживаемый HACS способ добавления custom repository](https://www.hacs.xyz/docs/faq/custom_repositories/).
4. Загрузите Asterisk. Если диалог предлагает версию, выберите проверенную
   `1.0.5`; проверьте установленный `custom_components/asterisk/manifest.json`.
5. Перезапустите HA, затем добавьте Asterisk через Devices & services и настройте
   AMI по [контракту PBX](pbx-contract.md).
6. После появления сущностей заполните локальный mapping и установите
   [package/dashboard](configuration.md).

HACS не проверяет наш `upstream.lock.json` при обычной загрузке. Для идентичности
закреплённому commit и SHA-256 используйте installer. Автоматическое обновление
не должно обходить тесты приёмки: изменение upstream может поменять поведение
датчиков, action или совместимость HA. Сначала backup, staging и проверка.
