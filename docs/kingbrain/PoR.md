KingBrain 技术实现方案

⸻

前言
	•	发布日期：2025-08-04
	•	适用环境：Kubernetes 1.32 / CRI-O 1.33，CPU x86-64 & ARM64（混合架构）
	•	锁定文件：所有组件和脚本的最终版本号与依赖关系，详见 /.collab/spec.lock.yaml。如本蓝本所列版本与锁文件冲突，以锁文件为准。
	•	符号说明：
	•	⚠️ 实验特性：上游尚在 Preview/未GA阶段，仅建议在 staging 集群或 featureGate 环境先行试用。
	•	📝 兼容提示：新旧字段、行为有差异，未迁移脚本/接口需注意适配。

⸻

L1 系统全息洞察引擎

作用与定位

整合全系统所有“事实”：目录、代码、服务、依赖、SBOM、许可证、谱系、图谱、向量库、外部API、监控面板等，统一由 Backstage 门户集中展示、聚合查询。此层为“可观测性事实的单一可信来源”。

⸻

关键依赖栈（融合修订）

组件	版本	说明/补充
Backstage	v1.41.0	最新稳定三位号，官方无 LTS 概念；upgrade_backstage.sh 固定1.41.0 Tag；plugins 兼容性由 package.json 控制
Sourcegraph	5.x	结构化代码检索与 Cody AI 深度集成
Neo4j	5.20 Enterprise	属性图查询/gds.alpha.graph.construct加速线下生成
Weaviate	1.25.x	语义向量检索，LLM/RAG接入
OpenLineage Server	1.4.1 + Marquez Web 0.39	运行级谱系与UI，Spec 1.1
Apicurio Registry	2.6	Schema/契约统一，Artifact Groups 支持
Syft	≥ 1.14	支持 SPDX 2.3, CycloneDX 1.7, OCI-attestation v0.2，⚠️ SPDX 3 由外部 sbom-tool 生成，Syft 待官方支持后升级
CycloneDX	1.7	默认输出格式，–schema-version 可降级
SPDX	3.0 Final	由 sbom-tool 生成与校验
Grype / Trivy / ScanCode / OSV.dev	最新	漏洞、许可证检测，精准到生态级CVE/SPDX
CloudEvents	v1.0 + CESQL 1.0	全链路事件采集与筛选

⸻

目录结构

保留并继承所有结构和注释，如下（结构必须完整）：

/srv/kingbrain
├── apps/
│   └── portal-backstage/
│       ├── app-config.yaml
│       └── packages/
│           ├── app/
│           ├── backend/
│           └── plugins/
│               ├── kb-neo4j-view/
│               ├── kb-openlineage/
│               ├── kb-sbom-licenses/
│               ├── kb-sourcegraph/
│               ├── kb-grafana-panels/
│               └── kb-slsa-provenance/    # 展示 Tekton Chains OCI Attestations & SLSA Provenance
├── services/
│   ├── insight-indexer/                   # 全量/增量扫描调度器
│   ├── insight-sbom-service/              # SBOM 生成/签名/下载
│   ├── insight-lineage-gateway/           # CloudEvents→OpenLineage
│   └── insight-sync-neo4j/                # 谱系/元数据同步Neo4j/Weaviate
├── scripts/
│   ├── scan_full.py
│   ├── reach_live.py
│   ├── sync_to_neo4j.py
│   ├── emb_ingest.py
│   ├── collect_and_update_meta.py
│   ├── sbom_generate.py                   # Syft 生成SBOM，默认spdx-json@2.3、cyclonedx-json@1.7，支持OCI-attestation/cosign签名，预留SPDX3.0切换
│   ├── sbom_scan_vuln.py                  # Grype/Trivy+OSV漏洞扫描
│   ├── license_scan.py                    # ScanCode解析许可证
│   ├── lineage_emit.py                    # CloudEvents→OpenLineage
│   └── exporters/
│       └── prometheus_exporter.py
│   └── kingbrain/
│       ├── init.py
│       └── utils.py
├── data/
│   ├── sbom/
│   └── lineage/
├── configs/
│   ├── cloudevents.json
│   ├── sbom-policy.yaml
│   └── registry/
│       └── apicurio.yaml
├── pkg/
│   ├── cli/
│   └── sg/
│       └── client.go
├── graphs/
├── grafana_dashboard.json
├── container_meta.db / embed_cache.db
├── Makefile / docker-compose.yml / go.mod
└── ...


⸻

关键脚本/服务职责（融合修订）
	•	insight-lineage-gateway
	•	POST /v1/lineage/events：Content-Type: application/cloudevents+json，支持Header: X-OL-Spec: 1.1，将 kb.* 事件转OpenLineage RunEvent写入Marquez并回写runId。
	•	sbom_generate.py
	•	默认用Syft ≥1.14，输出spdx-json@2.3与cyclonedx-json@1.7（--schema-version可降级）；⚠️ 当Syft官方合并SPDX3支持后，切换为SPDX3。现有SPDX3由外部sbom-tool生成。
	•	支持OCI-attestation v0.2产物，Cosign v2签名（trustedPublishing策略），产物可落MinIO/S3，Neo4j/Backstage插件同步。
	•	sbom_scan_vuln.py
	•	用Grype/Trivy扫描SBOM/镜像/FS，支持仅报可修复漏洞，忽略未修复漏洞。通过OSV API精确查询生态漏洞。
	•	license_scan.py
	•	用ScanCode解析许可证，输出SPDX3.0许可表达式，支持自定义规则、sbom-policy.yaml策略对比、输出阻断/豁免状态。
	•	Backstage Plugins
	•	kb-neo4j-view：展示Neo4j Cypher查询结果，支持分层图和Sourcegraph深链。
	•	kb-openlineage：聚合Marquez UI、Run明细与血缘，支持OpenLineage 1.1。
	•	kb-sbom-licenses：SBOM、许可证集合、漏洞、阻断（集成Kyverno/OPA策略摘要）。
	•	kb-slsa-provenance：展示Tekton Chains生成OCI-attestation与SLSA provenance。
	•	kb-sourcegraph：直链Sourcegraph搜索页，语法/模式支持。

⸻

对外/内部接口
	•	Backstage Catalog
	•	catalog-entities/*.yaml：组件/API/资源/Dataset/Pipeline/Domain，YAML遵循Backstage 1.41.0格式。
	•	SBOM 下载
	•	GET /v1/sbom/{component}/{version}?format=spdx|cyclonedx
	•	GET /v1/sbom/{component}@{digest}
	•	支持Accept: application/vnd.cyclonedx+json; version=1.7，响应含Cosign摘要/签名引用
	•	Lineage Gateway
	•	POST /v1/lineage/events，兼容CloudEvents及Header: X-OL-Spec: 1.1，写入Marquez Lineage API
	•	接口兼容
	•	全部查询/下载接口兼容最新API及官方文档字段，向后兼容。

⸻

可观测 & 告警
	•	指标：kb_insight_scan_latency_ms、sbom_generate_fail_rate、license_violation_count、lineage_event_lag_ms、catalog_sync_lag_s
	•	Alertmanager Webhook → L9；基于官方配置做路由、抑制、静默
	•	事件滞后 > 60s 连续3min 升级告警，许可证违规立即阻断
	•	Prometheus 2.53，远端写入OTLP/HTTP

⸻

失效与回退策略
	•	Marquez不可用→CloudEvents落盘data/lineage/*.json，定时重放
	•	SBOM生成失败→回退上一次有效签名SBOM，L6准入阻断
	•	许可证冲突→进入blocked队列，需法务豁免；ScanCode支持扩展规则审计

⸻

小结

L1以Backstage v1.41为“可视化与目录中枢”，聚合谱系、SBOM、合规、Neo4j/向量/监控能力。全面兼容OCI-attestation、SLSA、最新SBOM/Lineage/许可证管理，成为系统全局事实根。

⸻

L2 自省与健康监控中心

依赖栈

组件	版本 / 分支	说明/补充
OpenTelemetry Collector	0.128.0 (otel/opentelemetry-collector:0.128.0)	实现 Spec 1.34.0；processors.memory_limiter.limit_mib
Prometheus	2.53	
Grafana	11.x	
Alertmanager	0.27	
Kubecost	2.7 OSS ⚠️	BudgetPolicy Preview（需featureGate或EE≥2.7.0-ee）

⸻

目录与模块

/observability
├── otel/collector-config.yaml
├── prometheus/
├── grafana/
├── cost/kubecost-values.yaml

	•	otel/collector-config.yaml 必须写明processors.memory_limiter.limit_mib字段，check_interval推荐10s。

processors:
  memory_limiter:
    limit_mib: 4096
    check_interval: 10s


⸻

Kubecost BudgetPolicy 说明
	•	YAML示例文件顶部加experimental: true字段。
	•	⚠️ 需kubecostFeatureFlags: budgetPolicy=true或企业版≥2.7.0-ee。
	•	fallback方案：Budget API + Kyverno ScaleToZero policy。
	•	CRD示例：

apiVersion: cost.kubecost.io/v1alpha1
kind: BudgetPolicy
metadata:
  name: ml-serving-guard
spec:
  selector:
    namespace: kserve
  monthlyLimitUSD: 500
  alertThresholds: [0.7, 0.9, 1.0]
  action: "ScaleToZero"
experimental: true


⸻

健康评分服务
	•	/healthscore/
	•	算法：Score = 100 - Σ(wᵢ * metricᵢ)，权重配置迁移到OPA Bundle（Rego v1语法，bundle可签名）
	•	输出日报/周报，Webhook /v1/health/alerts自动转交L9

⸻

接口
	•	GET /v1/health/score?system=…&window=1h → {score, factors[], alerts[], suggestions[]}
	•	POST /v1/health/thresholds：权重/阈值配置，需签名，审计入L4

⸻

小结

L2将全量观测、成本治理（Kubecost AI-Forecast、BudgetPolicy CRD）融合为健康评分与行动信号，权重统一OPA Bundle，SLO与异常自动联动L9/L12。

⸻

L3 意图解析与传导控制器

依赖栈

组件	版本	说明/补充
Telegram/Discord	最新	网关输入统一为 CloudEvents
PaddleOCR	2.8	提升模型精度
Tesseract	最新	OCR备用
OpenCV	最新	图像预处理
Edge-TTS	最新	语音合成，LICENSE 链接已补充说明
NATS JetStream	2.14	Domain=kingbrain，至少一次消费、重放、对象存储
Temporal	最新	Durable Execution，长流程管控
Apicurio Registry	2.6	intents.avsc，含 priority、expiresAt 字段

⸻

目录结构

/intent
├── gateway/{telegram_bot.py,discord_bot.py,web_api.py}
├── parser/{ocr/*,confidence.py,schema/intents.avsc}
├── planner/{pseudo_code_builder.py,risk_guard.py,emit.py}
├── audit/{journal.db,replay.py}
└── configs/policy.yaml

	•	planner/pseudo_code_builder.py 支持输出 WASM module stub，跨语言沙箱执行。

⸻

事件主题（CloudEvents）
	•	kb.intent.created.v1
	•	kb.intent.plan.v1
	•	kb.intent.escalated.v1
	•	kb.intent.executed.v1

全部支持 CESQL 1.0 过滤，Go/Java SDK全覆盖。

⸻

小结

L3支持文本、OCR、TTS多模态输入，任务优先级/过期控制，指令链可回放与审计，决策链自动沉淀至L4。WASM stub为自动化跨语言任务提供安全保障。

⸻

L4 记忆与因果归因引擎

依赖栈

组件	版本	说明/补充
Neo4j	5.20 Enterprise	属性图，gds.alpha.graph.construct加速
OpenLineage Server	1.4.1 + Marquez Web 0.39	Spec 1.1
ClickHouse	24.6	时间轴存储，表分区toStartOfTenMinutes(ts)
PostgreSQL	可选	关系型补充
dbt-OpenLineage adapter	已集成	lineage/adapters/dbt-ol-adapter/ 新增
GraphQL API	/graphql/v2	支持@defer streaming，REST用于写入

⸻

目录结构

/memory
├── neo4j/{schema.cql,procedures/,queries/}
├── timeline/{migrations/,api/{rest.go,graphql/}}
└── lineage/{marquez/,adapters/,dbt-ol-adapter/}


⸻

小结

L4融合大规模属性图、时间轴、谱系、DBT事件直接映射，供全系统“事实回溯/根因定位”服务。GraphQL流式查询兼容大规模谱系溯源。

⸻

L5 偏好世界观建模器

依赖栈

组件	版本	说明/补充
OPA Core	1.4.0	默认Rego v0，Rego v1 GA（import rego.v1或features.rego_v1=true启用）；冗余flag可安全删除
OpenFGA	1.8.14	动态TLS、Check-Opt优化、Server Helm chart bump、SDK≥0.9.5，schema_version: 1.3
Keycloak	23.x	OIDC/MFA/事件/审计日志/REST Admin API

	•	OPA Bundle manifest包含mediaType: application/vnd.cncf.opa.bundle.v2+gzip
	•	worldview/switcher API 保持不变。

⸻

目录结构

/preference
├── models/worldview/{guardian.yaml,assault.yaml,learning.yaml}
├── models/opa/{rego/,bundles/}
├── models/fga/{dsl/,json/}
├── orchestrator/{compiler.py,switcher.py,audit.py}
└── api/{rest.go}


⸻

关键点
	•	POST /v1/worldview/switch 原子性切换OPA Bundle版本 + OpenFGA模型ID，广播kb.worldview.changed.v1
	•	OPA支持bundle签名、决策日志上报

OpenFGA DSL示例：

{
  "schema_version": "1.3",
  "type_definitions": [
    {
      "type": "strategy",
      "relations": {
        "owner": { "this": {} },
        "viewer": {
          "union": { "child": [ { "this": {} }, { "computedUserset": { "relation": "owner" } } ] }
        }
      }
    }
  ]
}


⸻

小结

L5统一“人格/世界观/权限”抽象、版本化与审计，OPA与OpenFGA均已切至最新主版本，支持Rego v1与bundle v2、模型schema v1.3。

⸻

L6 策略演化与版本管理引擎

依赖栈

组件	版本	说明/补充
Tekton Pipelines + Chains	0.25.1	内置cosign v2，override为可选，签名策略trustedPublishing，Rekor v1
Cosign	v2	由Tekton Chains内置
SLSA	1.2-RC1	provenance产物标准
Kyverno	1.12	VerifyImage v2alpha1，支持endorsements、publicKeys: cosign://fulcio-v2
MLflow Model Registry	2.21.2	见L7
Argo CD	2.11	OCI Helm source verifier

⸻

Tekton Chains 部署运维建议
	•	Helm values.yaml建议显式设置signer: x509，cosignVersion: v2
	•	如需fulcio v2 keyless: env.CHAIN_SIGNER_OVERRIDES=keyless-latest（⚠️实验特性，可选）
	      •	Chains 0.25.1 已内置 cosign v2；若需 Fulcio v2 keyless，可设置 env.CHAIN_SIGNER_OVERRIDES=keyless-latest（实验特性）

⸻

目录结构

/strategy
├── ci/tekton/
│   ├── pipelines/
│   ├── chains/
│   └── migrate_github.py
├── attestations/in-toto/*.jsonl
├── policies/
│   ├── slsa-requirements.md
│   ├── kyverno/
│   ├── gatekeeper/
│   └── policyset-slsa-v1.2.yaml
└── api/rest.go


⸻

核心流程
	1.	PR合入→Tekton Pipeline构建/回测→Chains生成SLSA provenance+in-toto attestation（.intoto.jsonl），签名并登记Rekor v1。
	2.	发布前，Kyverno verifyImages在Admission阶段校验签名与断言（endorsements支持keyless/issuer，predicateType强校验）。
	3.	Gatekeeper/Policyset校验SLSA版本与provenance。
	4.	通过Argo CD 2.11部署，支持OCI Helm source签名校验。

⸻

Kyverno verifyImages/endorsements 示例

endorsements:
  - keyless:
      subject: https://github.com/kingbrain/*
      issuer: https://token.actions.githubusercontent.com
    attestations:
      - predicateType: https://slsa.dev/provenance/v1
        sourceSpec:
          uri: git+https://github.com/kingbrain/repo@refs/heads/main
          digest:
            sha1: "{{ .image.digest }}"


⸻

小结

L6集成Tekton Chains、Cosign、SLSA v1.2、Kyverno endorsements准入，所有attestation与关键签名策略均给出标准配置建议。默认用Sigstore v1，v2为可选实验，所有关键流程、目录、策略全量融合。

⸻

L7 模型训练与生命周期调度中心

依赖栈

组件	版本	说明/补充
Ray Train	2.48.0	内置CheckpointManager，GPU DV-switch
Feast	0.44.0	Vector Similarity API v2，feature_store.yaml bumped
MLflow	2.21.2	Helm chart推荐 image.tag: 2.21.2（mlflowTags弃用）
KServe	0.15.0	deploymentMode: RawDeployment支持
BentoML	1.4	bentoml build –build-ctx，移除dockerfile_template
Knative Serving	1.15.2	activator HA默认启用

⸻

目录结构

/ml
├── pipelines/
│   ├── train_ray.py
│   ├── evaluate.py
│   ├── register_mlflow.py
│   └── deploy_kserve.py
├── features/feast/feature_store.yaml   # apiVersion: feast.dev/v2beta
├── serving/
│   ├── kserve/
│   │   └── llm-infer.yaml             # deploymentMode: RawDeployment
│   └── bentoml/
├── governance/
│   ├── privacy_policies.yaml
│   ├── lineage_emit.py
│   └── attestations/
│       └── model_slsa_v1.2.intoto.jsonl


⸻

关键点
	•	KServe支持RawDeployment和Serverless(HPA/KEDA)两种伸缩模式，HF dtype能力与调优
	•	Feast Vector Similarity API v2，支持LLM/RAG应用
	•	MLflow支持新Diff UI与原生Delta Lake表版本
	•	Ray Train新版内置CheckpointManager，分布式断点恢复
	•	BentoML多模型/多框架统一流水线
	•	所有模型溯源证明（SLSA provenance/attestation）均存于/ml/governance/attestations/

⸻

接口
	•	POST /v1/train/run：body新增checkpoint_uri、vector_index字段，便于分布式训练/召回
	•	GET /v1/inference/status/{modelName}：返回scalingPolicy（Raw, Serverless）字段，兼容KServe 0.15.0

⸻

小结

L7打通训练、评估、注册、推理全链路流水线，provenance/attestation纳入治理闭环，全面兼容大模型分布式训练与推理最新能力。Helm chart运维建议已收录。

⸻

L8 代码改写与持续交付控制器

依赖栈

组件	版本	说明/补充
OpenRewrite	Recipe-Bundle 8.2	支持 Java 21、YAML visitor
Comby	最新	结构化跨语言替换
Sourcegraph + Cody	5.x	结构化搜索、AI 批量重构/修复
Semgrep	1.40.0	Go generics 误报修复
Gitleaks	最新	密钥扫描
Trivy	最新	镜像、依赖、IaC 漏洞与配置扫描
Argo CD	2.11	OCI Helm source verifier
Kyverno	1.12（VerifyImage v2alpha1）	支持 endorsements、attestations、cosign://fulcio-v2
Tekton Trigger	0.25.1	migrate_github.py 支持 GitHub Actions→Tekton Pipeline 自动迁移

⸻

目录结构

/strategy
├── ci/tekton/
│   ├── pipelines/
│   ├── chains/
│   └── migrate_github.py
├── policies/
│   ├── slsa-requirements.md
│   ├── kyverno/
│   ├── gatekeeper/
│   └── policyset-slsa-v1.2.yaml


⸻

Kyverno VerifyImage 示例

apiVersion: kyverno.io/v2alpha1
kind: VerifyImage
metadata:
  name: verify-slsa-attest
spec:
  images:
    - "ghcr.io/kingbrain/*:*"
  attestations:
    - predicateType: https://slsa.dev/provenance/v1
      verifier:
        publicKeys: cosign://fulcio-v2


⸻

关键点
	•	所有 CI/CD 变更、代码重构、批量修复都走审计/回溯闭环
	•	所有容器、镜像变更都需 attestation（SLSA provenance）与签名校验
	•	兼容 Tekton/Argo CD 最新主流 OCI 签名、准入门槛
	•	GitOps 同步，所有策略版本化/回滚可溯

⸻

小结

L8 使代码安全、重构、交付全生命周期受强约束，所有变更均自动进入审计与准入闭环。

⸻

L9 执行调度与自愈编排引擎

依赖栈

组件	版本	说明/补充
Temporal Server	1.27.2 LTS	Multi-Region Namespace GA，KEDA auto-scaler，Go SDK 1.25.0，StartDelay 字段新特性
Rundeck OSS	5.0.1	Pipeline GUI，Vault KVv2 插件，日志捕获增强
StackStorm	最新	ChatOps 支持
Chaos Mesh	2.7.2	Web UI 监控插件，Workflow DSL v1.2 已于2025-05-15 GA
Falco	0.41.0	eBPF CO-RE 加速，K8s Audit v1.29 支持

⸻

目录结构

/orchestrator
├── temporal/{workflows/,activities/}
│   └── workflows/heal.go
├── runbooks/{rundeck/{jobs/,plugins/,rules/},stackstorm/{packs/,rules/}}
├── chaos/experiments/
├── detectors/falco/
└── reporters/grafana_markdown.py


⸻

自愈闭环流程
	1.	Falco 规则命中→生成 kb.alert.created.v1（CloudEvents）
	2.	Temporal Workflow HealRunbook picks（StartDelay 字段支持延迟调度）→ 调 Rundeck Job
	3.	Rundeck Job 完成→emit kb.heal.executed.v1（CloudEvents）
	4.	L2 Health score 自动刷新，形成观测—执行—反馈—回放闭环

Go SDK 代码示例：

wfOptions := workflow.Options{
  TaskQueue: "kb-heal",
  StartDelay: time.Minute*2, // 新增字段，支持延迟启动自愈任务
}


⸻

关键接口
	•	POST /v1/heal/execute：输入 Alert/Severity/Runbook，启动 Temporal 流程，完成后生成 Markdown 报告 URL
	•	Rundeck Log Filter 支持 Key/Value、JSON jq 提取，变量可在后续步骤复用

⸻

小结

L9 层打通全观测自愈链路，所有动作可延迟、回放、溯源、复盘，混沌、威胁检测与多种调度方式统一编排，面向大规模场景与强安全需求。

⸻

L10 资源弹性与成本治理引擎

依赖栈

组件	版本	说明/补充
Kubecost	2.7 OSS	AI-Forecast、Savings Action API GA，BudgetPolicy Preview 需 kubecostFeatureFlags: budgetPolicy=true 或企业版≥2.7.0-ee
BudgetPolicy CRD	v0.1-preview  	⚠️ 实验
KServe/Knative/HPA/Cluster-autoscaler	最新	资源伸缩联动，预算越界自动触发 L9 降级

⸻

目录结构与 CRD 示例

/cost
└── kubecost-values.yaml

BudgetPolicy CRD 示例：

apiVersion: cost.kubecost.io/v1alpha1
kind: BudgetPolicy
metadata:
  name: ml-serving-guard
spec:
  selector:
    namespace: kserve
  monthlyLimitUSD: 500
  alertThresholds: [0.7, 0.9, 1.0]
  action: "ScaleToZero"
experimental: true

	•	fallback：Budget API + Kyverno ScaleToZero policy

⸻

关键点
	•	预算/成本告警直接通过事件桥接：kb.cost.threshold.v1
	•	Kubecost指标与优化建议全自动集成至观测、门户
	•	与L7/L9自动联动，支撑多租户与成本敏感场景

⸻

小结

L10 把“成本”作为决策一等指标，与SLO/SLA并列，治理能力增强。BudgetPolicy CRD为实验特性，建议关注版本与featureGate，生产环境优先保障稳定。

⸻

L11 安全审计与权限动态中心

依赖栈

组件	版本	说明/补充
Keycloak	23.x	SSO/MFA/事件与审计日志/REST Admin API
OpenFGA	1.8.14	Check-OPT endpoint, 延迟<15ms P95, 动态TLS, 版本化审计
OPA	1.4.0	默认 Rego v0；Rego v1 GA，可通过 import rego.v1 或 --set=features.rego_v1=true 启用（CVE-46569 已修复）
Kyverno	1.12	verifyImages策略与PolicyReport事件桥
Falco	0.41.0	eBPF安全检测，K8s/容器/主机威胁感知
PolicyReport→CloudEvents 转换	-	/security/emit_policy_report.py 自动桥接事件

⸻

关键点
	•	所有安全、策略评估结果都作为CloudEvents事件写入审计与溯源（L4归档）
	•	OpenFGA Check-OPT提升高并发鉴权性能
	•	Kyverno策略异常直接联动Portal与L9/L12

⸻

小结

L11 实现“身份→授权→签名→运行时”闭环，所有决策/拦截动作全可追溯，自动桥接事件流与溯源链。

⸻

L12 意识态切换与多模态接口引擎

依赖栈

组件	版本	说明/补充
CloudEvents	v1.0	切换广播与过滤
CESQL	v1.0	CloudEvents SQL过滤
pkg/mode_switch/hotwords.go	最新	支持读取worldview/*/keywords.txt，Weaviate过滤，LLM Prompt热插拔
Mode API	-	POST /v1/mode/switch 增加 header X-Consistency: strong，两阶段提交/回滚安全
Backstage	v1.41.0	kb-mode-status 插件展示OPA Bundle版本、FGA Model ID、Persona SLO、切换历史

⸻

目录结构

/pkg/mode_switch/hotwords.go
/worldview/guardian/keywords.txt


⸻

接口
	•	POST /v1/mode/switch：支持 X-Consistency: strong header，返回所有Bundle、Model版本及生效时间
	•	GET /v1/mode/status：返回当前人格/OPA/Bundle版本、FGA模型、阈值摘要

⸻

CloudEvents样例

{
  "id": "3eef0c9e-b3da-458d-9fb0-8357b3a4c214",
  "type": "kb.worldview.changed.v1",
  "source": "kingbrain://preference/switcher",
  "time": "2025-08-02T09:17:48Z",
  "datacontenttype": "application/json",
  "data": {
    "mode": "guardian",
    "bundleVersion": "2025.08.02-12",
    "fgaModelId": "02dea09e-...",
    "thresholdsHash": "sha256:abcd...",
    "initiator": "user:sysadmin",
    "effectiveAt": "2025-08-02T09:18:00Z"
  }
}


⸻

小结

L12 将人格/世界观/权限状态切换变成强事务、可追溯的事件闭环，全部能力门户可见，支持多模态交互与自动联动。

⸻

Backstage 门户插件导航（落地指引）
	1.	Insight Map（L1）：Neo4j全景、Sourcegraph深链、SBOM/许可证部件、Tekton/SLSA Provenance视图
	2.	Health & Score（L2）：SLO达成率、TOP告警、成本预测（Kubecost）
	3.	Intent Bridge（L3）：会话/置信度分布/升级队列、CloudEvents回放（CESQL过滤）
	4.	Memory & Causality（L4）：时间轴/根因链路、Marquez血缘嵌入、DBT事件
	5.	Worldview & Access（L5/L11）：OPA Bundle版本/签名校验、OpenFGA模型ID、决策日志与安全事件
	6.	Strategy & SLSA（L6）：Tekton Chains SLSA Provenance、Cosign验证、Kyverno准入统计、policyset校验
	7.	Train & Deploy（L7）：Ray训练、MLflow Registry、KServe/Bento部署与指标、模型provenance/attestation
	8.	Codecraft & GitOps（L8）：OpenRewrite/Comby报告、Semgrep/Gitleaks/Trivy发现、Argo CD漂移
	9.	Self-Healing（L9）：Temporal工作流轨迹、Rundeck作业、Falco/Chaos Mesh事件
	10.	Cost Governance（L10）：Kubecost预算/预测/建议、BudgetPolicy
	11.	Persona & Mode（L12）：kb-mode-status插件展示OPA/FGA/Persona历史及即时切换

⸻

统一事件与契约（样例）
	•	CloudEvents 1.0 全统一封装，CESQL 1.0可过滤。
	•	kb.worldview.changed.v1
data: {mode, bundleVersion, fgaModelId, thresholdsHash, initiator, reason, effectiveAt}
	•	kb.strategy.promoted.v1
data: {artifactRef, from, to, chainsProvenanceRef, cosignDigest, decision}（chainsProvenanceRef 指向 Tekton Chains生成的.intoto.jsonl）
	•	kb.heal.executed.v1
data: {workflowId, steps[], success, mttrSeconds, rollback, reportUrl}
	•	kb.cost.threshold.v1
data: 预算策略/阈值触发，自动桥接 L9 降级

⸻

首批 SLO / 告警模板

指标	触发阈值	动作
opa_bundle_sync_lag_s	> 30 s 连续 3 次	Warn → L9 自愈
fga_check_p95_ms	> 15 ms 或 5xx 升高	Warn
attestation_verify_fail	> 0	阻断发布
Kubecost BudgetPolicy	≥ 0.9 阈值	触发 kb.cost.threshold.v1 → L9降级
KServe backlog	超动态阈值	切scalingPolicy→Raw/ScaleToZero

⸻

数据与证据留存规范

数据类型	保留期限	存储介质
Prometheus TSDB	90 d	Long-term bucket
Marquez/OpenLineage Run	≥ 90 d	Export JSON
SBOM & Attestations	≥ 90 d	MinIO/S3 + Rekor
OPA/OpenFGA决策日志	90 d	ClickHouse
Kubecost/Cost CRD/事件	≥ 180 d	PostgreSQL

⸻

参考来源（部分引用）
	•	Backstage Releases v1.41.0
	•	OpenTelemetry Collector 0.128.0 & Spec 1.34
	•	CycloneDX 1.7 GA (2025-05-20)
	•	Syft Roadmap Issue #1970 (SPDX 3 support)
	•	Kubecost BudgetPolicy Proposal PR (draft, 2025-06-12)
	•	Tekton Chains 0.25.1 go-mod bump to cosign/v2
	•	其余：见正文段落逐项所用上游开源项目。

⸻