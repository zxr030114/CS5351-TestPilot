# E 模块输入字段对照表

> 用途：E（Test Gap Scoring）要消费 A/B/C/D 四份 JSON，本表逐字段列出**真实结构**（不是设计稿），并标出 E 用哪些、怎么用。
> 数据来源：2026-10-30 实测产物（`pallets/flask#6168` 的 A 输出 + `fixtures/` 里的 B/C/D 输出）。
> 维护：字段随上游变化时同步更新本文，并在 `TASKS.zh.md` 变更记录里记一笔。

---

## 0. 四份输入的关系

```
A output.json (Schema 2.0)
  └─ changes[]  ──────────────┐ 每个元素是一个「结构变更实体」
                              │
B behavior.json (1.0)         │
  └─ behavior_changes[] ──────┼─────┐ 每个元素是一次「行为变化」
                              │     │
C mapping_result.json (1.0)   │     │
  └─ changes[].change ────────┘     │ 原样携带 A 的 change 对象
     changes[].tests[]              │
                                    │
D adequacy.json (1.0)               │
  └─ results[].change ──────────────┘ 原样携带 B 的 behavior_change 对象
     results[].evidence[]
```

**结论**：

- `A ↔ C`：C 的 `changes[].change` 就是 A 的 `changes[]` 元素，可以直接按对象相等或 `(file_path, entity_name, line_start)` 匹配。
- `B ↔ D`：D 的 `results[].change` 就是 B 的 `behavior_changes[]` 元素，直接对。
- `A ↔ B`：**没有公共键**。只能按 `(file_path, function/entity_name)` 匹配，失败时用行号容差兜底。

---

## 1. A：`output.json`（Schema 2.0）

顶层：

| 字段 | 类型 | 示例 | E 是否用 | 用途 |
| --- | --- | --- | --- | --- |
| `schema_version` | str | `"2.0"` | ✅ | 强校验，不等于 2.0 直接退出 |
| `mode` | str | `"pr_ast"` | ✅ | 区分 AST 流水线与 diff 报告（1.0） |
| `repo.owner` / `repo.name` | str | `pallets` / `flask` | ✅ | 报告头 |
| `repo.base_sha` / `repo.head_sha` | str | 40 位 sha | ⚠️ | 只用于报告标注 |
| `repo.pr_number` | int | `6168` | ✅ | 报告头 |
| `mirror.out_dir` | str | 绝对路径 | ✅ | 定位源码镜像，算模块路径要用 |
| `mirror.counts.*` | int | `files/downloaded/kept/failed` | ❌ | — |
| `summary.*` | int/obj | 见下 | ✅ | 交叉校验，不参与打分 |
| `files[]` | list | 每文件一条 | ❌ | 用扁平的 `changes[]` 即可 |
| `changes[]` | list | 见下 | ✅✅ | **核心输入** |
| `skipped[]` | list | `{path, reason}` | ✅ | 进报告的 `unscored` |
| `warnings[]` | list | str | ✅ | 进报告 |

`changes[]` 元素：

| 字段 | 类型 | 示例 | E 用途 |
| --- | --- | --- | --- |
| `entity_type` | str | `method` / `function` / `class` | 报告展示 |
| `entity_name` | str | `raise_any` | **与 B 的 `function` 对齐** |
| `change_type` | str | `modify` / `add` / `delete` / `rename` | 决定 Importance |
| `change_category` | str\|null | `Logic change` / `Signature change` / `Documentation change` / `Rename / Move` | **决定 Importance 主权重** |
| `line_start` / `line_end` | int | 676 / 679 | 行号容差匹配、证据定位 |
| `summary` | str | `AST structural change in method body` | 报告展示 |
| `file_path` | str | `src/flask/helpers.py` | **join 键**；注意是**仓库相对 POSIX 路径** |

> ⚠️ `change_category` 为 `Documentation change` 时直接跳过，不打分（A 已证明运行行为不变）。

---

## 2. B：`behavior.json`（Schema 1.0）

顶层：

| 字段 | 类型 | 示例 | E 用途 |
| --- | --- | --- | --- |
| `schema_version` | str | `"1.0"` | 校验 |
| `change_type` | str | `"unknown"` | ❌ 不可靠，用 `behavior_changes[].type` |
| `summary` | str | 一句话 | 报告头部摘要可用 |
| `affected_functions` | list[str] | `["withdraw"]` | 交叉校验 |
| `behavioral_impact` | str | 免责说明 | ❌ |
| `behavior_changes[]` | list | 见下 | ✅✅ **核心输入** |
| `suggested_tests[]` | list[str] | 建议 | ⚠️ 可作 `suggested_test_input` 的补充素材 |
| `errors[]` | list | — | 进 `unscored` |
| `analysis.runtime_behavior_proven` | bool | 恒 `false` | 报告免责声明 |

`behavior_changes[]` 元素（公共字段）：

| 字段 | 类型 | 示例 | E 用途 |
| --- | --- | --- | --- |
| `type` | str | `NEW_BRANCH` / `BOUNDARY_CHANGE` / `EXCEPTION_BEHAVIOR_CHANGE` / `RETURN_BEHAVIOR_CHANGE` / `DELETED_BRANCH` / `MODIFIED_BRANCH` | **决定 BehaviorRisk** |
| `function` | str | `withdraw` 或 `_CollectErrors.raise_any` | **与 A 的 `entity_name` 对齐**；⚠️ 方法给的是**限定名**，只能用最后一段比较（见 §5 J3） |
| `scope` | str | `withdraw` | 嵌套时用于区分 |
| `confidence` | str | `"potential"` | 固定值，不参与打分 |
| `file_path` | str | `payment.py` | ⚠️ **B 自己不输出**，由调用方注入，见 §5 |
| `new_line` / `old_line` | int\|null | 6 / 6 | 行号容差匹配 |

按类型额外的字段：

| 类型 | 额外字段 |
| --- | --- |
| `NEW_BRANCH` | `condition`, `old_condition`, `new_condition`, `effect`, `old_effect`, `new_effect`, `alternate_effect`, `branch_kind` |
| `BOUNDARY_CHANGE` | `variable`, `operator`, `old_boundary`, `new_boundary` |
| `EXCEPTION_BEHAVIOR_CHANGE` | `operation`, `exception_type`, `old_exception_type`, `new_exception_type`, `old_behavior`, `new_behavior`, `trigger_condition` |
| `RETURN_BEHAVIOR_CHANGE` | `old_expression`, `new_expression` |

> 示例里 `RETURN_BEHAVIOR_CHANGE` 只有 `old_expression` / `new_expression` / `old_line` / `new_line`。

---

## 3. C：`mapping_result.json`（Schema 1.0）

顶层：

| 字段 | 类型 | 示例 | E 用途 |
| --- | --- | --- | --- |
| `schema_version` | str | `"1.0"` | 校验 |
| `mode` | str | `"test_dependency_mapping"` | 校验 |
| `base_sha` | str | 40 位 | 报告标注 |
| `environment` | str | `"default"` | testmon 环境名 |
| `selected_tests` | list[str] | `["mapping-demo/test_payment.py::test_normal_withdraw", ...]` | **仓库相对 node ID**，供报告展示 |
| `selection_complete` | bool | `true` | ⚠️ `false` 时降置信度 |
| `changes[]` | list | 见下 | ✅✅ **核心输入** |
| `limitations[]` | list[str] | 四条免责 | 报告尾部 |
| `summary.*` | int | `changes/selected_tests/unresolved_changes/file_fallbacks` | 交叉校验 |

`changes[]` 元素：

| 字段 | 类型 | 示例 | E 用途 |
| --- | --- | --- | --- |
| `change` | obj | **A 的 change 对象原样** | join 键 |
| `status` | str | `matched` / `file_fallback` / `needs_new_tests` / `skipped_documentation` / `unresolved` | **决定 Reachability 等级** |
| `tests[]` | list[str] | node ID | 报告展示 |
| `evidence` | obj | 见下 | 解释素材 |
| `reason` | str | 仅异常状态有 | 进 `unscored` |

`evidence` 结构（`matched` 时才有）：

| 字段 | 类型 | 示例 |
| --- | --- | --- |
| `file_path` | str | `payment.py` ← **已经是模块路径** |
| `function` | str | `withdraw` |
| `environment` | str | `default` |
| `evidence_level` | str | `testmon_block_fingerprint` |
| `status` | str | `matched` |
| `tests[]` | list[obj] | 每个 `{test, reason, same_file_revision, blocks[]}` |
| `unresolved_tests[]` | list | 空 |
| `limitations[]` | list[str] | 四条 |

> `tests[].test` 是**相对 test-root 的 node ID**（如 `test_payment.py::test_insufficient_balance`），比顶层 `selected_tests` 少了目录前缀。

---

## 4. D：`adequacy.json`（Schema 1.0）

顶层：

| 字段 | 类型 | 示例 | E 用途 |
| --- | --- | --- | --- |
| `schema_version` | str | `"1.0"` | 校验 |
| `mode` | str | `"test_adequacy_analysis"` | 校验 |
| `analysis.method` | str | `"python-ast"` | 报告标注 |
| `analysis.runtime_adequacy_proven` | bool | 恒 `false` | 免责声明 |
| `analysis.input_complete` | bool | `true` | 不完整时降置信度 |
| `analysis.selection_complete` | bool\|null | `true` | 来自 C |
| `results[]` | list | 见下 | ✅✅ **核心输入** |
| `tests` | obj | 解析出的全部断言记录 | 解释素材 |
| `errors[]` | list | — | 进 `unscored` |
| `summary.*` | int | `behavior_changes/tests_analyzed/with_assertion_evidence` | 交叉校验 |
| `limitations[]` | list[str] | — | 报告尾部 |

`results[]` 元素：

| 字段 | 类型 | 示例 | E 用途 |
| --- | --- | --- | --- |
| `change` | obj | **B 的 behavior_change 原样** | join 键 |
| `status` | str | `assertion_evidence_found` / `weak_assertions` / `no_matching_assertion` / `no_candidate_test` / `unsupported_behavior` | **决定 AdequacyLevel（初判）** |
| `candidate_tests[]` | list[str] | node ID | 报告展示 |
| `evidence[]` | list | 见下 | **决定最终等级、解释、降级判定** |
| `recommendations[]` | list[str] | 建议 | 解释素材（含"证据不足"的权威说法） |

`evidence[]` 元素：

| 字段 | 类型 | 示例 | E 用途 |
| --- | --- | --- | --- |
| `nodeid` | str | `mapping-demo/test_payment.py::test_normal_withdraw` | 关联 C |
| `line` | int | 7 | 证据行号 |
| `kind` | str | `assert` / `pytest.raises` | 解释 |
| `strength` | str | `potential_match` / `weak_or_mismatched` | ⚠️ **关键：二次判定用** |
| `assertion.kind` | str | `assert` / `pytest.raises` | 解释 |
| `assertion.line` | int | 7 | 解释 |
| `assertion.expression` | str\|null | `withdraw(100, 30) == 70` | 解释 |
| `assertion.strength` | str\|null | `comparison` | 解释 |
| `assertion.expected_exceptions[]` | list[str] | `["ValueError"]` | 异常匹配 |
| `assertion.match` | str\|null | `'Insufficient balance'` | 异常消息 |
| `calls[].target` | str | `payment.withdraw` | 关联函数 |
| `calls[].expression` | str | `withdraw(100, 30)` | 建议输入推导 |
| `calls[].literal_arguments[]` | list | `[100, 30]` | **`suggested_test_input` 素材** |

---

## 5. E 的四条 join 规则

| # | 连接 | 用的字段 | 条件 |
| --- | --- | --- | --- |
| J1 | A.change ↔ C.change | 对象相等，或 `(file_path, entity_name, line_start)` | 一一对应，C 保证覆盖 A 的每条 change |
| J2 | B.behavior ↔ D.result | 对象相等 | 一一对应 |
| J3 | A.change ↔ B.behavior | `(file_path, 短名相等)`；失败则 `line_start ≤ new_line ≤ line_end` | **一对多**：一个实体可能有多条行为变化；**短名**=限定名最后一段 |
| J4 | C.tests ↔ D.evidence | node ID 前缀 | C 用仓库相对，D 用 test-root 相对，需按**后缀**匹配 |

> **J3 是唯一的启发式**，必须记录匹配方式（`exact` / `line_window` / `unmatched`）并进置信度。
> **J4 必须按后缀匹配**：`mapping-demo/test_payment.py::test_x` 与 `mapping-demo/...` 不同前缀，但尾部 `test_x` 相同。

---

## 6. `file_path` 的三种形态（最容易踩的坑）

| 来源 | 形态 | 示例 |
| --- | --- | --- |
| A 的 `change.file_path` | **仓库相对 POSIX** | `mapping-demo/payment.py` |
| C 的 `evidence.file_path` | **模块相对** | `payment.py` |
| D 的 `results[].change.file_path` | 原样回显调用方传入的值 | `payment.py` |

D 拿 `file_path` 去匹配测试文件里的 `from payment import withdraw`，**必须是模块相对路径**。传仓库相对路径不会报错，只会静默返回 `no_candidate_test`（实测）。

→ E 必须把 A 的路径转换成模块路径，实现见 `module_path.py`。

---

## 7. 状态 → 分数因子的映射（初版）

### 7.1 C 的 `status` → Reachability

| C status | 含义 | reachability |
| --- | --- | --- |
| `matched` | 有历史依赖 | `reached` |
| `file_fallback` | 退化为整文件依赖 | `file_level` |
| `needs_new_tests` | 新代码无基线 | `no_baseline` |
| `skipped_documentation` | 纯文档 | `skipped` |
| `unresolved` | 数据缺失 | `unknown` |

### 7.2 D 的 `status` → AdequacyLevel

| D status | Level | Deficit |
| --- | --- | --- |
| `no_candidate_test` | 0 | 1.00 |
| `no_candidate_test` + C 的 `matched` | 1 | 0.85 |
| `no_matching_assertion` | 2 | 0.70 |
| `weak_assertions` | **2.5** | 0.35 |
| `assertion_evidence_found` | 3 | 0.00 |
| `unsupported_behavior` | — | 不出分，进 `unscored` |

> ⚠️ `weak_assertions` 必须排在 `no_matching_assertion` **之上**。曾经把它映射成 Level 1.5，结果"有弱断言"比"完全没断言"分还高（96.2 vs 87.0），排序反了。断言再弱也比没有强，所以它落在 2.0 和 3.0 之间。`tests/test_scoring.py::Ordering` 现在会守住这个性质。

### 7.3 二次判定（降级规则，已实现于 `scoring.resolve_level`）

D 的顶层 `status` 是乐观的，E 必须复核 `evidence[]`：

| 条件 | 调整 | 代码里的原因码 |
| --- | --- | --- |
| 所有 `evidence[].strength` 都是 `weak_or_mismatched` | 降为 Level 2 | `all_evidence_weak` |
| 有 `potential_match`，但断言参数全是整数，且变更改的是 `round()` 精度 | 降为 Level 2.5 | `precision_blind_evidence` |
| `evidence[]` 为空（D 声称有证据却没列出） | 等级不变，标不确定 | `no_evidence_records` |
| 参数里出现浮点数 | 不降级（浮点能区分精度） | — |

> 这三条来自实测：`withdraw` 的精度改动，D 报 `assertion_evidence_found`，但唯一断言是 `withdraw(100, 30) == 70`。120 和 30 都是整数，`round(70.0, 2)` 与 `round(70.0, 1)` 都得 70，这个断言根本区分不了新旧实现。
>
> 修复前该样例得 0 分（完全信任 D），修复后得 43.55（LOW）。**这是 E 相对 D 的增量价值所在。**

置信度相关的两条（`selection_complete=false`、`file_fallback` 降一级）在 §7.4 实现。

### 7.4 置信度模型（`confidence.py`，已实现）

置信度描述的是**这个分数有多可信**，不是分数高低。每个"警号"计一次罚分，0 分 HIGH、1 分 MEDIUM、2 分以上 LOW：

| 警号 | 罚分 | 触发条件 |
| --- | --- | --- |
| `no_adequacy` | 2 | 没有 D 的输出 |
| `assumed_level` | 1 | 等级是假设出来的（缺 D 时按 Level 2） |
| `no_mapping` | 1 | 没有 C 的映射 |
| `selection_incomplete` | 1 | C 的 `selection_complete=false` |
| `file_fallback` | 1 | C 退化为文件级映射 |
| `mapping_unresolved` | 1 | C 的该条状态是 `unresolved` |
| `weak_join` | 1 | A↔B 只能靠函数名匹配（`function_only`） |
| `uncertain_evidence` | 1 | D 声称有证据但没列出 `evidence[]` |
| `input_incomplete` | 1 | D 的 `input_complete=false` |

实测效果：

| 输入 | 分数 | 等级 | 置信度 | 信号 |
| --- | --- | --- | --- | --- |
| 四份齐全 | 43.5 | LOW | **HIGH** | — |
| 缺 D | 62.8 | MEDIUM | **LOW** | `no_adequacy`, `assumed_level` |
| 缺 C 和 D | 62.8 | MEDIUM | **LOW** | `no_mapping`, `no_adequacy`, `assumed_level` |

> 缺 D 时分数反而更高（62.8 vs 43.5），因为按 Level 2 假设（deficit 0.70）比证据复核后的 2.5（0.35）更悲观。**这正说明置信度的必要性**：低置信度的高分不能当成"更危险"，只能当成"不知道"。

---

## 8. 版本与兼容

| 输入 | 校验字段 | 期望值 | 不符时 |
| --- | --- | --- | --- |
| A | `schema_version` + `mode` | `"2.0"` + `"pr_ast"` | 报错退出 |
| B | `schema_version` | `"1.0"` | 警告，继续 |
| C | `schema_version` + `mode` | `"1.0"` + `"test_dependency_mapping"` | 报错退出 |
| D | `schema_version` + `mode` | `"1.0"` + `"test_adequacy_analysis"` | 报错退出 |

A 的 diff 报告模式（Schema `1.0`）与 AST 流水线结构完全不同，必须拒绝，不能静默降级。
