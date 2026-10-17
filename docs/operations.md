# Эксплуатация и диагностика

## Перед обновлением

Сохраните версию HA, `manifest.json` Asterisk integration, commit/hash из lock,
backup HA config вместе с `.storage`, предыдущие packages/dashboard и приватный
entity mapping. Устанавливайте изменение сначала в тестовый HA. Затем повторите
[приёмку](acceptance.md), особенно DTMF mapping и текущий Channel для Hangup.
Автоматический upstream update не должен незаметно менять закреплённый installer.

Новая версия upstream требует просмотра diff, нового commit/ZIP SHA-256,
обновлённого `upstream.lock.json` и теста staging. Не исправляйте checksum только
потому, что загрузка перестала проходить. GitHub-generated ZIP иногда меняется
при повторном архивировании; подтвердите дерево исходников и происхождение.

## Симптомы

**Integration не подключается.** Проверьте адрес и TCP5038 с HA-узла, source IP
после SNAT, точный `/32` в ACL, отдельный AMI secret и Asterisk logs. Для сетевого
теста достаточно проверки TCP без передачи пароля. Не выводите secret через
командную строку, историю shell, `kubectl describe` или общий log.
Для проверки входа, версии, PJSIP discovery и запрета CLI из контейнера HA
используйте [AMI-пробник с передачей пароля через stdin](ami-verification.md).

**HA подключился, сущностей нет.** Проверьте `PJSIPShowEndpoints` и права discovery,
наличие PJSIP endpoint до загрузки integration. Эта версия перечисляет устройства
при setup, поэтому после добавления endpoint на PBX reload/restart integration
может быть необходим. Ошибка `SIPpeers` допустима для PBX только с PJSIP, если
последующее перечисление PJSIP проходит. Не добавляйте `write=all` как общий fix.

**Registered отличается от `pjsip show contacts`.** Оно вычисляется по DeviceState.
Считайте PBX исходным источником регистрации и сопоставьте AMI events. Это не
система учёта SIP REGISTER. После потери AMI проверьте свежесть данных, а не
только зелёный значок последнего сохранённого state.

**Originate не работает.** На PBX нужен `allow_originate:true`; проверьте context,
наличие целевого extension и доступность исходного телефона. Сначала ответьте на
звонок исходного аппарата: только потом Asterisk соединяет destination.

**Hangup ничего не делает.** Это нормально для idle, ringing, чужого/stale Channel
или Connected Line `None`. Проверьте attributes живого соединённого вызова и
точность mapping. Сопоставьте канал с `core show channels concise` на PBX.
Не заменяйте защитное условие на отправку сохранённого Channel.

**DTMF не вызывает уведомление.** Автоматизация выключена по умолчанию. Проверьте
фактический DTMF sensor, направление, изменение timestamp, атрибут Digit и Context
во время тестового разговора. Не ожидайте HA event-bus события от этого upstream.

**Assist звонит, но не слышит/не отвечает.** Отдельно проверьте pipeline в HA,
разрешённость звонка устройства VoIP, SIP/SDP, RTP между узлами и обе translation
paths G.711↔Opus на PBX. SIP 200 OK или наличие `codec_opus` в каталоге не являются
проверкой аудио. Не меняйте transport Cisco на UDP только ради другого transport
на плече Asterisk→HA.

## Восстановление

Восстановите компонент и HA config согласованной версии, затем entity mapping.
Сохранение registry обычно сохраняет ID; всё равно проверьте их перед загрузкой
package. При ротации AMI secret обновляйте PBX и config entry HA согласованно,
проверяйте reconnect и не публикуйте оба значения в diff. Резервные копии должны
быть зашифрованы и проверены восстановлением в отдельной среде.
