# 数字登记表

规则：这里的每个数字都来自真实运行，写明来源命令或运行 ID。没跑出来的不填。

## 解析器选型（2026-10-23，ADR 001）

| 项 | 数值 | 来源 |
|---|---|---|
| docling 2.142.0（无 OCR）解析北师大手册 378 页 | 59.8s，峰值内存 5.2GB，5191 个元素，10 张表 | `scripts/parser_spike.py`，/usr/bin/time -l |
| docling 开 OCR 解析同一本 | 15 分钟未完成，已中止 | 同上 |
| MinerU 2.7.6 热启动解析 5 页夹具 | 43.9s，峰值内存 2.7GB | `mineru -b pipeline` |
| 中石油夹具 3 页"暋"乱码数（docling） | 18 | parser_spike 输出 |

## 下载（2026-10-23）

| 项 | 数值 | 来源 |
|---|---|---|
| 清单条目 | 23（manual 3） | `data/sources.toml` |
| 已下载 | 20 份，24MB | `scripts/fetch.py` |
| 缺失 | 南理工学籍、学位、奖学金 3 份（反爬） | 同上 |

## 入库（2026-10-23，`uv run docpilot ingest`）

| 项 | 数值 |
|---|---|
| 入库文档 | 20 份（PDF 14、网页 5、DOCX 1），830 页（PDF），9294 个块 |
| 全量解析耗时 | 161.5s（M 系列 CPU，docling 无 OCR） |
| 重复执行 | 解析 0，未变 20，耗时 0.0s |
| 质量标记 | 无文件被标为疑似扫描件或乱码；中石油手册乱码比例 0.08%（清洗后残留，阈值 0.5%） |
| 结构化表 | national_aid 6 行、scholarships 39 行、graduation_rules 4 行、calendar 3 行 |
