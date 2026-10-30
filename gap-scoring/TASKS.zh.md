# Member 5（E 模块）任务清单

> 负责人：gaoge
> 模块：Test Gap Scoring & Explanation
> 创建：2026-10-30　最近更新：2026-10-30
> 状态图例：✅ 已完成　🔄 进行中　⏳ 待开始　🚫 被阻塞　⏸️ 暂缓

---

## 一、当前进度总览

### 1.1 上游四个模块（2026-10-30 实测）

| 模块 | 负责人 | 位置 | 状态 | 验证结论 |
| --- | --- | --- | --- | --- |
| A PR / Git Change Analyzer | Member 1 | `pr-analyzer/` | ✅ 可用 | 跑通 flask#6168，7 条 change |
| B Behavioral Change Detector | Member 2 | `pr-analyzer/behavioral_change_detector.py` | ✅ 可用 | 真实数据产出 1 条 `RETURN_BEHAVIOR_CHANGE` |
| C Test Dependency Mapper | Member 3 | `mapping-demo/` | ✅ **已跑通** | `matched`，1 变更 → 3 测试 |
| D Test Adequacy Analyzer | Member 4 | `adequacy-analyzer/` | ✅ **已跑通** | demo + CLI 都正常 |

> C/D 的验证在 `work\` 副本里完成（仓库对我的沙箱只读）。产物已固化到 `gap-scoring/fixtures/`。

### 1.2 E 模块自身

| 项 | 状态 |
| --- | --- |
| 契约草案 v0.1 | ✅ 已写（`docs/gap-scoring-contract.zh.md`），⏸️ **已过期**，待改 v0.2 |
| 字段对照表 | ✅ 已完成（`gap-scoring/docs/field-map.zh.md`） |
| 测试夹具 | ✅ 已有 7 个真实文件（`gap-scoring/fixtures/`，含 A 的 `changes.json`） |
| `module_path.py` | ✅ 已完成，17 个测试全过，端到端验证有效 |
| `loader.py` | ✅ 已完成，21 个测试全过，真实数据冒烟测试通过 |
| `join.py` | ✅ 已完成，16 个测试全过，含覆盖率检查 |
| `weights.py` + `scoring.py` | ✅ 已完成，40 个测试全过，87/100 基准复现，含 evidence 复核 |
| `confidence.py` | ✅ 已完成，18 个测试全过，9 个信号合成三级置信度 |
| 契约 v0.2 | ✅ 已完成（`gap-scoring/docs/gap-scoring-contract.zh.md`） |
| F 对接说明 | ✅ 已完成（`docs/integration-with-reporter.zh.md`） |
| `status_line` | ✅ 已完成（PR 级一行摘要，两种特殊形态） |
| 英文 README | ✅ 已完成（`gap-scoring/README.md`） |
| 中文交接 / demo / 论文总结 | ✅ 已完成（`docs/HANDOFF.zh.md`、`demo.py`、`docs/paper-notes.zh.md`） |
| 部署脚本 | ✅ 已完成（`project/deploy-to-repo.ps1`，含预演模式） |
| 文献清单 | ✅ 已完成（`gap-scoring/docs/related-work.zh.md`，20 条，含同题工业研究） |
| `explain.py` | ✅ 已完成，27 个测试全过，能渲染 5 行内的 PR 评论 |
| `report.py` + `gap_scoring.py` | ✅ 已完成，31 个测试全过，一条命令产出 JSON / Markdown |
| `pipeline.py` + `run_pipeline.py` | ✅ 已完成，19 个测试全过，A→B→C→D→E 实跑通过 |
| 场景 fixtures | ✅ 已完成，6 个场景 + `expected.json` 黄金测试 |
| 解释生成 / CLI | ⏳ 未开始 |

### 1.3 已验证的端到端链路

`work\` 里跑通的完整故事：`withdraw` 的返回精度从 2 位改成 1 位。

```
源码改动  round(balance - amount, 2) → round(..., 1)
   │
   ├─ B：1 条 RETURN_BEHAVIOR_CHANGE（function=withdraw, new_line=6）
   ├─ C：matched，选中 3 个测试，selection_complete=true
   └─ D：assertion_evidence_found —— 但证据全是弱证据
```

D 的证据明细：

| 测试 | 行 | 断言 | strength |
| --- | --- | --- | --- |
| `test_normal_withdraw` | 7 | `withdraw(100, 30) == 70` | `potential_match` |
| `test_insufficient_balance` | 11 | `pytest.raises(ValueError, ...)` | `weak_or_mismatched` |
| `test_negative_amount` | 16 | `pytest.raises(ValueError, ...)` | `weak_or_mismatched` |

**唯一的数值断言是整数 70**，`round(70.0, 2)` 和 `round(70.0, 1)` 都得 70，区分不了新旧实现。D 自己的 `recommendations` 也写了："an integer-only example cannot establish rounding adequacy."

→ 这就是 E 的核心价值：**顶层 `status` 会骗人，真相在 `evidence[].strength` 里**。

### 1.4 关键接口事实（写代码前必须知道）

| # | 事实 | 影响 | 来源 |
| --- | --- | --- | --- |
| 1 | C 的输出里带 **A 的原始 change 对象** | `A ↔ C` 可直接对 | 读 `mapping_result.json` |
| 2 | D 的输出里带 **B 的原始 behavior_change 对象** | `B ↔ D` 可直接对 | 读 `adequacy.json` |
| 3 | **A ↔ B 没有公共键** | 必须按 `(file_path, function)` + 行号容差对齐 | 对比 schema |
| 4 | **B 不输出 `file_path`** | 多模块 PR 必须调用方注入 | `main.py` L779-782 |
| 5 | **D 要的 `file_path` 是「模块相对路径」，不是仓库相对路径** | 传 `payment.py` ✅ / 传 `mapping-demo/payment.py` ❌ | 实测对比 |
| 6 | D 给 5 个 status，不是 Level 0-3 | E 需要映射表 | `README.md` |
| 7 | D 的 `status` 可能是乐观的（证据弱也报 `assertion_evidence_found`） | E 必须读 `evidence[].strength` 做二级判定 | 本次实测 |
| 8 | C 的 `.testmondata` / `mapping_result.json` 都被 gitignore | 只能本地重建，GitHub 上没有 | `git log --all` 查证 |

---

## 二、任务清单

### 阶段 0：对齐与契约（4h）

- [x] **0.1** 写字段对照表 `docs/field-map.zh.md`（1h）✅ 完成
- [x] **0.2** D status → 覆盖等级映射（1h）✅ 完成（契约 §4 + `weights.ADEQUACY_STATUS_LEVEL`）
  - `no_candidate_test` → Level 0
  - `no_matching_assertion` → Level 2
  - `weak_assertions` → Level 1.5
  - `assertion_evidence_found` → Level 3（**但要被 2.6 降级**）
  - `unsupported_behavior` → 不出分
- [x] **0.3** A↔B 对齐规则（1h）✅ 完成（契约 §2.3，含短名匹配与全局分配）
- [x] **0.4** 契约文档升级 v0.2（1h）✅ 完成（`gap-scoring/docs/gap-scoring-contract.zh.md`）

### 阶段 1：数据接入层（13h）

- [x] **1.1** `loader.py`：读四份 JSON + schema 校验 + 容错（3h）✅ 完成，21 个测试
- [x] **1.2** `join.py`：四表连接，产出统一的 `ScoringUnit`（3h）✅ 完成，16 个测试
- [x] **1.3** 降级逻辑（2h）✅ 完成（`loader._load_optional` + `scoring.resolve_level` 的 assumed_level）
- [x] **1.4** 单测用真实 fixture（2h）✅ 完成（`test_loader` 21 个测试）
- [x] **1.5** `module_path.py`：仓库相对路径 → 模块相对路径（3h）✅ 完成，17 个测试
  - 输入 `mapping-demo/payment.py`，输出 `payment.py`
  - 规则：找 import root（有 `__init__.py` 的连续包目录 / setup.py 所在目录），剥掉前缀
  - 反例保护：`tests/test_x.py` 与 `test_x.py` 不能混

### 阶段 2：打分引擎（9h + 2h）

- [x] **2.1** 三因子权重表 ✅ 完成（`weights.py`）
- [x] **2.2** 规则引擎：5 条规则 + bonus 上限 15（3h）✅ 完成
- [x] **2.3** 归一化 + 分档 + 聚合（2h）✅ 完成（`band_for` / `score_units` / `summarize`）
- [x] **2.4** 置信度模型（2h）✅ 完成（`confidence.py`，9 个信号 → HIGH/MEDIUM/LOW）
- [x] **2.5** 单测：含 87/100 基准 ✅ 完成（`test_scoring.py::ReferenceCase`，用内存对象构造替代 fixture 文件）
- [x] **2.6** evidence 强度二次判定（2h）✅ 完成（`resolve_level`，三条规则）
  - 规则：`status=assertion_evidence_found` 但 evidence 全是 `weak_or_mismatched` → 降为 Level 2
  - 规则：`potential_match` 且断言参数是整数、变更涉及精度/边界 → 标 `uncertain`

### 阶段 3：解释生成（8h）

- [x] **3.1** 三段式模板（2h）✅ 完成（`explain.render_markdown`）
- [x] **3.2** 证据追溯（2h）✅ 完成（`EvidenceRef`，每条结论可追溯到行号/测试名）
- [x] **3.3** `suggested_test_input`（2h）✅ 完成（基础版：精度/异常/边界三类，锚定 D 的调用表达式）
- [x] **3.4** 措辞约束 + 单测（2h）✅ 完成（`FORBIDDEN_PHRASES` + 运行时断言）

### 阶段 4：CLI 与报告（6h）

- [x] **4.1** CLI 入口（2h）✅ 完成（`gap_scoring.py`）
- [x] **4.2** JSON 输出（1h）✅ 完成（`report.build_report`，契约 §4 字段）
- [x] **4.3** Markdown 报告（2h）✅ 完成（汇总 + Top-N + 省略计数）
- [x] **4.4** 退出码 + 错误处理（1h）✅ 完成（0/1/2/3，`--fail-on-high` 门禁）

### 阶段 5：集成与验收（10h）

- [x] **5.1** 端到端脚本 `run_pipeline.py`（4h）✅ 完成，flask#6168 实跑通过
- [x] **5.2** 典型场景 fixture（3h）✅ 完成，6 个场景覆盖 HIGH/MEDIUM/LOW/NONE/未评分
- [x] **5.3** 真实 PR 降级路径（2h）✅ 完成（`--behavior-file/--mapping-file/--adequacy-file` 复用入口）
- [x] **5.4** 与 F 对接（1h）✅ 完成（`docs/integration-with-reporter.zh.md`）

### 阶段 6：文档与交付（5h）

- [x] **6.1** 英文 README（2h）✅ 完成
- [x] **6.2** 中文交接文档（2h）✅ 完成（`docs/HANDOFF.zh.md`）
- [x] **6.3** demo 脚本（1h）✅ 完成（`demo.py`，离线跑六个场景）
- [x] **6.4** 论文阅读总结（2h）✅ 完成（`docs/paper-notes.zh.md`，8 篇 + 引用位置表）
  - 必读三篇：Inozemtseva & Holmes 2014、Zhang & Mesbah 2015、TSE 2025 风险化 test gap
  - ⚠️ TSE 2025 的作者列表、Eder 等的年份需在 IEEE Xplore 核对后再引用

---

## 三、前置任务

- [x] **X.1** 把 `adequacy-analyzer/` 移到仓库根目录（用户已用 `git mv` 完成）
- [x] **X.2** 重建 C 的样例数据 —— 已完成，见 `fixtures/README.zh.md`
- [x] **X.3** 固化测试夹具到 `gap-scoring/fixtures/`（6 个文件，12.9 KB）
- [x] **X.4** 给 `adequacy-analyzer/` 补 `.gitignore`（0.2h）✅ 已写进 `deploy-to-repo.ps1`，执行部署时生效
  - D 的交接文档说有这个文件，但实际没交付 → `adequacy-analyzer/out/` 现在在 `git status` 里是未跟踪状态
- [ ] **X.5** 确认长期工作流：仓库只读，E 的代码在 `gap-scoring/` 写好后手动复制进仓库

---

## 四、阻塞项与需协调事项

| # | 事项 | 影响 | 找谁 |
| --- | --- | --- | --- |
| 1 | D 的 `file_path` 期望值没写清楚（README 例子 `payment.py` 有歧义） | 传错格式会静默返回 `no_candidate_test`，很难查 | Member 4 |
| 2 | B 不输出 `file_path` | 多文件 PR 会误匹配 | Member 2 |
| 3 | C 的样例数据不入库 | 队友和 CI 都拿不到 | Member 3 |
| 4 | 端到端编排脚本归属未定 | 若归 E，5.1 就是 E 的活 | 全队 / F |
| 5 | 87/100 验收基准用真实数据复现不了 | 需自造 fixture | 自己 |

---

## 五、下一步（按顺序做）

**全部 34 项任务完成**（合计 52h）。代码、测试、文档、demo 齐备。

剩下只有交付动作（不占工时）：

1. **执行部署**：`deploy-to-repo.ps1`（预演 → 执行）
2. **提交并推送**：`git add` → `commit` → `push`
3. **X.5 工作流确认**：以后 E 的代码都在仓库里了，不再需要"工作区 → 仓库"的复制

交付物清点：

| 类别 | 数量 |
| --- | --- |
| Python 文件 | 12 个（含 demo、pipeline、tools） |
| 测试 | 215 个 |
| 文档 | 6 份（README + 5 份中文） |
| 场景 fixture | 6 组 × 4 份 JSON |

---

## 六、时间估算与里程碑

净工时：**P0 37h + P1 23h = 60h**

按每天 4h：

| 里程碑 | 内容 | 累计 | 目标日期 |
| --- | --- | --- | --- |
| M1 | 契约 v0.1→v0.2 + loader + join + 路径解析 | 17h | 11-04 |
| M2 | 打分引擎 + 单测，能出分数 | 24h | 11-06 |
| M3 | 解释 + CLI，**P0 完成可演示** | 37h | 11-09 |
| M4 | 置信度 + evidence 降级 + Markdown | 48h | 11-11 |
| M5 | 真实 PR + 文档 + 交接，**完整版** | 60h | 11-14 |

按每天 6h：M3 约 11-05，M5 约 11-09。
若 deadline 只剩 3 天：砍到 12h 版（字段表 + 路径解析 + loader + mock 打分 + 一页报告）。

---

## 七、环境备忘（本机实测）

| 项 | 状态 |
| --- | --- |
| Git | 2.50.1 |
| Python | 3.12.8 |
| pytest | 8.4.1（在 `project\deps`） |
| coverage | 7.9.2（在 `project\deps`） |
| testmon | ✅ 可用，但**不要 `pip install`**（会卡在编译）；用源码路径加载 |
| 依赖目录 | `D:\Desktop\港城课程\CS5351\project\deps` |
| 仓库写入 | ❌ 沙箱拒绝；代码先写在 `project\gap-scoring\` |

标准环境变量：

```powershell
$root = "D:\Desktop\港城课程\CS5351\project"
$repo = "$root\CS5351-TestPilot"
$env:PYTHONPATH = "$root\pyshim;$root\deps;$repo\pytest-testmon;$repo\pr-analyzer"
$env:TMP = "$root\.tmp3"; $env:TEMP = $env:TMP   # 绕开 0o700 临时目录问题
```

> `pyshim` 是强制 `os.mkdir` 用 `0o777` 的垫片，没有它 testmon / map_changes 会在建临时目录时崩。

---

## 八、变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-10-30 | 核查 A/B/C/D 交付物；在 `work\` 里跑通 C/D 链路；固化真实 fixture；发现 `file_path` 格式陷阱和 evidence 强度问题；任务 31 → 33 项，净工时 55h → 60h。同日完成 **0.1 字段对照表**、**1.5 模块路径解析器**（17 测试，修复 D 的静默失败）、**1.1 `loader.py`**（21 测试）、**1.2 `join.py`**（16 测试，含覆盖率检查）、**2.1 权重表 + 2.2 规则引擎 + 2.3 聚合 + 2.5 基准测试**（31 测试，87/100 复现）、**2.6 evidence 复核**（3 条降级规则，payment 样例从 0 分修正为 43.55 分）、**2.4 置信度模型**（9 个信号合成三级）、**3.1–3.4 解释生成**（27 测试，渲染出 5 行 PR 评论）、**4.1–4.4 CLI/JSON/Markdown/退出码**（31 测试，`gap_scoring.py` 一条命令产出报告，支持 `--fail-on-high` 门禁）。`withdraw` 样例的最终输出：「`withdraw(100, 30) == 70` 整数输入区分不了新旧实现，建议补 `withdraw(100, 30.05)`」。**5.1 端到端脚本**（19 测试）+ **5.2 六个场景 fixture**（黄金测试锁定 HIGH/MEDIUM/LOW/NONE 四档）、**契约 v0.2 + F 对接说明 + 5.3 产物复用入口 + `status_line`**。5.1 实跑 flask#6168：B 合并 6 个文件、D 分析 127 个测试、E 产出报告；期间发现 B 的 `function` 是**限定名**（`_CollectErrors.raise_any`）而 A 给短名，J3 因此漏掉所有方法，已改为按短名匹配。跑敏感性矩阵时发现 `weak_assertions` 被映射到 Level 1.5 导致"弱断言比没断言更糟"的排序反转，已改为 2.5 并加 `Ordering` 测试守住单调性。全套 215 个测试通过；文档与 demo 完成 |
