"""全局配置：路径与学校。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "docpilot.db"
CAMPUS_DB = DATA_DIR / "campus" / "campus.sqlite"
SOURCES_PATH = DATA_DIR / "sources.toml"

# 学校 id → 中文名；national 是所有学校共享的国家层面文件
SCHOOLS = {
    "national": "通用（国家层面）",
    "bnu": "北京师范大学",
    "upc": "中国石油大学（华东）",
    "njust": "南京理工大学",
    "lzu": "兰州大学",
    "cumt": "中国矿业大学",
    "zzu": "郑州大学",
}
