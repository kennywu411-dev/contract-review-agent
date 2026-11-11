"""金额测算：把合同里的数字条款换算成钱（ADR 014）。

分工：大模型只负责读条款、把数字抄出来（quant_extract.py）；这个文件里全部是确定性代码，
不调用模型，校验抄出来的数字是否真在合同原文里、按公式计算金额、写出算式和假设、汇总排序。

测算项（topic）：
- unit_price 比价：合同单价高于台账历史均价、且超过公司规范的偏离标准时，本单多付多少；
- payment    付款条件的资金占用成本：预付款、定金、账期相对公司标准付款条件的资金成本差额；
- penalty    违约金：约定情景下（双方各逾期 N 天）我方与对方的违约金敞口差，以及超出公司上限的部分；
- deposit    定金超过法定 20% 的部分；
- retention  质保金：我方付款时低于公司标准的保障缺口，我方收款时被占用的资金成本。

不能确定性计算的（背靠背付款、以实际损失为基数的违约金、解除权、管辖等）标"无法量化"，不硬算；
模型抄出来的数字在合同原文里核对不上的，不出金额，标"需人工核对"。
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass, field
from functools import cache

from contract_review.config import DATA_DIR

# ---------------- 公司测算参数 ----------------


@dataclass(frozen=True)
class QuantParams:
    """每家公司一份（data/tenants/<tenant>/quant.toml）。标准条件取自该公司的合同审查规范，
    资金成本率、情景天数是假设，都可以改。"""

    capital_cost_rate_pct: float  # 年化资金成本率（假设）
    scenario_delay_days: int  # 违约金情景：逾期天数（假设）
    std_payment_days: int  # 我方付款时的标准账期：验收合格后多少日内付款
    std_receivable_days: int  # 我方收款时可接受的最长账期
    max_deposit_pct: float  # 定金上限（民法典第五百八十六条：百分之二十）
    max_penalty_cap_pct: float  # 我方承担违约金的上限（占合同总额）
    price_alert_pct: float  # 单价高于台账均价多少算超标
    std_retention_pct: float  # 我方付款时的标准质保金比例（假设）
    default_delivery_days: int  # 合同没写交付期时的假设
    basis: dict = field(default_factory=dict)  # 每个参数的出处（规范条号或"假设"）


@cache
def load_params(tenant: str) -> QuantParams:
    raw = tomllib.loads((DATA_DIR / "tenants" / tenant / "quant.toml").read_text("utf-8"))
    basis = raw.pop("basis", {})
    return QuantParams(**raw, basis=basis)


# ---------------- 合同里的数字：中文数字、百分比、万分比 ----------------

_DIGIT = {c: i for i, c in enumerate("零一二三四五六七八九")} | {"〇": 0, "两": 2}
_UNIT = {"十": 10, "百": 100, "千": 1000}
_CN_CHARS = "零〇一二两三四五六七八九十百千万亿点"
_NUM = rf"(?:\d[\d,，]*(?:\.\d+)?|[{_CN_CHARS}]+)"
_RATIO = re.compile(rf"(百分之|千分之|万分之)\s*({_NUM})")
_SIGN = re.compile(rf"({_NUM})\s*(%|％|‰)")
_PLAIN = re.compile(rf"(\d[\d,，]*(?:\.\d+)?)\s*(万|亿)?|([{_CN_CHARS}]+)")
_DIV = {"百分之": 1, "千分之": 10, "万分之": 100, "%": 1, "％": 1, "‰": 10}
_SPACES = re.compile(r"\s+")


def cn_to_number(s: str) -> float | None:
    """ "一百八十" → 180，"零点二" → 0.2，"五十万" → 500000，"30" → 30；不是数字返回 None。"""
    s = s.strip().replace(",", "").replace("，", "")
    if not s:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    if any(ch not in _CN_CHARS for ch in s):
        return None
    whole, _, frac = s.partition("点")
    total = section = cur = 0
    seen = False
    for ch in whole:
        if ch in _DIGIT:
            cur, seen = _DIGIT[ch], True
        elif ch in _UNIT:
            section += (cur or 1) * _UNIT[ch]
            cur, seen = 0, True
        else:  # 万、亿
            total = (total + section + cur or 1) * (10**4 if ch == "万" else 10**8)
            section = cur = 0
            seen = True
    if not seen and not frac:
        return None
    value = float(total + section + cur)
    if frac:
        if any(ch not in _DIGIT for ch in frac):
            return None
        value += sum(_DIGIT[ch] / 10 ** (i + 1) for i, ch in enumerate(frac))
    return value


def numbers_in(text: str) -> tuple[list[float], list[float]]:
    """返回（比例值列表，普通数值列表）。比例统一成百分数：万分之五 → 0.05，千分之一 → 0.1，30% → 30。"""
    pct: list[float] = []
    rest = text
    for rx, word, num in ((_RATIO, 1, 2), (_SIGN, 2, 1)):
        for m in rx.finditer(rest):
            v = cn_to_number(m.group(num))
            if v is not None:
                pct.append(v / _DIV[m.group(word)])
        rest = rx.sub("、", rest)  # 比例里的"百""千""万"不能再当成普通数字
    plain: list[float] = []
    for m in _PLAIN.finditer(rest):
        if m.group(1):
            v = cn_to_number(m.group(1))
            if v is not None:
                plain.append(v * {"万": 1e4, "亿": 1e8}.get(m.group(2) or "", 1))
        else:
            v = cn_to_number(m.group(3))
            if v is not None:
                plain.append(v)
    return pct, plain


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))


# ---------------- 字段定义与校验 ----------------

# 字段 → (数值种类, 中文名)。甲方固定是付款方/接收方，乙方是提供方；模型按字面抄，不用判断立场
FIELDS: dict[str, tuple[str, str]] = {
    "contract_total": ("plain", "合同总额（元）"),
    "delivery_days": ("plain", "交付期（签订后多少日内交付）"),
    "prepay_pct": ("pct", "预付款/首付款比例（验收前支付，不含定金）"),
    "payment_days": ("plain", "账期（验收合格或收到发票后多少日内付清）"),
    "deposit_pct": ("pct", "定金比例"),
    "jia_late_daily_pct": ("pct", "甲方逾期违约金日费率"),
    "jia_late_cap_pct": ("pct", "甲方逾期违约金上限（占合同总额）"),
    "jia_lump_pct": ("pct", "甲方一次性违约金（占合同总额）"),
    "yi_late_daily_pct": ("pct", "乙方逾期违约金日费率"),
    "yi_late_cap_pct": ("pct", "乙方逾期违约金上限（占合同总额）"),
    "yi_lump_pct": ("pct", "乙方一次性违约金（占合同总额）"),
    "retention_pct": ("pct", "质保金比例"),
    "retention_months": ("plain", "质保期（月）"),
}
# 模型可以声明"这一项有条款但算不了"的主题
UNCOMPUTABLE_TOPICS = ("payment", "penalty_jia", "penalty_yi", "deposit", "retention")
_DAILY = ("jia_late_daily_pct", "yi_late_daily_pct")
_PENALTY = tuple(f for f in FIELDS if f.startswith(("jia_", "yi_")))
_LOSS_BASE = re.compile(r"损失[^，。；]{0,6}的?(百分之|千分之|万分之|\d)")
_THIRD_PARTY = re.compile(r"下游|第三方|回款|其客户")
_TBD = re.compile(r"另行(协商|约定|商定|确定|议定)")
# 模型声明"算不了"时，摘录里必须出现的字样（防止把质保金条款标成定金算不了之类的张冠李戴）
_FLAG_WORDS = {
    "deposit": re.compile("定金"),
    "retention": re.compile("质保|质量保证金|保证金"),
    "penalty_jia": re.compile("违约|赔偿|扣留|罚"),
    "penalty_yi": re.compile("违约|赔偿|扣留|罚"),
    "payment": re.compile("付|支付|价款|结算"),
}


def _strip(s: str) -> str:
    return _SPACES.sub("", s or "")


def quote_parts(quote: str) -> list[str]:
    return [p for p in re.split(r"[…]+|\.{3,}", _strip(quote)) if p]


LOSS_BASED = "违约金以实际损失为基数，不能按合同金额计算"
THIRD_PARTY = "付款时间取决于第三方付款，没有确定的天数"


def verify_figure(name: str, value, quote: str, contract: str) -> tuple[bool, str]:
    """抄出来的数字必须：字段名合法、摘录原样出现在合同里、摘录里确实写着这个数（中文数字也算）。"""
    if name not in FIELDS:
        return False, "未知字段"
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        return False, "不是有效的数值"
    parts = quote_parts(quote)
    body = _strip(contract)
    if not parts or not all(p in body for p in parts):
        return False, "摘录在合同原文里找不到"
    kind = FIELDS[name][0]
    pct, plain = numbers_in("".join(parts))
    if not any(_close(float(value), v) for v in (pct if kind == "pct" else plain)):
        return False, "摘录里没有这个数"
    text = "".join(parts)
    if name == "deposit_pct" and "定金" not in text:
        return False, "摘录里没有“定金”，不能按定金计算"
    if name in _DAILY and not re.search(r"日|天", text):
        return False, "摘录里看不出是按日计算的比例"
    if name in _PENALTY and _LOSS_BASE.search(text):
        return False, LOSS_BASED
    if name == "payment_days" and _THIRD_PARTY.search(text):
        return False, THIRD_PARTY
    return True, ""


def clause_of(quote: str, clauses: dict[str, str]) -> str | None:
    """摘录落在哪一条（clauses 来自 multi_agent.split_contract）。"""
    parts = quote_parts(quote)
    if not parts:
        return None
    for no, text in clauses.items():
        if parts[0] in _strip(text):
            return no
    return None


def check_figures(raw: list[dict], contract: str, clauses: dict[str, str]) -> list[dict]:
    """逐个校验；同一字段出现多次且数值不同的，全部作废（模型自己都没抄一致）。"""
    out = []
    for f in raw:
        name, value, quote = f.get("field", ""), f.get("value"), f.get("quote", "")
        ok, why = verify_figure(name, value, quote, contract)
        soft = ""
        text = _strip(quote)
        if name in _PENALTY and _LOSS_BASE.search(text):
            # 模型把"实际损失的 130%"也填了进来：不是抄错，是这一方的违约金本来就算不了
            ok, why, soft = False, LOSS_BASED, f"penalty_{name.split('_')[0]}"
        elif name == "payment_days" and _THIRD_PARTY.search(text):
            ok, why, soft = False, THIRD_PARTY, "payment"
        elif ok and name == "prepay_pct" and "定金" in text and not re.search("预付|首付", text):
            # 定金被同时抄成了预付款：定金已经单独计入提前支付的部分，这一条丢掉，避免重复计算
            ok, why, soft = False, "摘录写的是定金，已按定金计算，不重复算作预付款", "ignore"
        out.append(
            {
                "field": name,
                "label": FIELDS.get(name, ("", name))[1],
                "value": value,
                "quote": quote,
                "clause": clause_of(quote, clauses) if ok else None,
                "ok": ok,
                "why": why,
                # soft 非空：这个数字不用，但不算"核对不上"。值是它转成的"算不了"主题，或 ignore
                "soft": soft,
            }
        )
    seen: dict[str, float] = {}
    clash = set()
    for f in out:
        if f["ok"]:
            if f["field"] in seen and not _close(seen[f["field"]], f["value"]):
                clash.add(f["field"])
            seen.setdefault(f["field"], f["value"])
    for f in out:
        if f["field"] in clash:
            f["ok"], f["why"], f["clause"] = False, "同一字段抄出了两个不同的数", None
    return out


def check_flags(raw: list[dict], figures: list[dict], contract: str, clauses: dict[str, str]):
    """模型声明的"有条款但算不了"也要过规则：返回（采纳的, 没采纳的）。

    - 摘录必须在合同里，并且出现这个主题该有的字样；
    - 违约金只写"另行协商"的不算"算不了"，等于没有约定（对方的违约金按 0 计）；
    - 被规则判定为"以损失为基数""取决于第三方付款"的数字，自动补一条对应主题的声明。"""
    body = _strip(contract)
    kept, dropped = [], []
    for u in raw:
        topic, quote = u.get("topic", ""), u.get("quote", "")
        parts = quote_parts(quote)
        text = "".join(parts)
        why = ""
        if topic not in UNCOMPUTABLE_TOPICS:
            why = "未知主题"
        elif not parts or not all(p in body for p in parts):
            why = "摘录在合同原文里找不到"
        elif topic.startswith("penalty") and _TBD.search(text) and "损失" not in text:
            why = "只写了另行协商，等于没有约定，按 0 计"
        elif not _FLAG_WORDS[topic].search(text):
            why = "摘录和这个主题对不上"
        item = u | {"clause": clause_of(quote, clauses) if not why else None, "source": "model"}
        (dropped if why else kept).append(item | ({"why": why} if why else {}))
    have = {u["topic"] for u in kept}
    for f in figures:
        topic = f.get("soft", "")
        if topic in UNCOMPUTABLE_TOPICS and topic not in have:
            have.add(topic)
            kept.append(
                {
                    "topic": topic,
                    "quote": f["quote"],
                    "reason": f["why"],
                    "clause": clause_of(f["quote"], clauses),
                    "source": "rule",
                }
            )
    return kept, dropped


def check_lines(raw: list[dict], contract: str, clauses: dict[str, str]) -> list[dict]:
    """标的明细的每一行：摘录原样出现在合同里，摘录里写着这个品名、这个单价和这个数量。"""
    body = _strip(contract)
    out = []
    for ln in raw:
        item, quote = (ln.get("item") or "").strip(), ln.get("quote", "")
        price, qty = ln.get("unit_price"), ln.get("quantity")
        parts = quote_parts(quote)
        text = "".join(parts)
        _, plain = numbers_in(text)
        why = ""
        if not all(
            isinstance(x, int | float) and not isinstance(x, bool) and x > 0 for x in (price, qty)
        ):
            why = "单价或数量不是有效的数值"
        elif not parts or not all(p in body for p in parts):
            why = "摘录在合同原文里找不到"
        elif not item or _strip(item) not in text:
            why = "摘录里没有这个品名"
        elif not any(_close(float(price), x) for x in plain):
            why = "摘录里没有这个单价"
        elif not any(_close(float(qty), x) for x in plain):
            why = "摘录里没有这个数量"
        out.append(
            {
                "item": item,
                "unit": ln.get("unit", ""),
                "unit_price": price,
                "quantity": qty,
                "quote": quote,
                "clause": clause_of(quote, clauses) if not why else None,
                "ok": not why,
                "why": why,
                "ledger": None,
            }
        )
    return out


# ---------------- 计算 ----------------


def money(x: float) -> str:
    return f"{x:,.2f}"


def _num(x: float) -> str:
    return f"{x:,.4f}".rstrip("0").rstrip(".")


@dataclass
class Item:
    key: str
    topic: str
    title: str
    nature: str  # sure＝不改就确定多花；exposure＝最坏情景下才发生
    status: str  # quantified / within_standard / unquantifiable / needs_check
    amount: float | None = None
    counted: bool = False  # 是否计入合计
    group: str = ""  # 同组只计最大的一项，避免重复计算
    formula: str = ""
    inputs: list[dict] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    note: str = ""
    clause: str | None = None


def _inputs(figs: dict[str, dict], *names: str) -> list[dict]:
    return [
        {k: figs[n][k] for k in ("field", "label", "value", "quote", "clause")}
        for n in names
        if n in figs
    ]


def compute(
    figures: list[dict],
    our_side: str,
    p: QuantParams,
    lines: list[dict] | None = None,
    uncomputable: list[dict] | None = None,
) -> list[Item]:
    """figures：check_figures 的输出；lines：check_lines 的输出，每行可以带 ledger
    {"item", "avg", "n"}（查台账对上的品目，对不上为 None）；
    uncomputable：模型声明"有条款但算不了"的主题 [{"topic", "quote", "reason"}]。"""
    figs = {f["field"]: f for f in figures if f["ok"]}
    bad = {
        f["field"]: f for f in figures if not f["ok"] and f["field"] in FIELDS and not f.get("soft")
    }
    v = {k: float(f["value"]) for k, f in figs.items()}
    flagged = {u["topic"]: u for u in uncomputable or [] if u.get("topic") in UNCOMPUTABLE_TOPICS}
    payer = our_side == "甲"
    total = v.get("contract_total")
    rate = p.capital_cost_rate_pct
    items: list[Item] = []

    def clause(*names):
        return next((figs[n]["clause"] for n in names if n in figs and figs[n]["clause"]), None)

    def needs_check(key, topic, title, nature, names):
        """用到的字段里有校验没过的：不出金额。"""
        hit = [bad[n] for n in names if n in bad and n not in figs]
        if not hit:
            return False
        items.append(
            Item(
                key,
                topic,
                title,
                nature,
                "needs_check",
                note="；".join(f"{b['label']}抄出的是 {b['value']}，但{b['why']}" for b in hit),
            )
        )
        return True

    def unquantifiable(key, topic, title, nature, flag_topic, default=""):
        u = flagged.get(flag_topic)
        if not u and not default:
            return False
        note = (u or {}).get("reason") or default
        items.append(Item(key, topic, title, nature, "unquantifiable", note=note))
        return True

    # ---- 比价（只在我方付款时）：逐行和台账均价比，超标的行相加 ----
    if payer and lines:
        title = "单价高于台账历史均价：本单多付"
        bad_lines = [ln for ln in lines if not ln["ok"]]
        priced = [ln for ln in lines if ln["ok"] and ln.get("ledger")]
        if bad_lines:
            items.append(
                Item(
                    "price_overpay",
                    "unit_price",
                    title,
                    "sure",
                    "needs_check",
                    note="；".join(
                        f"标的“{ln.get('item', '')}”抄出的单价 {ln.get('unit_price')}、"
                        f"数量 {ln.get('quantity')}，但{ln['why']}"
                        for ln in bad_lines
                    ),
                )
            )
        elif not priced:
            unquantifiable(
                "price_overpay",
                "unit_price",
                title,
                "sure",
                "",
                "供应商台账里没有同一品目的历史成交记录，无法比价",
            )
        else:
            rows = []
            for ln in priced:
                price, qty = float(ln["unit_price"]), float(ln["quantity"])
                avg = float(ln["ledger"]["avg"])
                dev = (price / avg - 1) * 100
                rows.append((ln, price, qty, avg, dev, round((price - avg) * qty, 2)))
            over = [r for r in rows if r[4] > p.price_alert_pct]
            above = [r for r in rows if r[1] > r[3]]
            shown = over or above
            if shown:
                amount = round(sum(r[5] for r in shown), 2)
                parts = [
                    f"{r[0]['ledger']['item']}：（合同单价 {_num(r[1])} − 台账均价 {_num(r[3])}）"
                    f"× 数量 {_num(r[2])} = {money(r[5])} 元（单价高出 {r[4]:.1f}%）"
                    for r in shown
                ]
                skipped = len(lines) - len(priced)
                assumptions = [
                    "台账均价：供应商台账里同一品目历史成交单价的简单平均（"
                    + "、".join(f"{r[0]['ledger']['item']} {r[0]['ledger']['n']} 笔" for r in shown)
                    + "）",
                    f"公司标准：单价高于台账均价 {_num(p.price_alert_pct)}% 以上才算超标"
                    f"（{p.basis.get('price_alert_pct', '')}）；没有超标的行不计入",
                ]
                if skipped:
                    assumptions.append(f"有 {skipped} 行标的在台账里没有历史成交记录，没有比价")
                items.append(
                    Item(
                        "price_overpay",
                        "unit_price",
                        title,
                        "sure",
                        "quantified" if over else "within_standard",
                        amount=amount,
                        counted=bool(over),
                        formula="；".join(parts)
                        + (f"；合计 {money(amount)} 元" if len(parts) > 1 else ""),
                        inputs=[
                            {
                                "field": "line",
                                "label": f"{r[0]['item']}的单价和数量",
                                "value": f"{_num(r[1])} × {_num(r[2])}",
                                "quote": r[0]["quote"],
                                "clause": r[0]["clause"],
                            }
                            for r in shown
                        ],
                        assumptions=assumptions,
                        note=""
                        if over
                        else "高于台账均价，但在公司规范允许的偏离范围内，不计入合计",
                        clause=shown[0][0]["clause"],
                    )
                )

    # ---- 付款条件的资金占用成本 ----
    pay_names = ("contract_total", "prepay_pct", "deposit_pct", "payment_days", "delivery_days")
    title = "付款条件的资金占用成本" if payer else "回款账期的资金占用成本"
    has_pay = any(n in v or n in bad for n in ("prepay_pct", "deposit_pct", "payment_days"))
    if "payment" in flagged and "payment_days" not in v:
        unquantifiable("payment_capital", "payment", title, "sure", "payment")
    elif has_pay and not needs_check("payment_capital", "payment", title, "sure", pay_names):
        if total is None:
            unquantifiable(
                "payment_capital", "payment", title, "sure", "", "合同里没有抄到合同总额"
            )
        else:
            early = min(100.0, v.get("prepay_pct", 0.0) + v.get("deposit_pct", 0.0))
            days = v.get("payment_days")
            assumptions = [
                f"年化资金成本率 {_num(rate)}%（{p.basis.get('capital_cost_rate_pct', '假设')}）"
            ]
            if payer:
                std = p.std_payment_days
                lead = v.get("delivery_days", float(p.default_delivery_days))
                assumptions.append(
                    f"公司标准付款条件：不预付，验收合格后 {std} 日内付款"
                    f"（{p.basis.get('std_payment_days', '')}）"
                )
                if "delivery_days" not in v:
                    assumptions.append(f"合同没写交付期，按签订后 {_num(lead)} 日交付计（假设）")
                assumptions.append("验收按交付当日完成计；定金按抵作价款计入提前支付的部分")
                a1 = total * early / 100 * (lead + std) / 365 * rate / 100
                short = max(0.0, std - days) if days is not None else 0.0
                a2 = total * (100 - early) / 100 * short / 365 * rate / 100
                amount = round(a1 + a2, 2)
                parts = []
                if early > 0:
                    parts.append(
                        f"合同总额 {_num(total)} × 提前支付 {_num(early)}%"
                        f" ×（交付期 {_num(lead)} + 标准账期 {std}）÷ 365 × {_num(rate)}%"
                        f" = {money(a1)}"
                    )
                if short > 0:
                    parts.append(
                        f"合同总额 {_num(total)} × 余款 {_num(100 - early)}%"
                        f" ×（标准账期 {std} − 合同账期 {_num(days)}）÷ 365 × {_num(rate)}%"
                        f" = {money(a2)}"
                    )
                if days is None and early < 100:
                    assumptions.append("余款的账期没有抄到，按公司标准账期计")
            else:
                std = p.std_receivable_days
                assumptions.append(
                    f"公司可接受的最长回款账期：{std} 日（{p.basis.get('std_receivable_days', '')}）"
                )
                longer = max(0.0, days - std) if days is not None else 0.0
                amount = round(total * (100 - early) / 100 * longer / 365 * rate / 100, 2)
                parts = (
                    [
                        f"合同总额 {_num(total)} × 待收 {_num(100 - early)}%"
                        f" ×（合同账期 {_num(days)} − 可接受账期 {std}）÷ 365 × {_num(rate)}%"
                        f" = {money(amount)}"
                    ]
                    if longer > 0
                    else []
                )
            if amount > 0:
                items.append(
                    Item(
                        "payment_capital",
                        "payment",
                        title,
                        "sure",
                        "quantified",
                        amount=amount,
                        counted=True,
                        formula="；".join(parts)
                        + (f"；合计 {money(amount)} 元" if len(parts) > 1 else " 元"),
                        inputs=_inputs(figs, *pay_names),
                        assumptions=assumptions,
                        clause=clause("prepay_pct", "payment_days", "deposit_pct"),
                    )
                )

    # ---- 定金超过法定上限（只在我方付款时）----
    if payer:
        title = f"定金超过法定上限 {_num(p.max_deposit_pct)}% 的部分"
        if "deposit_pct" not in v and "deposit" in flagged:
            unquantifiable("deposit_excess", "deposit", title, "exposure", "deposit")
        elif ("deposit_pct" in v or "deposit_pct" in bad) and not needs_check(
            "deposit_excess", "deposit", title, "exposure", ("deposit_pct", "contract_total")
        ):
            if total is None:
                unquantifiable(
                    "deposit_excess", "deposit", title, "exposure", "", "合同里没有抄到合同总额"
                )
            elif v["deposit_pct"] > p.max_deposit_pct:
                amount = round(total * (v["deposit_pct"] - p.max_deposit_pct) / 100, 2)
                items.append(
                    Item(
                        "deposit_excess",
                        "deposit",
                        title,
                        "exposure",
                        "quantified",
                        amount=amount,
                        counted=True,
                        formula=f"合同总额 {_num(total)} ×（定金 {_num(v['deposit_pct'])}%"
                        f" − 上限 {_num(p.max_deposit_pct)}%）= {money(amount)} 元",
                        inputs=_inputs(figs, "contract_total", "deposit_pct"),
                        assumptions=[
                            f"定金上限 {_num(p.max_deposit_pct)}%（{p.basis.get('max_deposit_pct', '')}）；"
                            "超过的部分不产生定金效力，但钱已经付给对方"
                        ],
                        clause=clause("deposit_pct"),
                    )
                )

    # ---- 违约金：敞口差与超出公司上限 ----
    ours, theirs = ("jia", "yi") if payer else ("yi", "jia")
    days_n = p.scenario_delay_days

    def exposure(side):
        """约定情景下这一方要付的违约金占合同总额的百分比，以及算式片段；没约定返回 None。"""
        daily, cap, lump = (
            v.get(f"{side}_{k}") for k in ("late_daily_pct", "late_cap_pct", "lump_pct")
        )
        best = None
        if daily is not None:
            pct = daily * days_n
            text = f"日费率 {_num(daily)}% × {days_n} 天 = {_num(pct)}%"
            if cap is not None and pct > cap:
                pct, text = cap, text + f"，超过上限，按上限 {_num(cap)}%"
            best = (pct, text)
        if lump is not None and (best is None or lump > best[0]):
            best = (lump, f"一次性违约金 {_num(lump)}%")
        return best

    side_names = {
        s: tuple(f"{s}_{k}" for k in ("late_daily_pct", "late_cap_pct", "lump_pct"))
        for s in ("jia", "yi")
    }
    our_exp, their_exp = exposure(ours), exposure(theirs)
    title_gap = "违约金不对等：同样逾期，我方比对方多付"
    title_cap = f"违约金超出公司上限（合同总额的 {_num(p.max_penalty_cap_pct)}%）"
    scenario = f"情景：双方各逾期 {days_n} 天、逾期金额按合同总额计（假设，可配置）"
    pen_names = ("contract_total", *side_names[ours], *side_names[theirs])
    our_flag, their_flag = f"penalty_{ours}", f"penalty_{theirs}"
    our_bad = any(n in bad and n not in figs for n in side_names[ours])
    their_bad = any(n in bad and n not in figs for n in side_names[theirs])
    if our_exp is None and our_bad:
        needs_check("penalty_gap", "penalty", title_gap, "exposure", side_names[ours])
    elif our_exp is None and our_flag in flagged:
        unquantifiable("penalty_gap", "penalty", title_gap, "exposure", our_flag)
    elif our_exp is not None and total is None:
        if not needs_check("penalty_gap", "penalty", title_gap, "exposure", ("contract_total",)):
            unquantifiable(
                "penalty_gap", "penalty", title_gap, "exposure", "", "合同里没有抄到合同总额"
            )
    elif our_exp is not None:
        our_amt = total * our_exp[0] / 100
        if their_exp is None and their_bad:
            needs_check("penalty_gap", "penalty", title_gap, "exposure", side_names[theirs])
        elif their_exp is None and their_flag in flagged:
            unquantifiable("penalty_gap", "penalty", title_gap, "exposure", their_flag)
        else:
            their_amt = total * their_exp[0] / 100 if their_exp else 0.0
            gap = round(our_amt - their_amt, 2)
            if gap > 0:
                assumptions = [scenario]
                if their_exp is None:
                    assumptions.append("合同没有约定对方逾期的违约金比例，对方的违约金按 0 计")
                items.append(
                    Item(
                        "penalty_gap",
                        "penalty",
                        title_gap,
                        "exposure",
                        "quantified",
                        amount=gap,
                        counted=True,
                        group="penalty",
                        formula=f"我方：合同总额 {_num(total)} ×（{our_exp[1]}）= {money(our_amt)}；"
                        + (
                            f"对方：合同总额 {_num(total)} ×（{their_exp[1]}）= {money(their_amt)}；"
                            if their_exp
                            else "对方：0；"
                        )
                        + f"差额 {money(gap)} 元",
                        inputs=_inputs(figs, *pen_names),
                        assumptions=assumptions,
                        clause=clause(*side_names[ours]),
                    )
                )
        over = round(our_amt - total * p.max_penalty_cap_pct / 100, 2)
        if over > 0:
            items.append(
                Item(
                    "penalty_over_cap",
                    "penalty",
                    title_cap,
                    "exposure",
                    "quantified",
                    amount=over,
                    counted=True,
                    group="penalty",
                    formula=f"我方：合同总额 {_num(total)} ×（{our_exp[1]}）= {money(our_amt)}；"
                    f"公司上限：合同总额 {_num(total)} × {_num(p.max_penalty_cap_pct)}%"
                    f" = {money(total * p.max_penalty_cap_pct / 100)}；超出 {money(over)} 元",
                    inputs=_inputs(figs, "contract_total", *side_names[ours]),
                    assumptions=[
                        scenario,
                        f"公司上限：我方承担的违约金不超过合同总额的 {_num(p.max_penalty_cap_pct)}%"
                        f"（{p.basis.get('max_penalty_cap_pct', '')}）",
                    ],
                    clause=clause(*side_names[ours]),
                )
            )

    # ---- 质保金 ----
    if "retention_pct" not in v and "retention" in flagged:
        unquantifiable(
            "retention", "retention", "质保金", "exposure" if payer else "sure", "retention"
        )
    elif "retention_pct" in v or "retention_pct" in bad:
        if payer:
            title = f"质保金低于公司标准 {_num(p.std_retention_pct)}%：保障缺口"
            names = ("contract_total", "retention_pct")
            if not needs_check("retention", "retention", title, "exposure", names):
                if total is None:
                    unquantifiable(
                        "retention", "retention", title, "exposure", "", "合同里没有抄到合同总额"
                    )
                elif v["retention_pct"] < p.std_retention_pct:
                    amount = round(total * (p.std_retention_pct - v["retention_pct"]) / 100, 2)
                    items.append(
                        Item(
                            "retention",
                            "retention",
                            title,
                            "exposure",
                            "quantified",
                            amount=amount,
                            counted=True,
                            formula=f"合同总额 {_num(total)} ×（标准 {_num(p.std_retention_pct)}%"
                            f" − 合同 {_num(v['retention_pct'])}%）= {money(amount)} 元",
                            inputs=_inputs(figs, *names),
                            assumptions=[
                                f"公司标准质保金比例 {_num(p.std_retention_pct)}%"
                                f"（{p.basis.get('std_retention_pct', '假设')}）；"
                                "缺口指出现质量问题时，我方手里少留的可抵扣金额"
                            ],
                            clause=clause("retention_pct"),
                        )
                    )
        else:
            title = "质保金被占用的资金成本"
            names = ("contract_total", "retention_pct", "retention_months")
            if not needs_check("retention", "retention", title, "sure", names):
                if total is None or "retention_months" not in v:
                    unquantifiable(
                        "retention",
                        "retention",
                        title,
                        "sure",
                        "",
                        "合同里没有抄到合同总额或质保期",
                    )
                else:
                    amount = round(
                        total * v["retention_pct"] / 100 * v["retention_months"] / 12 * rate / 100,
                        2,
                    )
                    if amount > 0:
                        items.append(
                            Item(
                                "retention",
                                "retention",
                                title,
                                "sure",
                                "quantified",
                                amount=amount,
                                counted=True,
                                formula=f"合同总额 {_num(total)} × 质保金 {_num(v['retention_pct'])}%"
                                f" × 质保期 {_num(v['retention_months'])} 个月 ÷ 12 × {_num(rate)}%"
                                f" = {money(amount)} 元",
                                inputs=_inputs(figs, *names),
                                assumptions=[
                                    f"年化资金成本率 {_num(rate)}%"
                                    f"（{p.basis.get('capital_cost_rate_pct', '假设')}）"
                                ],
                                clause=clause("retention_pct"),
                            )
                        )
    return items


def consistency_warnings(figures: list[dict], lines: list[dict] | None = None) -> list[str]:
    """各行单价 × 数量之和应当等于合同总额（差 1% 以内）；对不上时提示，金额仍按合同写明的总额算。"""
    v = {f["field"]: float(f["value"]) for f in figures if f["ok"]}
    lines = lines or []
    total = v.get("contract_total")
    if total and lines and all(ln["ok"] for ln in lines):
        calc = sum(float(ln["unit_price"]) * float(ln["quantity"]) for ln in lines)
        if abs(calc - total) > 0.01 * total:
            return [
                f"合同写的总额 {money(total)} 元与各行单价 × 数量之和 {money(calc)} 元对不上，"
                "请核对；下面的金额按合同写明的总额计算"
            ]
    return []


# ---------------- 汇总与挂到意见上 ----------------


def summarize(items: list[Item]) -> dict:
    """最坏情况合计 = 不改就确定多花的 + 最坏情景下的敞口；同组（如两种违约金口径）只计最大的一项。
    谈判优先级按金额从大到小排。"""
    counted = [i for i in items if i.counted and i.amount]
    best: dict[str, Item] = {}
    for i in counted:
        k = i.group or i.key
        if k not in best or i.amount > best[k].amount:
            best[k] = i
    chosen = sorted(best.values(), key=lambda i: -i.amount)
    sure = round(sum(i.amount for i in chosen if i.nature == "sure"), 2)
    exposure = round(sum(i.amount for i in chosen if i.nature == "exposure"), 2)
    return {
        "sure_total": sure,
        "exposure_total": exposure,
        "worst_total": round(sure + exposure, 2),
        "priorities": [
            {
                "rank": n,
                "key": i.key,
                "title": i.title,
                "clause": i.clause,
                "nature": i.nature,
                "amount": i.amount,
            }
            for n, i in enumerate(chosen, 1)
        ],
        "unquantifiable": sum(1 for i in items if i.status == "unquantifiable"),
        "needs_check": sum(1 for i in items if i.status == "needs_check"),
    }


# 这些风险类型没有可以确定性换算成钱的数字：损失取决于合同之外的事实
NOT_QUANTIFIABLE = {
    "liability": "损失大小取决于将来是否出事、损失多少，合同里没有可以直接计算的数",
    "termination": "对方提前解除造成的损失取决于届时已经投入多少，无法事先算出",
    "inspection": "检验期过短的后果是丧失质量异议权，损失取决于货物是否有质量问题",
    "confidentiality": "泄密损失取决于泄露的内容和后果，无法事先算出",
    "ip": "知识产权归属的价值取决于成果后续怎么用，无法事先算出",
    "jurisdiction": "异地诉讼多出的成本取决于是否发生争议和案件情况",
    "auto_renew": "续期的代价取决于续期后的价格和用量，合同里没有写明",
    "other": "这条意见不涉及可以直接计算的金额",
}
TOPIC_OF_TYPE = {
    "unit_price": "unit_price",
    "payment": "payment",
    "deposit": "deposit",
    "penalty": "penalty",
}


def attach(findings: list[dict], items: list[Item]) -> list[dict]:
    """给每条审查意见挂上金额：同类型的测算项挂到意见上（优先同一条款），其余类型标"无法量化"。
    返回带 quant 字段的新列表；没被任何意见认领的测算项由调用方单独展示。"""
    out = []
    for f in findings:
        topic = TOPIC_OF_TYPE.get(f.get("risk_type", ""))
        g = dict(f)
        if topic is None:
            g["quant"] = {
                "status": "unquantifiable",
                "keys": [],
                "amount": None,
                "reason": NOT_QUANTIFIABLE.get(f.get("risk_type", ""), NOT_QUANTIFIABLE["other"]),
            }
            out.append(g)
            continue
        mine = [i for i in items if i.topic == topic]
        # 质保金条款通常被当成付款条件报出来：同一条款的质保金测算也挂上
        if topic == "payment":
            mine += [i for i in items if i.topic == "retention" and i.clause == f.get("clause")]
        same = [i for i in mine if i.clause == f.get("clause")]
        mine = same or mine
        quantified = [i for i in mine if i.status == "quantified"]
        if quantified:
            best: dict[str, Item] = {}
            for i in quantified:
                k = i.group or i.key
                if k not in best or i.amount > best[k].amount:
                    best[k] = i
            g["quant"] = {
                "status": "quantified",
                "keys": [i.key for i in quantified],
                "amount": round(sum(i.amount for i in best.values()), 2),
                "reason": "",
            }
        elif mine:
            first = mine[0]
            g["quant"] = {
                "status": first.status,
                "keys": [i.key for i in mine],
                "amount": None,
                "reason": first.note,
            }
        else:
            g["quant"] = {
                "status": "unquantifiable",
                "keys": [],
                "amount": None,
                "reason": "这条意见涉及的条款里没有可以确定性计算的数字",
            }
        out.append(g)
    return out


def items_to_dicts(items: list[Item]) -> list[dict]:
    return [asdict(i) for i in items]
