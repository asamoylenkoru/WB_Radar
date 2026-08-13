# WB Radar

Telegram-бот для отслеживания товаров Wildberries. Пользователь отправляет ссылку на товар, бот сохраняет товар и каждые 30 минут проверяет:

- изменилась ли цена;
- появилась или изменилась ли скидка;
- стал ли товар заканчиваться.

## Статус: отключено

Автоматический запуск через GitHub Actions **отключён**: workflow удалён из `.github/workflows/`, cron каждые 30 минут больше не срабатывает.

Из-за падений workflow GitHub слал письма об ошибках — это и было причиной спама.

Чтобы снова включить бота:

1. Скопируйте `wb-radar.yml` в `.github/workflows/wb-radar.yml`
2. Верните расписание `schedule` / уберите `if: false` у job
3. Убедитесь, что секрет `WB_RADAR_TELEGRAM_TOKEN` задан

## Важно про приватность

Workflow сохраняет состояние в `state/wb_radar_state.json`: chat_id пользователей, артикулы и последние цены. Поэтому репозиторий должен быть **Private**.

Токен Telegram-бота нельзя коммитить в репозиторий. Его нужно хранить только в GitHub Secrets.

## GitHub Secret

В репозитории откройте:

`Settings -> Secrets and variables -> Actions -> New repository secret`

Создайте секрет:

```text
Name: WB_RADAR_TELEGRAM_TOKEN
Secret: токен вашего Telegram-бота
```

## Как пользоваться

1. Напишите боту `/start`.
2. Отправьте ссылку Wildberries, например:

```text
https://www.wildberries.ru/catalog/123456789/detail.aspx
```

Бот добавит товар в отслеживание и пришлет текущую цену, скидку и остаток.

Команды:

```text
/list
/remove 123456789
```

## Расписание (когда включено)

GitHub Actions запускал проверку каждые 30 минут:

```yaml
cron: "*/30 * * * *"
```

Сейчас это отключено.
