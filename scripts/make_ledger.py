"""生成构造的供应商台账 CSV（data/ledger_src/），入 git；`cra ingest` 再建成 data/ledger/ledger.sqlite。

全部是构造数据（ADR 007）：公司名、统一社会信用代码、价格都是虚构的，固定随机种子可复现。
每个租户：6 家供应商（1 家黑名单、1 家资质已过期），每个品目 10 笔历史采购，单价在基准价 ±6% 内波动。
评测合同里"单价明显高于台账历史价"的风险，按这里的历史均价植入。
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "ledger_src"
SOURCE = "构造"
SEED = 20261023

# 租户 → 供应商（名称, 类别, 状态, 黑名单原因, 资质, 资质有效期）
SUPPLIERS = {
    "huaxin": [
        ("苏州恒锐金属材料有限公司", "原材料", "正常", "", "ISO9001", "2027-06-30"),
        ("无锡精联轴承有限公司", "零部件", "正常", "", "ISO9001", "2027-03-31"),
        ("常州驰远电机有限公司", "零部件", "正常", "", "CCC 认证", "2026-12-31"),
        ("南通海拓润滑油有限公司", "辅料", "正常", "", "ISO9001", "2026-05-31"),
        ("昆山铭泰精密机械有限公司", "外协加工", "正常", "", "ISO9001", "2027-09-30"),
        (
            "嘉兴宏途钢管有限公司",
            "原材料",
            "黑名单",
            "2025 年两次交付不合格品且拒不退换",
            "ISO9001",
            "2027-01-31",
        ),
    ],
    "yunqi": [
        ("杭州数衡信息技术有限公司", "数据服务", "正常", "", "DCMM 二级", "2027-08-31"),
        ("成都标点智能科技有限公司", "数据服务", "正常", "", "ISO27001", "2026-04-30"),
        ("北京启明软件开发有限公司", "软件开发", "正常", "", "CMMI 3", "2027-05-31"),
        ("上海云栈网络科技有限公司", "云资源", "正常", "", "等保三级", "2027-02-28"),
        ("合肥语库数据有限公司", "数据产品", "正常", "", "ISO27001", "2026-11-30"),
        (
            "深圳极讯外包服务有限公司",
            "运维外包",
            "黑名单",
            "2025 年泄露客户数据被通报",
            "ISO27001",
            "2027-03-31",
        ),
    ],
    "ruifeng": [
        ("黑龙江北垦粮食购销有限公司", "粮食", "正常", "", "粮食收购资格", "2027-04-30"),
        ("吉林丰禾农产品有限公司", "粮食", "正常", "", "粮食收购资格", "2026-08-31"),
        ("山东鲁穗粮油贸易有限公司", "粮食", "正常", "", "食品经营许可", "2027-07-31"),
        ("河南中原冷链物流有限公司", "物流仓储", "正常", "", "道路运输许可", "2027-01-31"),
        ("安徽谷仓仓储服务有限公司", "物流仓储", "正常", "", "仓储备案", "2027-10-31"),
        (
            "江西赣丰米业有限公司",
            "粮食",
            "黑名单",
            "2025 年以次充好、虚开发票",
            "食品生产许可",
            "2027-05-31",
        ),
    ],
}

# 租户 → 品目（品名, 规格, 单位, 基准单价元, 供货商下标）
ITEMS = {
    "huaxin": [
        ("冷轧钢板", "SPCC 1.0mm", "吨", 4800, 0),
        ("深沟球轴承", "6205-2RS", "套", 9.5, 1),
        ("三相异步电动机", "YE3-7.5kW", "台", 2600, 2),
        ("液压油", "L-HM46 200L", "桶", 1650, 3),
        ("机加工外协件", "按图纸 HX-302", "件", 85, 4),
        ("不锈钢无缝管", "304 Φ25×2", "米", 38, 5),
    ],
    "yunqi": [
        ("图像标注服务", "2D 框标注", "张", 0.35, 0),
        ("文本标注服务", "实体与关系标注", "条", 0.12, 1),
        ("定制软件开发", "高级工程师", "人天", 1800, 2),
        ("云服务器租用", "16 核 64G", "台·月", 900, 3),
        ("行业语料数据集", "金融领域中文语料", "GB", 3200, 4),
        ("系统运维外包", "7×24 驻场", "人月", 22000, 5),
    ],
    "ruifeng": [
        ("粳米", "一级", "吨", 5200, 0),
        ("玉米", "二等", "吨", 2450, 1),
        ("大豆", "三等", "吨", 4300, 2),
        ("冷链运输服务", "-18℃ 干线", "吨", 260, 3),
        ("粮食仓储服务", "平房仓", "吨·月", 25, 4),
        ("籼米", "二级", "吨", 4600, 5),
    ],
}
ORDERS_PER_ITEM = 10
START = date(2025, 1, 6)


def credit_code(rng: random.Random) -> str:
    return "91" + "".join(rng.choice("0123456789ABCDEFGHJKLMNPQRTUWXY") for _ in range(16))


def main() -> None:
    rng = random.Random(SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    suppliers = []
    for tenant, rows in SUPPLIERS.items():
        for i, (name, cat, status, reason, qual, until) in enumerate(rows, 1):
            sid = f"{tenant[:2].upper()}-S{i:02d}"
            suppliers.append(
                [tenant, sid, name, credit_code(rng), cat, status, reason, qual, until]
            )
    # 每个租户把一家正常供应商的资质有效期改成已过期（评测"资质过期"）
    for tenant in SUPPLIERS:
        row = next(r for r in suppliers if r[0] == tenant and r[2] == SUPPLIERS[tenant][3][0])
        row[8] = "2026-05-31" if tenant != "ruifeng" else "2026-08-31"
    with (OUT / "suppliers.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "tenant",
                "supplier_id",
                "supplier_name",
                "credit_code",
                "category",
                "status",
                "blacklist_reason",
                "qualification",
                "qual_valid_until",
                "source",
            ]
        )
        w.writerows(r + [SOURCE] for r in suppliers)

    orders = []
    for tenant, items in ITEMS.items():
        n = 0
        for item, spec, unit, base, si in items:
            for k in range(ORDERS_PER_ITEM):
                n += 1
                day = START + timedelta(days=k * 60 + rng.randint(0, 20))
                price = round(base * rng.uniform(0.94, 1.06), 2 if base < 100 else 0)
                qty = rng.choice([5, 10, 20, 50, 100, 200]) * (1000 if base < 1 else 1)
                orders.append(
                    [
                        tenant,
                        f"{tenant[:2].upper()}-PO{n:04d}",
                        day.isoformat(),
                        SUPPLIERS[tenant][si][0],
                        item,
                        spec,
                        unit,
                        price,
                        qty,
                        round(price * qty, 2),
                    ]
                )
    with (OUT / "purchase_history.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "tenant",
                "order_id",
                "order_date",
                "supplier_name",
                "item",
                "spec",
                "unit",
                "unit_price_yuan",
                "quantity",
                "amount_yuan",
                "source",
            ]
        )
        w.writerows(r + [SOURCE] for r in orders)
    print(f"suppliers {len(suppliers)} 行，purchase_history {len(orders)} 行 → {OUT}")


if __name__ == "__main__":
    main()
