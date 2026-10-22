# DocPilot：高校规章可信问答

学生用中文问学校规章（转专业、推免、奖学金、学分、缓考、学位授予……），agent 从本校和国家层面的正式文件里找答案，**逐句标出处（文件 + 页码），查不到就明确拒答**；需要查数字时，交给 [DataPilot](../datapilot) 去查结构化表。

DataPilot 负责结构化数据，DocPilot 负责非结构化文档，两者由同一个 agent 调度。产品定义见 [docs/PRD.md](docs/PRD.md)。

> 当前进度：**D1 解析入库**已完成；检索、agent、引用校验、界面和评测按 D2–D7 推进。

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
  cli.py         docpilot ingest
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
