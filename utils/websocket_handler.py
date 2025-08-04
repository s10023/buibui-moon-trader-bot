import json
import threading
import websocket

live_prices = {}

def on_price_message(ws, message):
    data = json.loads(message)
    symbol = data['s']
    current_price = float(data['c'])
    live_prices[symbol] = current_price

def on_ws_error(ws, error):
    print("WebSocket Error:", error)

def on_ws_close(ws, close_status_code, close_msg):
    print("WebSocket closed")

def on_ws_open(ws):
    print("WebSocket connection opened")

def start_price_websocket(symbols):
    streams = "/".join([f"{symbol.lower()}@ticker" for symbol in symbols])
    url = f"wss://stream.binance.com:9443/stream?streams={streams}"
    ws = websocket.WebSocketApp(
        url,
        on_open=on_ws_open,
        on_message=on_price_message,
        on_error=on_ws_error,
        on_close=on_ws_close,
    )
    threading.Thread(target=ws.run_forever, daemon=True).start()
