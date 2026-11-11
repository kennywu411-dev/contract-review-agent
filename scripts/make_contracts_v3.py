"""生成 v3 采购金额测算评测集（evals/contracts_v3.jsonl），入 git。ADR 014。

v1、v2 只有风险的位置和类型，没有金额。v3 是 60 份采购合同（我方都是甲方：买受人、定作人、委托方、需方），
每份的单价、数量、交付期、预付款、账期、定金、违约金、质保金都由本脚本随机取值，所以每一项金额的
标准答案可以直接算出来。已有的 contracts.jsonl、contracts_v2.jsonl 不改。

- 标的明细：工业品和农产品买卖里有 18 份是多行标的（2–5 种，逐行和台账均价比），其余每份一行。
- 数字写法：一半合同用中文数字（百分之三十、万分之五、四十五日），一半用阿拉伯数字（30%、0.05%、45日）。
- 算不了的条款（陷阱）：背靠背付款、付款时间另行确定、以实际损失为基数的违约金、质保金比例另行约定；
  名为"预付款"、带双倍返还的条款（应计入预付款，不算定金）。
- 标准答案：gold_lines（标的明细）、gold_fields（应当抄出的其他数字）、gold_uncomputable（有条款但算不了的主题）、
  gold_amounts（五个测算项各自的金额，或 none / unquantifiable）。
  金额在本脚本里按 ADR 014 的口径独立计算，不调用 src/contract_review/quant.py；
  tests/test_quant_evalset.py 核对两边算出来的结果一致。

全部是构造数据：公司、价格、条款均为虚构；单价按 data/ledger_src/purchase_history.csv 的历史均价取值。
"""

from __future__ import annotations

import json
import random
import sys
import tomllib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_contracts import (  # noqa: E402
    OURS,
    SUPPLIER_CITY,
    TYPES,
    cn_num,
    fixed_text,
    fmt_price,
    ledger,
    liability_safe,
    quality_text,
    t_auto_renew,
    t_confidentiality,
    t_inspection,
    t_ip,
    t_jurisdiction,
    t_termination,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "contracts_v3.jsonl"
OUT_HOLDOUT = ROOT / "evals" / "contracts_v3_holdout.jsonl"
SEED = 20261111
SEED_HOLDOUT = 20261112
PER_TYPE = {"goods": 20, "agri": 16, "work": 6, "dproc": 6, "dsupply": 6, "ddev": 6}
TOPICS = ("unit_price", "payment", "penalty", "deposit", "retention")
# 多行标的从同一家公司台账里的实物品目选（服务类品目不和货物混在一份合同里）
MULTI_POOL = {
    "goods": ["冷轧钢板", "深沟球轴承", "三相异步电动机", "液压油", "不锈钢无缝管"],
    "agri": ["粳米", "玉米", "大豆", "籼米"],
}

_RATE_CN = {
    0.01: "万分之一",
    0.03: "万分之三",
    0.05: "万分之五",
    0.08: "万分之八",
    0.1: "千分之一",
    0.3: "千分之三",
    0.5: "千分之五",
    1: "百分之一",
}


def cn_int(n: int) -> str:
    if n < 100:
        return cn_num(n)
    h, rest = divmod(n, 100)
    head = "零一二三四五六七八九"[h] + "百"
    if rest == 0:
        return head
    if rest < 10:
        return head + "零" + cn_num(rest)
    if rest < 20:
        return head + "一" + cn_num(rest)
    return head + cn_num(rest)


def days(n: int, style: str) -> str:
    return cn_int(n) if style == "cn" else str(n)


def pct(x: float, style: str) -> str:
    """比例的写法。日费率这类小数用万分之、千分之（中文）或带小数的百分号（阿拉伯）。"""
    if style == "cn":
        return _RATE_CN[x] if x in _RATE_CN and x <= 1 else f"百分之{cn_int(int(x))}"
    return f"{x:g}%"


def params(tenant: str) -> dict:
    return tomllib.loads((ROOT / "data" / "tenants" / tenant / "quant.toml").read_text("utf-8"))


def build(cid: str, ctype: str, rng: random.Random, stats: dict, force: dict) -> dict:
    t = TYPES[ctype]
    tenant = t["tenant"]
    p = params(tenant)
    style = force["style"]
    alert = p["price_alert_pct"]
    # 标的明细：工业品和农产品买卖有一半是多行（2–5 种标的，逐行比价），其余合同只有一行
    first = rng.choice(t["items"])  # 第一行决定供应商（对方当事人）
    if force["lines"] > 1 and ctype in MULTI_POOL:
        rest = [i for i in MULTI_POOL[ctype] if i != first]
        names = [first, *rng.sample(rest, min(force["lines"] - 1, len(rest)))]
    else:
        names = [first]
    item = names[0]
    st = stats[(tenant, item)]
    our = OURS[tenant]
    sname = st["supplier"]
    city = SUPPLIER_CITY[sname[:2]]
    lines = []
    for k, name in enumerate(names):
        ls = stats[(tenant, name)]
        band = force["price"] if k == 0 else rng.choices(["fair", "mild", "high"], [45, 15, 40])[0]
        while True:
            factor = {
                "fair": rng.uniform(0.96, 1.02),
                "mild": rng.uniform(1.03, 1 + (alert - 2) / 100),
                "high": rng.uniform(1 + (alert + 2) / 100, 1.45),
            }[band]
            price = float(fmt_price(ls["avg"] * factor))
            dev = (price / ls["avg"] - 1) * 100
            if abs(dev - alert) > 0.5:  # 四舍五入后别落在超标线上
                break
        qty = (
            rng.choice([20, 50, 100, 200])
            if ls["avg"] > 100
            else rng.choice([20000, 50000, 100000])
        )
        lines.append(
            dict(
                item=name,
                spec=ls["spec"],
                unit=ls["unit"],
                unit_price=price,
                quantity=qty,
                avg=ls["avg"],
                n=len(ls["prices"]),
                over=dev > alert,
            )
        )
    total = round(sum(ln["unit_price"] * ln["quantity"] for ln in lines), 2)
    whole = total == int(total)
    total_s = (f"{total:,.0f}" if force["comma"] else str(int(total))) if whole else f"{total:.2f}"
    lead = rng.choice([15, 30, 45, 60])
    fields: dict[str, float] = {"contract_total": total, "delivery_days": lead}
    uncomputable: list[str] = []
    ctx = dict(
        thing=t["thing"],
        item=item,
        spec=st["spec"],
        unit=st["unit"],
        our_side="甲",
        our_side_or_A="甲",
        ctype=ctype,
        city={"甲": our[2], "乙": city[1]},
    )

    def line_text(ln):
        return (
            f"品名：{ln['item']}；规格：{ln['spec']}；计量单位：{ln['unit']}；数量：{ln['quantity']}；"
            f"单价：人民币{fmt_price(ln['unit_price'])}元/{ln['unit']}（含税）"
        )

    if len(lines) == 1:
        subject = f"{line_text(lines[0])}；合同总额：人民币{total_s}元。"
    else:
        rows = "\n".join(f"（{cn_num(k)}）{line_text(ln)}；" for k, ln in enumerate(lines, 1))
        subject = f"本合同标的明细如下：\n{rows}\n以上各项合计，合同总额：人民币{total_s}元。"
    if t["thing"] in ("货物", "定作物"):
        delivery = f"乙方应当在合同签订后{days(lead, style)}日内向甲方交付{t['thing']}，交付地点为甲方指定地点，运输费用由乙方承担。"
    else:
        delivery = f"乙方应当在合同签订后{days(lead, style)}日内按第一条约定的品名、规格和数量完成工作，并以书面或者电子方式向甲方交付{t['thing']}。"

    # ---- 付款 ----
    pay = force["payment"]
    std = p["std_payment_days"]
    prepay = 0.0
    pay_days = None
    if pay == "full":
        pay_days = rng.choice([7, 10, 15, std, std, 90])
        payment = (
            f"甲方在验收合格并收到乙方开具的合规发票后{days(pay_days, style)}日内支付全部价款。"
        )
    elif pay == "prepay":
        prepay = rng.choice([10, 20, 30, 50])
        pay_days = rng.choice([10, 15, std, std, 90])
        payment = (
            f"合同签订后五日内，甲方向乙方支付合同总额的{pct(prepay, style)}作为预付款；"
            f"余款在验收合格并收到乙方开具的合规发票后{days(pay_days, style)}日内付清。"
        )
    elif pay == "b2b":
        payment = "甲方在收到其下游客户支付的相应款项后十日内向乙方付款；下游客户未付款的，甲方暂不承担付款义务。"
    else:  # tbd
        payment = "合同价款在项目整体验收合格后支付，具体付款时间由双方另行书面确定。"
    if pay in ("b2b", "tbd"):
        uncomputable.append("payment")

    # ---- 定金 / 名为预付款的条款 ----
    dep = force["deposit"]
    deposit_pct = 0.0
    deposit_clause = None
    if dep == "deposit":
        deposit_pct = rng.choice([10, 20, 25, 30, 40])
        fields["deposit_pct"] = deposit_pct
        deposit_clause = (
            "定金",
            f"甲方应当在合同签订后五日内向乙方支付合同总额{pct(deposit_pct, style)}的定金，定金在最后一期付款时抵作价款。"
            "一方违约时，对方可以选择适用定金条款或者违约金条款，但不得同时适用。",
        )
    elif dep == "prepay_named":  # 只在付款条款没有预付款时出现
        prepay = rng.choice([20, 30])
        deposit_clause = (
            "预付款",
            f"甲方应当在合同签订后五日内向乙方支付合同总额{pct(prepay, style)}的预付款；"
            "乙方违约的，应当双倍返还预付款；甲方违约的，预付款不予退还。",
        )
    if prepay:
        fields["prepay_pct"] = prepay
    if pay_days is not None:
        fields["payment_days"] = pay_days

    # ---- 质保金 ----
    ret = force["retention"]
    retention_clause = None
    ret_pct = None
    if ret == "retention":
        ret_pct = rng.choice([2, 3, 5, 10])
        months = rng.choice([6, 12, 24])
        fields["retention_pct"] = ret_pct
        fields["retention_months"] = months
        retention_clause = (
            f"甲方有权从合同价款中预留合同总额的{pct(ret_pct, style)}作为质量保证金，"
            f"质保期为验收合格之日起{days(months, style)}个月；质保期满且无质量问题的，甲方在十日内无息支付。"
        )
    elif ret == "tbd":
        uncomputable.append("retention")
        retention_clause = (
            "甲方有权从合同价款中预留质量保证金，质量保证金的比例和期限由双方另行约定。"
        )

    # ---- 违约金 ----
    pen = force["penalty"]
    n = p["scenario_delay_days"]
    our_pct = their_pct = None  # 情景下各自的违约金占合同总额的百分比

    def daily(rate, cap):
        e = rate * n
        return min(e, cap) if cap is not None else e

    if pen == "sym":
        r, c = rng.choice([0.03, 0.05]), rng.choice([10, 20])
        penalty = (
            "任何一方违约的，应当赔偿因此给对方造成的直接损失。逾期付款或者逾期履行的，每逾期一日，"
            f"按逾期部分金额的{pct(r, style)}支付违约金，违约金总额不超过合同总额的{pct(c, style)}。"
        )
        for s in ("jia", "yi"):
            fields[f"{s}_late_daily_pct"], fields[f"{s}_late_cap_pct"] = r, c
        our_pct = their_pct = daily(r, c)
    elif pen in ("asym", "oneside", "fav"):
        if pen == "fav":
            ra, ca, rb, cb = rng.choice([0.03, 0.05]), 10, rng.choice([0.1, 0.3]), 20
        else:
            ra = rng.choice([0.1, 0.3, 0.5, 1])
            ca = rng.choice([None, 30, 50])
            rb, cb = rng.choice([0.01, 0.03, 0.05]), rng.choice([5, 10])
        cap_a = (
            "违约金总额不设上限" if ca is None else f"违约金总额不超过合同总额的{pct(ca, style)}"
        )
        penalty = f"甲方逾期付款的，每逾期一日，应当按合同总额的{pct(ra, style)}向乙方支付违约金，{cap_a}。"
        fields["jia_late_daily_pct"] = ra
        if ca is not None:
            fields["jia_late_cap_pct"] = ca
        our_pct = daily(ra, ca)
        if pen == "oneside":
            penalty += "乙方逾期的，双方另行协商。"
            their_pct = 0.0
        else:
            penalty += (
                f"乙方逾期交付的，每逾期一日，应当按合同总额的{pct(rb, style)}向甲方支付违约金，"
                f"违约金总额不超过合同总额的{pct(cb, style)}。"
            )
            fields["yi_late_daily_pct"], fields["yi_late_cap_pct"] = rb, cb
            their_pct = daily(rb, cb)
    elif pen == "lump":
        x = rng.choice([20, 30, 50])
        rb, cb = rng.choice([0.03, 0.05]), rng.choice([5, 10])
        penalty = (
            f"甲方违反本合同任何约定的，应当向乙方支付合同总额{pct(x, style)}的违约金。"
            f"乙方逾期交付的，每逾期一日，应当按合同总额的{pct(rb, style)}向甲方支付违约金，"
            f"违约金总额不超过合同总额的{pct(cb, style)}。"
        )
        fields["jia_lump_pct"] = x
        fields["yi_late_daily_pct"], fields["yi_late_cap_pct"] = rb, cb
        our_pct, their_pct = float(x), daily(rb, cb)
    else:  # loss：以实际损失为基数，算不了
        penalty = (
            "任何一方违约的，应当按照守约方因此遭受的实际损失的百分之一百三十向守约方支付违约金。"
        )
        uncomputable += ["penalty_jia", "penalty_yi"]

    # ---- 标准答案里的金额（独立计算）----
    rate = p["capital_cost_rate_pct"] / 100
    gold: dict[str, dict] = {}

    def put(topic, amount):
        amount = round(amount, 2)
        gold[topic] = (
            {"status": "amount", "amount": amount}
            if amount > 0
            else {"status": "none", "amount": 0}
        )

    put(
        "unit_price",
        sum((ln["unit_price"] - ln["avg"]) * ln["quantity"] for ln in lines if ln["over"]),
    )
    if pay in ("b2b", "tbd"):
        gold["payment"] = {"status": "unquantifiable", "amount": 0}
    else:
        early = (prepay + deposit_pct) / 100
        put(
            "payment",
            total * early * (lead + std) / 365 * rate
            + total * (1 - early) * max(0, std - pay_days) / 365 * rate,
        )
    if our_pct is None:
        gold["penalty"] = {"status": "unquantifiable", "amount": 0}
    else:
        put("penalty", total * (our_pct - their_pct) / 100)
    put("deposit", total * max(0, deposit_pct - p["max_deposit_pct"]) / 100)
    if ret == "tbd":
        gold["retention"] = {"status": "unquantifiable", "amount": 0}
    else:
        put(
            "retention",
            total * max(0, p["std_retention_pct"] - ret_pct) / 100 if ret_pct is not None else 0,
        )

    # ---- 组装合同 ----
    slots = [
        ("标的、数量和价款", subject),
        ("质量标准", quality_text(ctx)),
        ("交付", delivery),
        ("检验与验收", t_inspection("safe", "甲", ctx)),
        ("价款支付", payment),
    ]
    if deposit_clause:
        slots.append(deposit_clause)
    if retention_clause:
        slots.append(("质量保证金", retention_clause))
    slots.append(("保密", t_confidentiality("safe", "甲", ctx)))
    if ctype in ("dproc", "ddev", "dsupply"):
        slots.append(("数据安全", fixed_text("data", ctx)))
    if t["ip"]:
        slots.append(("知识产权", t_ip("safe", "甲", ctx)))
    slots += [
        ("违约责任", penalty),
        ("责任限制", liability_safe(ctx)),
        ("合同解除", t_termination("safe", "甲", ctx)),
        ("合同期限", t_auto_renew("safe", "甲", ctx)),
        ("不可抗力", fixed_text("force_majeure", ctx)),
        ("争议解决", t_jurisdiction("safe", "甲", ctx)),
        ("通知与送达", fixed_text("notice", ctx)),
        ("其他", fixed_text("misc", ctx)),
    ]
    header = "\n".join(
        [
            t["title"],
            f"合同编号：{cid.upper()}",
            f"甲方（{t['A']}）：{our[0]}",
            f"住所：{our[1]}",
            f"乙方（{t['B']}）：{sname}",
            f"住所：{city[0]}",
            f"签订地点：{our[2]}",
            "根据《中华人民共和国民法典》等有关法律法规，甲乙双方在平等、自愿、公平和诚信的基础上，经协商一致，订立本合同。",
        ]
    )
    text = (
        header
        + "\n"
        + "\n".join(f"第{cn_num(i)}条 {h}\n{b}" for i, (h, b) in enumerate(slots, 1))
        + "\n"
    )
    return dict(
        id=cid,
        tenant=tenant,
        contract_type=ctype,
        title=t["title"],
        our_side="甲",
        our_name=our[0],
        counterparty=sname,
        text=text,
        variants=force,
        ledger={ln["item"]: {"avg": round(ln["avg"], 4), "n": ln["n"]} for ln in lines},
        gold_lines=[{k: ln[k] for k in ("item", "unit", "unit_price", "quantity")} for ln in lines],
        gold_fields=fields,
        gold_uncomputable=sorted(uncomputable),
        gold_amounts=gold,
        constructed=True,
    )


def plan(rng: random.Random, n: int) -> list[dict]:
    """每个维度按固定配比排好再打乱，保证各种写法和陷阱都有足够的份数。"""

    def spread(options: dict[str, int]) -> list[str]:
        out = [k for k, w in options.items() for _ in range(w)]
        assert len(out) == n, (options, len(out))
        rng.shuffle(out)
        return out

    payment = spread({"full": 24, "prepay": 24, "b2b": 6, "tbd": 6})
    deposit = spread({"none": 26, "deposit": 26, "prepay_named": 8})
    penalty = spread({"sym": 12, "asym": 16, "oneside": 10, "lump": 8, "loss": 8, "fav": 6})
    retention = spread({"none": 30, "retention": 24, "tbd": 6})
    price = spread({"fair": 24, "mild": 10, "high": 26})
    style = spread({"cn": 30, "ar": 30})
    comma = spread({"yes": 20, "no": 40})
    # 多行标的只对工业品、农产品买卖生效（前 36 份），其中一半多行
    lines = [1] * n
    multi = rng.sample(range(36), 18)
    for i in multi:
        lines[i] = rng.choice([2, 3, 4, 5])
    out = []
    for i in range(n):
        dep = deposit[i]
        if dep == "prepay_named" and payment[i] != "full":
            dep = "none"  # 付款条款已经有预付款，或者付款时间本来就算不了：不再叠一个
        out.append(
            dict(
                payment=payment[i],
                deposit=dep,
                penalty=penalty[i],
                retention=retention[i],
                price=price[i],
                style=style[i],
                comma=comma[i] == "yes",
                lines=lines[i],
            )
        )
    return out


def main() -> None:
    # --holdout：换一个随机种子再生成 60 份（evals/contracts_v3_holdout.jsonl）。规则修正只看 v3，
    # 留出集只用来报最终数字，不看着它调（ADR 014）
    holdout = "--holdout" in sys.argv[1:]
    out, prefix = (OUT_HOLDOUT, "v3h") if holdout else (OUT, "v3")
    rng = random.Random(SEED_HOLDOUT if holdout else SEED)
    stats, _ = ledger()
    n = sum(PER_TYPE.values())
    forces = plan(rng, n)
    contracts = []
    k = 0
    for ctype, count in PER_TYPE.items():
        for i in range(1, count + 1):
            contracts.append(build(f"{prefix}-{ctype}-{i:02d}", ctype, rng, stats, forces[k]))
            k += 1
    assert len(contracts) == 60 and len({c["id"] for c in contracts}) == 60
    out.write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in contracts), encoding="utf-8"
    )
    print(f"{len(contracts)} 份 → {out.relative_to(ROOT)}")
    for topic in TOPICS:
        print(topic, dict(Counter(c["gold_amounts"][topic]["status"] for c in contracts)))
    print(
        "应抄出的数字",
        sum(len(c["gold_fields"]) + 2 * len(c["gold_lines"]) for c in contracts),
        "标的行数",
        dict(Counter(len(c["gold_lines"]) for c in contracts)),
    )
    print("写法", dict(Counter(c["variants"]["style"] for c in contracts)))


if __name__ == "__main__":
    main()
