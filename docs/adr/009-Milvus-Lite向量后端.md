# ADR 009：向量检索后端可切换到 Milvus Lite；默认仍用 numpy

- 状态：已采纳
- 运行与脚本：
  - 合同场景检索评测：`runs/contract-retrieval-numpy-f02dc6`、`runs/contract-retrieval-milvus-073ff0`（`cra eval-contract-retrieval --backend numpy|milvus`）；
  - 纯检索微基准：`scripts/bench_vector_backend.py`（查询向量预先编码，两种后端交替跑 5 轮）。

## 背景

ADR 002 选了 numpy 暴力余弦：几千个 chunk，一次矩阵乘法就够。求职材料里需要回答"数据量上去怎么办、多个服务进程怎么共享索引"，所以把向量后端做成可切换，接入一个真正的向量数据库，并用数字说明什么时候值得切。

## 决定

1. 接口不变：`Retriever(..., vector_backend="numpy" | "milvus")`。向量仍走原来的 sha1 缓存（不重复编码），Milvus 只负责存向量和租户、做过滤检索。
2. 租户过滤下推：检索时把 `tenant in ["huaxin", "common"]` 作为过滤表达式交给 Milvus，而不是取回后再删，保证前 k 个不会被别家租户占位（与 numpy 后端的 allow 回调语义一致）。租户取值先按白名单校验再拼表达式。
3. 集合复用：内容指纹（全部文本 sha1 + 租户）不变就复用集合，变了才删掉重建。
4. 连接方式：默认在本机 127.0.0.1 起一个 Milvus Lite 服务子进程，用 URI 连接；设置 `MILVUS_URI` 可以直接连外部 Milvus（如 Docker 里的 Standalone）。
5. 可选依赖：`uv sync --extra milvus`，默认安装不带。

## 实施中踩到的两个坑

- 新进程复用集合时集合是 released 状态，直接检索报错（code=101）。同进程内建完马上查的测试测不出来，是写微基准时发现的。修复：复用前先 `load_collection`；补了回归测试 `test_reused_collection_is_loaded_before_search`。
- OMP #15：Milvus Lite 3.x 已改成进程内纯 Python 实现，检索用 faiss（自带一份 libomp），和 torch 自带的 libomp 在同一进程里冲突，进程直接退出。没有用 `KMP_DUPLICATE_LIB_OK=TRUE` 这种官方标注"不安全"的绕法，而是把 Milvus Lite 放到独立服务进程里，两份 OpenMP 天然隔离，连接方式也和生产环境一致。回归测试：`test_default_uri_runs_server_in_separate_process`。

## 结果

合同场景检索评测：87 条查询（金标准风险条款的正文 → 找该风险的合法依据条文），Recall@5：

| 检索方式 | numpy | Milvus Lite |
|---|---|---|
| BM25 | 37.9% | 37.9% |
| 向量 | 56.3% | 56.3% |
| 混合（RRF） | 46.0% | 46.0% |
| 混合 + 重排 | 63.2% | 63.2% |

纯检索微基准（647 个 chunk，87 条查询 × 5 轮，候选 15，带租户过滤）：

| 后端 | 前 15 名与 numpy 一致 | P50 | P95 |
|---|---|---|---|
| numpy | — | 0.075 ms | 0.149 ms |
| Milvus Lite（独立进程，gRPC） | 100% | 3.7 ms | 5.5 ms |

- 两种后端结果完全一致，切换不影响效果。
- 这个规模下 numpy 快约 50 倍：Milvus 多了一次进程间调用，向量又少，ANN 索引没有用武之地。默认后端保持 numpy。
- 值得切到 Milvus 的情况：chunk 到几十万以上（暴力检索开始变慢、内存吃紧）、多个服务进程要共享同一份索引、需要增量写入和持久化。届时把 `MILVUS_URI` 指向 Standalone 即可，代码不用改。

## 附带发现：条款原文直接检索法条，召回只有六成

拿合同条款原文当查询，最好的"混合 + 重排"Recall@5 也只有 63.2%，合同语言（"乙方交付即视为验收合格"）和法条语言（"买受人应当在约定的检验期限内……"）差距很大。而端到端的依据引用准确率是 97.3%（`runs/review-full-357021`），差距来自 agent 先把条款改写成法律检索语句（如"买受人检验期间 视为标的物质量符合约定"）再检索。这说明在这个场景里，查询改写比换向量库重要得多。
