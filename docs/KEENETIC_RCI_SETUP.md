# Настройка KeenDNS RCI

## Web application

В Keenetic откройте настройки KeenDNS и создайте web application:

- имя: `rci`;
- устройство: этот Keenetic;
- protocol: `HTTP`;
- TCP port: `79`;
- доступ: авторизованный.

Внешний адрес для FQDN-updater:

```text
https://rci.<domain>/rci/
```

`HTTP` в Keenetic относится к локальному upstream до RCI API на роутере. `HTTPS` в config относится к внешнему соединению updater -> KeenDNS endpoint.

## Пользователь

Создайте отдельного пользователя, например `api_updater`, без admin rights и с минимальными правами для HTTP Proxy/RCI. Используйте сгенерированный панелью пароль и храните его вне git.

## Preconditions

- DNS proxy на Keenetic включён.
- Клиенты, для которых работают DNS-based routes, используют Keenetic как DNS.
- Целевой interface или gateway доступен.
- Для статического VPS IP рекомендуется ограничить доступ к публикации firewall whitelist.
