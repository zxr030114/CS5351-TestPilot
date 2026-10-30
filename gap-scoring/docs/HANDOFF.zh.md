# Test Gap Scoring 模块交接文档

> 给接手这个模块的人。
> README 讲**怎么用**，本文讲**为什么这么做、要改的时候动哪里、哪里有坑**。
> 版本：2026-10-30，对应契约 v0.2，210 个测试。

---

## 1. 三十秒理解

E 的核心断言是一句话：

> **"测试覆盖到了"不等于"测试验证了"。**

D 用 AST 检查"有没有断言碰过这个调用"，只要语法上匹配就报 `assertion_evidence_found`。但 `withdraw(100, 30) == 70` 这样的断言，在返回值精度从 2 位改成 1 位时**什么都测不出来**——它照样通过。

E 的工作就是把这些"看起来测了、实际没测住"的地方找出来，给一个分数、一个置信度、一段人话。

判断这套东西有没有用，看一个数字：payment 样例在只有 D 的时候得 **0 分**（被当成"已覆盖"过滤掉），加上 E 的复核后得 **43.55 分**——同一个改动，从"不存在"变成"低危缺口"。

---

## 2. 代码地图

```
gap_scoring.py   CLI 入口，五步串起来：load → join → score → explain → report
run_pipeline.py  CLI：跑 A-D 再调 E（可选，真实 PR 用）
pipeline.py      上面那个 CLI 的实现：子进程编排 + 产物复用 + 降级
loader.py        读四份 JSON、校验 schema、归一化成 dataclass
join.py          四条 join 规则，产出 ScoringUnit
module_path.py   仓库相对路径 → 模块相对路径（D 要求的格式）
weights.py       所有可调数字（权重、阈值、罚分），只有常量
scoring.py       公式、规则、证据复核、分档
confidence.py    九个警号 → HIGH/MEDIUM/LOW
explain.py       三段式解释、措辞守卫
report.py        JSON 报告 / Markdown 评论 / 一行摘要
tools/           场景 fixture 生成器
```

依赖方向是单向的，不能反过来：

```
loader ← join ← scoring ← explain ← report ← gap_scoring
                   ↑
              weights / confidence
module_path 被 join 和 pipeline 使用，不依赖任何业务模块
```

**改代码前先看这条链**：如果想让报告多一个字段，动 `report.py`；如果想让分数变化，动 `weights.py`；如果想让判定逻辑变化，动 `scoring.py`。三者不要混着改。

---

## 3. 数据流

```
四份 JSON
   │  loader.load_pipeline()
   ▼
PipelineInputs{changes, behavior, mapping, adequacy, problems}
   │  join.join_inputs()
   ▼
JoinResult{units[], orphan_behaviors, coverage, stats, problems}
   │  scoring.score_units(context)
   ▼
UnitScore[]{score, band, confidence, links[]}
   │  explain.explain_unit()
   ▼
Explanation{changed_behavior, problem, suggested_input, evidence}
   │  report.build_report()
   ▼
dict  ──► JSON / Markdown / status_line
```

每层的产物都是 frozen dataclass，**没有任何一层会回头改上一层的输入**。这让单测可以只构造需要的那一层（比如测打分不用造 JSON 文件）。

---

## 4. 六个设计决策

### 4.1 分数与置信度正交

分数回答"缺口多大"，置信度回答"这个判断有多可信"。两者分开存、分开显示。

**为什么**：缺 D 时分数反而更高（Level 2 假设的 deficit 0.70 > 复核后的 0.35）。如果只给分数，reviewer 会去追一个数据不全导致的假问题。

**代码**：`confidence.py` 是独立的罚分模型，`scoring.score_link` 只负责把信号收集起来。改置信度规则不需要碰打分。

### 4.2 不信任 D 的 status，要复核 evidence

`assertion_evidence_found` 只说明"有语法匹配的断言"。三条复核规则见契约 §5。

**为什么**：D 的角度是"能不能找到断言"，E 的角度是"这个断言能不能观测到改动"。两个问题不同。

**代码**：`scoring.resolve_level()`。它返回 `LevelDecision`，带 `downgraded` 原因码，报告里能直接显示是为什么降的。

### 4.3 覆盖到位时分数归零

Level 3 且没有规则命中 → `score = 0`、`band = NONE`、**不进报告**。

**为什么**：`Base` 公式里有个结构项 `0.45 × risk × importance`，即使完全覆盖也会有 40 分左右的底噪。但那些是"改动本身重要"，不是"测试缺口"。测试缺口是测试能关掉的东西，覆盖满了就没有缺口。

**代码**：`scoring.score_link()` 里的 `SUPPRESS_WHEN_COVERED` 分支。

### 4.4 join 用全局分配，不是逐条贪心

每条 B 的行为记录**只归一个** A 的变更；两个变更同样匹配时不归属任何一方，记 `join.ambiguous_behavior`。

**为什么**：第一版是"每个 change 独立找匹配的行为"，结果两个同名函数会各自认领同一条行为，分数被重复计算且不报错。宁可漏挂，不可重复。

**代码**：`join._assign_behaviors()`。改这里要同时看 `tests/test_join.py::Ambiguity`。

### 4.5 场景期望值手写，不让代码生成

`fixtures/scenarios/expected.json` 里的分数是人算的，测试拿它比对。

**为什么**：如果期望值由代码跑一遍存下来，测试就变成"代码和它自己一致"，校准漂了也是绿的。手写才有意义。

**代价**：改权重后要手动更新期望值。这是**故意的摩擦**——改校准就该有感觉。

### 4.6 措辞红线做成运行时断言

`FORBIDDEN_PHRASES = ("missing test", ...)`，`explain_unit()` 返回前会检查。

**为什么**：这类要求靠 code review 守不住，写模板的人一不留神就写"缺少测试"。让机器守，改错了当场抛异常。

**代码**：`explain.assert_forbidden_absent()`，`tests/test_explain.py::WordingGuard` 遍历所有禁用短语。

---

## 5. 要改的话，动哪里

| 想改什么 | 动哪个文件 | 注意 |
| --- | --- | --- |
| 分数权重、分档线、规则加分 | `weights.py` | 只有常量；改完跑 `test_scoring`，期望值可能要更新 |
| 加一条打分规则 | `scoring._rules()` + `weights.py` 的 `RULE_*` | 规则返回 `RuleHit`，`bonus` 有 15 分上限 |
| 改等级映射 | `weights.ADEQUACY_STATUS_LEVEL` / `ADEQUACY_DEFICIT` | deficit 必须随等级递减，`Ordering` 测试会拦 |
| 改置信度罚分 | `confidence.SIGNAL_PENALTIES` | 新增信号要在 `scoring._confidence_signals` 里产出 |
| 报告加字段 | `report._gap_payload()` | 只增不改，F 依赖现有字段 |
| 改解释文案 | `explain.describe_behavior` / `describe_problem` | 注意禁用词；名词要保持英文（与 A/B/D 的字段一致） |
| 加一个场景 | `tools/make_scenarios.py` 的 `SCENARIOS`，重新生成 | 期望值手写在 `expected` 里 |
| 加一种行为类型 | `weights.BEHAVIOR_RISK` + `explain.describe_behavior` | B 出现新类型时先兜底 0.70，再补文案 |
| 支持别的语言 | `module_path` 不用改；`scoring` 几乎不用改 | 真正要改的是 D 的 AST 抽取，不属 E |

---

## 6. 六个坑（踩过，别再踩）

**① B 的 `function` 是限定名**

B 给 `_CollectErrors.raise_any`，A 给 `raise_any`。精确比较会漏掉所有方法——实测 7 个变更只匹配 1 个。必须取最后一段比较（`join._short_name`）。

**② D 要的 `file_path` 是模块相对路径**

传 `mapping-demo/payment.py` 不报错，只是静默返回 `no_candidate_test`。必须先用 `module_path.to_module_path` 转换。

**③ 临时目录的权限**

这台机器上 `os.mkdir(path, 0o700)` 建出的目录连自己都进不去，而 `tempfile.mkdtemp` 正是用它。所以项目里**测试不使用 `tempfile`**，一律在 `tests/` 下建固定目录、`tearDownClass` 清理。

**④ PowerShell 脚本要带 BOM**

给 Windows PowerShell 5.1 写的 `.ps1` 如果是 UTF-8 无 BOM，中文注释会按 GBK 解码成乱码，把引号和括号吃掉，脚本直接语法错误。部署脚本加了 BOM。

**⑤ 聚合时"最大"不是"平均"**

一个实体多条行为时取**最高分**（最危险的那条代表它）。用平均会把一个高危缺口稀释掉。

**⑥ `gaps=[]` 不等于"没问题"**

可能是没数据。所以报告里必须有 `summary.changes_scored` 和 `inputs`，`status_line` 也专门区分了三种形态。

---

## 7. 测试策略

210 个测试，分四层：

| 层 | 文件 | 测什么 |
| --- | --- | --- |
| 单元 | `test_loader` / `test_join` / `test_module_path` / `test_scoring` / `test_confidence` / `test_explain` | 每个模块的逻辑与边界 |
| 结构 | `test_report` | JSON 字段、排序、Markdown 渲染、`status_line` |
| 场景 | `test_scenarios` | 六个场景的**黄金**分数（手写期望） |
| 端到端 | `test_cli` / `test_pipeline` | 命令行退出码、产物复用、降级 |

跑法：

```bash
python -m unittest discover -s tests
```

不联网、不依赖被测项目、约 0.15 秒。

---

## 8. 已知限制

1. 只支持 Python（D 的断言抽取基于 Python AST）。
2. 从不执行测试，所有结论是静态证据。
3. C 的 testmon 库通常拿不到，此时可达性依赖 D，置信度多为 LOW。
4. 证据复核只覆盖"精度盲区"一种模式。
5. J3 仍是启发式：同名函数跨文件靠路径后缀，重命名后可能失配。
6. 置信度是罚分相加，没有概率含义。

---

## 9. 未来工作（按收益排序）

1. **接 C 的真实数据库**：现在多数场景 C 是跳过的，接上后置信度会明显提升。
2. **更多证据复核模式**：目前只认精度盲区，还可以做"断言期望值与新实现矛盾"的检测。
3. **LLM 生成解释**：模板已经能产出结构化三段式，接 LLM 只需替换 `describe_problem`，但要有评估方案，否则容易产生幻觉。
4. **跨语言**：取决于 D 能不能支持 Java。
5. **PR 级趋势**：多次运行的分数变化，能看出"测试债"在恶化还是改善。

---

## 10. 接手清单

- [ ] 跑一遍 `python -m unittest discover -s tests`，确认 210 个测试通过
- [ ] 跑 `python demo.py`，看六个场景的分数表
- [ ] 读 `docs/gap-scoring-contract.zh.md` §3–§6（打分、等级、复核、置信度）
- [ ] 读 `docs/field-map.zh.md` §5（四条 join 规则）
- [ ] 改一次 `weights.py` 里的权重，看哪些测试会红——这能让你快速理解校准和测试的关系
- [ ] 如果接手后第一个任务是"调分数"，先看 `fixtures/scenarios/expected.json`，那里定义了每个风险档应该落在哪个区间
