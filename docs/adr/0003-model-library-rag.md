# Layer 2 引入模型知识库 RAG 检索，生成候选模型池

数学建模的模型选择有强「题型 → 模型」映射规律，但 Layer 2 的三位建模师（A/B/C）目前完全开放式选题，模型全靠 LLM 自身知识，缺少结构化的起点，容易跑偏（如预测题直接上神经网络、不看数据量）。我们决定：新增一个**模型知识库**（范围以历年国赛真题驱动的方法清单为准），在 Layer 2 建模前通过 **embedding 向量检索**，按「题目/方案的特点」匹配出**候选模型池**，注入三位建模师的第一轮，供其从中选择（可超出池子，风格自然分化）。

## 为什么 RAG 而非静态 prompt 内嵌

知识库有几十个模型条目，全量注入会挤爆 system prompt、推高每轮 token 成本、且与「16 个 system prompt 全静态、prompt caching 100% 命中」的设计冲突。RAG 每次只注入与本题目相关的 Top-K 候选子集，既精准又省 token。

## 为什么 embedding 向量检索而非标签关键词匹配

用户明确要求 embedding 语义检索。数学模型的特点是有限、可枚举的属性（样本量、线性/非线性、可解释性、机理/数据驱动、时间/截面……），用受控 trait 词汇描述后，语义检索能自然匹配近义表达（如「样本极少」→「小样本」标签），比关键词匹配更鲁棒。

## embedding 选型：bge-small-zh-v1.5

- **模型**：`BAAI/bge-small-zh-v1.5`，512 维，中文，MIT license。
- **运行时**：`fastembed`（本地 ONNX 推理，不依赖 torch——项目沙盒当前无 torch，不为此引入重依赖）。
- **离线开箱**：知识库向量离线预计算为 `.npz`（约 100KB）随 repo 提交；embedding 模型（ONNX，约 90MB）主路径随 repo 提交，fastembed 通过 `cache_dir` 指向 repo 内路径加载，拉取即可用、不每次向量化。
- **镜像兜底**：模型 ONNX 约 90MB，超过 GitHub 50MB 警告线（但低于 100MB 硬限制），提交可能受阻。故准备双路径——优先 Git LFS 提交模型文件；若不可行则改用 `HF_ENDPOINT=https://hf-mirror.com` 镜像首次下载，README 写明两种路径的操作说明。无论哪种方式，首次就绪后均离线可用、不重复向量化。
- **一致性铁律**：知识库向量与 query 向量必须由同一模型生成，否则向量空间不对齐、检索失效。该模型名写入配置锁死，换模型必须重建向量库。

## 检索触发点与双通道

1. **候选池注入**（主通道）：ProblemManager CONCLUDE 后追加一次结构化 LLM 调用（复用 ADR-0001 的 `with_structured_output` 模式），提炼「题目特点需求」，编码为 query 向量 → 检索 Top-K → 存入 `state["model_candidates"]`，`_build_context` 在 modeling 层第一轮注入 A/B/C。
2. **按需补充**（工具通道）：新增 `model_search` 工具，A/B/C 在辩论中可主动检索补充候选。

## fail-open 与范围

- 检索/提炼失败 → 全量谱系原样降级注入（不阻塞主流程）。
- 范围以《数学模型方法清单》为准，**全部收录含降级项**（检索自己决定用不用）；条目**不含任何倾向性/频率标注**（避免改变 ModelerA 的创新定位，让风格自然分化）。

## Consequences

- 新增依赖：`fastembed`（含 `onnxruntime`）。
- 新增 state 字段 `model_candidates`（候选模型池文本）。
- 新增资产：知识库数据文件（结构化模型条目）、离线向量 `.npz`、embedding 模型目录（Git LFS 或镜像兜底）、离线建库脚本。
- README 补充 embedding 模型的获取说明（LFS 提交 / 镜像下载两种路径）。
- ProblemManager 节点追加一次结构化提炼调用（与敏感性决策同款 fail-open 兜底）。
- Layer 2 modeler 节点新增 `model_search` 工具。
- 换 embedding 模型 = 必须重建向量库 + 重新提交模型文件，属于破坏性变更。
