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

Если из Keenetic скопирован URL вида `http://rci.<domain>`, CLI и панель нормализуют его в `https://rci.<domain>/rci/`.

## Пользователь

Создайте отдельного пользователя, например `api_updater`, без admin rights и с минимальными правами для HTTP Proxy/RCI. Используйте сгенерированный панелью пароль и храните его вне git.

Секрет можно хранить:

- в `.env.secrets` через `password_env`;
- в `.env`, если это удобнее для Docker env;
- в `secrets/` через `password_file`.

## Preconditions

- DNS proxy на Keenetic включён.
- Клиенты, для которых работают DNS-based routes, используют Keenetic как DNS.
- Целевой interface или gateway доступен.
- WireGuard interface виден в `show interface`, если вы хотите использовать discovery.
- Для статического VPS IP рекомендуется ограничить доступ к публикации firewall whitelist.

`fqdn-updater status` проверяет remote preconditions read-only. Если `status` unhealthy, сначала исправьте Keenetic/RCI setup и только потом запускайте `sync`.
