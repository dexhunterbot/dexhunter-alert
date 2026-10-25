"""
DexHunter big-sell alert -> Telegram
Kol sa3a: kayjbed les ordres li tkemmlou f DexHunter, w ila chi wa7ed ba3 token b > MIN_ADA ADA, kaysift message f Telegram.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

API = "https://api-us.dexhunterv3.app"
KEY = os.environ.get("DEXHUNTER_KEY", "").strip()
TG_TOKEN = os.environ.get("TG_TOKEN", "").strip()
TG_CHAT = os.environ.get("TG_CHAT_ID", "").strip()
MIN_ADA = float(os.environ.get("MIN_ADA", "10000"))
LOOKBACK_MIN = int(os.environ.get("LOOKBACK_MIN", "75"))
TEST = os.environ.get("TEST_MODE", "false").lower() == "true"
SEEN_FILE = "seen.json"

ADA_IDS = {"", "ada", "lovelace", "000000000000000000000000000000000000000000000000000000006c6f76656c616365"}
H = {"X-Partner-Id": KEY, "Content-Type": "application/json", "Accept": "application/json"}


def tg(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=20,
    )
    if not r.ok:
        print("Telegram error:", r.status_code, r.text)


def is_ada(tid):
    return (tid or "").lower() in ADA_IDS


def g(o, *names, default=None):
    for n in names:
        if isinstance(o, dict) and o.get(n) not in (None, ""):
            return o[n]
    return default


def to_ts(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e12 else v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def ada_amount(v):
    """Amount dyal ADA: ila kbir bzaf rah b lovelace."""
    try:
        v = float(v)
    except Exception:
        return 0.0
    return v / 1_000_000 if v > 1e11 else v


def orders_payload(page, token_id=None):
    filters = [{"filterType": "STATUS", "values": ["COMPLETE"]}]
    if token_id:
        filters.append({"filterType": "TOKENID", "values": [token_id]})
    return {"page": page, "perPage": 100, "filters": filters,
            "orderSorts": "STARTTIME", "sortDirection": "DESC"}


def extract_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("orders", "data", "items", "results"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


def fetch_recent_orders(since_ts):
    """Kanjarrbo global orders; ila ma khdmach, kandouzo 3la trending tokens w ordersByPair."""
    out = []
    for endpoint in ("/swap/globalOrders", "/swap/ordersByPair"):
        out = []
        ok = False
        for page in range(0, 10):
            r = requests.post(API + endpoint, headers=H, json=orders_payload(page), timeout=30)
            print(f"{endpoint} page {page}: HTTP {r.status_code}")
            if not r.ok:
                print(r.text[:500])
                break
            ok = True
            lst = extract_list(r.json())
            if not lst:
                break
            out.extend(lst)
            oldest = to_ts(g(lst[-1], "submission_time", "created_at", "createdAt", "update_time", "last_update"))
            if oldest and oldest < since_ts:
                break
            time.sleep(0.3)
        if ok and out:
            return out
    # fallback: trending tokens
    print("Fallback: trending tokens")
    r = requests.post(API + "/trending", headers=H, json={}, timeout=30)
    print("/trending:", r.status_code)
    tokens = []
    if r.ok:
        for t in extract_list(r.json()):
            tid = g(t, "token_id", "tokenId", "id")
            if tid and not is_ada(tid):
                tokens.append(tid)
    for tid in tokens[:40]:
        r = requests.post(API + "/swap/ordersByPair", headers=H, json=orders_payload(0, tid), timeout=30)
        if r.ok:
            out.extend(extract_list(r.json()))
        time.sleep(0.3)
    return out


_names = {}


def token_name(tid):
    if tid in _names:
        return _names[tid]
    name = tid[:10] + "…"
    try:
        r = requests.get(f"{API}/swap/token/{tid}", headers=H, timeout=15)
        if r.ok:
            d = r.json()
            name = g(d, "ticker", "token_ascii", default=name)
    except Exception:
        pass
    _names[tid] = name
    return name


def main():
    if not (KEY and TG_TOKEN and TG_CHAT):
        print("Khass DEXHUNTER_KEY, TG_TOKEN w TG_CHAT_ID f GitHub Secrets.")
        sys.exit(1)

    since = time.time() - LOOKBACK_MIN * 60
    seen = set()
    if os.path.exists(SEEN_FILE):
        try:
            seen = set(json.load(open(SEEN_FILE)))
        except Exception:
            pass

    orders = fetch_recent_orders(since)
    print(f"Jbedt {len(orders)} ordres")
    if orders:
        print("Mital (bach nfhmou format):", json.dumps(orders[0])[:800])

    alerts = 0
    for o in orders:
        tin = g(o, "token_id_in", "tokenIdIn", "token_in", default="")
        tout = g(o, "token_id_out", "tokenIdOut", "token_out", default="")
        if is_ada(tin) or not is_ada(tout):
            continue  # machi bay3a dyal token -> ADA
        ts = to_ts(g(o, "submission_time", "created_at", "createdAt", "update_time", "last_update"))
        if ts and ts < since:
            continue
        ada = ada_amount(g(o, "actual_out_amount", "amount_out", "expected_out_amount", "output_amount", default=0))
        if ada < MIN_ADA:
            continue
        tx = g(o, "tx_hash", "txHash", "_id", "id", default="")
        uid = str(tx or json.dumps(o, sort_keys=True))
        if uid in seen:
            continue
        seen.add(uid)
        amt_in = g(o, "amount_in", "amountIn", default="?")
        when = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else "?"
        link = f'\n<a href="https://cardanoscan.io/transaction/{tx}">Chouf transaction</a>' if tx and len(str(tx)) == 64 else ""
        tg(f"🚨 <b>BIG SELL</b> f DexHunter\n"
           f"Token: <b>{token_name(tin)}</b>\n"
           f"Tba3: {amt_in} {token_name(tin)}\n"
           f"Qima: <b>{ada:,.0f} ADA</b>\n"
           f"Wqt: {when}{link}")
        alerts += 1

    json.dump(list(seen)[-5000:], open(SEEN_FILE, "w"))
    print(f"Sifet {alerts} alerte(s)")

    if TEST:
        tg(f"✅ Test: robot khddam. Jbed {len(orders)} ordres, l9a {alerts} vente(s) > {MIN_ADA:,.0f} ADA.")


if __name__ == "__main__":
    main()
