"""全局路径与配置加载。所有模块都从这里取配置，别各自硬编码。"""
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_config() -> dict:
    p = ROOT / "config.yaml"
    if not p.exists():
        raise SystemExit("找不到 config.yaml。先从模板复制一份：\n"
                         "    cp config.example.yaml config.yaml")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def load_rules() -> dict:
    return yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8"))


def resolve(cfg, relpath: str) -> pathlib.Path:
    """把 config 里的相对路径解析成绝对路径。"""
    p = pathlib.Path(relpath)
    return p if p.is_absolute() else ROOT / p


def ensure_dirs(cfg):
    for key in ("session_dir", "data_dir", "inbox_dir"):
        resolve(cfg, cfg["paths"][key]).mkdir(parents=True, exist_ok=True)
