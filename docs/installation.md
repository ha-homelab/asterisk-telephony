# Закреплённая установка

## Что устанавливается

`upstream.lock.json` закрепляет исходный ZIP
`TECH7Fox/asterisk-hass-integration` на commit
`65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e`, manifest version `1.0.5`.
SHA-256:
`697792a522eb90db62672ff57bc0b03bec4caba45c0748583bdd32768e6dcad1`.
Это контроль выбранного архива, не криптографическая подпись автора. Upstream
может перегенерировать ZIP: несовпадение требует проверки источника и файлов,
а не автоматического принятия нового хеша. [Происхождение](sources.md).

Установщик использует только HTTPS GitHub/codeload, ограничивает размер архива,
проверяет SHA-256 до распаковки, отклоняет traversal, symlink и дубли имён,
извлекает только `custom_components/asterisk`, сверяет domain и version.
По умолчанию он не касается работающего HA.

## Staging

```bash
python3 scripts/stage_integration.py --stage .local/upstream-stage
```

Результат: `.local/upstream-stage/custom_components/asterisk/`.
Для заранее загруженного архива:

```bash
python3 scripts/stage_integration.py \
  --archive .local/upstream.zip --stage .local/upstream-stage-offline
```

SHA-256 и размер проверяются также в offline-режиме. Не загружайте архив или
распакованный компонент в этот public-репозиторий. Существующий staging не
перезаписывается: используйте новый путь.

## Установка в доступный локально /config

1. Создайте backup HA, включая `.storage`, и сохраните текущий каталог
   `custom_components/asterisk`, если он есть.
2. Остановите HA на время замены файлов. В HA Container используйте привычный
   способ управления контейнером, в k3s — инструкцию следующего документа.
3. Если интеграцией уже управляет HACS, обновляйте её через HACS. Не смешивайте
   владельцев одного каталога.
4. Для чистой установки укажите реальный существующий каталог конфигурации:

```bash
python3 scripts/stage_integration.py \
  --stage .local/upstream-install-stage \
  --install-to /absolute/path/to/ha-config --apply
```

Без `--apply` даже при `--install-to` выполняются лишь staging и проверки.
Скрипт требует `configuration.yaml`, отклоняет symlink в пути и отказывается
перезаписывать установленную интеграцию. На macOS `/tmp` и `/var` могут быть
системными symlink: используйте канонический путь. Права новых файлов — `0600`,
каталогов — `0700`; запускайте от владельца HA config либо приведите владельца
к UID/GID контейнера перед стартом. Не делайте config доступным всем пользователям.
5. Выполните проверку конфигурации и запустите HA. В Settings → Devices & services
   добавьте **Asterisk**, укажите LAN/VPN адрес PBX, порт `5038`, AMI username и
   password из приватного хранилища. SIP-пароль телефона сюда не подходит.

Интеграция при первом запуске может устанавливать Python-зависимости из своего
manifest; учитывайте доступ HA к package registry. Установщик этого репозитория
не исполняет код скачанного компонента.

## Откат

Сохраните текущую `.storage` и остановите HA. Переместите новый компонент в
локальный quarantine-каталог, восстановите ранее сохранённый каталог версии,
согласованной с вашим HA, проверьте конфигурацию и запустите HA. Не удаляйте
config entry и entity registry как первый шаг: это может нарушить mapping.
Пакеты и dashboard откатываются отдельно к предыдущим файлам.

![Порядок установки](diagrams/installation.svg)
