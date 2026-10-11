"""Záznam provozu PID pro VJŘ.

Každé spuštění stáhne polohy vozů z Golemio API a do souboru
vjr-RRRRMMDD.json připíše, které spoje jednotlivá pořadí (linka/pořadí) jela.
Spouští ho GitHub Actions podle .github/workflows/zaznam.yml.
"""
import datetime
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

API = "https://api.golemio.cz/v2/vehiclepositions?limit=10000&preferredTimezone=Europe%2FPrague"
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "out")
KEEP_DAYS = 14
NEW_TRIP_GAP_MS = 20 * 60 * 1000
PRAGUE = ZoneInfo("Europe/Prague")


def find(obj, *keys, depth=0):
    """Najde první neprázdnou hodnotu pod některým z klíčů, i ve vnořených datech."""
    if not isinstance(obj, dict) or depth > 4:
        return None
    for k in keys:
        v = obj.get(k)
        if v not in (None, "") and not isinstance(v, (dict, list)):
            return v
    for v in obj.values():
        if isinstance(v, dict):
            r = find(v, *keys, depth=depth + 1)
            if r is not None:
                return r
    return None


def delay_of(p):
    lp = p.get("last_position") or {}
    d = lp.get("delay")
    if isinstance(d, dict):
        d = d.get("actual")
    if d is None:
        d = p.get("delay")
    try:
        return None if d is None else int(d)
    except (TypeError, ValueError):
        return None


def service_day(now):
    return (now - datetime.timedelta(hours=3)).strftime("%Y%m%d")


def main():
    key = os.environ.get("GOLEMIO_KEY", "").strip()
    if not key:
        sys.exit("Chybí tajný klíč GOLEMIO_KEY v nastavení repozitáře.")
    req = urllib.request.Request(API, headers={"X-Access-Token": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"Golemio odpovědělo chybou {e.code}. Zkontrolujte klíč GOLEMIO_KEY.")
    feats = data.get("features", data if isinstance(data, list) else [])

    now = datetime.datetime.now(PRAGUE)
    now_ms = int(time.time() * 1000)
    day = service_day(now)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"vjr-{day}.json"
    log = {"day": day, "since": now_ms, "runs": {}}
    if path.exists():
        try:
            log = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass

    seen = 0
    for f in feats:
        p = f.get("properties") or {}
        line = find(p, "route_short_name", "gtfs_route_short_name")
        run = find(p, "sequence_id", "run_number", "internal_run_number")
        route = find(p, "origin_route_name", "internal_route_name") or line
        if run is None or route is None:
            continue
        k = f"{route}/{run}"
        tid = str(find(p, "trip_id", "gtfs_trip_id") or f"{line}|{find(p, 'trip_headsign', 'gtfs_trip_headsign')}")
        rt = find(p, "route_type")
        rec = {
            "tid": tid,
            "line": str(line or route),
            "t": int(rt) if isinstance(rt, (int, float)) or (isinstance(rt, str) and rt.isdigit()) else None,
            "head": find(p, "trip_headsign", "gtfs_trip_headsign") or "",
            "evc": find(p, "vehicle_registration_number"),
            "start": find(p, "start_timestamp"),
        }
        d = delay_of(p)
        trips = log["runs"].setdefault(k, {"trips": []})["trips"]
        last = trips[-1] if trips else None
        if not last or last["tid"] != tid or now_ms - last["t1"] > NEW_TRIP_GAP_MS:
            trips.append({**rec, "t0": now_ms, "t1": now_ms, "d0": d, "d1": d})
        else:
            last["t1"] = now_ms
            last["d1"] = d
            if rec["evc"] is not None:
                last["evc"] = rec["evc"]
        seen += 1

    log["updated"] = now_ms
    path.write_text(json.dumps(log, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # staré dny smazat
    limit = (now - datetime.timedelta(days=KEEP_DAYS)).strftime("%Y%m%d")
    for old in OUT.glob("vjr-*.json"):
        if old.stem[4:] < limit:
            old.unlink()

    print(f"{now:%H:%M} · {len(feats)} vozů z Golemia · {seen} s pořadím · {len(log['runs'])} pořadí dnes")


if __name__ == "__main__":
    main()
