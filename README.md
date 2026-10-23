# DocPilot：高校规章可信问答

学生用中文问学校规章（转专业、推免、奖学金、学分、缓考、学位授予……），agent 从本校和国家层面的正式文件里找答案，**逐句标出处（文件 + 页码），查不到就明确拒答**；需要查数字时，交给 [DataPilot](../datapilot) 去查结构化表。

DataPilot 负责结构化数据，DocPilot 负责非结构化文档，两者由同一个 agent 调度。产品定义见 [docs/PRD.md](docs/PRD.md)。

> 当前进度：D1 解析入库、D2 检索、**D3 Agent 与工具**已完成；引用校验、界面和评测看板按 D4–D7 推进。

## 一条命令复现

```bash
uv sync && uv run docpilot ingest
```

这条命令依次完成：
1. 按 [data/sources.toml](data/sources.toml) 下载缺失的公开文件到 `data/raw/`；
2. 解析入库到 `data/docpilot.db`：保留页码、坐标、标题层级和表格；文件 sha256 没变就跳过；
3. 生成质量报告 [docs/解析质量.md](docs/解析质量.md)；
4. 用手工整理的 CSV 构建结构化表库 `data/campus/campus.sqlite`（供 DataPilot 查询）。

首次运行约 3 分钟，会下载 docling 模型和 24MB 文件。南京理工大学有 3 份文件因网站反爬拿不到，需要手动放到 `data/raw/njust/`（文件名见 `sources.toml` 中 `manual = true` 的条目）；不放也能运行，只是这 3 份标为缺失。

## 检索（D2）

固定长度切块 → BM25（手写，中文字二元组）与 bge-m3 向量各取前 15 → RRF 融合 → bge-reranker-v2-m3 重排；只在"本校 + 国家层面"范围内检索。

| 评测集 v1（174 题，真实来源 67%） | Recall@5 | R@1 | 单题检索 P50 |
|---|---|---|---|
| 选定方案 | **97.9%** | 70.2% | 1.21s |
| 不重排（降级方案） | 95.7% | 63.1% | 21ms |

命中口径是页级或条款级，不是句级；句级的引用准确率在 D4 测。3 种切块 × 4 种模式的对比和选型理由见 [ADR 002](docs/adr/002-检索与切块.md)。

```bash
uv run docpilot index
```

```bash
uv run docpilot search "国奖能拿多少钱" --school bnu
```

```bash
uv run docpilot eval-retrieval --sample 20
```

首次运行会下载 bge-m3 和 bge-reranker-v2-m3（各约 2.3GB）。

## Agent（D3）

学生提问后，agent 最多走 6 步，在 4 个工具里自己选：检索本校和国家层面的文件、通过 DataPilot 的只读 MCP 查 campus 表、反问澄清、结构化作答（每句话都列出处编号）。每一步都写 trace。

| 174 题（Haiku 5.5） | 结果 |
|---|---|
| 作答率 / 误拒率 | 99.3% / 0.7% |
| 引用命中率 | 97.1% |
| 需查表题走查表 | 91.7% |
| 拒答正确率（严格口径） | 81.8% |
| 单题花费 / 耗时 P50 | $0.0011 / 8.1s |

```bash
uv run docpilot ask "国家奖学金一年能拿多少钱？" --school upc
```

```bash
uv run docpilot eval-agent --sample 20
```

需要 `ANTHROPIC_API_KEY`（环境变量或仓库根目录的 `.env`）。设计取舍见 [ADR 003](docs/adr/003-agent设计.md)。

## 数据

| 层 | 内容 |
|---|---|
| 通用层 | 教育部令第 41 号、《学位法》、财教〔2024〕181 号 |
| 深度校 | 北京师范大学 2025 学生手册（378 页）、中国石油大学（华东）2025 学生手册（274 页） |
| 其余 4 校 | 南京理工、兰州大学、中国矿业大学、郑州大学的学籍、推免、奖学金、学位、转专业等单行本 |
| 结构化表 | 国家奖助标准、两所深度校的奖学金、毕业规则、校历；每行都带出处 |

文件来源、取舍和缺口见 [docs/校园文件调研.md](docs/校园文件调研.md) 和 [data/structured/README.md](data/structured/README.md)。

## 目录

```
src/docpilot/
  sources.py     文件清单与下载（sha256 记在 data/sources.lock.json）
  pdf_parse.py   PDF/DOCX → 块（docling，关闭 OCR，见 ADR 001）
  html_parse.py  网页正文抽取（标准库）
  headings.py    按"部分 > 章 > 节 > 条 > 一、 > （一） > 1. > （1）"推断标题层级
  textclean.py   清除错误字形映射、中文字间断行空格
  structure.py   组装块：层级、标题链（section_path）、断行段落合并
  store.py       SQLite 入库（documents / blocks / pages）
  quality.py     质量检查：疑似扫描件、未解析页、乱码比例
  campus_db.py   结构化表库
  chunking.py    三种切块策略（block / fixed / section），记录页码与条款号
  bm25.py        手写 BM25
  embed.py       bge-m3 向量（transformers 直接加载）
  vector.py      numpy 余弦检索 + 按文本哈希的向量缓存
  rerank.py      bge-reranker-v2-m3
  retrieve.py    学校过滤 + RRF + 重排
  evalset.py     评测集加载与校验
  eval_retrieval.py  检索评测
  llm.py         Claude 调用与成本计算
  datapilot_client.py  DataPilot MCP 客户端（含本地只读降级）
  tools.py       agent 工具与安全边界
  prompts.py     按校生成的 system 提示词
  agent.py       工具循环与 trace
  eval_agent.py  agent 评测
  cli.py         docpilot ingest / index / search / eval-retrieval / ask / eval-agent
docs/            PRD、ADR、问题日志、数字登记表、质量报告
```

## 开发

```bash
uv run pytest -q
```

```bash
uv run ruff check .
```

## 诚实边界

- 不招募真实用户，所有结论都基于离线评测；评测题优先来自学校官方 FAQ 和公开渠道的真实学生提问（见 PRD 第 7 节）。
- 只收官网公开文件；版本以下载日期为准，学校之后更新的文件不会自动同步。
