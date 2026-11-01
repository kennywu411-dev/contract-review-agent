# 评测集

`questions.jsonl`：一行一题。由 `evals/parts/*.jsonl` 合并而成，校验见 `tests/test_evalset.py`。

## 字段

| 字段 | 说明 |
|---|---|
| `id` | `{school}-{三位序号}`，如 `bnu-007`；通用层用 `national-001` |
| `school` | 提问者所在学校：bnu/upc/njust/lzu/cumt/zzu。通用题也必须指定一个学校（随机分给 6 校） |
| `question` | 学生口吻的一句话问题，不照抄文件原句 |
| `type` | `answerable`（文件里能找到答案）/ `table`（需要查结构化表）/ `refuse`（已收录文件里没有答案，应拒答） |
| `subtype` | `null` / `cross_school`（用了别校的术语或校名）/ `version`（新旧版本、年级适用）/ `conflict`（本校规定和国家规定冲突）/ `clarify`（缺关键信息，应先反问） |
| `source_type` | `faq`（学校或教育部官方 FAQ 的问题）/ `public`（知乎、贴吧等公开提问，已改写）/ `doc`（照着文件目录出的题） |
| `source_url` | faq/public 必填，取自 `docs/真实提问来源.md`；doc 为 null |
| `gold` | 标准出处列表，命中任意一个即算对。PDF：`{"doc_id", "page", "clause": null}`；网页：`{"doc_id", "page": null, "clause": "第二十八条"}`（clause 必须是该段开头的原文前缀；没有"第X条"的通知类文件写段首几个字，如 `（一）增加本专科生国家奖学金`）；需查表题另加 `{"table": "scholarships", "where": "school='bnu' AND name='京师奖学金'"}`。refuse 题为 `[]` |
| `answer_hint` | 标准答案要点（一两句，用于 D4/D5 判分），refuse 题写应如何拒答 |
| `note` | 出题说明，可空 |

## 出题规则

1. 标准出处只用 SQL 在 `data/docpilot.db` 的 blocks 表里找，不用检索器（避免用被测系统给自己出答案）。
2. doc 题只看标题和目录出题，不照抄正文原句。
3. 真实提问是主观问题（"难不难""该不该"）、或问的是学校文件里没有的信息（挂科率、导师人选、保研率）时，归为 refuse。
