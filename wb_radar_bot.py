#!/usr/bin/env python3
"""Telegram bot that tracks Wildberries product price, discount, and stock changes."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
WB_ENDPOINTS = [
    "https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}",
    "https://card.wb.ru/cards/detail?appType=1&curr=rub&dest=-1257786&nm={nm_id}",
]
USER_AGENT = "Mozilla/5.0 (compatible; WB_Radar/1.0)"
DEFAULT_STATE = Path("state/wb_radar_state.json")
LOW_STOCK_THRESHOLD = int(os.getenv("WB_RADAR_LOW_STOCK_THRESHOLD", "5"))


@dataclass
class Snapshot:
    nm_id: str
    name: str
    brand: str
    price_rub: float | None
    old_price_rub: float | None
    discount_percent: int | None
    total_qty: int | None
    url: str
    checked_at: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "nm_id": self.nm_id,
            "name": self.name,
            "brand": self.brand,
            "price_rub": self.price_rub,
            "old_price_rub": self.old_price_rub,
            "discount_percent": self.discount_percent,
            "total_qty": self.total_qty,
            "url": self.url,
            "checked_at": self.checked_at,
        }


def request_json(url: str, *, method: str = "GET", data: dict[str, Any] | None = None, timeout: int = 25) -> Any:
    body = None
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def telegram_call(token: str, method: str, payload: dict[str, Any] | None = None) -> Any:
    url = TELEGRAM_API.format(token=token, method=method)
    return request_json(url, method="POST" if payload else "GET", data=payload)


def send_message(token: str, chat_id: int | str, text: str) -> None:
    chunks = [text[i : i + 3900] for i in range(0, len(text), 3900)] or [text]
    for chunk in chunks:
        telegram_call(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": "false",
            },
        )


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"telegram_offset": 0, "tracks": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def extract_nm_id(text: str) -> str | None:
    patterns = [
        r"wildberries\.ru/catalog/(\d+)/",
        r"\.wb\.ru/catalog/(\d+)/",
        r"\bnm=(\d+)\b",
        r"\b(\d{7,12})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    return None


def money_from_units(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return round(number / 100, 2)


def product_price(product: dict[str, Any]) -> tuple[float | None, float | None]:
    sizes = product.get("sizes") or []
    for size in sizes:
        price = size.get("price") or {}
        if price:
            current = money_from_units(price.get("product") or price.get("total") or price.get("salePriceU"))
            old = money_from_units(price.get("basic") or price.get("priceU") or price.get("oldPrice"))
            if current:
                return current, old
        for key in ("salePriceU", "priceU", "basicPriceU"):
            current = money_from_units(size.get(key))
            if current:
                old = money_from_units(size.get("priceU") or size.get("basicPriceU"))
                return current, old
    current = money_from_units(product.get("salePriceU") or product.get("priceU"))
    old = money_from_units(product.get("priceU") or product.get("basicPriceU"))
    return current, old


def product_qty(product: dict[str, Any]) -> int | None:
    total = 0
    seen_stock = False
    for size in product.get("sizes") or []:
        for stock in size.get("stocks") or []:
            qty = stock.get("qty")
            if isinstance(qty, int):
                seen_stock = True
                total += qty
    return total if seen_stock else None


def fetch_product(nm_id: str) -> Snapshot:
    last_error: Exception | None = None
    for endpoint in WB_ENDPOINTS:
        url = endpoint.format(nm_id=urllib.parse.quote(nm_id))
        try:
            data = request_json(url)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            continue
        products = ((data.get("data") or {}).get("products") or []) if isinstance(data, dict) else []
        if not products:
            continue
        product = products[0]
        price, old_price = product_price(product)
        return Snapshot(
            nm_id=nm_id,
            name=str(product.get("name") or "Товар Wildberries"),
            brand=str(product.get("brand") or ""),
            price_rub=price,
            old_price_rub=old_price,
            discount_percent=product.get("sale") if isinstance(product.get("sale"), int) else None,
            total_qty=product_qty(product),
            url=f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx",
            checked_at=int(time.time()),
        )
    raise RuntimeError(f"Wildberries product {nm_id} was not found or API failed: {last_error}")


def format_price(value: float | None) -> str:
    if value is None:
        return "нет данных"
    return f"{value:,.2f} руб.".replace(",", " ")


def product_title(snapshot: Snapshot) -> str:
    return f"{snapshot.brand} {snapshot.name}".strip()


def baseline_message(snapshot: Snapshot) -> str:
    return (
        "Товар добавлен в отслеживание\n\n"
        f"{product_title(snapshot)}\n"
        f"Артикул: {snapshot.nm_id}\n"
        f"Цена: {format_price(snapshot.price_rub)}\n"
        f"Скидка: {snapshot.discount_percent if snapshot.discount_percent is not None else 'нет данных'}%\n"
        f"Остаток: {snapshot.total_qty if snapshot.total_qty is not None else 'нет данных'}\n"
        f"{snapshot.url}"
    )


def diff_message(old: dict[str, Any], new: Snapshot) -> str | None:
    lines: list[str] = []
    old_price = old.get("price_rub")
    if old_price != new.price_rub:
        lines.append(f"Цена: {format_price(old_price)} -> {format_price(new.price_rub)}")

    old_discount = old.get("discount_percent")
    if old_discount != new.discount_percent:
        lines.append(
            f"Скидка: {old_discount if old_discount is not None else 'нет данных'}% -> "
            f"{new.discount_percent if new.discount_percent is not None else 'нет данных'}%"
        )

    old_qty = old.get("total_qty")
    if new.total_qty is not None:
        if old_qty is None and new.total_qty <= LOW_STOCK_THRESHOLD:
            lines.append(f"Товар заканчивается: осталось {new.total_qty} шт.")
        elif isinstance(old_qty, int) and old_qty > LOW_STOCK_THRESHOLD >= new.total_qty:
            lines.append(f"Товар заканчивается: было {old_qty}, осталось {new.total_qty} шт.")
        elif isinstance(old_qty, int) and old_qty > 0 and new.total_qty == 0:
            lines.append("Товар закончился")

    if not lines:
        return None

    return (
        "Изменения по товару Wildberries\n\n"
        f"{product_title(new)}\n"
        f"Артикул: {new.nm_id}\n"
        + "\n".join(lines)
        + f"\n\n{new.url}"
    )


def handle_command(token: str, state: dict[str, Any], chat_id: int, text: str) -> None:
    tracks = state.setdefault("tracks", {}).setdefault(str(chat_id), {})
    normalized = text.strip()

    if normalized.startswith("/start") or normalized.startswith("/help"):
        send_message(
            token,
            chat_id,
            "Отправьте ссылку на товар Wildberries, и я буду проверять цену, скидку и остатки каждые 30 минут.\n\n"
            "Команды:\n"
            "/list - список отслеживаемых товаров\n"
            "/remove <артикул> - удалить товар",
        )
        return

    if normalized.startswith("/list"):
        if not tracks:
            send_message(token, chat_id, "Пока нет отслеживаемых товаров. Пришлите ссылку Wildberries.")
            return
        lines = ["Отслеживаемые товары:"]
        for nm_id, item in tracks.items():
            last = item.get("last") or {}
            lines.append(f"- {nm_id}: {last.get('brand', '')} {last.get('name', '')}".strip())
        send_message(token, chat_id, "\n".join(lines))
        return

    if normalized.startswith("/remove"):
        nm_id = extract_nm_id(normalized)
        if not nm_id:
            send_message(token, chat_id, "Укажите артикул: /remove 123456789")
            return
        if tracks.pop(nm_id, None):
            send_message(token, chat_id, f"Удалил товар {nm_id} из отслеживания.")
        else:
            send_message(token, chat_id, f"Товар {nm_id} не найден в вашем списке.")
        return

    nm_id = extract_nm_id(normalized)
    if not nm_id:
        send_message(token, chat_id, "Не вижу ссылку или артикул Wildberries. Пришлите ссылку на карточку товара.")
        return

    snapshot = fetch_product(nm_id)
    tracks[nm_id] = {"url": snapshot.url, "last": snapshot.to_dict(), "created_at": int(time.time())}
    send_message(token, chat_id, baseline_message(snapshot))


def process_updates(token: str, state: dict[str, Any]) -> None:
    offset = int(state.get("telegram_offset") or 0)
    result = telegram_call(token, "getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["message"])})
    for update in result.get("result", []):
        state["telegram_offset"] = max(int(state.get("telegram_offset") or 0), int(update["update_id"]) + 1)
        message = update.get("message") or {}
        text = message.get("text") or ""
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not chat_id or not text:
            continue
        try:
            handle_command(token, state, int(chat_id), text)
        except Exception as exc:
            send_message(token, chat_id, f"Не смог обработать сообщение: {exc}")


def check_tracks(token: str, state: dict[str, Any]) -> None:
    tracks_by_chat = state.setdefault("tracks", {})
    for chat_id, tracks in list(tracks_by_chat.items()):
        for nm_id, item in list(tracks.items()):
            try:
                snapshot = fetch_product(nm_id)
            except Exception as exc:
                item["last_error"] = str(exc)
                continue
            message = diff_message(item.get("last") or {}, snapshot)
            item["last"] = snapshot.to_dict()
            item.pop("last_error", None)
            if message:
                send_message(token, chat_id, message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--skip-checks", action="store_true")
    args = parser.parse_args()

    token = os.getenv("WB_RADAR_TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("WB_RADAR_TELEGRAM_TOKEN is not set", file=sys.stderr)
        return 2

    state = load_state(args.state)
    process_updates(token, state)
    if not args.skip_checks:
        check_tracks(token, state)
    state["last_run"] = int(time.time())
    save_state(args.state, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
