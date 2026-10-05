"""统一的数据结构。所有信息源都产出 Item，下游只看 Item，不需要知道它来自哪。

这样加新源的成本就只有「写个抓取函数产出 Item」，过滤、去重、推送、展示全都不用动。
"""
import dataclasses
import datetime
import re
import typing


@dataclasses.dataclass
class Item:
    source: str          # byr / portal
    channel: str         # 版面名、通知栏目名
    title: str
    url: str

    # 时间。三个不同含义，别混：
    published: str = ""       # 发布日期
    last_active: str = ""     # 最后活跃（论坛回帖、通知更新）
    deadline: str = ""        # 报名截止 —— 有这个字段的优先级最高

    # 现场类条目（讲座 / 活动）专用 —— 光有链接没法去，得知道几点、在哪
    start_time: str = ""
    place: str = ""
    capacity: str = ""        # 名额/余量

    author: str = ""
    replies: int = 0
    score: int = 0
    reasons: typing.List[str] = dataclasses.field(default_factory=list)
    extra: dict = dataclasses.field(default_factory=dict)

    def __post_init__(self):
        # 网页 innerText 常带着换行和整段摘要。收敛成一行，否则表格和 inbox 全被顶歪
        self.title = re.sub(r"\s+", " ", (self.title or "")).strip()

    def key(self) -> str:
        """去重用主键：URL 去掉查询串。"""
        return self.url.split("?")[0].rstrip("/")

    _DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")

    def parse_day(self, field="published"):
        s = (getattr(self, field) or "").strip()
        # 先整体匹配。注意别按 fmt 长度截断 s —— 之前就是这么写的，
        # 结果 "2026-09-24" 被截成 "2026-09-" 全部解析失败，冷启动窗把新帖全丢了。
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                    "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        # 再退一步：字符串里随便哪里有一串日期就算数
        m = self._DATE_RE.search(s)
        if m:
            try:
                return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
        return None

    def age_days(self, today=None) -> typing.Optional[int]:
        """最后活跃距今几天。取 published 与 last_active 里较新的。"""
        today = today or datetime.date.today()
        ds = [d for d in (self.parse_day("published"), self.parse_day("last_active")) if d]
        if not ds:
            return None
        return (today - max(ds)).days

    def urgency(self) -> typing.Optional[int]:
        """距离截止还有几天。没有 deadline 就 None。"""
        d = self.parse_day("deadline")
        return (d - datetime.date.today()).days if d else None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)
