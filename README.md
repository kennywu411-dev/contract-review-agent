# Contract Risk Review Agent（合同风险审查智能体）

**逐条溯源 + 幻觉治理 + 转人工兜底的合同风险审查 Agent。**

提交一份商业合同，agent 站在我方（租户公司）的立场逐条找出不利条款，给出风险类型、风险等级和可以直接替换的修改建议；每条意见都挂着《民法典》、合同编通则解释、《民事诉讼法》、官方合同示范文本或本公司审查规范的**原文**，点编号就能看到高亮。单价会经 [DataPilot](../datapilot) 的只读 MCP 查询供应商台账，比对历史成交价。看不出我方是甲方还是乙方时先反问；依据核实不了的意见**不删除，标"转法务复核"**。

> 演示数据（示例合同、三家公司及其审查规范、供应商台账）**全部是构造的**，见 [evals/README.md](evals/README.md)。项目由高校规章问答改造而来，场景变更理由见 [ADR 007](docs/adr/007-场景变更为合同风险审查.md)。

## 结果（Haiku 5.5，48 份构造合同）

| 指标 | 全部 48 份 | 留出集 28 份 |
|---|---|---|
| 风险检出率（召回，73 处） | 100% | 100%（55 处） |
| 风险类型判对率 | 97.3% | 96.4% |
| 依据引用准确率（引用落在该风险的合法依据条文） | 97.3% | 96.4% |
| 意见精确率（高/中等级） | 72.6% | 81.1% |
| 当事人空白时反问 / 多余反问 | 100% / 0% | — |
| 对我方有利的条款被误报 | 0% | 0% |
| 单份合同花费 P50 | $0.0038 | $0.0039 |

- 20 份小样参与了调试（问题日志 P19、P20），所以另报未参与调试的 28 份留出集。
- 检出率触顶，说明植入的风险写得较显眼；这套数据真正拉开差距的是误报：12 份干净合同里 8 份被报出高/中等级意见。另外真风险里有近一半被模型自己标了转人工，偏保守。
- 依据校验（Sonnet 5.5 逐条评判 122 条意见）：理由里有无依据说法的意见 18.9%，其中**没被标转人工的从 6.6% 降到 0.8%**，误拦 2.0%；修改建议均分 3.86/5，≥4 分 77%。见 [ADR 008](docs/adr/008-合同审查的输出结构与依据校验.md)。
- 全部数字、运行 ID 和测量条件见 [docs/numbers.md](docs/numbers.md)。

## 它怎么工作

```
合同全文 ──► 审查 agent（Haiku 5.5，手写工具循环，最多 8 轮）
              ├─ search_docs   混合检索：BM25 + bge-m3 向量 → RRF → bge-reranker 重排
              │                 范围 = 通用法规与示范文本 + 本租户规范（租户由会话注入，模型改不了）
              ├─ run_sql       DataPilot 只读 MCP 查供应商台账（同名 CTE 遮蔽，只看得到本租户的行）
              ├─ ask_clarification   看不出我方是甲方还是乙方时反问
              └─ submit_review       逐条意见：条款、摘录、类型、等级、理由、修改建议、依据编号、是否转人工
        ──► 依据校验：规则层（点名的条文是否真被检索到）+ Haiku 逐条核对（依据不足 → 转人工，不删除）
        ──► Web：合同高亮 · 意见卡片 · 点编号看法条原文 · 多轮追问（"你列的第 2 条怎么改""换成乙方视角"）
```

- 一次审查通常 2 轮模型调用：第一轮并发发起 5–7 个检索和查表，第二轮提交全部意见。
- 每一步都写 trace（输入输出、token、花费、耗时），Web 端可以逐步回放。
- 向量后端可切换到 Milvus Lite（结果与 numpy 完全一致；这个规模下 numpy 更快，默认不切），见 [ADR 009](docs/adr/009-Milvus-Lite向量后端.md)。

## 运行

```bash
uv sync
```

```bash
uv run cra ingest
```

```bash
uv run cra index
```

```bash
uv run cra serve
```

- `ingest` 按 [data/sources.toml](data/sources.toml) 下载法规与 7 份示范文本、解析入库，并构建供应商台账库；`index` 首次会下载 bge-m3 和 bge-reranker-v2-m3（各约 2.3GB）。
- `serve` 在 http://127.0.0.1:8000 打开 Web 界面（只监听本机），需要 `ANTHROPIC_API_KEY`（环境变量或仓库根目录 `.env`）。
- 示范文本来自市场监管总局合同示范文本库，网站只写了"版权所有"，所以原文只在本地使用、不进 git；仓库里只有文本 id 和下载脚本。

评测命令（会调用 Claude API，均有预算上限参数 `--max-cost`）：

```bash
uv run cra eval-review --sample 20
```

```bash
uv run cra review-d4 <eval-review 运行目录名>
```

```bash
uv run cra eval-review-dialog <eval-review 运行目录名>
```

```bash
uv run cra eval-contract-retrieval --backend milvus
```

可选：`uv sync --extra milvus` 安装 Milvus Lite 后端。

## 评测集

48 份构造合同，覆盖 7 类合同（工业品买卖、农产品买卖、加工承揽、仓储委托、数据委托处理、数据产品开发、数据供应），条款顺序参照官方示范文本、正文全部自写：

- 36 份植入 1–3 处风险，11 类风险每类 6–7 处；12 份干净合同（其中 6 份含"对我方有利的单方条款"，测视角）；
- 4 份当事人名称空白（测反问），6 处临界条款（测转人工）；
- 每处风险都标了合法依据集合（法条 + 公司规范条号），依据准确率按集合自动判分。

生成与校验：`uv run python scripts/make_contracts.py`，`uv run pytest tests/test_contract_evalset.py`。

## 目录

```
src/contract_review/
  review_agent.py     合同审查 agent：工具定义、结构化意见、追问与历史
  review_verify.py    依据校验：点名条文核对 + Haiku 逐条核对 + 转人工策略
  eval_review.py      审查评测：检出、类型、依据、误报、视角、转人工、反问
  eval_review_d4.py   依据校验评测（Sonnet 评判幻觉与修改建议质量）
  eval_review_dialog.py  多轮追问评测
  contract_evalset.py 评测集加载与分层小样
  agent.py            通用工具循环与 trace（审查和法规问答共用）
  tools.py            检索与查表工具、租户隔离（scope_sql）
  retrieve.py / bm25.py / embed.py / rerank.py / vector.py / milvus_index.py  检索
  datapilot_client.py DataPilot MCP 客户端（含本地只读降级）
  ledger_db.py        构造的供应商台账库
  html_parse.py / pdf_parse.py / structure.py / headings.py  解析：编、分编、章、节、条
  api.py              FastAPI：审查与追问 SSE、原文与高亮、评测运行回放
scripts/              构造数据生成（台账、合同）、分析与基准
web/                  Next.js 15 前端（合同审查、法规问答、评测看板、成本面板）
docs/                 ADR、问题日志、数字登记表、调研
evals/                构造评测合同
```

## 方法验证：高校规章问答（ADR 007 之前）

项目最初做的是高校规章问答，检索（Recall@5 97.9%）、agent（引用命中 96.3%）、生成后校验（幻觉率 31.9% → 15.6%）、多轮记忆等方法都在那个场景里验证过，数字保留在 [docs/numbers.md](docs/numbers.md) 和 ADR 002–005，**不当作合同场景的效果**。

## 开发

```bash
uv run pytest -q
```

```bash
uv run ruff check .
```

## 诚实边界

- 评测数据全部是构造的，存在"自己出题自己考"的风险；缓解办法是每类风险多种写法、agent 看不到植入规则、保留干净合同和视角干扰条款、另报留出集。
- 不构成法律意见；拿不准的条款由法务人工复核。
- 法规文本以下载日期为准（《民事诉讼法》2023 年修正版取自上海市发改委网站转载的全文，原定的最高院公报页下载失败）。
