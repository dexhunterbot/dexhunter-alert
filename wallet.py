"""
Wallet pump alert -> Telegram
Kaychouf tokens li f wallet, w kol mra token kaytl3 +10% (b MCAP) mn l'entry
(+10%, +20%, +30%...), kaysifet notif. W nafs l7aja ila hbat -10%, -20%...
"""
import json
import math
import os
import sys
import time

import requests

KEY = os.environ.get("DEXHUNTER_KEY", "").strip()
TG_TOKEN = os.environ.get("TG_TOKEN", "").strip()
TG_CHAT = os.environ.get("TG_CHAT_ID", "").strip()
WALLET = os.environ.get("WALLET_ADDR", "").strip()
STEP = float(os.environ.get("STEP_PCT", "10"))
POLL_SEC = int(os.environ.get("POLL_SEC", "120"))
RUN_MINUTES = int(os.environ.get("RUN_MINUTES", "0"))
MIN_VALUE_ADA = float(os.environ.get("MIN_VALUE_ADA", "20"))  # tokens sghar bzaf ma kanraqbohomch
ENTRY = os.environ.get("ENTRY_MCAP", "")  # b7al "SOLADA=2070000,SNEK=50000000"
TEST = os.environ.get("TEST_MODE", "false").lower() == "true"
STATE_FILE = "wallet_state.json"

KOIOS = "https://api.koios.rest/api/v1"
DH = "https://api-us.dexhunterv3.app"
DH_PUBLIC = "https://dhapi.io"
H = {"X-Partner-Id": KEY, "Content-Type": "application/json", "Accept": "application/json"}
S = requests.Session()


def tg(text):
    try:
        r = S.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                   json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
                         "disable_web_page_preview": True}, timeout=20)
        if not r.ok:
            print("Telegram error:", r.status_code, r.text[:200])
    except Exception as e:
        print("Telegram exception:", e)


def koios(path, body):
    try:
        r = S.post(f"{KOIOS}/{path}", json=body, timeout=30)
        return r.json() if r.ok else []
    except Exception as e:
        print("Koios error:", e)
        return []


def num(v):
    try:
        return float(v)
    except Exception:
        return None


def kfmt(x):
    if x is None:
        return "?"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"{int(x / div * 100 + 1e-6) / 100:g}{suf}"
    return f"{x:,.0f}" if x >= 10 else f"{x:g}"


# ---------- data ----------
_stake = [None]


def holdings():
    """{token_id: {"name", "qty"}} (qty b units 3adiyin)"""
    if not _stake[0]:
        ai = koios("address_info", {"_addresses": [WALLET]})
        _stake[0] = (ai[0].get("stake_address") if ai else None) or ""
    if _stake[0]:
        rows = koios("account_assets", {"_stake_addresses": [_stake[0]]})
    else:
        rows = koios("address_assets", {"_addresses": [WALLET]})
    out = {}
    for a in rows or []:
        tid = a.get("policy_id", "") + a.get("asset_name", "")
        dec = int(a.get("decimals") or 0)
        q = (num(a.get("quantity")) or 0) / (10 ** dec)
        try:
            name = bytes.fromhex(a.get("asset_name", "")).decode("utf-8")
            if not name.isprintable() or not name:
                raise ValueError
        except Exception:
            name = tid[:10]
        out[tid] = {"name": name, "qty": q}
    return out


_supply = {}


def supply(tid):
    if tid in _supply:
        return _supply[tid]
    rows = koios("asset_info", {"_asset_list": [[tid[:56], tid[56:]]]})
    s = None
    if rows:
        a = rows[0]
        dec = int((a.get("token_registry_metadata") or {}).get("decimals") or 0)
        s = (num(a.get("total_supply")) or 0) / (10 ** dec)
    _supply[tid] = s
    return s


def price_ada(tid):
    for url, hdr in ((f"{DH}/swap/averagePrice/ADA/{tid}", H), (f"{DH_PUBLIC}/swap/averagePrice/ADA/{tid}", {})):
        try:
            r = S.get(url, headers=hdr, timeout=20)
            if r.ok:
                d = r.json()
                p = num(d.get("price_ba")) if isinstance(d, dict) else None
                if p:
                    return p
        except Exception:
            pass
    return None


def entry_from_orders(tid):
    """Avg prix dyal chra mn DexHunter (ila chriti mn DexHunter)."""
    body = {"page": 0, "perPage": 100, "filters": [{"filterType": "STATUS", "values": ["COMPLETE"]}]}
    try:
        r = S.post(f"{DH}/swap/orders/{WALLET}", headers=H, json=body, timeout=30)
        if not r.ok:
            return None
        d = r.json()
        lst = d if isinstance(d, list) else next((d[k] for k in ("orders", "data", "items") if isinstance(d.get(k), list)), [])
    except Exception:
        return None
    ada_in = tok_out = 0.0
    for o in lst:
        tin = o.get("token_id_in") or ""
        tout = o.get("token_id_out") or ""
        if tout == tid and tin in ("", "lovelace", "ada"):
            a = num(o.get("amount_in")) or 0
            t = num(o.get("actual_out_amount") or o.get("amount_out")) or 0
            if a > 1e11:
                a /= 1e6
            ada_in += a
            tok_out += t
    return ada_in / tok_out if tok_out else None


def manual_entries():
    out = {}
    for part in ENTRY.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if num(v):
                out[k.strip().upper()] = num(v)
    return out


# ---------- main ----------
def check(state, first):
    hold = holdings()
    manual = manual_entries()
    sent = 0
    for tid, h in hold.items():
        p = price_ada(tid)
        if not p:
            continue
        value = p * h["qty"]
        if value < MIN_VALUE_ADA:
            continue
        sup = supply(tid)
        if not sup:
            continue
        mcap = p * sup
        st = state.get(tid)
        if st is None:
            entry = manual.get(h["name"].upper())
            src = "manual"
            if not entry:
                ep = entry_from_orders(tid)
                entry, src = (ep * sup, "dexhunter") if ep else (mcap, "daba")
            st = state[tid] = {"entry": entry, "level": 0, "src": src}
            print(f"Jdid: {h['name']} entry {kfmt(entry)} ({src}), daba {kfmt(mcap)}")
        elif h["name"].upper() in manual and st.get("src") != "manual":
            st.update(entry=manual[h["name"].upper()], level=0, src="manual")
        gain = (mcap / st["entry"] - 1) * 100
        old = level = st["level"]
        while gain >= (level + 1) * STEP:
            level += 1
        while gain <= (level - 1) * STEP:
            level -= 1
        st["level"] = level
        if level != old and level != 0:
            cost = h["qty"] * st["entry"] / sup
            pnl = value - cost
            head = (f"\U0001F680 <b>{h['name']} +{gain:.0f}%</b>" if gain > 0
                    else f"\U0001F53B <b>{h['name']} {gain:.0f}%</b>")
            pnl_line = (f"\U0001F7E2 Profit: +{kfmt(pnl)} ADA" if pnl >= 0
                        else f"\U0001F534 Loss: -{kfmt(-pnl)} ADA")
            tg(f"{head}\n"
               f"\U0001F4CA MCAP: {kfmt(st['entry'])} \u279C {kfmt(mcap)} ADA\n"
               f"\U0001F4BC 3ndk: {kfmt(h['qty'])} {h['name']} \u2248 {kfmt(value)} ADA\n"
               f"{pnl_line}\n"
               f"\U0001F4C4 CA: <code>{tid}</code>\n"
               f"\U0001F517 <a href=\"https://app.dexhunter.io/swap?tokenIdSell={tid}&tokenIdBuy=\">DexHunter</a>")
            sent += 1
        st["last"] = mcap
    for tid in list(state):
        if tid not in hold:
            del state[tid]  # token tba3
    if first and TEST:
        lines = [f"✅ Wallet alert khddam. Kanraqeb {len(state)} token (kol \u00b1{STEP:g}% mn entry):"]
        for tid, st in state.items():
            g = (st.get("last", st["entry"]) / st["entry"] - 1) * 100
            lines.append(f"• {hold[tid]['name']}: entry {kfmt(st['entry'])} → daba {kfmt(st.get('last'))} ({g:+.1f}%)")
        tg("\n".join(lines))
    return sent


def main():
    if not (TG_TOKEN and TG_CHAT and WALLET):
        print("Khass TG_TOKEN, TG_CHAT_ID w WALLET_ADDR f GitHub Secrets.")
        sys.exit(1)
    try:
        state = json.load(open(STATE_FILE))
    except Exception:
        state = {}
    end = time.time() + RUN_MINUTES * 60
    first = True
    while True:
        t0 = time.time()
        try:
            n = check(state, first)
            if n:
                print(f"Sifet {n} notif")
        except Exception as e:
            print("Error:", e)
        json.dump(state, open(STATE_FILE, "w"))
        first = False
        if RUN_MINUTES <= 0 or time.time() + POLL_SEC > end:
            break
        time.sleep(max(1, POLL_SEC - (time.time() - t0)))


if __name__ == "__main__":
    main()
