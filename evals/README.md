# 合同审查评测集（构造）

三套：v1（`contracts.jsonl`，48 份）、v2 难评测集（`contracts_v2.jsonl`，60 份）、v3 采购金额测算集（`contracts_v3.jsonl` 和留出集，各 60 份）。v1、v2 评审查意见，v3 评金额。

`contracts.jsonl` 由 `uv run python scripts/make_contracts.py` 生成（固定随机种子，可复现），全部是构造数据：公司、价格、条款均为虚构。

## 怎么构造的

- 7 类合同（工业品买卖、农产品买卖、加工承揽、仓储委托、数据委托处理、数据产品开发、数据供应），条款顺序参照 7 份官方示范文本（`data/sources.toml` 里的 `tpl-*`）。条款正文全部自写，不复制示范文本原文，所以评测合同可以入 git（示范文本没有开放许可，见 ADR 007）。
- 我方是 3 家构造公司之一（租户 huaxin / yunqi / ruifeng），合同里写明是甲方还是乙方；甲方固定是付款方、接收方。
- 每份合同 14–16 条。干净条款是对双方都常规的写法；风险条款按"受损方 = 我方"植入，每类 2–3 种写法。
- 单价风险按 `data/ledger_src/purchase_history.csv` 的历史均价植入：风险合同 ×1.28–1.45，干净合同 ×0.97–1.03。

## 组成

| 子集 | 份数 | 用途 |
|---|---|---|
| 含风险 | 36（其中 4 份当事人名称空白） | 检出率、类型判对率、依据准确率；空白的 4 份测反问，不计入检出 |
| 干净 | 12（其中 6 份含"对我方有利的单方条款"） | 误报率、视角错误 |
| 临界条款 | 6 处（分布在含风险合同里） | 转人工率 |

计分合同（32 份）内共 73 处风险，11 类每类 6–7 处；4 处是"保密条款缺失"（金标准条款号为空）。

## 字段

- `gold`：`clause`（第X条，缺失型为 null）、`type`、`variant`（植入写法）、`level`（高/中）、`victim`、`basis`（合法依据：`[doc_id, 第X条]` 列表，引用其中任意一条即算依据正确；单价风险只有租户规范，查台账的 `[T]` 引用也算）。
- `borderline`：临界条款，期望 agent 标"转法务人工复核"。
- `favorable`：受损方是对方的单方条款，agent 不应当作我方风险报出。

## 局限

自己出题自己考：植入规则和 agent 提示词由同一人写。缓解：每类多种写法、agent 看不到植入规则、保留干净合同与"对我方有利"的干扰条款。结论只代表这套构造数据上的表现。

## v2 难评测集（ADR 013）

`contracts_v2.jsonl` 由 `uv run python scripts/make_contracts_v2.py` 生成（固定种子），全部是构造数据。60 份长合同（每份 25–30 条），30 份植入 75 处风险、30 份干净：

- 风险分 5 类：隐蔽位置 18、换说法 18、跨条款组合 12（`clauses` 记多条）、临界值 15、标准位置对照 12；每处带 `category`、`why`、`alt_types`。
- 干净合同带 `distractors`：刚好合规的数值、合法免责、对我方有利的单方条款、"签订地法院 + 签订地在我方"。
- 抽查清单 `contracts_v2_spotcheck.md`（20 条）随生成器一起输出。
- 跑法：`cra eval-review --evalset v2 [--multi-agent]`。

## v3 采购金额测算集（ADR 014）

`contracts_v3.jsonl` 和 `contracts_v3_holdout.jsonl` 由 `uv run python scripts/make_contracts_v3.py`（加 `--holdout` 生成留出集）生成，固定种子，全部是构造数据。各 60 份采购合同，我方都是甲方：工业品买卖 20、农产品买卖 16、加工承揽 6、数据委托处理 6、数据供应 6、数据产品开发 6。

- 每份的单价、数量、交付期、预付款、账期、定金、违约金、质保金随机取值，所以每项金额的标准答案可以直接算出。
- 18 份是多行标的（2–5 种），逐行和台账均价比。一半合同用中文数字，一半用阿拉伯数字。
- 算不了的条款：背靠背付款、付款时间另行确定、以实际损失为基数的违约金、质保金比例另行约定。
- 字段：`gold_lines`（标的明细）、`gold_fields`（应抄出的其他数字）、`gold_uncomputable`（有条款但算不了的主题）、`gold_amounts`（比价、资金占用、违约金敞口差、定金超额、质保金 5 项各自的金额，或 `none` / `unquantifiable`）、`ledger`（每行标的的台账均价）。
- 标准答案的金额在生成器里独立计算，`tests/test_quant_evalset.py` 核对它和 `quant.py` 的结果逐项一致。
- 调试只看 v3；留出集换了随机种子，规则冻结后才跑，只用来报最终数字。两套出自同一个生成器，条款写法相同。
- v3 没有审查意见的标准答案，不用来评检出率和误报。
- 跑法：`cra eval-quant [--evalset v3-holdout]`（三个方案：抄数 + 规则、模型直接给金额的两种推理强度）；`cra eval-quant --rescore <运行>` 用当前规则重算保存的模型输出，不调 API；`cra eval-quant-e2e` 跑完整的审查 + 测算流程。运行目录 `runs/quant-*`，里面是 `results.jsonl`、`summary.json`、`config.json`。

## 运行记录格式

`cra eval-review` 由评测平台 evalkit 运行，运行目录 `runs/<tag>-<时间>-<随机串>/` 里：

- `results.jsonl`、`config.json`：evalkit 格式，每份合同一行（主指标 = 风险全部检出且没有误报；反问合同看是否反问），配置快照记录评测集各合同的内容哈希、模型和参数；
- `traces/<trace_id>.json`：每份合同的完整过程，格式与之前相同；
- `review_results.jsonl`、`summary.json`：由 `results.jsonl` 派生的逐项计数和汇总，依据校验、多轮追问、分析脚本和 Web 运行记录页都读它们；
- `metrics.json`：看板上显示的检出率、精确率等指标。

接入 evalkit 之前的运行只有 `review_results.jsonl`，`--resume` 续跑、`cra eval-gate`、`cra eval-dashboard` 会现场转换，原文件不改。
