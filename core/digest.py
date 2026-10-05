"""把结果写成早上打开就能读的东西。

原则：论坛的条目必须带上「多久前还在动」，不然不知道它是不是已经凉了。
这条比分数重要。
"""
import datetime
import pathlib

from core.config import resolve

SOURCE_LABEL = {
    "byr": "北邮人论坛",
    "portal": "教务/学院通知",
}


def _head(it) -> str:
    u = it.urgency()
    if u is None:
        return ""
    if u < 0:
        return "**已截止**"
    if u == 0:
        return "**今天截止**"
    if u <= 2:
        return f"剩 {u} 天截止"
    return f"剩 {u} 天"


def line_for(it) -> str:
    """单条 markdown。"""
    mark = "★" if it.score >= 12 else "·"
    out = [f"- `{mark}` **[{SOURCE_LABEL.get(it.source, it.source)}·{it.channel}]** "
           f"[{it.title}]({it.url})"]

    detail = []
    age = it.age_days()
    detail.append(f"最后活跃 {age} 天前" if age is not None else "时间未知")
    if it.author:
        detail.append(f"`{it.author}`")
    if it.replies:
        detail.append(f"{it.replies} 回复")

    dl = _head(it)
    if dl:
        detail.append(dl)

    out.append("  \n  " + " · ".join(detail))
    if it.reasons:
        out.append(f"  \n  <sub>{', '.join(it.reasons[:6])}</sub>")
    return "\n".join(out)


def write_inbox(cfg, items, label=""):
    """按天追加到 inbox/YYYY-MM-DD.md。没有命中就不写 —— 空块只会稀释真正重要的那条。"""
    now = datetime.datetime.now()
    inbox = resolve(cfg, cfg["paths"]["inbox_dir"])
    inbox.mkdir(parents=True, exist_ok=True)
    out: pathlib.Path = inbox / f"{now:%Y-%m-%d}.md"
    if not items:
        return None

    action = [i for i in items if i.score >= 12]
    fyi = [i for i in items if i.score < 12]

    blocks = [f"\n## {now:%H:%M} {label or '扫描'} · "
              f"{len(items)} 条（{len(action)} 条要动手）\n"]

    # 按来源分组。有截止时间的排前面
    for source, title in (("portal", "官方通知"), ("byr", "论坛机会")):
        grp = [i for i in action if i.source == source]
        if not grp:
            continue
        grp.sort(key=lambda x: (x.urgency() if x.urgency() is not None else 999,
                                -x.score))
        blocks.append(f"### {title}\n")
        blocks.extend(line_for(i) for i in grp)

    if fyi:
        blocks.append("\n### 仅供参考\n")
        blocks.extend(line_for(i) for i in sorted(fyi, key=lambda x: -x.score))

    with out.open("a", encoding="utf-8") as f:
        f.write("\n".join(blocks) + "\n")
    return out
