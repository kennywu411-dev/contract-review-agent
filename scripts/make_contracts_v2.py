"""生成 v2 难评测集（evals/contracts_v2.jsonl），入 git。ADR 013。

v1（make_contracts.py）的风险都写在标准位置、用典型说法，单/多 Agent 检出率都是 100%，区分不出能力。
v2 改成长合同（25–30 条，补齐定义、包装、运输、发票、人员、变更、廉洁等真实合同常见条款），
30 份植入风险、30 份干净，风险分 5 类：

- hidden 隐蔽位置（18 处）：风险句夹在非标准条款里（如发票条款里的违约金、变更条款里的解除权），
  对应的标准条款本身是安全写法；
- paraphrase 换说法（18 处）：标准位置，但不用"违约金""解除""视为合格""自动续期"等触发词；
- combo 跨条款组合（12 处）：每条单看都正常，合起来才有风险（如付款以收到发票为前提、开票又以收到付款为前提）；
- threshold 临界值（15 处）：数值刚越过租户规范或法律的红线（定金 22%、检验期 12 日、单价高 21% 等）；
- standard 标准位置对照（12 处）：v1 那种典型写法，作对照。

干净合同带干扰项（distractors）：刚好在红线内的数值、合法的不可抗力免责、合理的质保除外、对我方有利的单方条款、
"签订地法院管辖 + 签订地在我方"这类看似组合风险其实合规的写法。

金标准每处风险记：条款（组合风险记多条）、类别、风险类型（可接受的近义类型 alt_types）、等级、受损方、
为什么是风险（why）、合法依据（法条/租户规范条号）。全部是构造数据。
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_contracts import (  # noqa: E402
    CUSTOMERS,
    LEVEL,
    OURS,
    RISK_TYPES,
    SUPPLIER_CITY,
    TYPES,
    VARIANTS,
    basis,
    cn_num,
    fixed_text,
    fmt_price,
    ledger,
    other,
    subject_text,
    supplier_flag,
    t_auto_renew,
    t_confidentiality,
    t_deposit,
    t_inspection,
    t_ip,
    t_jurisdiction,
    t_liability,
    t_payment,
    t_penalty,
    t_termination,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "contracts_v2.jsonl"
SPOTCHECK = ROOT / "evals" / "contracts_v2_spotcheck.md"
SEED = 20261025
LEDGER_12M_FROM = "2025-10-23"  # 规范写的是"近十二个月"均价：两种均价下临界值都要成立

FAMILY = {
    "goods": "goods",
    "agri": "agri",
    "work": "work",
    "entrust": "entrust",
    "dproc": "data",
    "ddev": "data",
    "dsupply": "data",
}
CORE_FN = {
    "inspection": t_inspection,
    "payment": t_payment,
    "deposit": t_deposit,
    "confidentiality": t_confidentiality,
    "ip": t_ip,
    "penalty": t_penalty,
    "liability": t_liability,
    "termination": t_termination,
    "auto_renew": t_auto_renew,
    "jurisdiction": t_jurisdiction,
}
# 条款顺序：v1 的 17 个标准条款 + 17 个真实合同常见的补充条款
ORDER = [
    "definitions",
    "subject",
    "price_adjust",
    "quality",
    "packaging",
    "delivery",
    "transport",
    "ownership",
    "inspection",
    "warranty",
    "payment",
    "invoice",
    "deposit",
    "bond",
    "personnel",
    "confidentiality",
    "data",
    "privacy",
    "ip",
    "ip_warranty",
    "change",
    "assignment",
    "penalty",
    "liability",
    "termination",
    "auto_renew",
    "force_majeure",
    "anti_bribery",
    "jurisdiction",
    "notice",
    "contacts",
    "waiver",
    "entire",
    "misc",
]
OPTIONAL = {
    "price_adjust",
    "packaging",
    "transport",
    "ownership",
    "warranty",
    "invoice",
    "bond",
    "personnel",
    "privacy",
    "ip_warranty",
    "change",
    "assignment",
    "anti_bribery",
    "contacts",
    "waiver",
    "entire",
}
HEADING = {
    "definitions": "定义",
    "subject": "标的、数量和价款",
    "price_adjust": "价格调整",
    "packaging": "包装与标识",
    "transport": "运输与保险",
    "ownership": "所有权与风险转移",
    "warranty": "质量保证期",
    "quality": "质量标准",
    "delivery": "交付",
    "inspection": "检验与验收",
    "payment": "价款支付",
    "invoice": "发票与税费",
    "deposit": "定金",
    "bond": "履约能力保证",
    "personnel": "人员管理",
    "confidentiality": "保密",
    "data": "数据安全",
    "privacy": "个人信息保护",
    "ip": "知识产权",
    "ip_warranty": "知识产权担保",
    "change": "合同变更",
    "assignment": "转让与分包",
    "penalty": "违约责任",
    "liability": "责任限制",
    "termination": "合同解除",
    "auto_renew": "合同期限",
    "force_majeure": "不可抗力",
    "anti_bribery": "廉洁条款",
    "jurisdiction": "争议解决",
    "notice": "通知与送达",
    "contacts": "联系人",
    "waiver": "权利保留与可分割",
    "entire": "合同附件与完整协议",
    "misc": "其他",
}
HEADING_BY_FAMILY = {
    "packaging": {"entrust": "保管记录", "data": "交付形式"},
    "transport": {"entrust": "出入库运输", "data": "数据传输"},
    "ownership": {"entrust": "仓储物权属"},
    "warranty": {
        "goods": "质量保证期",
        "work": "质量保证期",
        "agri": "质量承诺",
        "entrust": "保管养护",
        "data": "维护与技术支持",
    },
}
EQUIPMENT = {"三相异步电动机"}  # 华信规范第十五条：设备类验收期不少于三十日


TERM_END = "2027-10-23"  # 合同期一年：资质在履行期内到期也算（华信规范第六条、瑞丰办法第四条）


def supplier_risk(tenant: str, name: str) -> str | None:
    """台账里对方的异常：黑名单、资质已过期，或者资质将在一年履行期内到期（审查 v2 高 1）。"""
    flag = supplier_flag(tenant, name)
    if flag:
        return flag
    _, status = ledger()
    s = status.get((tenant, name))
    if s and s["qual_valid_until"] < TERM_END:
        return (
            f"对方资质（{s['qualification']}）将于 {s['qual_valid_until']} 到期，早于合同履行期届满"
        )
    return None


def heading(slot: str, fam: str) -> str:
    return HEADING_BY_FAMILY.get(slot, {}).get(fam) or HEADING.get(slot) or HEADING[slot]


def possible_slots(ctype: str) -> set[str]:
    t = TYPES[ctype]
    s = set(ORDER)
    if FAMILY[ctype] != "data":
        s -= {"data", "privacy"}
    if not t["ip"]:
        s -= {"ip", "ip_warranty"}
    return s


# ---------------- 安全写法（补充条款） ----------------


def filler_text(slot: str, ctx: dict) -> str:
    fam, thing, item = ctx["family"], ctx["thing"], ctx["item"]
    goodsish = fam in ("goods", "agri", "work")
    return {
        "price_adjust": "本合同单价为固定单价，合同履行期间不因原材料、人工价格波动而调整；确需调整的，双方应当另行签订书面补充协议。",
        "packaging": (
            f"乙方应当按照国家标准或者行业通用方式对{thing}进行包装，包装应当适合运输并具备防潮、防锈、防震性能；外包装应当标明品名、规格、数量、生产日期和收货单位。"
            if goodsish
            else "粮食入库后，乙方应当按批次、货位建立保管卡，载明品种、数量、等级、产地和入库时间，并与甲方每月核对一次。"
            if fam == "entrust"
            else "交付成果应当以双方确认的格式和介质交付，并随附数据字典、交付清单和质量说明。"
        ),
        "transport": (
            f"{thing}由乙方负责运输至交付地点，运输途中的货物保险由乙方投保，费用包含在合同价款中。"
            if goodsish
            else "粮食入库运输由甲方负责，出库运输由甲方或者其指定的提货人负责；乙方应当提供装卸设备和人员予以配合。"
            if fam == "entrust"
            else "数据传输应当通过加密通道或者双方确认的安全介质进行，传输记录应当留存不少于一年。"
        ),
        "ownership": (
            "仓储期间粮食的所有权属于甲方，乙方不得擅自出借、质押或者以其他方式处分仓储粮食。"
            if fam == "entrust"
            else f"{thing}的所有权自交付时转移给甲方；{thing}毁损、灭失的风险，在交付前由乙方承担，交付后由甲方承担，但因乙方原因造成的除外。"
        ),
        "warranty": {
            "goods": f"{thing}的质量保证期为验收合格之日起十二个月。质量保证期内{thing}出现质量问题的，乙方应当在接到甲方通知后七日内免费修理或者更换，并承担由此产生的合理费用。",
            "work": f"{thing}的质量保证期为验收合格之日起十二个月。质量保证期内{thing}出现质量问题的，乙方应当在接到甲方通知后七日内免费修理或者重作，并承担由此产生的合理费用。",
            "agri": f"乙方保证所供{item}为当季新粮，无霉变、无污染，农药残留和重金属等指标符合食品安全国家标准。",
            "entrust": "乙方应当按照粮食储存技术规范进行通风、测温和虫害防治，并每月向甲方提供一次粮情报告。",
            "data": "交付成果验收合格后六个月内为维护期。维护期内乙方应当免费修复交付成果中存在的缺陷，并在接到通知后两个工作日内响应。",
        }[fam],
        "invoice": "乙方应当在每次收款前向甲方开具等额、合法有效的增值税专用发票；因发票不合规导致甲方不能抵扣的，乙方应当重新开具。合同价款已含税，税率调整的，不含税价款不变。",
        "bond": "乙方应当具备履行本合同所需的资质、设备和人员，并在合同履行期内保持有效；资质发生变化的，应当在五日内书面通知甲方。",
        "personnel": (
            "乙方应当指定项目负责人，项目负责人变更的，应当提前五个工作日书面通知甲方。乙方对其人员的劳动报酬、社会保险和人身安全自行负责。"
            if fam == "data"
            else "乙方人员进入甲方场所的，应当遵守甲方的安全生产和现场管理制度。乙方对其人员的劳动报酬、社会保险和人身安全自行负责。"
        ),
        "privacy": "涉及个人信息的，双方应当遵守《中华人民共和国个人信息保护法》，仅在实现处理目的所必需的最小范围内处理个人信息，并采取相应的安全保护措施。",
        "ip_warranty": "乙方保证交付成果不侵犯任何第三方的知识产权；第三方就此主张权利的，乙方应当负责处理，并赔偿甲方因此遭受的直接损失。",
        "change": "任何一方需要变更本合同内容的，应当书面通知对方，经双方协商一致并签订书面补充协议后生效。",
        "assignment": "未经对方书面同意，任何一方不得将本合同项下的权利义务全部或者部分转让给第三方；乙方不得将主要工作分包给第三方。",
        "anti_bribery": "双方不得向对方工作人员提供回扣、礼金或者其他不正当利益；一方违反本条的，对方有权书面通知解除本合同。",
        "contacts": "双方各自指定一名联系人负责本合同的日常沟通。联系人变更的，应当提前三日书面通知对方。",
        "waiver": "一方未行使或者迟延行使本合同项下的权利，不视为放弃该权利。本合同任何条款被认定无效的，不影响其他条款的效力。",
        "entire": "本合同的附件是本合同的组成部分，与本合同具有同等法律效力。本合同构成双方就合同标的达成的完整协议，取代此前双方的口头或者书面约定。",
    }[slot]


def delivery_text(ctx: dict) -> str:
    """v1 的交付条款写的是"第一条约定的品名"，v2 第一条是定义，改成按条款号引用。"""
    if ctx["family"] in ("goods", "agri") or ctx["thing"] == "定作物":
        return fixed_text("delivery", ctx)
    if ctx["ctype"] == "entrust":
        return fixed_text("delivery", ctx)
    return f"乙方应当在合同签订后三十日内按<subject>约定的品名、规格和数量完成工作，并以书面或者电子方式向甲方交付{ctx['thing']}。"


def std_text(slot: str, ctx: dict) -> str | None:
    if slot == "subject":
        return subject_text(ctx)
    if slot == "delivery":
        return delivery_text(ctx)
    if slot in ("quality", "data", "force_majeure", "notice", "misc"):
        return fixed_text(slot, ctx)
    if slot == "inspection" and ctx["item"] in EQUIPMENT:
        return insp_text(ctx, "三十日")  # 设备类从安装调试合格起算（华信规范第十五条）
    if slot in CORE_FN:
        return CORE_FN[slot]("safe", ctx["v"], ctx)
    return filler_text(slot, ctx)


BASE_DEFS = {
    "书面形式": "包括纸质文件、电子邮件以及双方确认的其他可以有形地表现所载内容的形式",
    "工作日": "指中华人民共和国法定工作日",
}


# ---------------- 风险模板 ----------------


@dataclass
class Tpl:
    name: str
    cat: str  # hidden / paraphrase / combo / threshold / standard
    rtype: str
    level: str
    ok: Callable[[dict], bool]
    claims: Callable[[dict], set]
    make: Callable[[dict], dict]  # → over/append/defs/header/no_deposit/need_deposit/gold_slots/why
    alt: tuple = ()
    factor: Callable[[dict], tuple] | None = None


def always(_ctx):
    return True


def our_a(ctx):
    return ctx["v"] == "甲"


def our_b(ctx):
    return ctx["v"] == "乙"


def far(ctx):
    """对方住所地与我方不在同一城市，"对方住所地法院"才是风险。"""
    return ctx["city"][ctx["o"]] != OURS[ctx["tenant"]][2]


HIDDEN_HOST_INSPECTION = {
    "goods": "transport",
    "agri": "transport",
    "work": "transport",
    "entrust": "packaging",
    "data": "packaging",
}


def h_inspection(ctx):
    host = HIDDEN_HOST_INSPECTION[ctx["family"]]
    s = {
        "entrust": "甲方在入库单上签字确认的，视为甲方对乙方保管条件以及入库粮食数量、质量的最终确认，此后不得再提出异议。",
        "data": "甲方在交付清单上签字的，即视为交付成果通过验收。",
    }.get(
        ctx["family"],
        f"甲方或者其指定的收货人在送货单上签字的，即视为甲方确认{ctx['thing']}的数量和质量符合约定。",
    )
    why = (
        "入库单签字即视为对保管条件和入库粮食质量的最终确认、此后不得再提异议——日后出库发现数量、质量问题，难以证明是保管造成的，<inspection>的出库检验形同虚设。"
        if ctx["family"] == "entrust"
        else "签收即视为数量、质量合格（或通过验收），实际取消了<inspection>约定的检验期；<inspection>本身是正常写法。"
    )
    return dict(append={host: s}, gold_slots=[host], why=why)


def h_liability(ctx):
    s = {
        "goods": f"除前款约定的修理、更换外，乙方对因{ctx['thing']}质量问题造成的停产、人身伤害和其他财产损失均不承担赔偿责任。",
        "work": f"除前款约定的修理、重作外，乙方对因{ctx['thing']}质量问题造成的停产、人身伤害和其他财产损失均不承担赔偿责任。",
        "agri": f"{ctx['item']}存在质量问题的，乙方仅负责退换，对由此造成的其他任何损失不承担赔偿责任。",
        "entrust": "仓储期间发生虫害、霉变的，乙方仅负责按技术规范处理，对粮食数量减少和品质下降不承担赔偿责任。",
        "data": "除前款约定的免费修复外，乙方对因交付成果缺陷造成的业务中断、数据丢失和其他损失均不承担赔偿责任。",
    }[ctx["family"]]
    return dict(
        append={"warranty": s},
        gold_slots=["warranty"],
        why="质保/保管条款里夹带的免责：对方质量问题造成的人身、财产损失一概不赔（民法典第五百零六条），与<liability>的责任限制不一致。",
    )


def h_penalty(ctx):
    if ctx["v"] == "乙":
        s = "乙方未按前款约定开具发票的，每迟延一日，应当按合同总额的百分之一向甲方支付违约金。"
    else:
        s = "甲方应当在收到发票后三日内完成认证；逾期的，每迟延一日，按合同总额的百分之一向乙方支付违约金。"
    return dict(
        append={"invoice": s},
        gold_slots=["invoice"],
        why="发票条款里的违约金只约束我方，按合同总额日百分之一计、不设上限，绕开了<penalty>里对等、有上限的违约责任。",
    )


def h_termination(ctx):
    v, o = ctx["v"], ctx["o"]
    return dict(
        append={
            "change": f"{o}方因经营安排调整或者取消订单的，可以书面通知{v}方终止本合同尚未履行的部分，{o}方不因此承担任何费用或者赔偿责任。"
        },
        gold_slots=["change"],
        why="'合同变更'条款里藏着对方的任意解除权，我方已投入的成本得不到补偿；<termination>本身是对等的法定解除。",
    )


def h_jurisdiction(ctx):
    o = ctx["o"]
    return dict(
        append={
            "entire": f"本合同关于争议解决的约定与<jurisdiction>不一致的，以本条为准：因本合同产生的争议，由{o}方住所地人民法院管辖。"
        },
        gold_slots=["entire"],
        why="'完整协议'条款改写了管辖并声明优先，覆盖<jurisdiction>，实际由对方住所地法院管辖。",
    )


def h_auto_renew(ctx):
    v, o = ctx["v"], ctx["o"]
    return dict(
        over={
            "price_adjust": f"本合同单价在合同有效期内固定不变。本合同期满前{v}方未书面提出终止的，本合同按原条款延续一年，延续期间的单价由{o}方参照市场行情确定。"
        },
        gold_slots=["price_adjust"],
        why="价格条款里藏着自动续期，续期价格由对方单方确定，与<auto_renew>'期满终止'矛盾。",
    )


def h_ip(ctx):
    s = (
        "乙方在质量保证期内为修理、改进定作物形成的图纸、模具和工艺文件，知识产权归乙方所有。"
        if ctx["family"] == "work"
        else "乙方在提供维护和技术支持过程中形成的改进、优化及衍生成果，知识产权归乙方所有。"
    )
    return dict(
        append={"warranty": s},
        gold_slots=["warranty"],
        why="维护/质保条款把改进和衍生成果的知识产权留给对方，与<ip>'成果归甲方'冲突。",
    )


def h_confidentiality(ctx):
    v, o = ctx["v"], ctx["o"]
    return dict(
        append={
            "personnel": f"{v}方人员应当遵守{o}方的保密制度；{v}方人员泄露{o}方任何信息的，{v}方应当向{o}方支付违约金人民币五十万元。"
        },
        gold_slots=["personnel"],
        why="人员条款里加了只约束我方的保密违约金（五十万元），打破了<confidentiality>的对等保密。",
    )


def h_payment(ctx):
    return dict(
        append={
            "invoice": "甲方收到发票后，在其下游客户就相关项目向甲方付款后，再向乙方支付对应价款。"
        },
        gold_slots=["invoice"],
        why="发票条款里夹带背靠背付款，实际改写了<payment>的付款期限。",
    )


def h_deposit(ctx):
    return dict(
        append={
            "bond": "为担保本合同的履行，甲方应当在合同签订后三日内向乙方支付合同总额百分之三十五的定金。"
        },
        gold_slots=["bond"],
        no_deposit=True,
        why="'履约能力保证'条款里约定了百分之三十五的定金，超过法定百分之二十上限（民法典第五百八十六条）。",
    )


def h_liability_unlimited(ctx):
    if ctx["v"] == "乙":
        return dict(
            over={
                "ip_warranty": "乙方保证交付成果不侵犯任何第三方的知识产权；因此引起的一切损失，包括甲方的间接损失、可得利益损失和律师费，均由乙方承担，不受本合同任何责任限制条款的约束。"
            },
            gold_slots=["ip_warranty"],
            why="担保条款让我方承担不设上限、含间接损失的赔偿，并明确排除<liability>的责任上限。",
        )
    return dict(
        over={
            "privacy": "甲方保证所提供数据的来源合法；因数据来源问题引起的一切索赔、处罚和损失，均由甲方承担，不受本合同任何责任限制条款的约束。"
        },
        gold_slots=["privacy"],
        why="个人信息条款让我方对数据来源问题承担不设上限的全部责任，并明确排除<liability>的责任上限。",
    )


HIDDEN = [
    Tpl(
        "h_inspection",
        "hidden",
        "inspection",
        "高",
        our_a,
        lambda c: {HIDDEN_HOST_INSPECTION[c["family"]], "inspection"},
        h_inspection,
    ),
    Tpl(
        "h_liability",
        "hidden",
        "liability",
        "高",
        our_a,
        lambda c: {"warranty", "liability"},
        h_liability,
    ),
    Tpl(
        "h_penalty", "hidden", "penalty", "高", always, lambda c: {"invoice", "penalty"}, h_penalty
    ),
    Tpl(
        "h_termination",
        "hidden",
        "termination",
        "高",
        always,
        lambda c: {"change", "termination"},
        h_termination,
    ),
    Tpl(
        "h_jurisdiction",
        "hidden",
        "jurisdiction",
        "中",
        far,
        lambda c: {"entire", "jurisdiction"},
        h_jurisdiction,
    ),
    Tpl(
        "h_auto_renew",
        "hidden",
        "auto_renew",
        "中",
        always,
        lambda c: {"price_adjust", "auto_renew"},
        h_auto_renew,
    ),
    Tpl(
        "h_ip",
        "hidden",
        "ip",
        "高",
        lambda c: our_a(c) and TYPES[c["ctype"]]["ip"],
        lambda c: {"warranty", "ip"},
        h_ip,
    ),
    Tpl(
        "h_confidentiality",
        "hidden",
        "confidentiality",
        "中",
        always,
        lambda c: {"personnel", "confidentiality"},
        h_confidentiality,
        alt=("penalty",),
    ),
    Tpl("h_payment", "hidden", "payment", "高", our_b, lambda c: {"invoice", "payment"}, h_payment),
    Tpl("h_deposit", "hidden", "deposit", "中", our_a, lambda c: {"bond", "deposit"}, h_deposit),
    Tpl(
        "h_liability_unlimited",
        "hidden",
        "liability",
        "高",
        lambda c: (
            (our_b(c) and TYPES[c["ctype"]]["ip"]) or (our_a(c) and c["ctype"] in ("dproc", "ddev"))
        ),
        lambda c: {"ip_warranty" if c["v"] == "乙" else "privacy", "liability"},
        h_liability_unlimited,
    ),
]


def para(slot, text_fn, why):
    def make(ctx):
        return dict(over={slot: text_fn(ctx)}, gold_slots=[slot], why=why)

    return make


PARA = [
    Tpl(
        "p_penalty",
        "paraphrase",
        "penalty",
        "高",
        always,
        lambda c: {"penalty"},
        para(
            "penalty",
            lambda c: (
                f"{c['v']}方每迟延一日，应当向{c['o']}方补偿相当于合同金额千分之三十的款项，补偿款累计计算，且不以{c['o']}方的实际损失为限；{c['o']}方迟延的，按实际损失协商处理。"
            ),
            "没出现'违约金'字样，实质是只约束我方、按合同金额日千分之三十累计、无上限的违约金。",
        ),
    ),
    Tpl(
        "p_liability_cap",
        "paraphrase",
        "liability",
        "高",
        our_a,
        lambda c: {"liability"},
        para(
            "liability",
            lambda c: (
                "乙方在本合同项下的全部赔偿，以甲方最近一期已经支付的款项为限，无论损失系因何种原因造成。"
            ),
            "'无论何种原因'把故意和重大过失也纳入上限（民法典第五百零六条），上限还只是最近一期付款。",
        ),
    ),
    Tpl(
        "p_liability_indemnity",
        "paraphrase",
        "liability",
        "高",
        always,
        lambda c: {"liability"},
        para(
            "liability",
            lambda c: (
                f"{c['v']}方承诺使{c['o']}方免受因本合同产生或者与本合同有关的一切索赔、损失和费用，该项承诺不设金额限制。"
            ),
            "用'承诺使对方免受'表述的单方、无上限赔偿责任。",
        ),
    ),
    Tpl(
        "p_termination",
        "paraphrase",
        "termination",
        "高",
        always,
        lambda c: {"termination"},
        para(
            "termination",
            lambda c: (
                f"如{c['o']}方内部业务调整，本合同自{c['o']}方书面告知之日起不再继续履行，{c['v']}方已经投入的成本由{c['v']}方自行消化。"
            ),
            "没用'解除'二字，实质是对方无理由单方解除且不补偿我方已投入的成本。",
        ),
    ),
    Tpl(
        "p_inspection",
        "paraphrase",
        "inspection",
        "高",
        our_a,
        lambda c: {"inspection"},
        para(
            "inspection",
            lambda c: (
                f"甲方的签收行为即构成对{c['thing']}品质的最终确认，此后甲方不再就{c['thing']}的品质另行主张权利。"
            ),
            "签收即最终确认品质、放弃此后的质量主张，等于没有检验期。",
        ),
    ),
    Tpl(
        "p_payment_b2b",
        "paraphrase",
        "payment",
        "高",
        our_b,
        lambda c: {"payment"},
        para(
            "payment",
            lambda c: "价款于甲方终端客户回款后结算，结算周期以甲方内部财务流程为准。",
            "没写'背靠背'，实质是以下游回款为付款条件、付款期限由甲方单方决定。",
        ),
    ),
    Tpl(
        "p_payment_audit",
        "paraphrase",
        "payment",
        "高",
        our_b,
        lambda c: {"payment"},
        para(
            "payment",
            lambda c: (
                "甲方在项目全部完成并经甲方上级单位审核确认后，按审核确认的金额向乙方支付价款。"
            ),
            "付款时间和金额都取决于甲方单方（及其上级）审核，没有确定的付款期限。",
        ),
    ),
    Tpl(
        "p_auto_renew",
        "paraphrase",
        "auto_renew",
        "高",
        always,
        lambda c: {"auto_renew"},
        para(
            "auto_renew",
            lambda c: (
                f"本合同期满时，双方未另行书面约定的，本合同效力延续至下一合同年度，此后亦同；延续期间{c['v']}方不得单方终止。"
            ),
            "没写'自动续期'，实质是无限期自动延续且我方不能终止。",
        ),
    ),
    Tpl(
        "p_jurisdiction",
        "paraphrase",
        "jurisdiction",
        "中",
        far,
        lambda c: {"jurisdiction"},
        para(
            "jurisdiction",
            lambda c: (
                f"双方同意，与本合同有关的一切纠纷交由{c['city'][c['o']]}有管辖权的人民法院专属处理。"
            ),
            "没写'对方住所地'，但指定的是对方所在城市的法院。",
        ),
    ),
    Tpl(
        "p_jurisdiction_or",
        "paraphrase",
        "jurisdiction",
        "中",
        always,
        lambda c: {"jurisdiction"},
        para(
            "jurisdiction",
            lambda c: (
                f"争议可以提交{c['city'][c['o']]}仲裁委员会裁决；任何一方也可以向有管辖权的人民法院起诉。"
            ),
            "没写'或裁或诉'，实质是既可仲裁又可诉讼，仲裁约定无效，也违反租户规范。",
        ),
    ),
    Tpl(
        "p_deposit",
        "paraphrase",
        "deposit",
        "中",
        our_a,
        lambda c: {"deposit"},
        lambda c: dict(
            over={
                "deposit": "甲方于签约时交付合同总额三成的款项作为订约及履约担保；甲方未履行合同的，该款项归乙方所有，不予返还。"
            },
            gold_slots=["deposit"],
            need_deposit=True,
            why="没称'定金'，但有定金罚则效果（违约不返还），比例百分之三十超过法定百分之二十上限。",
        ),
    ),
    Tpl(
        "p_ip_a",
        "paraphrase",
        "ip",
        "高",
        lambda c: our_a(c) and TYPES[c["ctype"]]["ip"],
        lambda c: {"ip"},
        para(
            "ip",
            lambda c: "成果的全部权益由乙方保留，甲方取得的是在本项目范围内使用成果的许可。",
            "没写'知识产权归乙方'，实质是我方付费定制的成果归对方，我方只有项目内许可。",
        ),
    ),
    Tpl(
        "p_ip_b",
        "paraphrase",
        "ip",
        "中",
        lambda c: our_b(c) and TYPES[c["ctype"]]["ip"],
        lambda c: {"ip"},
        para(
            "ip",
            lambda c: (
                "乙方交付成果时，一并交付其为本项目使用的全部工具、组件和源代码，上述内容的全部权益随成果一并归属甲方。"
            ),
            "我方既有的工具、组件和源代码随成果一并转让给对方，且没有对价。",
        ),
    ),
    Tpl(
        "p_confidentiality",
        "paraphrase",
        "confidentiality",
        "中",
        always,
        lambda c: {"confidentiality"},
        para(
            "confidentiality",
            lambda c: (
                f"{c['v']}方应当对{c['o']}方的全部信息永久守密，不论该信息是否已经公开；{c['o']}方对{c['v']}方信息的使用不受限制。"
            ),
            "没出现'保密义务'字样，实质是只约束我方的永久保密，对方使用我方信息不受限制。",
        ),
    ),
]


def c_jur_sign(ctx):
    return dict(
        over={
            "jurisdiction": "因本合同发生的争议，双方应当协商解决；协商不成的，由合同签订地人民法院管辖。"
        },
        header={"sign": ctx["city"][ctx["o"]]},
        gold_slots=["jurisdiction"],
        why=f"单看'由合同签订地法院管辖'没问题，但合同首部的签订地点是对方所在地（{ctx['city'][ctx['o']]}），实际由对方所在地法院管辖。",
    )


def c_deposit_pay(ctx):
    return dict(
        over={
            "deposit": "甲方应当在合同签订后五日内向乙方支付合同总额百分之二十的定金，定金在最后一期付款时抵作价款。一方违约时，对方可以选择适用定金条款或者违约金条款，但不得同时适用。",
            "payment": "合同签订后五日内，甲方向乙方支付合同总额百分之二十的预付款，该预付款同时作为本合同的定金；余款在验收合格并收到乙方开具的合规发票后三十日内付清。",
        },
        need_deposit=True,
        gold_slots=["deposit", "payment"],
        why="<deposit>单看是百分之二十（未超限），但<payment>又把百分之二十的预付款'同时作为定金'，定金合计百分之四十。",
    )


def c_liab_pay(ctx):
    return dict(
        over={
            "liability": "除因故意或者重大过失造成的损失外，乙方在本合同项下的赔偿责任总额不超过合同总额，且不包括间接损失。"
        },
        defs={"合同总额": "在计算赔偿责任上限时，指甲方依照本合同已经实际支付的价款"},
        gold_slots=["liability", "definitions", "payment"],
        why="<liability>单看是'不超过合同总额'，但<definitions>把计算上限时的'合同总额'定义为甲方已实际支付的价款，而<payment>约定验收后才付全部价款——交付、验收阶段出问题时对方的赔偿上限实际为零。",
    )


def c_term_def(ctx):
    return dict(
        over={
            "termination": "一方迟延履行主要债务的，守约方可以书面通知对方解除本合同；因不可抗力致使不能实现合同目的的，任何一方可以解除本合同。"
        },
        defs={"主要债务": "包括乙方按照甲方要求提交工作周报、发票、资质文件及其他资料的义务"},
        gold_slots=["termination", "definitions"],
        why="<termination>看似对等，但<definitions>把提交周报、发票等次要义务也算作'主要债务'，且解除不需催告——我方任何资料晚交，对方即可解除。",
    )


def c_conf_def(ctx):
    return dict(
        over={
            "confidentiality": "双方对在履行本合同过程中知悉的保密信息负有保密义务，未经披露方书面同意不得向第三方披露；保密义务在本合同终止后五年内持续有效。"
        },
        defs={
            "保密信息": "指甲方以任何形式向乙方披露的，或者乙方在履行本合同过程中知悉的甲方技术信息、经营信息和数据"
        },
        gold_slots=["confidentiality", "definitions"],
        why="<confidentiality>写的是'双方'，但<definitions>把'保密信息'限定为甲方的信息，我方（乙方）的信息得不到保护。",
    )


IP_DEF = {
    "work": (
        "定作物",
        "仅指乙方交付的成品，不包括为制作定作物形成的图纸、模具和工艺文件",
        "图纸、模具和工艺文件",
    ),
    "dproc": (
        "交付成果",
        "指乙方交付的标注结果文件，不包括标注规范、标注工具和其他中间成果",
        "标注规范和标注工具",
    ),
    "ddev": (
        "交付成果",
        "指乙方最终交付的可执行程序和使用说明，不包括源代码、模型参数和其他中间成果",
        "源代码和模型参数",
    ),
}


def c_ip_def(ctx):
    term, meaning, core = IP_DEF[ctx["ctype"]]
    return dict(
        defs={term: meaning},
        gold_slots=["ip", "definitions"],
        why=f"<ip>约定'{ctx['thing']}的知识产权归甲方'，但<definitions>把'{term}'限定得很窄，{core}等核心成果不在其中，仍归对方。",
    )


def c_insp_batch(ctx):
    thing = ctx["thing"]
    return dict(
        over={
            "inspection": f"甲方应当自首批{thing}交付之日起三十日内进行检验；发现数量或者质量不符合约定的，应当在该期限内书面通知乙方，乙方应当在收到通知后十日内更换、补足或者修复。甲方在该期限内未书面提出异议的，视为{thing}的数量和质量符合约定。",
            "delivery": f"乙方可以在合同签订后九十日内分批向甲方交付{thing}，每批交付的数量由乙方根据生产安排确定，交付地点为甲方指定地点，运输费用由乙方承担。",
        },
        gold_slots=["inspection", "delivery"],
        why="检验期从'首批'交付起算三十日，而<delivery>允许乙方九十日内分批交付——第三十日以后到的货没有检验期，直接视为合格。",
    )


def c_pay_insp(ctx):
    return dict(
        over={
            "payment": "合同签订后五日内，甲方向乙方支付合同总额的百分之三十作为首付款；余款在验收合格并收到乙方开具的合规发票后三十日内付清。",
            "inspection": f"{ctx['thing']}的验收在甲方所承接项目整体竣工验收时一并进行，验收结论以甲方项目的竣工验收结论为准。",
        },
        gold_slots=["payment", "inspection"],
        why="<payment>单看正常（验收后三十日付余款），但<inspection>把验收推迟到甲方自己的项目整体竣工、也没有'逾期视为验收'——七成余款没有确定的付款期限，变相背靠背。",
    )


def c_deposit_penalty(ctx):
    return dict(
        over={
            "deposit": "甲方应当在合同签订后五日内向乙方支付合同总额百分之十五的定金，定金在最后一期付款时抵作价款。",
            "penalty": "任何一方违约的，应当向守约方支付合同总额百分之二十的违约金。违约金不足以弥补守约方损失的，违约方还应当赔偿差额部分。",
        },
        need_deposit=True,
        gold_slots=["penalty", "deposit"],
        why="<deposit>（百分之十五）和<penalty>（百分之二十）单看都在常见范围内，但两条都没约定'择一适用'——对方可以同时没收定金并主张违约金（民法典第五百八十八条只能择一，瑞丰办法第八条要求明确择一）；我方是交定金的一方。",
    )


def c_pay_invoice(ctx):
    return dict(
        over={
            "invoice": "乙方应当在收到甲方支付的相应款项后十日内，向甲方开具等额的增值税专用发票。"
        },
        gold_slots=["payment", "invoice"],
        why="<payment>以'收到合规发票'为余款付款前提，<invoice>却要求'收到款项后'才开票——两个条件互为前提，余款付款条件可能永远不成就。",
    )


def c_penalty_def(ctx):
    return dict(
        over={
            "penalty": "违约方应当按合同总额的百分之五向守约方支付违约金；违约金不足以弥补损失的，违约方还应当赔偿差额部分。"
        },
        defs={"违约方": "指未按约定履行付款、接收或者验收配合义务的一方"},
        gold_slots=["penalty", "definitions"],
        why="<penalty>看似对等，但<definitions>里'违约方'只列了甲方的义务（付款、接收、验收配合）——乙方逾期交付、质量不合格都不构成'违约方'，实际只约束我方。",
    )


def c_renew_def(ctx):
    return dict(
        over={"auto_renew": "本合同有效期为服务期。服务期届满，本合同终止。"},
        defs={"服务期": "自首次交付之日起算，每满一年自动顺延一年，直至甲方书面通知终止"},
        gold_slots=["auto_renew", "definitions"],
        why="<auto_renew>写'服务期届满终止'，但<definitions>让服务期每年自动顺延、只有甲方能叫停——我方被无限期锁定。",
    )


COMBO = [
    Tpl("c_jur_sign", "combo", "jurisdiction", "中", far, lambda c: {"jurisdiction"}, c_jur_sign),
    Tpl(
        "c_deposit_pay",
        "combo",
        "deposit",
        "中",
        our_a,
        lambda c: {"deposit", "payment"},
        c_deposit_pay,
    ),
    Tpl(
        "c_liab_pay",
        "combo",
        "liability",
        "高",
        our_a,
        lambda c: {"liability", "payment", "definitions"},
        c_liab_pay,
    ),
    Tpl(
        "c_term_def",
        "combo",
        "termination",
        "高",
        our_b,
        lambda c: {"termination", "definitions"},
        c_term_def,
    ),
    Tpl(
        "c_conf_def",
        "combo",
        "confidentiality",
        "中",
        our_b,
        lambda c: {"confidentiality", "definitions"},
        c_conf_def,
    ),
    Tpl(
        "c_ip_def",
        "combo",
        "ip",
        "高",
        lambda c: our_a(c) and c["ctype"] in IP_DEF,
        lambda c: {"ip", "definitions"},
        c_ip_def,
    ),
    Tpl(
        "c_insp_batch",
        "combo",
        "inspection",
        "高",
        lambda c: our_a(c) and c["family"] in ("goods", "agri"),
        lambda c: {"inspection", "delivery"},
        c_insp_batch,
    ),
    Tpl(
        "c_pay_insp",
        "combo",
        "payment",
        "高",
        lambda c: our_b(c) and c["family"] in ("goods", "work", "data"),
        lambda c: {"payment", "inspection"},
        c_pay_insp,
        alt=("inspection",),
    ),
    Tpl(
        "c_deposit_penalty",
        "combo",
        "penalty",
        "中",
        our_a,
        lambda c: {"penalty", "deposit"},
        c_deposit_penalty,
        alt=("deposit",),
    ),
    Tpl(
        "c_pay_invoice",
        "combo",
        "payment",
        "高",
        our_b,
        lambda c: {"payment", "invoice"},
        c_pay_invoice,
    ),
    Tpl(
        "c_penalty_def",
        "combo",
        "penalty",
        "高",
        our_a,
        lambda c: {"penalty", "definitions"},
        c_penalty_def,
    ),
    Tpl(
        "c_renew_def",
        "combo",
        "auto_renew",
        "中",
        lambda c: our_b(c) and c["family"] in ("work", "entrust", "data"),
        lambda c: {"auto_renew", "definitions"},
        c_renew_def,
    ),
]

# ---------------- 临界值（风险与合规干扰项共用一套参数） ----------------
# 单价倍数区间：risk 刚超过复核线、ok 低于说明线；两种均价（全部 / 近十二个月）下都成立
UNIT_RANGE = {
    "huaxin": {"risk": (1.225, 1.245), "ok": (1.05, 1.07), "thr": "百分之二十", "art": "第四条"},
    "yunqi": {"risk": (1.175, 1.195), "ok": (1.07, 1.10), "thr": "百分之十五", "art": "第八条"},
    "ruifeng": {"risk": (1.175, 1.195), "ok": (1.03, 1.05), "thr": "百分之十五", "art": "第五条"},
}


def inspection_days(ctx, risk: bool) -> tuple[str, str]:
    """(期限写法, 规范要求)"""
    if ctx["tenant"] == "huaxin":
        if ctx["item"] in EQUIPMENT:
            return ("二十五日" if risk else "三十日"), "设备类不少于三十日（华信规范第十五条）"
        return ("十二日" if risk else "十五日"), "不少于十五日（华信规范第十五条）"
    if ctx["tenant"] == "yunqi":
        return ("十个工作日" if risk else "十五个工作日"), "不少于十五个工作日（云启细则第十五条）"
    return ("五日" if risk else "七日"), "不少于七日（瑞丰办法第九条）"


def insp_text(ctx, days):
    thing = ctx["thing"]
    start = f"{thing}安装调试合格" if ctx["item"] in EQUIPMENT else f"收到{thing}"
    return f"甲方应当在{start}之日起{days}内进行检验；发现数量或者质量不符合约定的，应当在该期限内书面通知乙方，乙方应当在收到通知后十日内更换、补足或者修复。甲方在该期限内未书面提出异议的，视为{thing}的数量和质量符合约定。"


def conf_text(years):
    return f"双方对在履行本合同过程中知悉的对方商业秘密、技术资料和数据负有保密义务，未经对方书面同意不得向第三方披露；保密义务在本合同终止后{years}内持续有效。"


def deposit_text(pct):
    return f"甲方应当在合同签订后五日内向乙方支付合同总额{pct}的定金，定金在最后一期付款时抵作价款。一方违约时，对方可以选择适用定金条款或者违约金条款，但不得同时适用。"


def penalty_text(daily, cap):
    return f"任何一方违约的，应当赔偿因此给对方造成的直接损失。逾期付款或者逾期履行的，每逾期一日，按逾期部分金额的{daily}支付违约金，违约金总额不超过合同总额的{cap}。"


def renew_text(pct):
    return f"本合同有效期一年。期满前三十日任何一方未书面通知不续期的，本合同自动续期一年，续期价格涨幅不超过{pct}。"


def term_text(ctx, days):
    v, o = ctx["v"], ctx["o"]
    return f"{o}方可以提前{days}书面通知{v}方解除本合同，并按{v}方已完成的工作量结算费用；其他情形下的解除，适用法律规定。"


def pay_seller_text(ctx, risk):
    if ctx["tenant"] == "huaxin":
        days = "七十五日" if risk else "六十日"
        return f"合同签订后五日内，甲方向乙方支付合同总额的百分之三十作为首付款；余款在验收合格（含依约视为验收合格）并收到乙方开具的合规发票后{days}内付清。"
    pct = "百分之二十" if risk else "百分之三十"
    return f"合同签订后五日内，甲方向乙方支付合同总额的{pct}作为首付款；余款在验收合格（含依约视为验收合格）并收到乙方开具的合规发票后三十日内付清。"


def thr(slot, text_fn, why_fn, **kw):
    def make(ctx):
        return dict(over={slot: text_fn(ctx)}, gold_slots=[slot], why=why_fn(ctx), **kw)

    return make


def tenant_is(*ts):
    return lambda c: c["tenant"] in ts


THRESH = [
    Tpl(
        "t_unit_huaxin",
        "threshold",
        "unit_price",
        "中",
        lambda c: our_a(c) and c["tenant"] == "huaxin",
        lambda c: {"subject"},
        lambda c: dict(gold_slots=["subject"], why=""),
        factor=lambda c: UNIT_RANGE["huaxin"]["risk"],
    ),
    Tpl(
        "t_unit_yunqi",
        "threshold",
        "unit_price",
        "中",
        lambda c: our_a(c) and c["tenant"] == "yunqi",
        lambda c: {"subject"},
        lambda c: dict(gold_slots=["subject"], why=""),
        factor=lambda c: UNIT_RANGE["yunqi"]["risk"],
    ),
    Tpl(
        "t_unit_ruifeng",
        "threshold",
        "unit_price",
        "中",
        lambda c: our_a(c) and c["tenant"] == "ruifeng",
        lambda c: {"subject"},
        lambda c: dict(gold_slots=["subject"], why=""),
        factor=lambda c: UNIT_RANGE["ruifeng"]["risk"],
    ),
    Tpl(
        "t_deposit",
        "threshold",
        "deposit",
        "中",
        our_a,
        lambda c: {"deposit"},
        thr(
            "deposit",
            lambda c: deposit_text("百分之二十二"),
            lambda c: (
                "定金为合同总额的百分之二十二，刚超过法定百分之二十上限（民法典第五百八十六条），超出部分不产生定金效力。"
            ),
            need_deposit=True,
        ),
    ),
    Tpl(
        "t_inspection_huaxin",
        "threshold",
        "inspection",
        "中",
        lambda c: our_a(c) and c["tenant"] == "huaxin",
        lambda c: {"inspection"},
        thr(
            "inspection",
            lambda c: insp_text(c, inspection_days(c, True)[0]),
            lambda c: (
                f"检验期{inspection_days(c, True)[0]}，逾期视为合格；规范要求{inspection_days(c, True)[1]}。"
            ),
        ),
    ),
    Tpl(
        "t_inspection_yunqi",
        "threshold",
        "inspection",
        "中",
        lambda c: our_a(c) and c["tenant"] == "yunqi",
        lambda c: {"inspection"},
        thr(
            "inspection",
            lambda c: insp_text(c, inspection_days(c, True)[0]),
            lambda c: (
                f"检验期{inspection_days(c, True)[0]}，逾期视为合格；规范要求{inspection_days(c, True)[1]}。"
            ),
        ),
    ),
    Tpl(
        "t_conf_huaxin",
        "threshold",
        "confidentiality",
        "中",
        lambda c: c["ctype"] == "work",
        lambda c: {"confidentiality"},
        thr(
            "confidentiality",
            lambda c: conf_text("二年"),
            lambda c: (
                "加工承揽涉及我方图纸和工艺参数，保密期只有合同终止后二年；华信规范第十九条要求不少于三年。"
            ),
        ),
    ),
    Tpl(
        "t_conf_yunqi",
        "threshold",
        "confidentiality",
        "中",
        tenant_is("yunqi"),
        lambda c: {"confidentiality"},
        thr(
            "confidentiality",
            lambda c: conf_text("三年"),
            lambda c: "保密期为合同终止后三年；云启细则第三条要求不少于五年。",
        ),
    ),
    Tpl(
        "t_pay_huaxin",
        "threshold",
        "payment",
        "中",
        lambda c: our_b(c) and c["tenant"] == "huaxin",
        lambda c: {"payment"},
        thr(
            "payment",
            lambda c: pay_seller_text(c, True),
            lambda c: "我方是出卖方，余款赊销期七十五日；华信规范第八条要求赊销期限不超过六十日。",
        ),
    ),
    Tpl(
        "t_pay_yunqi",
        "threshold",
        "payment",
        "中",
        lambda c: our_b(c) and c["tenant"] == "yunqi",
        lambda c: {"payment"},
        thr(
            "payment",
            lambda c: pay_seller_text(c, True),
            lambda c: "我方是收款方，首付款只有百分之二十；云启细则第九条要求不低于百分之三十。",
        ),
    ),
    Tpl(
        "t_penalty_ruifeng",
        "threshold",
        "penalty",
        "中",
        lambda c: our_a(c) and c["tenant"] == "ruifeng",
        lambda c: {"penalty"},
        thr(
            "penalty",
            lambda c: penalty_text("万分之八", "百分之二十"),
            lambda c: "我方逾期付款的违约金日比例为万分之八；瑞丰办法第十一条要求不高于万分之五。",
        ),
    ),
    Tpl(
        "t_penalty_cap",
        "threshold",
        "penalty",
        "中",
        tenant_is("huaxin", "yunqi"),
        lambda c: {"penalty"},
        thr(
            "penalty",
            lambda c: penalty_text("万分之五", "百分之二十五"),
            lambda c: (
                "违约金总额上限为合同总额的百分之二十五；租户规范要求我方承担的违约金不超过合同总额的百分之二十。"
            ),
        ),
    ),
    Tpl(
        "t_termination_yunqi",
        "threshold",
        "termination",
        "中",
        lambda c: our_b(c) and c["tenant"] == "yunqi",  # 细则第十三条保护的是我方作为服务方的投入
        lambda c: {"termination"},
        thr(
            "termination",
            lambda c: term_text(c, "二十日"),
            lambda c: "对方可以提前二十日通知解除；云启细则第十三条要求提前通知期不少于三十日。",
        ),
    ),
    Tpl(
        "t_renew_yunqi",
        "threshold",
        "auto_renew",
        "中",
        lambda c: our_a(c) and c["tenant"] == "yunqi",
        lambda c: {"auto_renew"},
        thr(
            "auto_renew",
            lambda c: renew_text("百分之八"),
            lambda c: "自动续期的价格涨幅上限为百分之八；云启细则第十四条要求不超过百分之五。",
        ),
    ),
    Tpl(
        "t_liability_huaxin",
        "threshold",
        "liability",
        "中",
        lambda c: our_a(c) and c["tenant"] == "huaxin",
        lambda c: {"liability"},
        thr(
            "liability",
            lambda c: (
                "除因故意或者重大过失造成的损失外，乙方在本合同项下的赔偿责任总额不超过合同总额的百分之八十，且不包括间接损失和可得利益损失。"
            ),
            lambda c: (
                "对方的责任上限是合同总额的百分之八十，低于合同总额；华信规范第十三条要求报法务部审查。"
            ),
        ),
    ),
]

STD_WHY = {
    "penalty": "违约金过高、只约束我方或者不对等。",
    "liability": "免除对方责任，或者让我方承担无上限的责任。",
    "termination": "对方享有单方任意解除权，或者我方的解除条件明显苛刻。",
    "inspection": "检验期过短或者交付即视为合格。",
    "payment": "付款期过长、背靠背或者付款时间由甲方单方决定。",
    "confidentiality": "保密义务只约束我方，或者缺少保密条款。",
    "ip": "知识产权归属对我方不利。",
    "jurisdiction": "由对方所在地或者对方指定的法院管辖，或者或裁或诉。",
    "auto_renew": "自动续期且我方难以退出，或者续期价格由对方决定。",
    "deposit": "定金超过合同总额的百分之二十。",
    "unit_price": "单价明显高于台账历史均价。",
}


# 标准位置的具体理由（审查 v2 低 8：不要用"A 或者 B"式的通用说法）
STD_WHY_V = {
    (
        "ip",
        "R1",
        "乙",
    ): "我方投入的既有技术、工具、数据以及成果的知识产权全部归对方，且我方不得在其他项目中使用。",
    ("ip", "R2", "乙"): "我方原有技术的知识产权随交付一并转让给对方，没有另外对价。",
    (
        "penalty",
        "R1",
    ): "只约束我方的违约金：按合同总额日百分之一计、不设上限，对方逾期只'另行协商'。",
    (
        "penalty",
        "R2",
    ): "我方违反任何约定都要付合同总额百分之五十的违约金，另赔全部损失，明显过高且只约束我方。",
    (
        "penalty",
        "R3",
    ): "对方可以扣留全部款项作违约金，我方只能要求对方继续履行，违约责任完全不对等。",
    (
        "liability",
        "R1",
    ): "对方对给我方造成的任何损失都不承担责任，包括故意和重大过失（民法典第五百零六条）。",
    ("liability", "R2"): "我方对对方的一切直接和间接损失承担全部责任，不受合同金额限制。",
    (
        "liability",
        "R3",
    ): "对方故意或重大过失造成我方财产损失，赔偿也以一千元为限，属于无效免责（民法典第五百零六条）。",
    ("termination", "R1"): "对方可以随时、无理由解除且不承担任何责任。",
    ("termination", "R2"): "对方以'不符合经营需要'为由提前三日即可解除，我方已发生费用自担。",
    (
        "termination",
        "R3",
    ): "对方可单方解除，我方要等对方迟延一百八十日以上才能解除，解除权严重不对等。",
    ("inspection", "R1"): "检验期只有二日，逾期即视为合格。",
    ("inspection", "R2"): "交付即视为验收合格，我方此后不得再提质量异议。",
    ("inspection", "R3"): "异议期只有三日，且提出异议的检测费用一律由我方承担。",
    ("payment", "R1"): "收到发票后一百八十日才付款，付款周期过长。",
    ("payment", "R2"): "背靠背付款：下游客户不付款，甲方就不付。",
    ("payment", "R3"): "付款时间由甲方根据资金安排确定，没有确定期限。",
    ("deposit", "R1"): "定金为合同总额的百分之三十，超过法定百分之二十上限。",
    ("deposit", "R2"): "定金百分之四十，且我方违约时定金不退还要另付违约金，定金与违约金叠加。",
    (
        "confidentiality",
        "R1",
    ): "只有我方负永久保密义务，违反还要付五十万元违约金，对方不负保密义务。",
    ("confidentiality", "R2"): "合同涉及数据处理却没有保密条款。",
    (
        "ip",
        "R1",
    ): "我方付费开发的全部成果（含源代码、图纸）的知识产权归对方，我方只有项目内非独占使用权。",
    ("ip", "R2"): "成果知识产权归对方，我方修改或用于其他项目还要另付许可费。",
    ("jurisdiction", "R1"): "由对方住所地法院管辖，对我方应诉不利。",
    ("jurisdiction", "R2"): "既可以仲裁也可以诉讼（或裁或诉），仲裁约定无效，也违反租户规范。",
    ("jurisdiction", "R3"): "由对方指定的法院管辖，管辖地完全由对方决定。",
    ("auto_renew", "R1"): "对方不通知终止就自动续期，我方不得拒绝，续期价格由对方确定。",
    (
        "auto_renew",
        "R2",
    ): "无限次自动续期，我方要提前一百八十日通知并支付剩余期限费用百分之三十才能终止。",
}


def std_tpls() -> list[Tpl]:
    out = []
    for rtype, variants in VARIANTS.items():
        for var in variants:

            def make(ctx, rtype=rtype, var=var):
                body = CORE_FN[rtype](var, ctx["v"], ctx)
                why = STD_WHY_V.get((rtype, var, ctx["v"])) or STD_WHY_V.get(
                    (rtype, var), STD_WHY[rtype]
                )
                eff = dict(over={rtype: body}, gold_slots=[rtype], why=why)
                if rtype == "deposit":
                    eff["need_deposit"] = True
                return eff

            def ok(ctx, rtype=rtype, var=var):
                if rtype in ("inspection", "deposit") and ctx["v"] != "甲":
                    return False
                if rtype == "payment" and ctx["v"] != "乙":
                    return False
                if rtype == "ip" and not TYPES[ctx["ctype"]]["ip"]:
                    return False
                if rtype == "confidentiality" and var == "R2" and not TYPES[ctx["ctype"]]["data"]:
                    return False
                if rtype == "jurisdiction" and var == "R1" and not far(ctx):
                    return False
                return True

            out.append(
                Tpl(
                    f"s_{rtype}_{var}",
                    "standard",
                    rtype,
                    LEVEL[(rtype, var)],
                    ok,
                    lambda c, rtype=rtype: {rtype},
                    make,
                )
            )
    out.append(
        Tpl(
            "s_unit_price_R1",
            "standard",
            "unit_price",
            "中",
            our_a,
            lambda c: {"subject"},
            lambda c: dict(gold_slots=["subject"], why=STD_WHY["unit_price"]),
            factor=lambda c: (1.28, 1.45),
        )
    )
    return out


STANDARD = std_tpls()

# ---------------- 干扰项（只放在干净合同里） ----------------


@dataclass
class Dis:
    name: str
    kind: str  # near_threshold / legit_exemption / favorable / combo_ok
    ok: Callable[[dict], bool]
    claims: Callable[[dict], set]
    make: Callable[[dict], dict]  # → over/append/header/need_deposit/slot/note/favorable
    factor: Callable[[dict], tuple] | None = None


def dis(slot, text_fn, note, kind="near_threshold", **kw):
    def make(ctx):
        return dict(
            over={slot: text_fn(ctx)},
            slot=slot,
            note=note if isinstance(note, str) else note(ctx),
            **kw,
        )

    return make


LEGIT_EXEMPT = {
    "goods": "因甲方使用不当、擅自拆修造成的质量问题，不属于乙方质量保证范围。",
    "work": "因甲方提供的图纸、材料存在缺陷或者甲方使用不当造成的质量问题，不属于乙方质量保证范围。",
    "agri": "因甲方未按约定条件储存造成的霉变、虫害，乙方不承担责任。",
    "entrust": "因甲方入库时粮食已存在的质量问题，或者甲方指令的储存方式造成的损失，乙方不承担责任。",
    "data": "因甲方擅自修改交付成果，或者在约定环境以外运行交付成果造成的问题，不属于维护范围。",
}
FAV = [
    ("penalty", "R2"),
    ("termination", "R1"),
    ("liability", "R1"),
    ("jurisdiction", "R1"),
    ("auto_renew", "R1"),
    ("ip", "R1"),
    ("confidentiality", "R1"),
]


# 对我方有利、但租户规范要求"对等"或依法可能无效的条款：提示"不能依赖/需改对等"也是合理意见（审查 v2 中 6、低 11）
FAV_ACCEPT = {
    ("confidentiality", "yunqi"): "云启细则第三条要求保密义务双方对等",
    ("confidentiality", "ruifeng"): "瑞丰办法第十五条要求保密义务对等",
    ("penalty", "ruifeng"): "瑞丰办法第十二条要求违约条款双方对等",
    ("liability", "*"): "全部免责依民法典第五百零六条可能部分无效",
    # 问题日志 P25：自动续期条款没约定提前通知不续期，三家规范都有要求
    ("auto_renew", "huaxin"): "华信规范第十八条要求约定期满前三十日可书面通知不续期",
    ("auto_renew", "yunqi"): "云启细则第十四条要求约定到期前三十日可通知不续期",
    ("auto_renew", "ruifeng"): "瑞丰办法第十四条要求约定提前十五日书面通知不续期",
}


def fav_dis(rtype, var) -> Dis:
    def make(ctx):
        body = CORE_FN[rtype](var, ctx["o"], ctx)  # 受损方是对方
        return dict(
            over={rtype: body},
            slot=rtype,
            note=f"对我方有利的单方条款（{RISK_TYPES[rtype]}，受损方是对方）",
            favorable=dict(type=rtype, variant=var, victim=ctx["o"]),
        )

    def ok(ctx):
        if rtype == "ip":
            # ip R1 的"受损方是对方"写法只对甲方我方有意义（成果归甲方、乙方不得复用）
            return TYPES[ctx["ctype"]]["ip"] and ctx["v"] == "甲"
        if rtype == "jurisdiction":
            return ctx["city"][ctx["v"]] != ctx["city"][ctx["o"]]
        return True

    return Dis(f"d_fav_{rtype}", "favorable", ok, lambda c: {rtype}, make)


DISTRACTORS = [
    Dis(
        "d_unit",
        "near_threshold",
        our_a,
        lambda c: {"subject"},
        lambda c: dict(slot="subject", note="单价比台账均价略高，但低于规范的说明线"),
        factor=lambda c: UNIT_RANGE[c["tenant"]]["ok"],
    ),
    Dis(
        "d_deposit",
        "near_threshold",
        our_a,
        lambda c: {"deposit"},
        dis(
            "deposit",
            lambda c: deposit_text("百分之二十"),
            "定金恰好百分之二十（法定上限内）",
            need_deposit=True,
        ),
    ),
    Dis(
        "d_inspection",
        "near_threshold",
        our_a,
        lambda c: {"inspection"},
        dis(
            "inspection",
            lambda c: insp_text(c, inspection_days(c, False)[0]),
            lambda c: (
                f"检验期{inspection_days(c, False)[0]}，恰好满足{inspection_days(c, False)[1]}"
            ),
        ),
    ),
    Dis(
        "d_conf",
        "near_threshold",
        lambda c: c["ctype"] == "work" or c["tenant"] == "yunqi",
        lambda c: {"confidentiality"},
        dis(
            "confidentiality",
            lambda c: conf_text("五年" if c["tenant"] == "yunqi" else "三年"),
            "保密期恰好是规范下限",
        ),
    ),
    Dis(
        "d_pay",
        "near_threshold",
        lambda c: our_b(c) and c["tenant"] in ("huaxin", "yunqi"),
        lambda c: {"payment"},
        dis("payment", lambda c: pay_seller_text(c, False), "赊销期/首付款比例恰好是规范红线"),
    ),
    Dis(
        "d_penalty",
        "near_threshold",
        always,
        lambda c: {"penalty"},
        dis(
            "penalty",
            lambda c: penalty_text("万分之五", "百分之二十"),
            "违约金日万分之五、上限百分之二十，恰好是规范红线",
        ),
    ),
    Dis(
        "d_termination",
        "near_threshold",
        lambda c: our_b(c) and c["tenant"] == "yunqi",
        lambda c: {"termination"},
        dis(
            "termination",
            lambda c: term_text(c, "三十日"),
            "对方解除需提前三十日并结算工作量，恰好满足云启细则第十三条",
        ),
    ),
    Dis(
        "d_renew",
        "near_threshold",
        lambda c: our_a(c) and c["tenant"] == "yunqi",
        lambda c: {"auto_renew"},
        dis(
            "auto_renew",
            lambda c: renew_text("百分之五"),
            "续期涨幅上限百分之五、可提前三十日通知不续期，恰好满足云启细则第十四条",
        ),
    ),
    Dis(
        "d_force_majeure",
        "legit_exemption",
        always,
        lambda c: {"force_majeure"},
        dis(
            "force_majeure",
            lambda c: (
                "因不可抗力不能履行合同的，受不可抗力影响的一方在影响范围内不承担违约责任，也不承担赔偿责任；但迟延履行后发生不可抗力的，不免除其责任。受影响一方应当在不可抗力发生后五日内通知对方，并在十五日内提供证明。"
            ),
            "合法的不可抗力免责（民法典第五百九十条）",
            kind="legit_exemption",
        ),
    ),
    Dis(
        "d_exempt",
        "legit_exemption",
        always,
        lambda c: {"warranty"},
        lambda c: dict(
            append={"warranty": LEGIT_EXEMPT[c["family"]]},
            slot="warranty",
            note="质保除外情形合理（甲方原因造成的问题）",
        ),
    ),
    Dis(
        "d_jur_sign",
        "combo_ok",
        always,
        lambda c: {"jurisdiction"},
        lambda c: dict(
            over={
                "jurisdiction": "因本合同发生的争议，双方应当协商解决；协商不成的，由合同签订地人民法院管辖。"
            },
            slot="jurisdiction",
            note="由签订地法院管辖，首部签订地点在我方所在地，合规",
        ),
    ),
] + [fav_dis(r, v) for r, v in FAV]


# ---------------- 组装 ----------------


@dataclass
class Skeleton:
    cid: str
    ctype: str
    ctx: dict
    risks: list = field(default_factory=list)
    dis: list = field(default_factory=list)
    claimed: set = field(default_factory=set)


def make_skeleton(cid, ctype, our_side, rng, stats) -> Skeleton:
    t = TYPES[ctype]
    tenant = t["tenant"]
    item = rng.choice(t["items"])
    st = stats[(tenant, item)]
    our = OURS[tenant]
    if our_side == "甲":
        sname = st["supplier"]
        city = SUPPLIER_CITY[sname[:2]]
        cp = (sname, city[0], city[1])
    else:
        cp = rng.choice(CUSTOMERS[tenant])
    party = {"甲": our if our_side == "甲" else cp, "乙": cp if our_side == "甲" else our}
    ctx = dict(
        ctype=ctype,
        tenant=tenant,
        family=FAMILY[ctype],
        thing=t["thing"],
        item=item,
        spec=st["spec"],
        unit=st["unit"],
        our_side=our_side,
        our_side_or_A=our_side,
        v=our_side,
        o=other(our_side),
        city={"甲": party["甲"][2], "乙": party["乙"][2]},
        party=party,
        cp=cp,
        avg=st["avg"],
        avg12=st["avg12"],
    )
    return Skeleton(cid, ctype, ctx)


def fits(sk: Skeleton, tpl) -> bool:
    if not tpl.ok(sk.ctx):
        return False
    cl = tpl.claims(sk.ctx)
    if not cl <= possible_slots(sk.ctype):
        return False
    shared = {"definitions"}  # 定义条款可以同时承载几处组合风险的定义
    if (cl - shared) & sk.claimed:
        return False
    if isinstance(tpl, Tpl) and any(r.rtype == tpl.rtype for r in sk.risks):
        return False
    return True


def ledger_with_12m():
    import csv

    stats, status = ledger()
    rows = list(
        csv.DictReader(
            (ROOT / "data" / "ledger_src" / "purchase_history.csv").open(encoding="utf-8")
        )
    )
    for k, s in stats.items():
        recent = [
            float(r["unit_price_yuan"])
            for r in rows
            if (r["tenant"], r["item"]) == k and r["order_date"] >= LEDGER_12M_FROM
        ]
        s["avg12"] = sum(recent) / len(recent) if recent else s["avg"]
    return stats, status


def render(sk: Skeleton, rng: random.Random) -> dict:
    ctx = sk.ctx
    tenant = ctx["tenant"]
    effs = [(t, t.make(ctx)) for t in sk.risks] + [(d, d.make(ctx)) for d in sk.dis]
    # 单价
    src = next((t for t, _ in effs if t.factor), None)
    st_avg = ctx["avg"]
    factor = rng.uniform(*src.factor(ctx)) if src else rng.uniform(0.97, 1.03)
    # 规范写"近十二个月均价"，审查方也可能用全部历史均价：风险按两者较高的算、合规干扰项按较低的算
    if src is None:
        base = st_avg
    elif isinstance(src, Tpl):
        base = max(st_avg, ctx["avg12"])
    else:
        base = min(st_avg, ctx["avg12"])
    price = float(fmt_price(base * factor))
    qty = rng.choice([20, 50, 100, 200]) if st_avg > 100 else rng.choice([20000, 50000, 100000])
    ctx.update(
        price=fmt_price(price),
        total=fmt_price(price * qty) if price * qty >= 100 else f"{price * qty:.2f}",
        qty=qty,
        price_f=price,
    )
    # 重新 make：make 里可能用到 price（目前没有，但保持顺序正确）
    effs = [(t, t.make(ctx)) for t in sk.risks] + [(d, d.make(ctx)) for d in sk.dis]
    over, append, defs, header = {}, {}, dict(BASE_DEFS), {}
    need_dep = any(e.get("need_deposit") for _, e in effs)
    no_dep = any(e.get("no_deposit") for _, e in effs)
    for _, e in effs:
        over.update(e.get("over", {}))
        for k, s in e.get("append", {}).items():
            append.setdefault(k, []).append(s)
        defs.update(e.get("defs", {}))
        header.update(e.get("header", {}))
    has_deposit = need_dep or (not no_dep and "deposit" not in sk.claimed and rng.random() < 0.4)
    slots = [s for s in ORDER if s in possible_slots(sk.ctype) and (s != "deposit" or has_deposit)]
    # 明确缺失的条款（v1 的保密 R2）
    missing = [s for s, b in over.items() if b is None]
    slots = [s for s in slots if s not in missing]
    target = rng.randint(25, 30)
    keep = sk.claimed | {k for _, e in effs for k in e.get("append", {})} | set(over)
    droppable = [s for s in slots if s in OPTIONAL and s not in keep]
    rng.shuffle(droppable)
    while len(slots) > target and droppable:
        slots.remove(droppable.pop())
    no = {s: f"第{cn_num(i + 1)}条" for i, s in enumerate(slots)}

    def fill(text: str) -> str:
        def rep(m):
            s = m.group(1)
            return no.get(s, "首部") if s != "header" else "首部"

        return re.sub(r"<([a-z_]+)>", rep, text)

    # 定义条款
    thing_term = ctx["thing"]
    defs = {
        thing_term: "指乙方按照甲方出库通知交付的仓储粮食"
        if ctx["family"] == "entrust"
        else f"指乙方提供<subject>约定的{ctx['item']}所形成并向甲方交付的成果"
        if ctx["ctype"] in ("dproc", "ddev")
        else f"指乙方按照<subject>约定向甲方交付的{ctx['item']}"
    } | defs
    body_defs = (
        "本合同中下列用语的含义是："
        + "；".join(f"（{cn_num(i + 1)}）“{k}”，{v}" for i, (k, v) in enumerate(defs.items()))
        + "。"
    )
    clauses = []
    for s in slots:
        if s == "definitions":
            body = body_defs
        elif s in over:
            body = over[s]
        else:
            body = std_text(s, ctx)
        if s in append:
            body = body + "".join(append[s])
        clauses.append(f"{no[s]} {heading(s, ctx['family'])}\n{fill(body)}")
    # 金标准
    gold, acceptable, distractors, favorable = [], [], [], []
    for t, e in effs:
        if isinstance(t, Tpl):
            types = [t.rtype, *t.alt]
            bs = []
            for ty in types:
                for b in basis(tenant, ty):
                    if b not in bs:
                        bs.append(b)
            gslots = e["gold_slots"]
            if any(s in missing for s in gslots):
                clause, clauses_ = None, []
            else:
                clause, clauses_ = no[gslots[0]], [no[s] for s in gslots]
            why = e["why"]
            g = dict(
                clause=clause,
                clauses=clauses_,
                type=t.rtype,
                alt_types=list(t.alt),
                category=t.cat,
                variant=t.name,
                level=t.level,
                victim=ctx["v"],
                basis=bs,
            )
            if t.rtype == "unit_price":
                ratio = price / st_avg
                g |= dict(
                    ledger_avg=round(st_avg, 2),
                    ledger_avg_12m=round(ctx["avg12"], 2),
                    price=price,
                    ratio=round(ratio, 3),
                )
                if t.cat == "threshold":
                    u = UNIT_RANGE[tenant]
                    a12 = ctx["avg12"]
                    why = f"单价{fmt_price(price)}元，比台账近十二个月均价{a12:.2f}元高{(price / a12 - 1) * 100:.1f}%，超过规范{u['art']}的{u['thr']}复核线。"
            g["why"] = fill(why)
            gold.append(g)
            if t.cat == "hidden":
                # 在标准条款上报出"与隐蔽条款冲突"也是对的：不算误报，但也不算检出
                std_slot = t.rtype if t.rtype in no else None
                if std_slot:
                    acceptable.append(
                        {
                            "clauses": [no[std_slot]],
                            "types": [t.rtype, *t.alt],
                            "reason": "与隐蔽位置风险冲突的标准条款",
                        }
                    )
        else:
            d = dict(clause=no.get(e["slot"]), kind=t.kind, name=t.name, note=fill(e["note"]))
            if t.name == "d_unit":
                d["ratio"] = round(price / st_avg, 3)
            distractors.append(d)
            if e.get("favorable"):
                favorable.append(dict(clause=no[e["slot"]]) | e["favorable"])
                why_ok = FAV_ACCEPT.get((e["favorable"]["type"], tenant)) or FAV_ACCEPT.get(
                    (e["favorable"]["type"], "*")
                )
                if why_ok:
                    acceptable.append(
                        {
                            "clauses": [no[e["slot"]]],
                            "types": [e["favorable"]["type"]],
                            "reason": why_ok,
                        }
                    )
    # v1 的"合理意见"：我方为甲方时安全写法的责任上限例外对我方同样适用；台账里对方资质过期/黑名单
    if ctx["v"] == "甲" and "liability" not in over and "liability" in no:
        acceptable.append(
            {
                "clauses": [no["liability"]],
                "types": ["liability", "other"],
                "reason": "责任上限的例外对我方同样适用",
            }
        )
    # 问题日志 P25：数据集自身文本引出的合理意见（改进后的多 Agent 读了定义条款后报出），不算误报
    if TYPES[sk.ctype]["ip"] and "ip" in no and "definitions" in no:
        acceptable.append(
            {
                "clauses": [no["definitions"], no["ip"]],
                "types": ["ip"],
                "reason": "定义条款把成果限定为交付物，图纸、中间成果等归属不明（生成器的基础定义带来的）",
            }
        )
    if ctx["item"] in EQUIPMENT and "inspection" in no:
        acceptable.append(
            {
                "clauses": [no["inspection"], no["payment"]],
                "types": ["inspection", "payment"],
                "reason": "检验期从安装调试合格起算，但合同没约定安装调试期限，验收和付款可能被拖延",
            }
        )
    flag = supplier_risk(tenant, ctx["cp"][0]) if ctx["v"] == "甲" else None
    if flag:
        acceptable.append({"clauses": [None, no["subject"]], "types": ["other"], "reason": flag})
    t = TYPES[sk.ctype]
    our = OURS[tenant]
    party = ctx["party"]
    sign_place = header.get("sign", our[2])
    head = "\n".join(
        [
            t["title"],
            f"合同编号：{sk.cid.upper()}",
            f"甲方（{t['A']}）：{party['甲'][0]}",
            f"住所：{party['甲'][1]}",
            f"乙方（{t['B']}）：{party['乙'][0]}",
            f"住所：{party['乙'][1]}",
            f"签订地点：{sign_place}",
            "根据《中华人民共和国民法典》等有关法律法规，甲乙双方在平等、自愿、公平和诚信的基础上，经协商一致，订立本合同。",
        ]
    )
    return dict(
        id=sk.cid,
        tenant=tenant,
        contract_type=sk.ctype,
        template_id=t["tpl"],
        title=t["title"],
        our_side=ctx["v"],
        our_name=our[0],
        counterparty=party[ctx["o"]][0],
        clarify=False,
        text=head + "\n" + "\n".join(clauses) + "\n",
        gold=gold,
        borderline=[],
        favorable=favorable,
        acceptable=acceptable,
        distractors=distractors,
        constructed=True,
    )


RISKY_PER_TYPE = {
    "goods": 5,
    "agri": 4,
    "work": 4,
    "entrust": 4,
    "dproc": 5,
    "ddev": 4,
    "dsupply": 4,
}
CLEAN_PER_TYPE = {
    "goods": 5,
    "agri": 4,
    "work": 4,
    "entrust": 4,
    "dproc": 5,
    "ddev": 4,
    "dsupply": 4,
}
CAT_QUOTA = {"hidden": 18, "paraphrase": 18, "combo": 12, "threshold": 15, "standard": 12}
TPL_CAP = {"hidden": 2, "paraphrase": 2, "standard": 1}


def skeletons(kind: str, per_type: dict, rng, stats, offset: int) -> list[Skeleton]:
    out = []
    k = offset
    for ctype, n in per_type.items():
        for i in range(1, n + 1):
            out.append(
                make_skeleton(
                    f"v2-{ctype}-{kind}{i}", ctype, "甲" if k % 2 == 0 else "乙", rng, stats
                )
            )
            k += 1
    return out


def assign_risks(sks: list[Skeleton], rng: random.Random) -> None:
    """75 处风险分到 30 份合同，每份 2–3 处；先排约束最多的组合和临界值，失败就换种子重来。"""
    for _ in range(2000):
        r = random.Random(rng.random())
        for sk in sks:
            sk.risks, sk.claimed = [], set()
        used = Counter()
        jobs = [("combo", t) for t in COMBO] + [("threshold", t) for t in THRESH]
        jobs += [("hidden", None)] * CAT_QUOTA["hidden"] + [("paraphrase", None)] * CAT_QUOTA[
            "paraphrase"
        ]
        jobs += [("standard", None)] * CAT_QUOTA["standard"]
        pools = {"hidden": HIDDEN, "paraphrase": PARA, "standard": STANDARD}
        ok = True
        for cat, fixed in jobs:
            cands = []
            for sk in sks:
                if len(sk.risks) >= 3:
                    continue
                tpls = [fixed] if fixed else [t for t in pools[cat] if used[t.name] < TPL_CAP[cat]]
                for t in tpls:
                    if fits(sk, t):
                        cands.append((len(sk.risks), used[t.name], r.random(), sk, t))
            if not cands:
                ok = False
                break
            cands.sort(key=lambda x: x[:3])
            _, _, _, sk, t = cands[0]
            sk.risks.append(t)
            sk.claimed |= t.claims(sk.ctx)
            used[t.name] += 1
        if ok and all(2 <= len(sk.risks) <= 3 for sk in sks):
            return
    raise RuntimeError("风险分配失败")


DIS_TARGET = {"near_threshold": 26, "favorable": 20, "legit_exemption": 16, "combo_ok": 8}


def assign_distractors(sks: list[Skeleton], rng: random.Random) -> None:
    used = Counter()
    for sk in sks:
        n = rng.choice([2, 3])
        for _ in range(n):
            cands = [
                d
                for d in DISTRACTORS
                if d not in sk.dis
                and fits(sk, d)
                and not (d.factor and any(x.factor for x in sk.dis))
            ]
            if not cands:
                break
            # 按目标比例挑类别，类别内挑用得最少的模板
            kind = min(
                sorted({d.kind for d in cands}),
                key=lambda k: (used[k] / DIS_TARGET[k], rng.random()),
            )
            pool = [d for d in cands if d.kind == kind]
            low = min(used[d.name] for d in pool)
            pick = rng.choice([d for d in pool if used[d.name] == low])
            sk.dis.append(pick)
            sk.claimed |= pick.claims(sk.ctx)
            used[pick.kind] += 1
            used[pick.name] += 1


def main() -> None:
    rng = random.Random(SEED)
    stats, _ = ledger_with_12m()
    risky = skeletons("r", RISKY_PER_TYPE, rng, stats, 0)
    clean = skeletons("c", CLEAN_PER_TYPE, rng, stats, 1)
    assign_risks(risky, rng)
    assign_distractors(clean, rng)
    contracts = [render(sk, rng) for sk in risky + clean]
    check(contracts, stats)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for c in contracts:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    write_spotcheck(contracts, random.Random(SEED + 1))
    report(contracts)


def clause_body(text: str, no: str) -> str:
    m = re.search(rf"^{no} [^\n]*\n([^\n]*)", text, re.M)
    return m.group(1) if m else ""


def check(contracts: list[dict], stats) -> None:
    assert len(contracts) == 60
    risky = [c for c in contracts if c["gold"]]
    assert len(risky) == 30 and all(2 <= len(c["gold"]) <= 3 for c in risky)
    cats = Counter(g["category"] for c in risky for g in c["gold"])
    assert cats == Counter(CAT_QUOTA), cats
    for c in contracts:
        n = len(re.findall(r"^第[零一二三四五六七八九十]+条 ", c["text"], re.M))
        assert 25 <= n <= 30, (c["id"], n)
        assert "<" not in c["text"] and ">" not in c["text"], c["id"]
        for g in c["gold"]:
            for no in g["clauses"]:
                assert clause_body(c["text"], no), (c["id"], no)
            assert "<" not in g["why"], (c["id"], g["why"])
            if g["type"] == "unit_price":
                avg12 = g["ledger_avg_12m"]
                thr_ = {"huaxin": 1.20, "yunqi": 1.15, "ruifeng": 1.15}[c["tenant"]]
                assert g["price"] / g["ledger_avg"] > thr_ + 0.01, (c["id"], g)
                assert g["price"] / avg12 > thr_ + 0.01, (c["id"], g)
        for d in c["distractors"]:
            assert d["clause"] and clause_body(c["text"], d["clause"]), (c["id"], d)
            if d["name"] == "d_unit":
                line = {"huaxin": 1.10, "yunqi": 1.15, "ruifeng": 1.08}[c["tenant"]]
                assert d["ratio"] < line - 0.01, (c["id"], d)
    clean = [c for c in contracts if not c["gold"]]
    assert len(clean) == 30 and all(c["distractors"] for c in clean)
    sides = Counter(c["our_side"] for c in contracts)
    assert abs(sides["甲"] - sides["乙"]) <= 6, sides


def write_spotcheck(contracts: list[dict], rng: random.Random) -> None:
    """给用户抽查的清单：每类 3 处风险 + 5 个干扰项，附条款原文和金标准理由。"""
    lines = [
        "# v2 评测集抽查清单（构造数据）",
        "",
        "由 `scripts/make_contracts_v2.py` 生成。每类风险抽 3 处、干扰项抽 5 个，共 20 条。",
        "请看：①条款原文是否真的有（或没有）这个风险；②理由是否说得通；③有没有别的明显风险没标。",
        "",
    ]
    k = 0
    for cat in CAT_QUOTA:
        items = [(c, g) for c in contracts for g in c["gold"] if g["category"] == cat]
        for c, g in rng.sample(items, 3):
            k += 1
            lines += [
                f"## {k}. [{cat}] {c['id']}（{c['title']}，我方{c['our_side']}方，{c['tenant']}）",
                f"- 风险类型：{g['type']}（{RISK_TYPES[g['type']]}），等级 {g['level']}，模板 `{g['variant']}`",
                f"- 理由：{g['why']}",
            ]
            for no in g["clauses"]:
                lines.append(f"- {no}：{clause_body(c['text'], no)}")
            if g["variant"] == "c_jur_sign":
                sign = re.search(r"签订地点：(.*)", c["text"]).group(1)
                lines.append(f"- 首部签订地点：{sign}")
            lines.append("")
    items = [(c, d) for c in contracts for d in c["distractors"]]
    kinds = sorted({d["kind"] for _, d in items})
    picks = []
    for kd in kinds:
        picks.append(rng.choice([x for x in items if x[1]["kind"] == kd]))
    rest = [x for x in items if x not in picks]
    picks += rng.sample(rest, 5 - len(picks))
    for c, d in picks:
        k += 1
        lines += [
            f"## {k}. [干扰项 {d['kind']}] {c['id']}（{c['title']}，我方{c['our_side']}方，{c['tenant']}）",
            f"- 说明：{d['note']}（不应报出高/中风险）",
            f"- {d['clause']}：{clause_body(c['text'], d['clause'])}",
            "",
        ]
    SPOTCHECK.write_text("\n".join(lines), encoding="utf-8")


def report(contracts: list[dict]) -> None:
    print(
        f"{len(contracts)} 份 → {OUT.relative_to(ROOT)}；抽查清单 → {SPOTCHECK.relative_to(ROOT)}"
    )
    golds = [g for c in contracts for g in c["gold"]]
    print("风险", len(golds), dict(Counter(g["category"] for g in golds)))
    print("类型", dict(Counter(g["type"] for g in golds)))
    print("干扰项", dict(Counter(d["kind"] for c in contracts for d in c["distractors"])))
    print(
        "条款数",
        Counter(
            len(re.findall(r"^第[零一二三四五六七八九十]+条 ", c["text"], re.M)) for c in contracts
        ),
    )
    print("我方", dict(Counter(c["our_side"] for c in contracts)))


if __name__ == "__main__":
    main()
