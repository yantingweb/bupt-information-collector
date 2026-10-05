"""教务处 / 学院 / 学生处通知。

学校官网改版不频繁但一旦改就全改，所以这里写成**通用列表页抽取**：
默认抽「看起来像正文页」的链接，每个栏目可以在 config.yaml 里单独指定 CSS 选择器覆盖。
这样改版时只需要改配置里的一行 selector，不用改代码。
"""
import re
import time
from urllib.parse import urljoin

from core.browser import Session
from core.schema import Item

# 通用抽取：找同一列里「链接 + 后面跟着日期」的那些行
# 注意这里是 raw string —— 写成普通字符串时 \s 会被 Python 提前吃掉
JS_LINKS = r"""(cfg) => {
    const base = location.href;
    const bad = /(javascript:|mailto:|#$|\.(jpg|png|pdf|doc|xls|zip)$)/i;
    // 日期：年月日齐全最好；很多院系列表只给「2026-07」，也得认
    const DATE = /(20\d{2})[-\/年.](\d{1,2})(?:[-\/月.](\d{1,2}))?/;
    const rows = [];
    const push = (a, scope) => {
        // ① title 属性优先：院系站（VSB 系）列表常被 CSS 动画藏起来，innerText 是空的，
        //    标题只活在 title 里；反过来直接用 innerText 还会混进日期和摘要。
        let t = (a.getAttribute('title') || '').trim();
        if (t.length < 6) t = (a.innerText || '').trim().replace(/\s+/g, ' ');
        if (t.length < 6) return;
        const href = a.getAttribute('href') || '';
        if (!href || bad.test(href)) return;
        const abs = new URL(href, base).href;
        // ② textContent 而不是 innerText：不可见元素 innerText 返回空字符串，日期就丢了
        const rowText = (scope.textContent || '').replace(/\s+/g, ' ').trim();
        const m = rowText.match(DATE);
        rows.push({
            title: t, url: abs,
            published: m
                ? `${m[1]}-${String(m[2]).padStart(2,'0')}-${String(m[3]||'01').padStart(2,'0')}`
                : ''
        });
    };
    if (cfg.selector) {
        document.querySelectorAll(cfg.selector).forEach(el => {
            const a = el.tagName === 'A' ? el : el.querySelector('a');
            if (a) push(a, el);
        });
    } else {
        const seenAbs = new Set();
        document.querySelectorAll('a').forEach(a => {
            const href = a.getAttribute('href') || '';
            if (!href || bad.test(href)) return;
            const u = new URL(href, base).href;
            if (!u.startsWith(location.origin)) return;
            if (!/[\d]{3,}/.test(u)) return;          // 正文页 URL 一般带一串数字或.htm
            if (u === location.href) return;
            if (seenAbs.has(u)) return;
            seenAbs.add(u);
            push(a, a.closest('li') || a.parentElement || a);
        });
    }
    return rows;
}"""

# 标题里这些词的通知，几乎一定有时限，要看
DEADLINE_HINT = re.compile(r"报名|截止|通知|选拔|公示|推荐|认定|办法|招募|Result|结果|名单")


def fetch(cfg, only=None, quiet=False):
    src = cfg["sources"]["portal"]
    timeout = cfg["net"]["timeout_ms"]
    tries = cfg["net"]["retries"]
    delay = cfg["net"]["delay_ms"] / 1000
    per = src.get("rows_per_list", 30)

    lists = src["lists"]
    if only:
        want = {o.strip() for o in only.split(",")}
        lists = [l for l in lists if l["name"] in want]

    if not lists:
        raise SystemExit("config.yaml 的 sources.portal.lists 是空的，先填几个通知列表页")

    fetched: dict[str, list] = {}
    errors: list[str] = []

    with Session(cfg, "portal") as s:
        page = s.new_page()
        for entry in lists:
            time.sleep(delay)
            got, last = [], None
            for _ in range(tries):
                try:
                    page.goto(entry["url"], wait_until="domcontentloaded", timeout=timeout)
                    page.wait_for_timeout(1200)
                    rows = page.evaluate(JS_LINKS, {"selector": entry.get("selector")})
                    got = rows[:per]
                    break
                except Exception as e:
                    last = e
                    page.wait_for_timeout(1000)
            if not got and last:
                errors.append(f"{entry['name']}: {type(last).__name__} {str(last)[:60]}")
                if not quiet:
                    print(f"  {entry['name']:16} FAILED {str(last)[:50]}")
                continue

            dedup, seenurl = [], set()
            for r in got:
                k = r["url"].split("?")[0]
                if k in seenurl:
                    continue
                seenurl.add(k)
                dedup.append(r)

            fetched[entry["name"]] = [
                Item(source="portal", channel=entry["name"], **r) for r in dedup
            ]
            if not quiet:
                print(f"  {entry['name']:16} {len(dedup):>3} 条")

    return fetched, {l["name"]: {"name": l["name"], "tier": 1} for l in lists}, errors
