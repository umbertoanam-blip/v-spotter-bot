"""
V-SPOTTER TELEGRAM BOT
======================
Monitora 30 coin su Binance ogni 5 minuti e manda un alert su Telegram
quando rileva una V-formation in formazione (early signal).

CONFIGURAZIONE: modifica solo i valori in CONFIG qui sotto.
"""

import requests
import time
import json
import os
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================
TELEGRAM_TOKEN = "8994731342:AAEzrFnX1Yk8dLYWGNxIpdJ9qdHg2vlmBwc"
TELEGRAM_CHAT_ID = "1271634118"

# 12 coin principali (dashboard visiva) + 18 coin in background
COINS = [
    # Coin principali
    "BNBUSDT", "SOLUSDT", "AVAXUSDT", "NEARUSDT", "INJUSDT", "WLDUSDT",
    "SUIUSDT", "ARBUSDT", "SEIUSDT", "TAOUSDT", "BTCUSDT", "AAVEUSDT",
    # Coin background
    "DOGEUSDT", "LINKUSDT", "DOTUSDT", "OPUSDT", "APTUSDT", "TIAUSDT",
    "PEPEUSDT", "JUPUSDT", "RENDERUSDT", "FETUSDT", "ATOMUSDT", "LDOUSDT",
    "PENGUUSDT", "ZKUSDT", "ENAUSDT", "TONUSDT", "ONDOUSDT", "XRPUSDT"
]

# Coin principali — nell'alert viene indicato se è una coin "extra"
MAIN_COINS = {"BNBUSDT","SOLUSDT","AVAXUSDT","NEARUSDT","INJUSDT","WLDUSDT",
              "SUIUSDT","ARBUSDT","SEIUSDT","TAOUSDT","BTCUSDT","AAVEUSDT"}

CHECK_INTERVAL_SECONDS = 300
TIMEFRAME = "15m"
CANDLES_LIMIT = 60
HIGHER_TIMEFRAME = "1h"
HIGHER_TF_CANDLES_LIMIT = 30

STATE_FILE = "v_spotter_state.json"
SIGNALS_LOG_FILE = "v_spotter_signals_log.json"

# ============================================================
# INDICATORI TECNICI
# ============================================================

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    tr = []
    for i in range(1, len(closes)):
        tr.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr = sum(tr[:period]) / period
    for i in range(period, len(tr)):
        atr = (atr*(period-1)+tr[i]) / period
    return atr


def calc_supertrend(highs, lows, closes, period=10, mult=3):
    if len(closes) < period + 2:
        return None, None
    tr = []
    for i in range(1, len(closes)):
        tr.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr_arr = []
    atr = sum(tr[:period]) / period
    atr_arr.append(atr)
    for i in range(period, len(tr)):
        atr = (atr*(period-1)+tr[i]) / period
        atr_arr.append(atr)
    pu, pl, pd, direction = 0, 0, 1, 1
    for i in range(len(atr_arr)):
        ci = i + 1
        hl2 = (highs[ci]+lows[ci]) / 2
        bu = hl2 + mult*atr_arr[i]
        bl = hl2 - mult*atr_arr[i]
        ub = bu if (i==0 or bu<pu or closes[ci-1]>pu) else pu
        lb = bl if (i==0 or bl>pl or closes[ci-1]<pl) else pl
        if i == 0:
            direction = 1 if closes[ci] > ub else -1
        else:
            direction = (-1 if closes[ci]<lb else 1) if pd==1 else (1 if closes[ci]>ub else -1)
        pu, pl, pd = ub, lb, direction
    return ('up' if direction==1 else 'down'), (pl if direction==1 else pu)


def calc_vwap(highs, lows, closes, vols):
    n = len(closes)
    if n < 2:
        return None
    window = min(30, n)
    tvp, tv = 0.0, 0.0
    for i in range(n-window, n):
        tp = (highs[i]+lows[i]+closes[i]) / 3
        tvp += tp * vols[i]
        tv += vols[i]
    return tvp/tv if tv > 0 else None


def detect_early_v(klines, higher_tf_up=None):
    n = len(klines)
    if n < 12:
        return {'signal': False}
    closes = [float(k[4]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    vols   = [float(k[5]) for k in klines]
    atr = calc_atr(highs, lows, closes, min(14, n-2))
    if not atr:
        return {'signal': False}
    recent_window = 10
    search_start = n - recent_window
    min_idx, min_val = search_start, closes[search_start]
    for i in range(search_start, n-2):
        if closes[i] < min_val:
            min_val = closes[i]
            min_idx = i
    candles_after_min = n - 1 - min_idx
    if candles_after_min < 2 or candles_after_min > 6:
        return {'signal': False}
    min_move = atr * 1.5
    before_min = closes[max(0, min_idx-4)]
    if before_min - min_val < min_move:
        return {'signal': False}
    after_min = closes[min_idx:n]
    rising_count = sum(1 for i in range(1, len(after_min)) if after_min[i] >= after_min[i-1])
    rising_ratio = rising_count / (len(after_min)-1) if len(after_min) > 1 else 0
    total_rise = closes[n-1] - min_val
    recent_vols = vols[min_idx:n]
    avg_vol = sum(vols) / len(vols)
    avg_recent_vol = sum(recent_vols) / len(recent_vols)
    volume_confirms = avg_recent_vol >= avg_vol * 0.65
    vwap = calc_vwap(highs, lows, closes, vols)
    current_price = closes[n-1]
    price_before_min = closes[max(0, min_idx-1)]
    vwap_reclaim = vwap is not None and current_price > vwap and price_before_min <= vwap * 1.001
    vwap_ok = True if vwap is None else vwap_reclaim
    counter_trend = higher_tf_up is False
    rising_ratio_needed = 0.75 if counter_trend else 0.6
    rise_multiplier_needed = 0.6 if counter_trend else 0.4
    if rising_ratio >= rising_ratio_needed and total_rise > min_move*rise_multiplier_needed and volume_confirms and vwap_ok:
        return {'signal': True, 'vwap_reclaim': vwap_reclaim, 'counter_trend': counter_trend}
    return {'signal': False}


def fmt_price(p):
    if p < 0.01: return f"{p:.6f}"
    if p < 1:    return f"{p:.4f}"
    if p < 100:  return f"{p:.3f}"
    return f"{p:.2f}"

# ============================================================
# BINANCE API
# ============================================================

def get_klines(symbol, interval=None, limit=None):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval or TIMEFRAME}&limit={limit or CANDLES_LIMIT}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_ticker(symbol):
    url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()

# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"Errore Telegram: {e}")
        return False

# ============================================================
# STATO E LOG
# ============================================================

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def append_signal_log(entry):
    log = []
    if os.path.exists(SIGNALS_LOG_FILE):
        try:
            with open(SIGNALS_LOG_FILE, 'r') as f:
                log = json.load(f)
        except:
            log = []
    log.append(entry)
    log = log[-500:]
    with open(SIGNALS_LOG_FILE, 'w') as f:
        json.dump(log, f, indent=2)

# ============================================================
# CICLO PRINCIPALE
# ============================================================

def check_all_coins():
    state = load_state()
    new_alerts = []

    for symbol in COINS:
        try:
            klines = get_klines(symbol)
            if len(klines) < 15:
                continue
            ticker = get_ticker(symbol)
            price = float(ticker['lastPrice'])
            change = float(ticker['priceChangePercent'])
            closes = [float(k[4]) for k in klines]
            highs  = [float(k[2]) for k in klines]
            lows   = [float(k[3]) for k in klines]
            st_direction, _ = calc_supertrend(highs, lows, closes)

            higher_tf_up = None
            try:
                klines_1h = get_klines(symbol, interval=HIGHER_TIMEFRAME, limit=HIGHER_TF_CANDLES_LIMIT)
                if len(klines_1h) >= 15:
                    h1 = [float(k[2]) for k in klines_1h]
                    l1 = [float(k[3]) for k in klines_1h]
                    c1 = [float(k[4]) for k in klines_1h]
                    st1h_dir, _ = calc_supertrend(h1, l1, c1)
                    higher_tf_up = True if st1h_dir=='up' else (False if st1h_dir=='down' else None)
            except:
                pass

            early = detect_early_v(klines, higher_tf_up)
            is_early_v = early.get('signal', False)
            name = symbol.replace("USDT", "")
            was_belled = state.get(symbol, False)

            if is_early_v and not was_belled:
                emoji_trend = "🟢" if st_direction=='up' else "🔴"
                is_main = symbol in MAIN_COINS
                coin_badge = "" if is_main else " 🔍"  # indica coin extra non in dashboard
                badge_lines = []
                if early.get('vwap_reclaim'):
                    badge_lines.append("✅ VWAP riconquistato (alta fiducia)")
                if early.get('counter_trend'):
                    badge_lines.append("⚠️ Trend 1h contrario (più rischioso)")
                badge_text = ("\n" + "\n".join(badge_lines) + "\n") if badge_lines else "\n"

                msg = (
                    f"🔔 <b>V IN FORMAZIONE</b>\n\n"
                    f"💰 <b>{name}</b>{coin_badge}\n"
                    f"Prezzo: ${fmt_price(price)} ({'+' if change>=0 else ''}{change:.2f}%)\n"
                    f"Supertrend: {emoji_trend} {st_direction.upper() if st_direction else 'N/A'}\n"
                    f"Rimbalzo confermato da 2-3 candele"
                    f"{badge_text}"
                    f"⏰ {datetime.now().strftime('%H:%M:%S')}\n"
                    f"📊 Controlla il grafico ora"
                )
                send_telegram_message(msg)
                new_alerts.append(name)
                print(f"[{datetime.now()}] ALERT: {name} (main={is_main}, vwap={early.get('vwap_reclaim')}, counter={early.get('counter_trend')})")

                append_signal_log({
                    "symbol": symbol, "name": name, "price": price,
                    "time": datetime.now().isoformat(),
                    "supertrend": st_direction,
                    "vwap_reclaim": early.get('vwap_reclaim', False),
                    "counter_trend": early.get('counter_trend', False),
                    "higher_tf_up": higher_tf_up,
                    "is_main_coin": is_main
                })

            state[symbol] = is_early_v

        except Exception as e:
            print(f"Errore su {symbol}: {e}")
            continue

    save_state(state)
    return new_alerts


def main():
    print(f"=== V-SPOTTER TELEGRAM BOT avviato ===")
    print(f"Monitoraggio: {len(COINS)} coin ({len(MAIN_COINS)} principali + {len(COINS)-len(MAIN_COINS)} background)")
    print(f"Intervallo: {CHECK_INTERVAL_SECONDS}s — Filtri: VWAP reclaim + trend 1h")

    send_telegram_message(
        f"✅ <b>V-Spotter Bot avviato</b>\n\n"
        f"Monitoro <b>{len(COINS)} coin</b>\n"
        f"• {len(MAIN_COINS)} principali (in dashboard)\n"
        f"• {len(COINS)-len(MAIN_COINS)} background (solo alert)\n"
        f"Controllo ogni {CHECK_INTERVAL_SECONDS//60} minuti\n"
        f"Filtri: VWAP reclaim + trend 1h\n"
        f"🔍 = coin extra non in dashboard"
    )

    while True:
        try:
            print(f"\n[{datetime.now()}] Controllo in corso...")
            alerts = check_all_coins()
            if alerts:
                print(f"  -> Alert: {', '.join(alerts)}")
            else:
                print(f"  -> Nessun nuovo segnale")
        except Exception as e:
            print(f"Errore generale: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
