# Test Gap Scoring & Explanation（Member 5）契约 v0.2

> 状态：**已实现**（2026-10-30）。v0.1 是设计稿，v0.2 是跑通真实链路后回写的版本。
> 对应代码：`gap-scoring/`（`loader` / `join` / `weights` / `scoring` / `confidence` / `explain` / `report` / `gap_scoring` / `pipeline`）
> 测试：200 个，`python -m unittest discover -s gap-scoring/tests`

---

## 0. v0.1 → v0.2 的六处修正

设计稿里有几条假设，跑真实数据时被推翻了。记在这里，避免队友按旧版理解代码：

| # | v0.1 的写法 | 实际 | 影响 |
| --- | --- | --- | --- |
| 1 | D 输出 Level 0–3 | D 输出 5 个 `status`，没有等级 | 需要映射表，`weak_assertions` 落在 **2.5** |
| 2 | `weak_assertions` 比"没断言"更差 | 恰好相反，会导致排序反转 | 已改为 2.5，加了单调性测试 |
| 3 | 直接信任 D 的 `assertion_evidence_found` | D 会乐观上报 | 新增证据复核（§5），payment 样例 0 → 43.55 |
| 4 | `entity_key` 用 `file_path::name@line` 贯穿 | C/D 并不都提供这个键 | 改为四条 join 规则（§2.3） |
| 5 | A 的 `file_path` 可直接给 D | **不能**，必须是模块相对路径 | 新增 `module_path`，否则 D 静默返回"没有测试" |
| 6 | 只讲分数 | 分数高不代表可信 | 新增置信度（§6），与分数正交 |

---

## 1. 模块定位

```
GitHub PR
   │
   ▼
A  PR / Git Change Analyzer ──output.json──▶  B  Behavioral Change Detector
   │                                              │
   │                                              ▼
   └──────────────────────────────────────▶  C  Test Dependency Mapper
                                                  │
                                                  ▼
                                             D  Test Adequacy Analyzer
                                                  │  status + evidence[]
                                                  ▼
                                        ★ E  Test Gap Scoring & Explanation
                                                  │  分数 + 置信度 + 人话
                                                  ▼
                                             F  GitHub PR Reporter
```

**一句话职责**：把「改动有多危险」和「测试覆盖得有多到位」合成一个**可解释、带置信度**的缺口分数。

### E 不做的事

| 不做 | 理由 |
| --- | --- |
| 重新解析 diff / AST | A 已算好实体、行号、类别 |
| 判断行为类型 | B 的职责 |
| 查 testmon 或跑 coverage | C 的职责 |
| 判断断言强弱 | 原则上属 D；E 只做**复核**（§5），不重写 D 的 AST 分析 |
| 自动生成测试代码 | 会让项目漂成 AI Test Generator；只给**输入建议** |

E 是纯函数式的聚合层：给定四份 JSON，产出确定的结果，不联网、不执行被测代码。

---

## 2. 输入与连接

### 2.1 四份输入

字段细节见 [`field-map.zh.md`](field-map.zh.md)，这里只列 E 实际依赖的部分：

| 来源 | 关键字段 | 用途 |
| --- | --- | --- |
| A `output.json` | `changes[]`：`file_path`、`entity_name`、`change_category`、`line_start/end` | 变更重要度、实体标识 |
| B `behavior.json` | `behavior_changes[]`：`type`、`function`、`file_path`、`new_line`、类型专属字段 | 行为风险、解释素材 |
| C `mapping_result.json` | `changes[].status`（matched / file_fallback / needs_new_tests / unresolved / skipped_documentation）、`tests[]`、`selection_complete` | 可达性、置信度 |
| D `adequacy.json` | `results[].status`、`evidence[]`（含 `strength`、`assertion`、`calls`）、`recommendations[]` | 覆盖等级、证据复核、解释依据 |

### 2.2 版本校验

| 输入 | 期望 | 不符时 |
| --- | --- | --- |
| A | `schema_version="2.0"` 且 `mode="pr_ast"` | **报错退出**（diff 报告是 1.0，结构完全不同） |
| B | `schema_version="1.0"` | 警告，继续 |
| C | `"1.0"` + `mode="test_dependency_mapping"` | 报错退出 |
| D | `"1.0"` + `mode="test_adequacy_analysis"` | 报错退出 |

A 是必需输入；B/C/D 缺失时降级（§8）。

### 2.3 四条 join 规则

| # | 连接 | 键 | 说明 |
| --- | --- | --- | --- |
| J1 | A.change ↔ C.change | `(file_path, entity_name, line_start)`；失败则 `(file_path 后缀匹配, entity_name)` 且要求唯一 | C 原样回显 A 的对象 |
| J2 | B.behavior ↔ D.result | `(type, function, old_line, new_line)` | **刻意忽略 `file_path`**：它是 B→D 之间注入的 |
| J3 | A.change ↔ B.behavior | **短名**相等 + `file_path` 后缀匹配 + 行号窗口 | 唯一的启发式；一对多 |
| J4 | C.tests ↔ D.evidence | `::测试名` 相等 + 路径后缀 | 两侧根目录不同 |

**J3 的两个坑**（都踩过）：

1. B 的 `function` 是**限定名**（`_CollectErrors.raise_any`），A 的 `entity_name` 是**短名**（`raise_any`）——必须取最后一段比较，否则所有方法都匹配不上（实测 7 个变更只匹配 1 个）。
2. 一个实体可能对应**多条**行为变化（一个函数改了 3 处 → 1 条 A change + 3 条 B change）。

**J3 的分配是全局的，不是逐个 change 贪心**：每条行为记录只归一个 change；若两个 change 同样匹配，则不归属任何一方并记 `join.ambiguous_behavior`。逐 change 贪心会把同一条行为复制到每个同名函数上，分数被重复计算且不报错。

匹配强度（进置信度）：

```
exact         文件匹配 + 行号落在窗口内
line_window   只有一边有路径，或只有行号落在窗口内
function_only 两边都没有路径，只能靠函数名
```

### 2.4 覆盖率检查

C 存在时计算 `mapped / total`。低于 0.5 记 `join.low_coverage`：

> only 1/7 changes have a mapping entry; A and C may describe different revisions

这是防"把两个不同 PR 的报告拼在一起"的保险丝——实测能挡住，且不会崩。

---

## 3. 打分模型

### 3.1 公式

```
Base  = 100 × (0.45 × BehaviorRisk × ChangeImportance + 0.55 × AdequacyDeficit)
Bonus = Σ 命中规则（上限 15）
Gap   = min(100, Base + Bonus)
```

权重 0.45 / 0.55 是为了让契约里的参考案例落在 79（加规则后 87）。改校准只动 `weights.py`，不动公式。

### 3.2 三张因子表

**ChangeImportance**（来自 A 的 `change_category`，为空时退回 `change_type`）

| 类别 | 值 |
| --- | --- |
| `Signature change` | 1.00 |
| `Logic change` | 0.90 |
| `add` | 0.70 |
| `delete` | 0.60 |
| `rename` | 0.30 |
| `Documentation change` | 0.00（直接跳过） |

**BehaviorRisk**（来自 B 的 `type`）

| 类型 | 值 |
| --- | --- |
| `EXCEPTION_BEHAVIOR_CHANGE` | 1.00 |
| `BOUNDARY_CHANGE` | 0.85 |
| `NEW_BRANCH` / `MODIFIED_BRANCH` | 0.70 |
| `DELETED_BRANCH` | 0.60 |
| `RETURN_BEHAVIOR_CHANGE` | 0.60 |
| 未知类型 | 0.70（兜底） |

**AdequacyDeficit**（由 §4 判定的等级查表）

| Level | 含义 | Deficit |
| --- | --- | --- |
| 0 | 没有测试到达 | 1.00 |
| 1 | 有相关测试但没到达改动 | 0.85 |
| 2 | 执行到改动但没断言 | 0.70 |
| 2.5 | 有断言但观测不到改动效果 | 0.35 |
| 3 | 有针对性断言 | 0.00 |

> 数值必须**随等级严格递增而递减**，`tests/test_scoring.py::Ordering` 会守住。`weak_assertions` 曾在 v0.1 里被放到 1.5，导致"有弱断言"比"完全没断言"分还高，已修正。

### 3.3 规则加成

| 规则码 | 分 | 触发条件 |
| --- | --- | --- |
| `exception_without_raises_test` | +5 | 异常行为变了，且等级 < 3 |
| `boundary_without_boundary_test` | +4 | 边界值变了，且等级 < 3 |
| `executed_without_assertion` | +3 | D 状态是 `no_matching_assertion` 或 `weak_assertions` |
| `related_tests_do_not_reach` | +4 | C 说 matched，但等级 ≤ 1 |
| `new_code_without_baseline` | +3 | C 状态 `needs_new_tests` |
| `file_level_fallback` | +2 | C 状态 `file_fallback` |

合计上限 15。

### 3.4 抑制规则

**等级 3 且无规则命中 → 分数归零**（band `NONE`，不进报告）。

理由：覆盖率满分时残余的只是结构性风险，而结构性风险已经由 `ChangeImportance` 表达，它不是"测试缺口"，测试也关不掉。

### 3.5 分档

| 分数 | 档位 | 建议动作 |
| --- | --- | --- |
| ≥ 80 | `HIGH` | 阻断合并（F 可用 `--fail-on-high`） |
| 50–79.99 | `MEDIUM` | 提示，需人工说明 |
| 20–49.99 | `LOW` | 仅记录 |
| < 20 或被抑制 | `NONE` | 不进报告 |

### 3.6 参考案例（回归基准）

`src/payment.py::withdraw`：异常分支新增、`Logic change`、执行到但无断言

```
Base  = 100 × (0.45 × 1.00 × 0.90 + 0.55 × 0.70) = 79.00
Bonus = 5（异常）+ 3（执行无断言） = 8
Gap   = 87.00 → HIGH
```

由 `tests/test_scoring.py::ReferenceCase` 锁定。

---

## 4. 覆盖等级判定

```
D 的 status ──映射──▶ 等级 ──复核──▶ 最终等级
```

| D status | 初始等级 |
| --- | --- |
| `no_candidate_test` | 0 |
| `no_candidate_test` + C 的 `matched` | 1（相关测试存在但没到达） |
| `no_matching_assertion` | 2 |
| `weak_assertions` | 2.5 |
| `assertion_evidence_found` | 3 |
| `unsupported_behavior` / 未知 | 不评分 |

D 缺失时按 **Level 2** 假设，并标记 `assumed_level`。

---

## 5. 证据复核（E 相对 D 的增量）

D 的 `assertion_evidence_found` 只说明"语法上存在匹配的断言"，不说明这个断言能观测到改动。三条复核规则：

| 条件 | 处理 | 原因码 |
| --- | --- | --- |
| 所有 `evidence[].strength` 都是 `weak_or_mismatched` | 降为 **2.0** | `all_evidence_weak` |
| 有 `potential_match`，但断言参数全是整数，且变更改的是 `round()` 精度 | 降为 **2.5** | `precision_blind_evidence` |
| `evidence[]` 为空（声称有证据却没列出） | 等级不变，标不确定 | `no_evidence_records` |
| 参数里出现浮点数 | 不降级（浮点能区分精度） | — |

**实测效果**（payment 样例）：

| | 复核前 | 复核后 |
| --- | --- | --- |
| 等级 | 3 | 2.5 |
| 分数 | **0（被抑制）** | **43.55（LOW）** |
| 报告 | 不出现 | 出现，并说明"整数输入区分不了新旧实现" |

> 这是 E 唯一"比 D 更严格"的地方，也是 related work 里工业界 TGA 通常不做的一层。

---

## 6. 置信度

置信度与分数**正交**：分数说"缺口多大"，置信度说"这个判断有多可信"。

罚分制，0 分 HIGH、1 分 MEDIUM、≥2 分 LOW：

| 警号 | 罚分 | 触发 |
| --- | --- | --- |
| `no_adequacy` | 2 | 缺 D |
| `assumed_level` | 1 | 等级是假设的 |
| `no_mapping` | 1 | 缺 C |
| `selection_incomplete` | 1 | C 的 `selection_complete=false` |
| `file_fallback` | 1 | C 退化为文件级 |
| `mapping_unresolved` | 1 | C 该条 unresolved |
| `weak_join` | 1 | J3 只能靠函数名 |
| `uncertain_evidence` | 1 | D 声称有证据但没列出 |
| `input_incomplete` | 1 | D 的 `input_complete=false` |

**实测**：

| 输入 | 分数 | 档位 | 置信度 | 解读 |
| --- | --- | --- | --- | --- |
| 四份齐全 | 43.55 | LOW | HIGH | 结论可信 |
| 缺 D | 62.80 | MEDIUM | LOW | 分数更高但不可信，**不能当"更危险"读** |

> 缺 D 时分数反而更高（Level 2 假设的 deficit 0.70 > 复核后的 0.35）。没有置信度的话，这个数会被误读。

---

## 7. 输出契约

### 7.1 JSON

```json
{
  "schema_version": "1.0",
  "module": "test_gap_scoring",
  "pr": {"repo": "owner/name", "number": 1, "base_sha": "...", "head_sha": "..."},
  "inputs": {"behavior": "loaded|missing", "mapping": "...", "adequacy": "..."},
  "summary": {
    "changes_total": 7, "changes_scored": 3, "changes_unscored": 4,
    "gaps_high": 1, "gaps_medium": 2, "gaps_low": 0, "gaps_none": 0,
    "average_score": 86.2, "max_score": 100.0,
    "reported_gaps": 3, "mapping_coverage": 0.0, "orphan_behaviors": 0
  },
  "gaps": [{
    "entity_key": "src/flask/helpers.py::raise_any@676",
    "file_path": "src/flask/helpers.py", "entity_name": "raise_any", "entity_type": "method",
    "line_start": 676, "line_end": 679,
    "gap_score": 100.0, "risk": "HIGH", "confidence": "MEDIUM",
    "confidence_signals": ["no_mapping"],
    "components": {"behavior_type": "...", "behavior_risk": 1.0, "change_importance": 0.9,
                   "adequacy_level": 0.0, "adequacy_deficit": 1.0, "assumed_level": false,
                   "downgraded": null, "uncertain": null, "join_method": "line_window",
                   "confidence": "MEDIUM", "confidence_signals": ["no_mapping"],
                   "base": 95.5, "bonus": 5.0, "rule_hits": ["exception_without_raises_test"]},
    "rule_hits": ["exception_without_raises_test"],
    "affected_tests": ["test_checkout_basic"],
    "explanation": {
      "one_liner": "Potential test gap in `raise_any` (HIGH, 100/100)",
      "changed_behavior": "exception behaviour added: `BaseExceptionGroup`",
      "problem": "no test reaches this change",
      "suggested_test_input": "an input that makes the new `BaseExceptionGroup` branch run",
      "evidence": [{"kind": "changed_entity", "detail": "raise_any (Logic change)",
                    "file_path": "...", "line": 676, "test": null}]
    }
  }],
  "unscored": [{"entity_key": "...", "file_path": "...", "entity_name": "...", "reason": "no_behavior_change"}],
  "problems": [{"level": "warning", "code": "behavior.missing_file_path", "message": "...", "context": "..."}],
  "sources": {"changes": "...", "behavior": "...", "mapping": "...", "adequacy": "..."}
}
```

约定：

- `gaps` 按 `gap_score` 降序、同分按 `entity_key` 稳定排序（可复现）。
- `risk = NONE` 的变更默认不进 `gaps`（`--include-none` 可打开）。
- `unscored` 必须写清 `reason`，禁止静默丢弃。
- `inputs` 让下游立刻知道这是"四份齐全"还是"缺 D 硬估"。

### 7.2 命令行

```bash
python gap_scoring.py --changes output.json \
  [--behavior behavior.json] [--mapping mapping_result.json] [--adequacy adequacy.json] \
  [-o report.json] [--format json|markdown] [--top N] [--include-none] \
  [--fail-on-high] [-v]
```

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 未预期异常 |
| 2 | 输入/用法错误（文件缺失、schema 不符、输出不可写） |
| 3 | 门禁失败（`--fail-on-high` 且存在 HIGH） |

退出码 2 与 3 必须分开：**输入错误要人修文件，门禁失败要人补测试**。

### 7.3 Markdown 评论

头部给汇总（变更数、三档计数、平均分、映射覆盖率、未评分数），随后按分数列出前 N 条，每条 ≤ 5 行；超出部分写"还有 N 条"，不静默丢弃。

---

## 8. 降级矩阵

| 缺什么 | 行为 | 置信度 |
| --- | --- | --- |
| 缺 B | 该实体不评分（`no_behavior_change`） | — |
| 缺 C | 按 D 的等级打分，无 reachability | `no_mapping` → 多为 LOW |
| 缺 D | 假设 Level 2 | `no_adequacy` + `assumed_level` → LOW |
| C 的 `selection_complete=false` | 照常打分 | 降一级 |
| D 的 `input_complete=false` | 照常打分 | 降一级 |
| A 缺失或 schema 不符 | **直接报错** | — |

### 8.2 复用上游产物（真实 PR 的实际用法）

真实 PR 上，**C 往往跑不了**：它需要被分析项目自己的 git 历史（`--base`）和在该项目上采集的 testmon 数据库，而这两样通常不在我们的仓库里。同理 B 需要 A 下载的源码镜像。

所以 `run_pipeline.py` 允许把任一阶段的产物直接喂进来：

```bash
python run_pipeline.py --repo-dir ../CS5351-TestPilot --workdir ./gap-work \
  --changes   artifacts/output.json \
  --behavior-file  artifacts/behavior.json \
  --mapping-file   artifacts/mapping_result.json \
  --adequacy-file  artifacts/adequacy.json
```

| 参数 | 作用 | 典型场景 |
| --- | --- | --- |
| `--changes` | 复用 A 的报告（不联网） | 已有 PR 分析结果 |
| `--behavior-file` | 复用 B 的报告 | 没有源码镜像 |
| `--mapping-file` | 复用 C 的报告 | **没有 testmon 数据库** |
| `--adequacy-file` | 复用 D 的报告 | D 的测试根与被分析项目不一致 |

复用某个阶段时，命令行不会执行它，也不会覆盖该文件；`run_pipeline.py` 的状态表会显示 `reused`。

**约束**：复用的 `mapping_result.json` 里的 node ID 根（如 `mapping-demo/test_payment.py::...`）必须和 D 的 `--test-root` 对得上，否则 D 会找不到测试。这一点无法自动校验，只能靠调用方保证。

### 8.3 Scope 分级

对应讨论里的 MUST/SHOULD/COULD：

| 级别 | 内容 |
| --- | --- |
| Must | 只用 A+C 出二值判断 + 一句话解释 |
| Should（已实现） | 完整三因子 + 规则 + 证据复核 + 置信度 + JSON/Markdown |
| Could | 可视化、HTML、LLM 生成解释 |

---

## 9. 验收

`tests/test_scenarios.py` 对 6 个场景做黄金比对，预期值**手写**在 `fixtures/scenarios/expected.json`：

| 场景 | 分数 | 档位 | 置信度 |
| --- | --- | --- | --- |
| 精度改动、整数断言 | 43.55 | LOW | HIGH |
| 新增异常、无测试到达 | 96.25 | HIGH | HIGH |
| 边界改动、执行无断言 | 79.92 | MEDIUM | HIGH |
| 新分支、已被断言 | 0.00 | NONE | HIGH |
| 仅改文档 | 0.00（不评分） | NONE | HIGH |
| 映射无法解析 | 79.30 | MEDIUM | LOW |

另外：

- `tests/test_scoring.py::ReferenceCase` 锁定 87/100 基准。
- `tests/test_scoring.py::Ordering` 锁定"证据越好分数越低"的单调性。
- 全部测试 200 个，含 CLI 端到端、报告结构、措辞守卫。

---

## 10. 已知限制

1. **C 的 testmon 数据库常缺失**，此时可达性完全依赖 D，置信度多为 LOW。
2. **证据复核只覆盖精度盲区**一种"看得见但看不穿"的模式；弱断言只按 D 的 `strength` 判断，不做语义分析。
3. **J3 仍是启发式**：同名函数跨文件时依赖路径后缀，重命名后可能失配。
4. **置信度是罚分相加**，没有概率含义，不能与分数相乘。
5. **只支持 Python**：D 的断言抽取基于 Python AST。
6. **不执行任何测试**：所有结论都是静态证据，`runtime_*_proven` 恒为 false。

---

## 11. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-10-30 | v0.1 | 设计稿：公式、规则、D 的 Level 0–3、87/100 基准 |
| 2026-10-30 | v0.2 | 回写实现：Level 2.5、证据复核、置信度、四条 join 规则、`module_path`、CLI/JSON 契约、六场景验收；§0 列出六处修正 |
