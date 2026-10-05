"""北邮人论坛。

版面是 hash 路由的 SPA，`networkidle` 不稳 —— 用 domcontentloaded + 等选择器 + 重试，
这套组合跑 25 个版 0 失败。置顶行常年不动，必须跳过，否则每次都当成新帖。
"""
import json
import time

from core.browser import Session
from core.config import resolve
from core.schema import Item

JS_ROWS = """() => {
    const t = document.querySelector('table.board-list');
    if (!t) return [];
    const out = [];
    for (const tr of t.querySelectorAll('tr')) {
        if (tr.classList.contains('top')) continue;
        const tds = Array.from(tr.querySelectorAll('td'));
        if (tds.length < 5) continue;
        const a = tds[1].querySelector("a[href*='/article/']");
        if (!a) continue;
        const cells = tds.map(c => c.innerText.replace(/\\s+/g, ' ').trim());
        out.push({
            title: (a.innerText || '').trim(),
            url: 'https://bbs.byr.cn' + a.getAttribute('href').replace(/^#!?/, '/'),
            author: (cells[3] || '').replace(/^\\|\\s*/, ''),
            published: cells[2] || '',
            replies: parseInt(cells[4] || '0', 10) || 0,
            last_active: cells[5] || ''
        });
    }
    return out;
}"""

# nforum 正文在 class 带文章号的 td.a-content 里。
# 曾经用 document.body 兜底，结果抓回来满屏导航栏 —— 所以宁可空着也不兜底 body。
JS_BODY = """() => {
    const el = document.querySelector('td.a-content');
    return el ? el.innerText : '';
}"""


def read(cfg, url: str, max_chars: int = 1200) -> tuple[str, str]:
    """读单篇正文。降低信息成本的关键不是给链接，是告诉你要不要点开。"""
    import re

    with Session(cfg, "byr") as s:
        page = s.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(1200)
        body = page.evaluate(JS_BODY) or ""
        return page.title(), re.sub(r"\n{3,}", "\n\n", body.strip())[:max_chars]


def load_watchlist(cfg, only=None):
    wl = json.loads(resolve(cfg, "watchlist.json").read_text(encoding="utf-8"))
    wl = {k: v for k, v in wl.items() if not k.startswith("_")}
    if only:
        want = {b.strip() for b in only.split(",")}
        wl = {k: v for k, v in wl.items() if k in want}
    return wl


def fetch(cfg, only=None, quiet=False):
    """抓所有版面，返回 {board: [Item]}。不做过滤不打分的活。"""
    watch = load_watchlist(cfg, only)
    rows_per = cfg["sources"]["byr"]["rows_per_board"]
    timeout = cfg["net"]["timeout_ms"] + 10000
    tries = cfg["net"]["retries"]
    delay = cfg["net"]["delay_ms"] / 1000

    fetched: dict[str, list] = {}
    errors: list[str] = []

    with Session(cfg, "byr") as s:
        user = s.cookie("nforum[UTMPUSERID]", "login-user")
        if not user:
            raise SystemExit("会话失效，请重跑：python run.py login byr")
        if not quiet:
            print(f"SESSION ok: {user}   版面 {len(watch)} 个")

        page = s.new_page()

        def grab(board):
            last = None
            for _ in range(tries):
                try:
                    page.goto(f"https://bbs.byr.cn/board/{board}",
                              wait_until="domcontentloaded", timeout=timeout)
                    page.wait_for_selector("table.board-list tr", timeout=20000)
                    page.wait_for_timeout(400)
                    return page.evaluate(JS_ROWS)[:rows_per]
                except Exception as e:
                    last = e
                    page.wait_for_timeout(800)
            raise last

        for board, meta in watch.items():
            time.sleep(delay)
            try:
                rows = grab(board)
                fetched[board] = [
                    Item(source="byr", channel=meta["name"], **r) for r in rows
                ]
                if not quiet:
                    print(f"  {board:18} {meta['name']:16} {len(rows):>3} 条")
            except Exception as e:
                fetched[board] = []
                errors.append(f"{board}: {type(e).__name__} {str(e)[:60]}")
                if not quiet:
                    print(f"  {board:18} FAILED {str(e)[:50]}")

    return fetched, watch, errors
