# /.collab/aicollab-rules.md

## 1️⃣ 五阶段交互协议  
| 阶段 | Chat 指令 | 主要产物 | 存储路径 |
|------|-----------|----------|----------|
| ACK | `/kb ack <task>` | POR-ACK.md | /docs/kingbrain/ACK/ |
| PLAN | `/kb plan <task>` | PLAN.md | /docs/kingbrain/PLAN/ |
| BORROW | `/kb borrow <task>` | BorrowedArtifacts.yaml, Sources.md | /docs/kingbrain/BORROW/ |
| DIFF | `/kb diff <task>` | *.patch, Evidence.zip | /docs/kingbrain/DIFF/ |
| CR | `/kb cr <task>` | CR-<id>.yaml | Pull Request description |

> 执行脚本 `kb stage <phase> <task>` 会自动生成目录并 push。  
> 若未部署 Temporal workflow `ai_orchestrator`，则需人工执行上述指令。

## 2️⃣ AOC 七项硬性条目  
1. **PoR / Spec 对齐声明**  
2. **来源与许可证清单**  
3. **因果映射**（改动 ↔ 层级 ↔ 事件）  
4. **差异最小原则**（统一 diff / 文件级增量）  
5. **证据包摘要**（SBOM、漏洞、Kyverno、attestations）  
6. **回滚路径**（镜像 digest / git tag）  
7. **后置验证**（Prometheus、Marquez、OPA 决策日志）

> Reviewer-AI 发现缺任一项 → 自动退回 Builder-AI，最多重试 2 次。

## 3️⃣ 防幻觉三件套  
- **RAG 引证强制**  
- **离线 kb-validate** （见下）  
- **沙箱事件回放**  

---

首次推送仓库时，请确保 `/srv/kingbrain` 全部已实现代码已一并提交。
