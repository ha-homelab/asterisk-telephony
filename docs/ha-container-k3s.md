# Home Assistant Container в k3s

Здесь предполагается **уже существующий** Home Assistant Container с постоянным
`/config`. Установка интеграции не требует HA OS, Supervisor или нового кластера.
Компонент Asterisk — custom integration в процессе HA. Официальная VoIP —
отдельная встроенная интеграция, не add-on.

## Подготовка

Запишите локально namespace, Deployment, имя контейнера, PVC, UID/GID и IP,
с которого HA реально приходит к PBX. Эти значения не коммитятся. Проверьте,
что Pod не хранит `/config` в `emptyDir` и backup включает `.storage`.
Для одной HA config используйте одну реплику; при обновлении избегайте двух
процессов, одновременно пишущих в один config. Для существующего Deployment
подходит стратегия `Recreate`, но сначала сверяйте её с вашим runbook.

AMI требуется исходящее TCP-соединение HA к PBX:5038. Порт не надо публиковать
через Ingress, NodePort или роутер. При обычной pod-сети source IP может зависеть
от SNAT: /32 ACL на PBX должен совпасть с **наблюдаемым** адресом клиента.

Для VoIP удобен `hostNetwork: true` с фиксированным LAN-узлом и
`dnsPolicy: ClusterFirstWithHostNet`. Тогда SIP и RTP имеют понятные LAN-адреса.
Это проектный вариант, а не обязательное условие любой HA-инсталляции.
`NetworkPolicy` не заменяет host firewall для hostNetwork.

Проверьте занятость SIP-порта. Если Asterisk и HA находятся на одном узле,
два процесса не могут занять один и тот же IP/UDP-порт. Используйте разные
LAN-адреса/узлы либо измените порт VoIP через её настройки и синхронизируйте
`assist.sip_port` на PBX. HA [документирует изменение VoIP-порта](https://www.home-assistant.io/integrations/voip/).

## Доставка файлов

Самый простой способ — запустить installer на хосте, где config PVC/volume
доступен как каталог, с владельцем, от которого работает HA. Если доступ есть
только через Pod, сначала подготовьте staging локально, затем выполните backup
и используйте свой привычный способ копирования в PVC.

Пример для выбранного работающего Pod, после локальной проверки переменных:

```bash
set -eu
# Переменные задаются локально. Не вставляйте сюда production kubeconfig.
: "${HA_KUBE_CONTEXT:?set context}" "${HA_NAMESPACE:?set namespace}" "${HA_POD:?set pod}" "${HA_CONTAINER:?set container}"
kubectl --context "$HA_KUBE_CONTEXT" -n "$HA_NAMESPACE" exec "$HA_POD" -c "$HA_CONTAINER" -- \
  test -f /config/configuration.yaml
# Проверки должны пройти. Существующий каталог или symlink требуют остановки.
kubectl --context "$HA_KUBE_CONTEXT" -n "$HA_NAMESPACE" exec "$HA_POD" -c "$HA_CONTAINER" -- \
  test ! -L /config/custom_components
kubectl --context "$HA_KUBE_CONTEXT" -n "$HA_NAMESPACE" exec "$HA_POD" -c "$HA_CONTAINER" -- \
  test ! -e /config/custom_components/asterisk
kubectl --context "$HA_KUBE_CONTEXT" -n "$HA_NAMESPACE" exec "$HA_POD" -c "$HA_CONTAINER" -- \
  test ! -L /config/custom_components/asterisk
kubectl --context "$HA_KUBE_CONTEXT" -n "$HA_NAMESPACE" exec "$HA_POD" -c "$HA_CONTAINER" -- \
  mkdir -p /config/custom_components
kubectl --context "$HA_KUBE_CONTEXT" cp .local/upstream-stage/custom_components/asterisk \
  "$HA_NAMESPACE/$HA_POD:/config/custom_components/asterisk" -c "$HA_CONTAINER"
```

`kubectl cp` требует `tar` внутри контейнера. Если его нет, используйте временный
вспомогательный Pod с тем же PVC согласно своей политике доступа, а не меняйте
постоянный образ HA ради копирования. При замене существующего компонента сначала
остановите HA и работайте через PVC/maintenance Pod, чтобы не смешать версии.

Убедитесь, что каталог один: `/config/custom_components/asterisk/manifest.json`,
а не вложенный `asterisk/asterisk/manifest.json`. Проверьте UID/GID и чтение файлов
из контейнера. Установите YAML по [configuration.md](configuration.md), затем:

```bash
: "${HA_KUBE_CONTEXT:?set context}" "${HA_NAMESPACE:?set namespace}" "${HA_POD:?set pod}" "${HA_CONTAINER:?set container}"
kubectl --context "$HA_KUBE_CONTEXT" -n "$HA_NAMESPACE" exec "$HA_POD" -c "$HA_CONTAINER" -- \
  python -m homeassistant --script check_config --config /config
```

После успешной проверки перезапустите **только свой HA Deployment** штатным
способом и дождитесь Ready. Readiness HA не подтверждает регистрацию телефонов
или аудио Assist: выполните [приёмку](acceptance.md).

## Assist в Container

Добавьте интеграции **Voice over IP** и необходимые speech-провайдеры. Для
полностью локального pipeline можно использовать отдельно развёрнутые Wyoming
STT/TTS-сервисы, если они уже есть в доме. Этот репозиторий не создаёт их и не
подписывает вас на облачный сервис. Выберите язык, STT, conversation agent и TTS
в настройках Assist. Проверяйте pipeline сначала в интерфейсе HA, затем по SIP.
