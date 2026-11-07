"""生成构造的评测合同（evals/contracts.jsonl），入 git。

- 7 类合同，章节顺序参照 7 份官方示范文本（data/sources.toml 里的 tpl-*），条款正文全部自写，
  不复制示范文本原文（示范文本无开放许可，见 ADR 007）。
- 48 份：36 份植入 1–3 处风险（其中 4 份当事人名称空白，测反问），12 份干净（其中 6 份含
  "对我方有利的单方条款"，测视角）；另有 6 处临界条款，测转人工。
- 每处植入都记下金标准：条款号、风险类型、等级、合法依据（法条/租户规范条号集合）、受损方。
- 植入句式每类 2–3 种写法，评测时 agent 看不到这些规则。

全部是构造数据：公司、价格、条款均为虚构；单价按 data/ledger_src/purchase_history.csv 的历史均价计算。
"""

from __future__ import annotations

import csv
import json
import random
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "contracts.jsonl"
SEED = 20261024

OURS = {
    "huaxin": ("华信制造有限公司", "江苏省苏州市", "苏州"),
    "yunqi": ("云启科技有限公司", "浙江省杭州市", "杭州"),
    "ruifeng": ("瑞丰贸易有限公司", "山东省青岛市", "青岛"),
}
# 我方作为提供方时的客户（虚构）
CUSTOMERS = {
    "huaxin": [
        ("宁波远帆机械有限公司", "浙江省宁波市", "宁波"),
        ("合肥创铭汽车零部件有限公司", "安徽省合肥市", "合肥"),
    ],
    "yunqi": [
        ("南京易融金融服务有限公司", "江苏省南京市", "南京"),
        ("武汉智行出行科技有限公司", "湖北省武汉市", "武汉"),
    ],
    "ruifeng": [
        ("广州粤香食品有限公司", "广东省广州市", "广州"),
        ("天津津味餐饮管理有限公司", "天津市", "天津"),
    ],
}
SUPPLIER_CITY = {
    "苏州": ("江苏省苏州市", "苏州"),
    "无锡": ("江苏省无锡市", "无锡"),
    "常州": ("江苏省常州市", "常州"),
    "昆山": ("江苏省昆山市", "昆山"),
    "南通": ("江苏省南通市", "南通"),
    "上海": ("上海市", "上海"),
    "杭州": ("浙江省杭州市", "杭州"),
    "成都": ("四川省成都市", "成都"),
    "北京": ("北京市", "北京"),
    "合肥": ("安徽省合肥市", "合肥"),
    "黑龙": ("黑龙江省哈尔滨市", "哈尔滨"),
    "吉林": ("吉林省长春市", "长春"),
    "山东": ("山东省济南市", "济南"),
    "安徽": ("安徽省合肥市", "合肥"),
}

# 合同类型：示范文本 id、标题、甲乙方称谓、所属租户、标的品目（台账里的品名）、交付物称呼、有无知识产权条款
TYPES = {
    "goods": dict(
        tpl="tpl-industrial-goods-2023",
        title="工业品买卖合同",
        A="买受人",
        B="出卖人",
        tenant="huaxin",
        items=["冷轧钢板", "深沟球轴承", "三相异步电动机", "液压油"],
        thing="货物",
        ip=False,
        data=False,
    ),
    "agri": dict(
        tpl="tpl-agri-products-2025",
        title="农产品买卖合同",
        A="买受人",
        B="出卖人",
        tenant="ruifeng",
        items=["粳米", "玉米", "大豆"],
        thing="货物",
        ip=False,
        data=False,
    ),
    "work": dict(
        tpl="tpl-work-2024",
        title="加工承揽合同",
        A="定作人",
        B="承揽人",
        tenant="huaxin",
        items=["机加工外协件"],
        thing="定作物",
        ip=True,
        data=True,
    ),
    "entrust": dict(
        tpl="tpl-entrustment-2025",
        title="粮食仓储委托合同",
        A="委托人",
        B="受托人",
        tenant="ruifeng",
        items=["粮食仓储服务"],
        thing="出库粮食",
        ip=False,
        data=True,
    ),
    "dproc": dict(
        tpl="tpl-data-processing-2025",
        title="数据委托处理合同",
        A="委托方",
        B="受托方",
        tenant="yunqi",
        items=["图像标注服务", "文本标注服务"],
        thing="交付成果",
        ip=True,
        data=True,
    ),
    "ddev": dict(
        tpl="tpl-data-dev-2025",
        title="数据产品开发合同",
        A="委托方",
        B="开发方",
        tenant="yunqi",
        items=["定制软件开发"],
        thing="交付成果",
        ip=True,
        data=True,
    ),
    "dsupply": dict(
        tpl="tpl-data-supply-2025",
        title="数据供应合同",
        A="需方",
        B="供方",
        tenant="yunqi",
        items=["行业语料数据集"],
        thing="数据产品",
        ip=True,
        data=True,
    ),
}

RISK_TYPES = {
    "penalty": "违约金过高或只约束一方",
    "liability": "免责条款或责任无上限",
    "termination": "单方解除权",
    "inspection": "检验期过短或视为合格",
    "payment": "付款周期过长或背靠背付款",
    "confidentiality": "保密条款缺失或不对等",
    "ip": "知识产权归属不利",
    "jurisdiction": "管辖不利或或裁或诉",
    "auto_renew": "自动续约",
    "deposit": "定金超过百分之二十",
    "unit_price": "单价明显高于台账历史价",
}

CC = "law-civil-code"
JS = "law-contract-interp-2023"
MS = "law-civil-procedure"
# 每类风险的合法依据：法条 + 各租户规范条号；引用其中任意一条即算依据正确
LAW_BASIS = {
    "penalty": [(CC, "第五百八十五条"), (JS, "第六十四条"), (JS, "第六十五条")],
    "liability": [(CC, "第五百零六条"), (CC, "第四百九十七条"), (CC, "第五百八十四条")],
    "termination": [
        (CC, "第五百六十二条"),
        (CC, "第五百六十三条"),
        (CC, "第五百六十五条"),
        (CC, "第五百六十六条"),
    ],
    "inspection": [
        (CC, "第六百二十条"),
        (CC, "第六百二十一条"),
        (CC, "第六百二十二条"),
        (CC, "第六百二十三条"),
        (CC, "第七百八十条"),
    ],
    "payment": [
        (CC, "第五百一十条"),
        (CC, "第五百一十一条"),
        (CC, "第六百二十六条"),
        (CC, "第六百二十八条"),
    ],
    "confidentiality": [(CC, "第五百零一条"), (CC, "第五百零九条"), (CC, "第五百五十八条")],
    "ip": [(CC, "第八百五十九条"), (CC, "第八百六十条"), (CC, "第八百六十一条")],
    "jurisdiction": [(MS, "第三十五条"), (MS, "第二十四条")],
    "auto_renew": [(CC, "第四百九十六条"), (CC, "第四百九十七条"), (CC, "第四百九十八条")],
    "deposit": [(CC, "第五百八十六条"), (CC, "第五百八十七条"), (CC, "第五百八十八条")],
    "unit_price": [],
}
POLICY_BASIS = {
    "huaxin": {
        "penalty": ["第十一条", "第十二条"],
        "liability": ["第十三条", "第十四条"],
        "termination": ["第十七条"],
        "inspection": ["第十五条", "第十六条"],
        "payment": ["第七条", "第八条", "第九条"],
        "confidentiality": ["第十九条"],
        "ip": ["第二十条"],
        "jurisdiction": ["第二十一条"],
        "auto_renew": ["第十八条"],
        "deposit": ["第十条"],
        "unit_price": ["第四条"],
    },
    "yunqi": {
        "penalty": ["第十一条"],
        "liability": ["第十一条", "第十二条"],
        "termination": ["第十三条"],
        "inspection": ["第十五条"],
        "payment": ["第九条"],
        "confidentiality": ["第三条", "第四条"],
        "ip": ["第六条", "第七条"],
        "jurisdiction": ["第十六条"],
        "auto_renew": ["第十四条"],
        "deposit": ["第十条"],
        "unit_price": ["第八条"],
    },
    "ruifeng": {
        "penalty": ["第十一条", "第十二条"],
        "liability": ["第十三条"],
        "termination": ["第十四条"],
        "inspection": ["第九条", "第十条"],
        "payment": ["第六条", "第七条"],
        "confidentiality": ["第十五条"],
        "ip": [],
        "jurisdiction": ["第十六条"],
        "auto_renew": ["第十四条"],
        "deposit": ["第八条"],
        "unit_price": ["第五条"],
    },
}

CN = "零一二三四五六七八九"


def cn_num(n: int) -> str:
    if n < 10:
        return CN[n]
    if n < 20:
        return "十" + (CN[n % 10] if n % 10 else "")
    return CN[n // 10] + "十" + (CN[n % 10] if n % 10 else "")


def basis(tenant: str, rtype: str) -> list[list[str]]:
    policy = [[f"{tenant}-contract-policy", a] for a in POLICY_BASIS[tenant][rtype]]
    return [list(x) for x in LAW_BASIS[rtype]] + policy


def ledger():
    rows = list(
        csv.DictReader(
            (ROOT / "data" / "ledger_src" / "purchase_history.csv").open(encoding="utf-8")
        )
    )
    sup = list(
        csv.DictReader((ROOT / "data" / "ledger_src" / "suppliers.csv").open(encoding="utf-8"))
    )
    stats = {}
    for r in rows:
        k = (r["tenant"], r["item"])
        s = stats.setdefault(
            k, {"prices": [], "supplier": r["supplier_name"], "spec": r["spec"], "unit": r["unit"]}
        )
        s["prices"].append(float(r["unit_price_yuan"]))
    for s in stats.values():
        s["avg"] = sum(s["prices"]) / len(s["prices"])
    status = {(s["tenant"], s["supplier_name"]): s for s in sup}
    return stats, status


# ---------------- 条款文本 ----------------
# 约定：甲方是付款方/接收方（买受人、定作人、委托人、需方），乙方是提供方。
# v = 受损方（"甲"/"乙"），o = 对方。


def other(side: str) -> str:
    return "乙" if side == "甲" else "甲"


def t_penalty(variant, v, ctx):
    o = other(v)
    act = "逾期付款" if v == "甲" else "逾期交付"
    return {
        "safe": "任何一方违约的，应当赔偿因此给对方造成的直接损失。逾期付款或者逾期履行的，每逾期一日，按逾期部分金额的万分之五支付违约金，违约金总额不超过合同总额的百分之十。",
        "R1": f"{v}方{act}的，每逾期一日，应当按合同总额的百分之一向{o}方支付违约金，违约金总额不设上限。{o}方逾期的，双方另行协商。",
        "R2": f"{v}方违反本合同任何约定的，应当向{o}方支付合同总额百分之五十的违约金；违约金不足以弥补{o}方损失的，{v}方还应当赔偿全部损失。",
        "R3": f"{v}方违约的，{o}方有权扣留全部应付或者已收款项作为违约金；{o}方违约的，{v}方仅可以要求{o}方继续履行。",
        "B": "任何一方违约的，应当按照守约方因此遭受的实际损失的百分之一百三十向守约方支付违约金。",
    }[variant]


def liability_safe(ctx) -> str:
    """安全写法按我方视角：我方是接收方（甲）时，对方保管/处理我方财产和数据的损失不设上限；
    我方是提供方（乙）时，只保留法定的故意、重大过失例外（P20）。"""
    if ctx["our_side"] == "甲":
        extra = {
            "entrust": "仓储物毁损、灭失",
            "dproc": "数据泄露",
            "ddev": "数据泄露",
            "dsupply": "数据泄露",
        }
        items = "、".join(x for x in (extra.get(ctx["ctype"]), "侵犯知识产权", "违反保密义务") if x)
        return f"除因故意或者重大过失造成的损失，以及因{items}造成的损失外，任何一方在本合同项下的赔偿责任总额不超过合同总额，且不包括间接损失和可得利益损失。"
    return "除因故意或者重大过失造成的损失外，任何一方在本合同项下的赔偿责任总额不超过合同总额，且不包括间接损失和可得利益损失。"


def t_liability(variant, v, ctx):
    o = other(v)
    return {
        "safe": liability_safe(ctx),
        "R1": f"{o}方对因履行本合同给{v}方造成的任何损失均不承担赔偿责任。",
        "R2": f"{v}方对{o}方因本合同产生的一切直接损失和间接损失承担全部赔偿责任，赔偿范围不受合同金额限制。",
        "R3": f"{o}方因故意或者重大过失造成{v}方财产损失的，赔偿总额以人民币一千元为限。",
        "B": "因原材料或者人工价格上涨超过百分之十，导致乙方无法按原价履行的，视为不可抗力，乙方不承担违约责任。",
    }[variant]


def t_termination(variant, v, ctx):
    o = other(v)
    return {
        "safe": "有下列情形之一的，守约方可以书面通知对方解除本合同：（一）因不可抗力致使不能实现合同目的；（二）一方迟延履行主要债务，经催告后在三十日内仍未履行；（三）法律规定的其他情形。",
        "R1": f"{o}方可以随时以书面通知的方式解除本合同，无需说明理由，且不承担任何责任。",
        "R2": f"合同履行期间，{o}方认为继续履行不符合其经营需要的，有权提前三日通知{v}方解除本合同，{v}方已经发生的费用由{v}方自行承担。",
        "R3": f"{o}方有权单方解除本合同；{v}方仅在{o}方迟延履行超过一百八十日的情况下方可解除本合同。",
        "B": "任何一方均可以提前九十日书面通知对方解除本合同，解除前已经履行的部分按实际履行情况结算。",
    }[variant]


def t_inspection(variant, v, ctx):
    thing = ctx["thing"]
    return {
        "safe": f"甲方应当在收到{thing}之日起三十日内进行检验；发现数量或者质量不符合约定的，应当在该期限内书面通知乙方，乙方应当在收到通知后十日内更换、补足或者修复。甲方在该期限内未书面提出异议的，视为{thing}的数量和质量符合约定。",
        "R1": f"甲方应当在{thing}到达之日起二日内完成检验，逾期未提出书面异议的，视为{thing}的数量和质量均符合约定。",
        "R2": f"乙方交付{thing}即视为甲方验收合格，甲方此后不得再就质量问题提出异议。",
        "R3": "甲方应当在乙方交付后三日内提出书面异议，否则视为验收合格；甲方提出异议的，检测费用由甲方承担。",
    }[variant]


def t_payment(variant, v, ctx):
    return {
        # 安全写法按我方视角取常规条件：我方付款时验收后付清，我方收款时有首付款
        "safe": (
            "甲方在验收合格并收到乙方开具的合规发票后三十日内支付全部价款。"
            if ctx["our_side"] == "甲"
            else "合同签订后五日内，甲方向乙方支付合同总额的百分之三十作为首付款；余款在验收合格（含依约视为验收合格）并收到乙方开具的合规发票后三十日内付清。"
        ),
        "R1": "甲方在收到乙方开具的发票后一百八十日内支付全部价款。",
        "R2": "甲方在收到其下游客户支付的相应款项后十日内向乙方付款；下游客户未付款的，甲方不承担付款义务。",
        "R3": "合同价款在项目整体验收合格且甲方年度审计完成后支付，具体付款时间由甲方根据资金安排确定。",
    }[variant]


def t_deposit(variant, v, ctx):
    return {
        "safe": "甲方应当在合同签订后五日内向乙方支付合同总额百分之十的定金，定金在最后一期付款时抵作价款。一方违约时，对方可以选择适用定金条款或者违约金条款，但不得同时适用。",
        "R1": "甲方应当在合同签订后三日内向乙方支付合同总额百分之三十的定金。",
        "R2": "甲方应当在合同签订后三日内向乙方支付合同总额百分之四十的定金；甲方违约的，定金不予返还，甲方还应当另行支付违约金。",
        "B": "甲方应当在合同签订后五日内向乙方支付合同总额百分之三十的预付款；乙方违约的，应当双倍返还预付款；甲方违约的，预付款不予退还。",
    }[variant]


def t_confidentiality(variant, v, ctx):
    o = other(v)
    return {
        "safe": "双方对在履行本合同过程中知悉的对方商业秘密、技术资料和数据负有保密义务，未经对方书面同意不得向第三方披露；保密义务在本合同终止后五年内持续有效。",
        "R1": f"{v}方对在履行本合同过程中知悉的{o}方商业秘密、技术资料和数据负有永久保密义务；{v}方违反保密义务的，应当向{o}方支付违约金人民币五十万元。",
        "R2": None,  # 缺失：整条删去
    }[variant]


def t_ip(variant, v, ctx):
    thing = ctx["thing"]
    if variant == "safe":
        if ctx["our_side"] == "乙":
            return f"本合同项下{thing}的知识产权归乙方所有，甲方在其业务范围内享有永久、免费的使用权；乙方在本合同签订前已经拥有的技术的知识产权仍归乙方。"
        return f"本合同项下{thing}的知识产权归甲方所有；乙方在本合同签订前已经拥有的技术的知识产权仍归乙方，乙方授予甲方为使用{thing}所必需的永久、不可撤销的使用许可。"
    if v == "甲":
        return {
            "R1": f"乙方在履行本合同过程中完成的全部成果（包括{thing}、源代码、图纸及相关文档）的知识产权归乙方所有，甲方仅享有在本项目范围内的非独占使用权。",
            "R2": f"{thing}的知识产权归乙方所有；甲方如需修改{thing}或者将其用于其他项目，应当另行向乙方支付许可费。",
        }[variant]
    return {
        "R1": f"乙方为履行本合同投入的既有技术、工具和数据，以及{thing}的全部知识产权均归甲方所有，乙方不得在其他项目中使用。",
        "R2": f"乙方交付{thing}后，乙方原有技术的知识产权一并转让给甲方，转让费用包含在合同价款内。",
    }[variant]


def t_jurisdiction(variant, v, ctx):
    o = other(v)
    ocity = ctx["city"][o]
    return {
        "safe": f"因本合同发生的争议，双方应当协商解决；协商不成的，由{ctx['our_side_or_A']}方住所地人民法院管辖。",
        "R1": f"因本合同发生的争议，由{o}方住所地（{ocity}）人民法院管辖。",
        "R2": f"因本合同发生的争议，双方可以向{ocity}仲裁委员会申请仲裁，也可以向{o}方住所地人民法院提起诉讼。",
        "R3": f"因本合同发生的争议，由{o}方指定的人民法院管辖。",
        "B": "因本合同发生的争议，由合同签订地人民法院管辖。",
    }[variant]


def t_auto_renew(variant, v, ctx):
    o = other(v)
    return {
        "safe": "本合同有效期一年，期满终止。双方有意续签的，应当在期满前三十日内协商一致并另行签订书面合同。",
        "R1": f"本合同有效期一年。期满前{o}方未书面通知终止的，本合同自动续期一年，{v}方不得拒绝；续期后的价格由{o}方根据市场行情确定。",
        "R2": f"本合同期满后自动续期，续期次数不限；{v}方如需终止，应当提前一百八十日书面通知{o}方，并支付剩余期限费用的百分之三十作为补偿。",
    }[variant]


SLOTS = [
    # (slot, 条款标题, 生成函数)
    ("subject", "标的、数量和价款", None),
    ("quality", "质量标准", None),
    ("delivery", "交付", None),
    ("inspection", "检验与验收", t_inspection),
    ("payment", "价款支付", t_payment),
    ("deposit", "定金", t_deposit),
    ("confidentiality", "保密", t_confidentiality),
    ("data", "数据安全", None),
    ("ip", "知识产权", t_ip),
    ("penalty", "违约责任", t_penalty),
    ("liability", "责任限制", t_liability),
    ("termination", "合同解除", t_termination),
    ("auto_renew", "合同期限", t_auto_renew),
    ("force_majeure", "不可抗力", None),
    ("jurisdiction", "争议解决", t_jurisdiction),
    ("notice", "通知与送达", None),
    ("misc", "其他", None),
]
VARIANTS = {
    "penalty": ["R1", "R2", "R3"],
    "liability": ["R1", "R2", "R3"],
    "termination": ["R1", "R2", "R3"],
    "inspection": ["R1", "R2", "R3"],
    "payment": ["R1", "R2", "R3"],
    "deposit": ["R1", "R2"],
    "confidentiality": ["R1", "R2"],
    "ip": ["R1", "R2"],
    "jurisdiction": ["R1", "R2", "R3"],
    "auto_renew": ["R1", "R2"],
}
LEVEL = {
    ("penalty", "R1"): "高",
    ("penalty", "R2"): "高",
    ("penalty", "R3"): "高",
    ("liability", "R1"): "高",
    ("liability", "R2"): "高",
    ("liability", "R3"): "高",
    ("termination", "R1"): "高",
    ("termination", "R2"): "高",
    ("termination", "R3"): "中",
    ("inspection", "R1"): "高",
    ("inspection", "R2"): "高",
    ("inspection", "R3"): "中",
    ("payment", "R1"): "中",
    ("payment", "R2"): "高",
    ("payment", "R3"): "高",
    ("deposit", "R1"): "中",
    ("deposit", "R2"): "高",
    ("confidentiality", "R1"): "中",
    ("confidentiality", "R2"): "中",
    ("ip", "R1"): "高",
    ("ip", "R2"): "中",
    ("jurisdiction", "R1"): "中",
    ("jurisdiction", "R2"): "中",
    ("jurisdiction", "R3"): "中",
    ("auto_renew", "R1"): "中",
    ("auto_renew", "R2"): "高",
    ("unit_price", "R1"): "中",
}
# 只有某一方会受损的风险：我方必须是这一方才可能植入
PAYER_ONLY = {"inspection", "deposit", "unit_price"}
PAYEE_ONLY = {"payment"}


def compatible(rtype: str, ctype: str, our_side: str) -> bool:
    t = TYPES[ctype]
    if rtype in PAYER_ONLY and our_side != "甲":
        return False
    if rtype in PAYEE_ONLY and our_side != "乙":
        return False
    if rtype == "ip" and not t["ip"]:
        return False
    return True


# 粮食类标的的国家标准（瑞丰规范第九条要求约定质量标准、检验机构和抽样方法）
GRAIN_STD = {
    "粳米": "GB/T 1354《大米》",
    "籼米": "GB/T 1354《大米》",
    "玉米": "GB 1353《玉米》",
    "大豆": "GB 1352《大豆》",
}
SAMPLING = "由双方共同委托具有资质的粮油质量检验机构，按照 GB/T 5491《粮食、油料检验 扦样、分样法》扦样检验，检验结果作为质量判定依据"


def quality_text(ctx: dict) -> str:
    if ctx["item"] in GRAIN_STD:
        return f"{ctx['item']}的质量应当符合{GRAIN_STD[ctx['item']]}中{ctx['spec']}的规定。到货后{SAMPLING}。"
    if ctx["ctype"] == "entrust":
        return f"仓储期间粮食的水分、杂质、不完善粒等质量指标不得劣于入库检验结果，但因粮食自然性质造成的变化和年损耗率不超过百分之零点二的合理损耗除外。入库和出库时{SAMPLING}。"
    return f"乙方提供的{ctx['thing']}应当符合国家标准、行业标准；双方另有技术要求的，以双方签字确认的书面技术文件为准。"


def data_text(ctx: dict) -> str:
    """数据类合同的数据安全条款（云启规范第四条要求写明处理目的、安全措施、事件通知、返还删除）。双方对等。"""
    if ctx["ctype"] == "dsupply":
        return "乙方保证已就所供数据取得必要的授权，并已对其中的个人信息进行去标识化处理。甲方保证仅在本合同约定的用途范围内使用数据，不向第三方转让，不尝试重新识别个人身份，并采取加密存储、访问控制等必要的安全措施。任何一方发生数据泄露等安全事件的，应当在二十四小时内通知对方。"
    return "乙方仅可以为履行本合同的目的、按照甲方书面指示的方式处理甲方提供的数据，并采取加密存储、访问控制和日志审计等安全措施；发生数据泄露等安全事件的，应当在二十四小时内通知甲方。本合同终止后十五日内，乙方应当返还或者删除全部数据并出具书面证明。甲方保证其提供的数据来源合法，并已取得必要的授权或者同意；乙方保证不超出甲方指示的范围处理数据，不向第三方提供数据。"


def fixed_text(slot: str, ctx: dict) -> str:
    thing = ctx["thing"]
    return {
        "quality": quality_text(ctx),
        "data": data_text(ctx),
        "delivery": (
            "乙方应当自本合同生效之日起，按照甲方的书面入库通知接收粮食入库，并按照甲方的出库通知在三日内办理出库。"
            if ctx["ctype"] == "entrust"
            else f"乙方应当在合同签订后三十日内向甲方交付{thing}，交付地点为甲方指定地点，运输费用由乙方承担。"
            if thing in ("货物", "定作物")
            else f"乙方应当在合同签订后三十日内按第一条约定的品名、规格和数量完成工作，并以书面或者电子方式向甲方交付{thing}。"
        ),
        "force_majeure": "因不可抗力不能履行合同的，根据不可抗力的影响，部分或者全部免除责任，但是法律另有规定的除外。遭遇不可抗力的一方应当及时通知对方，并在合理期限内提供证明。",
        "notice": "本合同首部载明的地址为双方的送达地址。一方变更送达地址的，应当在变更后三日内书面通知对方。",
        "misc": "本合同自双方签字盖章之日起生效。本合同一式两份，双方各执一份，具有同等法律效力。",
    }[slot]


def subject_text(ctx: dict) -> str:
    return (
        f"品名：{ctx['item']}；规格：{ctx['spec']}；计量单位：{ctx['unit']}；数量：{ctx['qty']}；"
        f"单价：人民币{ctx['price']}元/{ctx['unit']}（含税）；合同总额：人民币{ctx['total']}元。"
    )


def fmt_price(p: float) -> str:
    return f"{p:.2f}".rstrip("0").rstrip(".") if p < 100 else str(int(round(p)))


GEN_DATE = "2026-10-23"  # 生成日：台账里资质有效期早于这天的供应商算资质已过期


def supplier_flag(tenant: str, name: str) -> str | None:
    """台账里对方的异常（黑名单或资质过期）；按规范报出属于正确意见。"""
    _, status = ledger()
    s = status.get((tenant, name))
    if s is None:
        return None
    if s["status"] == "黑名单":
        return "对方在供应商黑名单中"
    if s["qual_valid_until"] < GEN_DATE:
        return f"对方资质（{s['qualification']}）已于 {s['qual_valid_until']} 过期"
    return None


def build_contract(
    cid, ctype, our_side, risks, borderline, favorable, clarify, rng, stats, ctx_extra=None
):
    """risks: [(rtype, variant)]；borderline: [rtype]；favorable: [(rtype, variant)] 受损方是对方。"""
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
    rtypes = {r for r, _ in risks}
    if "unit_price" in rtypes:
        factor = rng.uniform(1.28, 1.45)
    elif "unit_price" in borderline:
        factor = rng.uniform(1.10, 1.12)  # 只在瑞丰：高于 8% 需说明、未到 15% 复核线
    else:
        factor = rng.uniform(0.97, 1.03)
    price = float(fmt_price(st["avg"] * factor))
    qty = rng.choice([20, 50, 100, 200]) if st["avg"] > 100 else rng.choice([20000, 50000, 100000])
    ctx = dict(
        thing=t["thing"],
        item=item,
        spec=st["spec"],
        unit=st["unit"],
        qty=qty,
        price=fmt_price(price),
        total=fmt_price(price * qty) if price * qty >= 100 else f"{price * qty:.2f}",
        our_side=our_side,
        city={"甲": party["甲"][2], "乙": party["乙"][2]},
        our_side_or_A=our_side,
        ctype=ctype,
    )
    plan = {r: v for r, v in risks}
    fav = {r: v for r, v in favorable}
    has_deposit = (
        "deposit" in plan or "deposit" in borderline or "deposit" in fav or rng.random() < 0.4
    )
    clauses, gold, border_gold, fav_gold, acceptable = [], [], [], [], []
    n = 0
    for slot, heading, fn in SLOTS:
        if slot == "deposit" and not has_deposit:
            continue
        if slot == "ip" and not t["ip"]:
            continue
        if slot == "data" and ctype not in ("dproc", "ddev", "dsupply"):
            continue
        if slot == "subject":
            body = subject_text(ctx)
        elif fn is None:
            body = fixed_text(slot, ctx)
        elif slot in plan:
            v = our_side
            body = fn(plan[slot], v, ctx)
        elif slot in borderline:
            body = fn("B", our_side, ctx)
        elif slot in fav:
            body = fn(fav[slot], other(our_side), ctx)
        else:
            body = fn("safe", our_side, ctx)
        if body is None:  # 条款缺失
            gold.append(
                dict(
                    clause=None,
                    type=slot,
                    variant=plan[slot],
                    level=LEVEL[(slot, plan[slot])],
                    victim=our_side,
                    basis=basis(tenant, slot),
                )
            )
            continue
        n += 1
        no = f"第{cn_num(n)}条"
        clauses.append(f"{no} {heading}\n{body}")
        if slot in plan:
            gold.append(
                dict(
                    clause=no,
                    type=slot,
                    variant=plan[slot],
                    level=LEVEL[(slot, plan[slot])],
                    victim=our_side,
                    basis=basis(tenant, slot),
                )
            )
        if slot == "subject" and "unit_price" in rtypes:
            gold.append(
                dict(
                    clause=no,
                    type="unit_price",
                    variant="R1",
                    level="中",
                    victim=our_side,
                    basis=basis(tenant, "unit_price"),
                    ledger_avg=round(st["avg"], 2),
                    price=price,
                    ratio=round(price / st["avg"], 3),
                )
            )
        if slot in borderline or (slot == "subject" and "unit_price" in borderline):
            btype = "unit_price" if slot == "subject" else slot
            border_gold.append(dict(clause=no, type=btype, basis=basis(tenant, btype)))
        if slot in fav:
            fav_gold.append(dict(clause=no, type=slot, variant=fav[slot], victim=other(our_side)))
        if slot == "subject":
            subject_no = no
        if (
            slot == "liability"
            and our_side == "甲"
            and slot not in plan | fav
            and slot not in borderline
        ):
            # 甲方视角的安全写法把"侵犯知识产权、违反保密义务"等排除在上限之外，例外对我方同样适用，
            # 我方对外赔偿可能无上限，按华信第十四条、云启第十一条报出是对的
            acceptable.append(
                {
                    "clauses": [no],
                    "types": ["liability", "other"],
                    "reason": "责任上限的例外对我方同样适用",
                }
            )
    flag = supplier_flag(tenant, cp[0]) if our_side == "甲" else None
    if flag:
        acceptable.append({"clauses": [None, subject_no], "types": ["other"], "reason": flag})
    names = {s: ("____________（待填）" if clarify else party[s][0]) for s in ("甲", "乙")}
    homes = {s: ("____________" if clarify else party[s][1]) for s in ("甲", "乙")}
    sign_place = "____________" if "jurisdiction" in borderline else our[2]
    header = "\n".join(
        [
            t["title"],
            f"合同编号：{cid.upper()}",
            f"甲方（{t['A']}）：{names['甲']}",
            f"住所：{homes['甲']}",
            f"乙方（{t['B']}）：{names['乙']}",
            f"住所：{homes['乙']}",
            f"签订地点：{sign_place}",
            "根据《中华人民共和国民法典》等有关法律法规，甲乙双方在平等、自愿、公平和诚信的基础上，经协商一致，订立本合同。",
        ]
    )
    text = header + "\n" + "\n".join(clauses) + "\n"
    return dict(
        id=cid,
        tenant=tenant,
        contract_type=ctype,
        template_id=t["tpl"],
        title=t["title"],
        our_side=our_side,
        our_name=our[0],
        counterparty=party[other(our_side)][0],
        clarify=clarify,
        text=text,
        gold=gold,
        borderline=border_gold,
        favorable=fav_gold,
        acceptable=acceptable,
        constructed=True,
    )


RISKY_PER_TYPE = {
    "goods": 6,
    "agri": 5,
    "work": 5,
    "entrust": 5,
    "dproc": 5,
    "ddev": 5,
    "dsupply": 5,
}
CLEAN_PER_TYPE = {
    "goods": 2,
    "agri": 2,
    "work": 2,
    "entrust": 2,
    "dproc": 2,
    "ddev": 1,
    "dsupply": 1,
}
QUOTA = {
    "penalty": 7,
    "liability": 7,
    "termination": 7,
    "inspection": 7,
    "payment": 7,
    "confidentiality": 7,
    "ip": 6,
    "jurisdiction": 7,
    "auto_renew": 7,
    "deposit": 6,
    "unit_price": 6,
}
CLARIFY_IDS = {"goods-r3", "agri-r2", "dproc-r4", "ddev-r1"}
BORDERLINE = {
    "goods-r2": "penalty",
    "agri-r4": "unit_price",
    "work-r2": "jurisdiction",
    "goods-r5": "liability",
    "dsupply-r2": "termination",
    "entrust-r1": "deposit",
}
FAVORABLE = {
    "goods-c1": ("penalty", "R2"),
    "agri-c2": ("termination", "R1"),
    "work-c1": ("liability", "R1"),
    "entrust-c1": ("jurisdiction", "R1"),
    "dproc-c2": ("auto_renew", "R1"),
    "ddev-c1": ("ip", "R1"),
}


def main() -> None:
    rng = random.Random(SEED)
    stats, _ = ledger()
    remaining = dict(QUOTA)
    contracts = []
    sizes = [2, 3, 2, 3, 1, 2, 3, 2, 2, 3, 2, 2]
    k = 0
    for ctype, n in RISKY_PER_TYPE.items():
        for i in range(1, n + 1):
            cid = f"{ctype}-r{i}"
            our_side = "甲" if (k % 2 == 0) else "乙"
            b = BORDERLINE.get(cid)
            if b in PAYER_ONLY:
                our_side = "甲"
            if b == "liability":  # 不可抗力扩张免责：乙方受益，我方须是甲方
                our_side = "甲"
            size = sizes[k % len(sizes)]
            k += 1
            pool = [
                r for r in QUOTA if compatible(r, ctype, our_side) and r != b and remaining[r] > 0
            ]
            if ctype == "goods" or ctype == "agri":
                pool = [r for r in pool if not (r == "confidentiality" and False)]
            rng.shuffle(pool)
            # 只能植入特定视角或合同类型的风险优先分配，避免配额用不完
            pool.sort(key=lambda r: (r not in PAYER_ONLY | PAYEE_ONLY | {"ip"}, -remaining[r]))
            chosen = pool[:size]
            risks = []
            for r in chosen:
                if cid not in CLARIFY_IDS:  # 反问合同不计分，不占配额
                    remaining[r] -= 1
                if r == "unit_price":
                    risks.append((r, "R1"))
                    continue
                variants = [
                    v
                    for v in VARIANTS[r]
                    if not (r == "confidentiality" and v == "R2" and not TYPES[ctype]["data"])
                ]
                risks.append((r, rng.choice(variants)))
            contracts.append(
                build_contract(
                    cid,
                    ctype,
                    our_side,
                    risks,
                    [b] if b else [],
                    [],
                    cid in CLARIFY_IDS,
                    rng,
                    stats,
                )
            )
    for ctype, n in CLEAN_PER_TYPE.items():
        for i in range(1, n + 1):
            cid = f"{ctype}-c{i}"
            our_side = "甲" if (k % 2 == 0) else "乙"
            k += 1
            fav = FAVORABLE.get(cid)
            if fav and fav[0] in PAYER_ONLY | PAYEE_ONLY:
                raise ValueError(cid)
            contracts.append(
                build_contract(
                    cid, ctype, our_side, [], [], [fav] if fav else [], False, rng, stats
                )
            )
    check(contracts)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for c in contracts:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    report(contracts)


def check(contracts: list[dict]) -> None:
    assert len(contracts) == 48
    scored = [c for c in contracts if not c["clarify"]]
    by_type = Counter(g["type"] for c in scored for g in c["gold"])
    assert all(by_type[r] >= 6 for r in RISK_TYPES), by_type
    risky = [c for c in contracts if c["gold"]]
    assert len(risky) == 36 and all(1 <= len(c["gold"]) <= 3 for c in risky)
    assert sum(1 for c in contracts if c["clarify"]) == 4
    assert sum(len(c["borderline"]) for c in contracts) == 6
    assert sum(len(c["favorable"]) for c in contracts) == 6
    sides = Counter(c["our_side"] for c in contracts)
    assert abs(sides["甲"] - sides["乙"]) <= 4, sides


def report(contracts: list[dict]) -> None:
    scored = [c for c in contracts if not c["clarify"]]
    print(f"{len(contracts)} 份 → {OUT.relative_to(ROOT)}")
    print(
        "风险总数",
        sum(len(c["gold"]) for c in contracts),
        "（计分合同内",
        sum(len(c["gold"]) for c in scored),
        "）",
    )
    print("按类型（计分合同）", dict(Counter(g["type"] for c in scored for g in c["gold"])))
    print("我方", dict(Counter(c["our_side"] for c in contracts)))
    print("条款缺失型", sum(1 for c in contracts for g in c["gold"] if g["clause"] is None))


if __name__ == "__main__":
    main()
