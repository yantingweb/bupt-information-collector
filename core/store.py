"""落盘：去重快照 + 累积条目。

两个文件，职责不同：
  data/seen/<source>.json   只看 URL，判断「这条见没见过」。永远不清。
  data/items.jsonl          每条 item 一行。给 dashboard 和周报用，保留最近 N 条。
"""
import datetime
import itertools
import json
import pathlib

from core.config import resolve

KEEP_ITEMS = 4000


def _seen_path(cfg, source: str) -> pathlib.Path:
    p = resolve(cfg, cfg["paths"]["data_dir"]) / "seen"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{source}.json"


def _items_path(cfg) -> pathlib.Path:
    return resolve(cfg, cfg["paths"]["data_dir"]) / "items.jsonl"


def load_seen(cfg, source: str) -> dict:
    p = _seen_path(cfg, source)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_seen(cfg, source: str, seen: dict):
    _seen_path(cfg, source).write_text(
        json.dumps(seen, ensure_ascii=False, indent=0), encoding="utf-8")


def reset_seen(cfg, source: str):
    p = _seen_path(cfg, source)
    if p.exists():
        p.unlink()


def history_count(cfg, source: str, items) -> dict:
    """每个 channel 以前见过多少条 —— 用来判断某栏目是不是冷启动。"""
    seen = load_seen(cfg, source)
    hist: dict[str, int] = {}
    for k in seen:
        ch = k.split("/article/")[0].rsplit("/", 1)[-1] if "/article/" in k else "?"
        hist[ch] = hist.get(ch, 0) + 1
    return hist


def append_items(cfg, item_list):
    """把这次抓到的条目追加进 items.jsonl（带 score/grade），并裁剪旧数据。"""
    if not item_list:
        return
    p = _items_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    with p.open("a", encoding="utf-8") as f:
        for it in item_list:
            d = it.to_dict()
            d["_seen_at"] = stamp
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    lines = p.read_text(encoding="utf-8").splitlines()
    if len(lines) > KEEP_ITEMS:
        p.write_text("\n".join(lines[-KEEP_ITEMS:]) + "\n", encoding="utf-8")


# ── 最近一次扫描的结果 ──────────────────────────────────────────────────────
# 没有新东西时 scan 是**故意不推**的（不打扰）。可这样有个副作用：
# 「今天确实什么都没发生」和「系统早就坏了」在手机上是同一个样子 —— 都是没消息。
# 所以把每次扫描的结果记下来，让心跳消息和 doctor 替它说话。

def _scan_report_path(cfg) -> pathlib.Path:
    return resolve(cfg, f"{cfg['paths']['data_dir']}/last_scan.json")


def save_scan_report(cfg, per_source: dict, pushed: int = 0):
    p = _scan_report_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sources": per_source,
        "pushed": pushed,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def load_scan_report(cfg):
    p = _scan_report_path(cfg)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def read_items(cfg, limit=500, source=None, min_score=None):
    p = _items_path(cfg)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if source:
        rows = [r for r in rows if r.get("source") == source]
    if min_score is not None:
        rows = [r for r in rows if r.get("score", 0) >= min_score]
    seen_key, uniq = set(), []
    for r in reversed(rows):
        k = r.get("url", "").split("?")[0]
        if k in seen_key:
            continue
        seen_key.add(k)
        uniq.append(r)
    return list(itertools.islice(uniq, limit))
