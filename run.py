"""总入口。所有日常操作都从这里走。

    python run.py scan              # 扫所有启用的源，过滤后写 inbox
    python run.py scan byr          # 只扫论坛
    python run.py scan byr --dump   # 打印全部新条目，不做过滤（调 rules.yaml 时用）
    python run.py scan --push       # 扫完直接推到手机（定时任务用这个）
    python run.py push              # 只推：把今天还没推过的结果发出去
    python run.py push --text "xx"  # 推一条任意文本（测通道用）
    python run.py read Paper/50368  # 读单篇正文
    python run.py login byr         # 开浏览器登录论坛，存会话快照
    python run.py cold              # 冷启动：建去重基线，一条都不推
    python run.py doctor            # 自检：为什么没收到推送
"""
import argparse
import datetime

from core.config import ensure_dirs, load_config
from core.digest import write_inbox
from core.rules import Scorer
from core import store
from sources import get


SOURCES = ["byr", "portal"]


def _diff(cfg, source, fetched, watch, cold=False, window=30):
    """返回新条目（已按标题跨栏目折叠）。"""
    seen = store.load_seen(cfg, source)
    hist = store.history_count(cfg, source, fetched)
    today = datetime.date.today()
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    new, fresh = [], dict(seen)
    for channel, items in fetched.items():
        # 冷启动：某个栏目从来没扫过时不推老帖，否则会把去年的东西全推出来
        first_scan = hist.get(channel, 0) == 0
        for it in items:
            k = it.key()
            if k in seen:
                continue
            fresh[k] = stamp
            if cold and first_scan:
                age = it.age_days(today)
                # 抽不到日期不等于陈旧 —— 通知列表页本来就是倒序的最新几条。
                # 早先写成 `if age is None or age > window: continue`，
                # 结果抽不到日期的条目被当成老帖全丢，最值钱的学院通知一条都没出来。
                if age is not None and age > window:
                    continue
            new.append(it)

    dedup = {}
    for it in new:
        dedup[it.title.strip()] = it
    return list(dedup.values()), fresh, len(new) - len(dedup)


def _scan_one(cfg, name, scorer, args):
    mod = get(name)
    if getattr(args, "reset", False):
        store.reset_seen(cfg, name)
        print(f"  已清掉 {name} 的去重快照，这次会把窗口内的重新算一遍")
    fetched, watch, errors = mod.fetch(cfg, getattr(args, "boards", None), quiet=False)

    if errors:
        print(f"  [{name}] 部分失败：")
        for e in errors:
            print("      ", e)

    # 论坛日更几十条，30 天窗有意义；学院通知一年才发几条，最新的也可能是两个月前，
    # 而那恰恰可能最重要的。所以窗宽按源配，不能一刀切。
    window = cfg["sources"][name].get("cold_window_days", args.window)
    new, fresh, folded = _diff(cfg, name, fetched, watch,
                               cold=not args.all, window=window)
    total = sum(len(v) for v in fetched.values())

    scored = []
    for it in new:
        it.score, it.reasons = scorer.score(it.title)
        scored.append(it)

    kept = scored if args.dump else [
        i for i in scored if i.score >= scorer.archive_line]

    store.save_seen(cfg, name, fresh)
    return total, len(new), folded, kept


def cmd_scan(cfg, args):
    ensure_dirs(cfg)
    scorer = Scorer()
    names = args.sources or [s for s in SOURCES
                             if cfg["sources"].get(s, {}).get("enabled")]

    print(f"扫描 {len(names)} 个源：{', '.join(names)}")
    all_items, summary = [], []

    for name in names:
        print(f"\n── {name} ──")
        try:
            total, new, folded, kept = _scan_one(cfg, name, scorer, args)
        except SystemExit as e:
            print(f"  跳过：{e}")
            continue
        all_items.extend(kept)
        summary.append((name, total, new, folded, len(kept)))

        top = sorted(kept, key=lambda x: -x.score)[:args.top]
        for it in top:
            u = it.urgency()
            urg = (f" 剩{u}天" if u is not None and u >= 0
                   else (" 已截止" if u is not None else ""))
            print(f"  [{it.score:>3}] {it.channel:>10} · {it.title[:38]}{urg}")

    # 即使冷启动也要落盘：dashboard / 历史视图靠它，只是不往 inbox 里推
    store.append_items(cfg, all_items)

    if args.cold:
        print("\n冷启动完成，未推送任何东西。")
        return

    out = write_inbox(cfg, all_items, label="全源扫描") if not args.store_only else None

    if args.push:
        print("\n── 推送 ──")
        n = _dispatch(cfg, all_items, force=args.push_force)
        print(f"  推了 {n} 条" if n else "  没有够格的新条目，不打扰")

    print(f"\n{'─' * 60}")
    for name, total, new, folded, kept in summary:
        print(f"  {name:<8} 抓 {total:>4} · 新 {new:>3} · 折叠 {folded:>2} · 保留 {kept:>3}")
    print(f"\n{'已写入 ' + str(out) if out else '今天没有值得推的，inbox 不动。'}")


def _score(it):
    return (it.get("score", 0) if isinstance(it, dict)
            else getattr(it, "score", 0)) or 0


# 推送失败进这里，main 退出前统一处理 —— 让计划任务看到真实的失败，
# 而不是明明一条没发出去，任务历史里还写着 Result=0
_PUSH_FAILURES: list[str] = []


def _dispatch(cfg, items, only=None, force=False):
    """一次性推送。只把「要动手」的那几条算作已推 —— 低优先级的下次升上来还得推。"""
    import core.push as push

    line = (cfg.get("push") or {}).get("action_line", 12)
    fresh = items if force else push.unpush(cfg, items)
    hot = [i for i in fresh if _score(i) >= line]

    if not hot and not force:
        return 0

    title, body = push.format_digest(
        fresh, cfg, (cfg.get("push") or {}).get("max_items", 12))
    results = push.send(cfg, title, body, only=only)
    for name, ok, detail in results:
        print(f"  {'✓' if ok else '✗'} {name}: {detail[:110]}")
    if any(ok for _, ok, _ in results):
        push.mark_pushed(cfg, [push.key_of(i) for i in hot])
    else:
        # 推不出去必须往上冒泡。异常被吞 + exit 0 会让计划任务记 Result=0，
        # 唯一的症状是「人没收到东西」——那要等他自己发现。详见 core/push.py 的 log_send
        _PUSH_FAILURES.append(title)
    return len(hot)


def cmd_push(cfg, args):
    """推送。带 --text 就是手动消息，否则推今天还没推过的结果。"""
    import core.push as push

    if args.text:
        title = args.title or f"手动 {datetime.datetime.now():%m-%d %H:%M}"
        results = push.send(cfg, title, args.text, only=args.channel)
        for name, ok, detail in results:
            print(f"  {'✓' if ok else '✗'} {name}: {detail[:110]}")
        if not any(ok for _, ok, _ in results):
            _PUSH_FAILURES.append(title)
        return

    rows = store.read_items(cfg, limit=800)
    today = datetime.date.today().isoformat()
    rows = [r for r in rows if str(r.get("_seen_at", "")).startswith(today)]
    if not rows:
        print("今天还没扫出东西。先跑 python run.py scan")
        return
    n = _dispatch(cfg, rows, only=args.channel, force=args.force)
    print(f"已推 {n} 条" if n else "没有新东西要推（都推过了，--force 可重推）")


def cmd_read(cfg, args):
    """读单篇正文。光给链接不算降低信息成本，得知道要不要打开。"""
    mod = get(args.source)
    url = args.target if args.target.startswith("http") else \
        f"{cfg['sources'][args.source]['base']}/article/{args.target}"
    title, body = mod.read(cfg, url, args.max)
    print(title)
    print(body or "（没抽到正文，选择器可能失效了）")


def cmd_login(cfg, args):
    from core.browser import login

    name = args.source
    start = {"byr": "https://bbs.byr.cn/index",
             "portal": cfg["sources"]["portal"]["lists"][0]["url"]}[name]
    # 论坛登录后 cookie 里会出现用户名；没登录时是 guest。
    # 判登录不要用页面文字，它会被导航栏带偏
    probe = lambda p: p.evaluate(  # noqa: E731
        "() => { const m = document.cookie.match(/UTMPUSERID=([^;]+)/);"
        " return m && m[1] !== 'guest' ? m[1] : null; }")
    login(name, start, wait_seconds=args.wait, probe=probe)


def cmd_doctor(cfg, args):
    """自检：回答「为什么我今天没收到推送」。"""
    from core import push

    print("── 推送自检 ──\n")

    chs = push.enabled_channels(cfg)
    if not chs:
        print("  ✗ 没有任何启用的通道（config.yaml 的 push.channels 全是 enabled: false）")
        return
    print(f"  启用了 {len(chs)} 个通道：")
    for name, ch in chs.items():
        to = (ch.get("webhook") or ch.get("user_id") or ch.get("chat_id")
              or ch.get("key") or ch.get("token") or "-")
        if to and len(str(to)) > 14 and not args.send:
            to = f"{str(to)[:10]}…（脱敏）"
        print(f"    · {name} ({ch.get('type', name)}) → {to}")

    last = push.last_success(cfg)
    if last:
        try:
            when = datetime.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
            age_h = (datetime.datetime.now() - when).total_seconds() / 3600
            flag = "  ← 超过一天没成功推送了" if age_h > 24 else ""
            print(f"\n  最后一次推送成功：{last}（{age_h:.1f} 小时前）{flag}")
        except ValueError:
            print(f"\n  最后一次推送成功：{last}")
    else:
        print("\n  还没有任何成功推送记录 —— 通道一次都没通过。")

    if args.send:
        print("\n── 端到端测试 ──")
        results = push.send(cfg, "自检：这条能收到就没问题",
                            "doctor --send 发的。收到说明整条链路是通的。")
        for name, ok, detail in results:
            print(f"  {'✓' if ok else '✗'} {name}: {detail[:110]}")


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(prog="run.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="扫一遍，过滤，写 inbox")
    p.add_argument("sources", nargs="*", choices=SOURCES)
    p.add_argument("--boards", help="只扫论坛指定版面，逗号分隔")
    p.add_argument("--dump", action="store_true", help="不做分数过滤，全打出来")
    p.add_argument("--all", action="store_true", help="忽略冷启动时间窗")
    p.add_argument("--window", type=int, default=30, help="冷启动时间窗（天）")
    p.add_argument("--top", type=int, default=12, help="每源打印前几名")
    p.add_argument("--reset", action="store_true", help="清掉该源的去重快照再扫")
    p.add_argument("--store-only", action="store_true", help="只落盘，不写 inbox")
    p.add_argument("--push", action="store_true", help="扫完把结果推到手机（定时任务用）")
    p.add_argument("--push-force", action="store_true", help="忽略已推记录，重推一遍")

    pc = sub.add_parser("cold", help="建去重基线，不推送")
    pc.add_argument("sources", nargs="*", choices=SOURCES)
    pc.set_defaults(dump=False, all=False, window=30, top=0, boards=None,
                    reset=False, store_only=True, push=False, push_force=False)

    pp = sub.add_parser("push", help="把今天还没推过的结果推到手机")
    pp.add_argument("--text", help="直接推这段文字（测通道用）")
    pp.add_argument("--title", help="配 --text 用的标题")
    pp.add_argument("--channel", nargs="*", help="只走指定通道，如 feishu")
    pp.add_argument("--force", action="store_true", help="忽略已推记录，重推一遍")

    pr = sub.add_parser("read", help="读单篇正文")
    pr.add_argument("target")
    pr.add_argument("--source", default="byr", choices=SOURCES)
    pr.add_argument("--max", type=int, default=1200)

    pl = sub.add_parser("login", help="浏览器里登录某个源")
    pl.add_argument("source", choices=SOURCES)
    pl.add_argument("--wait", type=int, default=300)

    pdoc = sub.add_parser("doctor", help="自检：为什么没收到推送")
    pdoc.add_argument("--send", action="store_true", help="顺带发一条测试消息")

    args = ap.parse_args()
    args.cold = (args.cmd == "cold")

    if args.cold:
        args.all = False
        cmd_scan(cfg, args)
        print("  （cold 模式：快照已建立，不推送）")
        return
    if args.cmd == "doctor":
        cmd_doctor(cfg, args)
        return

    {"scan": cmd_scan, "read": cmd_read, "login": cmd_login,
     "push": cmd_push}[args.cmd](cfg, args)

    # 推送失败要以非零退出 —— 计划任务得看见真实的失败，不能一路 Result=0 装成功
    if _PUSH_FAILURES:
        print(f"\n⚠ 有 {len(_PUSH_FAILURES)} 次推送没发出去，详见 data/push.log")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
