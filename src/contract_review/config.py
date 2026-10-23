"""全局配置：路径与租户。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "contract_review.db"
LEDGER_DB = DATA_DIR / "ledger" / "ledger.sqlite"
LEDGER_SRC = DATA_DIR / "ledger_src"  # 构造的供应商台账 CSV（入 git）
SOURCES_PATH = DATA_DIR / "sources.toml"

# 租户 id → 名称。common 是所有租户共享的法律法规与官方示范文本；其余三家公司是构造的（ADR 007）
TENANTS = {
    "common": "通用（法律法规与示范文本）",
    "huaxin": "华信制造有限公司（构造）",
    "yunqi": "云启科技有限公司（构造）",
    "ruifeng": "瑞丰贸易有限公司（构造）",
}
