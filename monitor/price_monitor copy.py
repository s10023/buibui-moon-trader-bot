import argparse
import asyncio
import json
import requests
import websockets
from datetime import datetime, timedelta
from rich.console import Console
from rich.table import Table
from rich.live import Live

# --- Configuration ---
console = Console()
TELEGRAM_ENABLED = True  # Turn off if not needed

# Binance symbols to track
SYMBOLS = ["btcusdt", "ethusdt", "solusdt"]
SYMBOL_LABELS = {"btcusdt": "BTC", "ethusdt": "ETH", "solusdt": "SOL"}

# Price data containers
prices = {}
price_refs = {}
price_changes = {s: {"1h": None, "4h": None, "24h": None, "asia": None} for s in SYMBOLS}

# --- Telegram Notification ---
def send_telegram(msg):
    try:
        token = open("telegram_token.txt").read().strip()
        chat_id = open("telegram_chat_id.txt").read().strip()
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}
        )
    except Exception as e:
        console.print(f"[red]Telegram error: {e}[/red]")

# --- Reference Price Fetching ---
def fetch_reference_prices():
    url = "https://api.binance.com/api/v3/klines"
    now = datetime.utcnow()
    targets = {
        "1h": now - timedelta(hours=1),
        "4h": now - timedelta(hours=4),
        "24h": now - timedelta(hours=24),
        "asia": (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=0))
    }
    for symbol in SYMBOLS:
        price_refs[symbol] = {}
        for label, dt in targets.items():
            params = {
                "symbol": symbol.upper(),
                "interval": "1h",
                "startTime": int(dt.timestamp() * 1000),
                "limit": 1
            }
            try:
                r = requests.get(url, params=params)
                if r.ok and r.json():
                    price_refs[symbol][label] = float(r.json()[0][4])
            except:
                price_refs[symbol][label] = None

# --- % Change Calculation ---
def format_change(curr, ref):
    if not curr or not ref:
        return "-"
    pct = (curr - ref) / ref * 100
    color = "green" if pct > 0 else "red" if pct < 0 else "white"
    return f"[{color}]{pct:+.2f}%[/{color}]"

def update_price_changes(symbol, curr_price):
    for key, ref in price_refs[symbol].items():
        price_changes[symbol][key] = format_change(curr_price, ref)

# --- Rich Table Renderer ---
def build_table(sort_by=None):
    table = Table(show_header=True, header_style="bold blue")
    table.add_column("Symbol")
    table.add_column("Price", justify="right")
    table.add_column("1H", justify="right")
    table.add_column("4H", justify="right")
    table.add_column("Asia", justify="right")
    table.add_column("24H", justify="right")

    def get_sort_key(sym):
        raw = price_changes[sym].get(sort_by, "0")
        return float(raw.strip("[]%+")) if raw and "%" in raw else 0

    display_syms = sorted(SYMBOLS, key=get_sort_key, reverse=True) if sort_by else SYMBOLS
    for sym in display_syms:
        p = prices.get(sym)
        price_str = f"[cyan]{p:.2f}[/cyan]" if p else "-"
        table.add_row(
            SYMBOL_LABELS[sym],
            price_str,
            price_changes[sym]["1h"],
            price_changes[sym]["4h"],
            price_changes[sym]["asia"],
            price_changes[sym]["24h"]
        )
    return table

# --- WebSocket Price Feed ---
async def price_feed():
    stream = "/".join([f"{s}@miniTicker" for s in SYMBOLS])
    url = f"wss://stream.binance.com:9443/stream?streams={stream}"
    async with websockets.connect(url) as ws:
        async for message in ws:
            data = json.loads(message).get("data", {})
            sym = data.get("s", "").lower()
            price = float(data.get("c", 0))
            prices[sym] = price
            update_price_changes(sym, price)

# --- CLI & Main Logic ---
def run_static(sort_by):
    fetch_reference_prices()
    # Dummy initial price snapshot
    for sym in SYMBOLS:
        ticker = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": sym.upper()}).json()
        prices[sym] = float(ticker["price"])
        update_price_changes(sym, prices[sym])

    table = build_table(sort_by)
    console.print(table)
    if TELEGRAM_ENABLED:
        msg = "📊 *Crypto Snapshot*\n"
        for row in table.rows:
            msg += " - " + " | ".join(cell.plain for cell in row.cells) + "\n"
        send_telegram(msg)

async def run_live(sort_by):
    fetch_reference_prices()
    with Live(build_table(sort_by), refresh_per_second=1) as live:
        await asyncio.gather(
            price_feed(),
            refresh_loop(live, sort_by)
        )

async def refresh_loop(live, sort_by):
    while True:
        live.update(build_table(sort_by))
        await asyncio.sleep(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Enable live terminal updates")
    parser.add_argument("--sort", choices=["1h", "4h", "24h", "asia"], help="Sort by change column")
    args = parser.parse_args()

    if args.live:
        asyncio.run(run_live(args.sort))
    else:
        run_static(args.sort)
