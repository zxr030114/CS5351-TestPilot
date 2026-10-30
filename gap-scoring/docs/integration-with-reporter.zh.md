# 给 Member 6（F：GitHub PR Reporter）的对接说明

> 来源：Member 5（E，Test Gap Scoring）
> 版本：2026-10-30，对应 E 的契约 v0.2
> 读者假设：**没读过 E 的代码**，只想把报告贴到 PR 上

---

## 0. 三句话版本

1. 调一条命令：`python gap-scoring/gap_scoring.py --changes ... --behavior ... --mapping ... --adequacy ... -o gap.json --format markdown`
2. 你会拿到两个产物：`gap.json`（结构化，供判断）和 `gap-report.md`（现成评论，可直接贴）
3. 退出码 `3` 表示"发现了 HIGH 缺口"，用它做合并门禁；`2` 表示"E 的输入有问题"，别当成"没问题"

---

## 1. 你会拿到什么

| 产物 | 用途 | 说明 |
| --- | --- | --- |
| `gap_report.json` | 程序消费 | 契约见 §4，`schema_version="1.0"`、`module="test_gap_scoring"` |
| `gap_report.md` | 直接贴 PR | 已有汇总 + Top-N 缺口，**不需要你再排版** |

命令一次跑完四步（读四份 JSON → 连接 → 打分 → 生成报告），不联网、不执行测试。

```bash
python gap-scoring/gap_scoring.py \
  --changes   artifacts/output.json \
  --behavior  artifacts/behavior.json \
  --mapping   artifacts/mapping_result.json \
  --adequacy  artifacts/adequacy.json \
  -o artifacts/gap_report.md --format markdown
```

`-o` 的文件名跟格式对齐：`--format markdown` 配 `.md`，`--format json`（默认）配 `.json`。两种格式可以各跑一次，也可以只用 JSON 自己渲染。

---

## 2. 三种对接方式

### 方式 A：子进程调用（推荐）

E 是一个独立命令行工具，当黑盒用最省事：

```python
result = subprocess.run([sys.executable, "gap-scoring/gap_scoring.py", *args],
                        capture_output=True, text=True)
if result.returncode == 3:
    ...
```

好处：E 内部怎么改都不影响你；坏处：多一次进程启动（约 0.2 秒，可忽略）。

### 方式 B：导入函数

```python
from gap_scoring import main as score
code = score(["--changes", "...", "-o", "gap.json"])
```

适合已经有 Python 环境、不想拼命令行的场景。注意 `sys.path` 要包含 `gap-scoring/`。

### 方式 C：只读 JSON，自己渲染

如果你对评论格式有强需求（比如要折叠、要表格），只调 E 拿 `gap.json`，渲染逻辑自己写。字段见 §4。

**不要做的事**：解析 E 的 stdout。stdout 默认输出报告，格式会随 `--format` 变化；结构化数据一律从 `-o` 的文件读。

---

## 3. 退出码与门禁语义

| 码 | 含义 | F 应该 |
| --- | --- | --- |
| `0` | 正常，报告已生成 | 贴评论（可能没有缺口） |
| `1` | E 内部异常 | 贴"分析失败"并 @ 负责人，**不要静默跳过** |
| `2` | 输入问题（文件缺失 / schema 不符 / 输出不可写） | job 失败；这通常意味着上游阶段没跑完 |
| `3` | 门禁失败（存在 HIGH 缺口） | 贴评论 + 标记 check 失败（**但报告是正常的**） |

**关键区分**：`3` 的报告和 `0` 的报告结构完全一样，只是"有 HIGH"。别把 `3` 当作"分析失败"。

### 门禁怎么用

```bash
python gap-scoring/gap_scoring.py ... --fail-on-high
```

不加这个参数时 **E 永远返回 0**（除非真出错）。也就是说：**是否阻断合并由 F 决定，E 只提供信号**。这个设计是刻意的——课程项目里如果默认阻断，队友一天到晚被卡。

建议做法：默认不阻断，只在 `main` 分支的 PR 上启用 `--fail-on-high`。

---

## 4. 报告字段速查（F 需要读的）

```json
{
  "schema_version": "1.0",
  "module": "test_gap_scoring",
  "pr": {"repo": "pallets/flask", "number": 6168, "base_sha": "...", "head_sha": "..."},
  "inputs": {"behavior": "loaded", "mapping": "loaded", "adequacy": "loaded"},
  "summary": {"changes_total": 7, "changes_scored": 3, "gaps_high": 1, "gaps_medium": 2,
              "gaps_low": 0, "average_score": 86.2, "mapping_coverage": 0.0},
  "gaps": [ { ... } ],
  "unscored": [ { ... } ],
  "problems": [ { ... } ]
}
```

| 字段 | F 怎么用 | 备注 |
| --- | --- | --- |
| `summary.gaps_high/medium/low` | 一行摘要 | 决定评论标题语气 |
| `summary.changes_scored` / `changes_total` | 说明覆盖率 | 例："分析了 3/7 个变更" |
| `summary.mapping_coverage` | 可选展示 | `null` 表示没有 C 的数据 |
| `inputs.*` | **必须展示缺失项** | `"missing"` 说明该维度降级了 |
| `gaps[].risk` | 高/中/低标签 | 只有 HIGH/MEDIUM/LOW 会出现在 `gaps` 里 |
| `gaps[].confidence` | **必须展示** | 见 §5 |
| `gaps[].gap_score` | 排序、阈值 | 已按分数降序 |
| `gaps[].explanation.one_liner` | 一行版 | 适合折叠摘要 |
| `gaps[].explanation.changed_behavior` / `problem` / `suggested_test_input` | 评论正文 | 已经是人话，不用二次加工 |
| `gaps[].affected_tests` | 列出相关测试 | 最多 3 个，已截断 |
| `unscored[].reason` | 可选展示 | 例 `no_behavior_change`（B 没发现行为变化） |
| `problems[]` | 可选折叠 | 加载阶段的警告（如 B 缺 `file_path`） |

**不要用 `components`**：那是调试用的中间量，字段可能变。

---

## 5. 渲染建议（三条硬要求）

### ① 必须同时展示 `confidence`

同一个 79 分，`confidence=HIGH` 和 `LOW` 含义完全不同：

| 分数 | 置信度 | 怎么读 |
| --- | --- | --- |
| 79 | HIGH | 数据齐全，这个缺口是实的 |
| 79 | LOW | 数据缺失（缺 D 或映射不可用），**可能只是不知道** |

只贴分数的后果：reviewer 会去追一个数据不全导致的假问题。

### ② 必须展示 `inputs` 里缺失的维度

```markdown
_分析降级：缺少 mapping（测试依赖不可用），置信度可能偏低_
```

这是诚实性问题。E 的输出里已经有 `inputs`，照抄即可。

### ③ 不要逐条罗列所有缺口

`--top N` 或 `--max-gaps-in-comment N` 控制条数，超出部分写"还有 N 条"。一个 PR 评论贴 20 条没人看。

---

## 6. 最小 GitHub Action 示例

> 只是示意，按你们仓库的实际结构改路径。

```yaml
name: test-gap
on: pull_request

jobs:
  gap:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      # 上游阶段 A-D 的产物（由各自的步骤生成）
      - name: Run stages A-D
        run: |
          python pr-analyzer/main.py --repo ${{ github.repository }} --pr ${{ github.event.number }} -o artifacts/output.json
          # ... B/C/D 各自产出

      - name: Score gaps
        id: gap
        continue-on-error: true          # 让退出码 3 不中断 job
        run: |
          python gap-scoring/gap_scoring.py \
            --changes artifacts/output.json \
            --behavior artifacts/behavior.json \
            --mapping artifacts/mapping_result.json \
            --adequacy artifacts/adequacy.json \
            -o artifacts/gap_report.md --format markdown --fail-on-high

      - name: Post comment
        if: always()
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const path = 'artifacts/gap_report.md';
            if (!fs.existsSync(path)) return;
            await github.rest.issues.createComment({
              ...context.repo,
              issue_number: context.issue.number,
              body: fs.readFileSync(path, 'utf8'),
            });

      - name: Fail on HIGH gaps
        if: steps.gap.outcome == 'failure'
        run: exit 1
```

要点：

- `continue-on-error: true` + 单独一步判断，才能"先贴评论、再让 check 失败"。
- **评论要在失败之前贴**，否则失败后讨论区什么也没有。
- 上游产物路径是你们自己定的，E 只认文件路径。

---

## 7. 降级与异常处理

| 情况 | E 的表现 | F 应该 |
| --- | --- | --- |
| 只有 A 的产物 | 正常退出 0，`gaps=[]`，`unscored` 列出全部 | 贴"暂无可用分析结果"，别贴空报告说"没问题" |
| `inputs.adequacy="missing"` | 分数按 Level 2 假设，置信度 LOW | 评论里标注降级 |
| 报告文件没生成但退出码 0 | 不应该发生；若发生是 bug | 不要贴空评论，记日志 |
| E 崩溃（退出码 1） | stderr 有异常摘要 | 贴"分析失败"并 @ 负责人 |

**最容易犯的错**：把 `gaps=[]` 渲染成"✅ 测试覆盖良好"。它可能是"没有发现缺口"，也可能是"没有数据"。看 `summary.changes_scored` 和 `inputs`。

---

## 8. 真实样例（flask#6168，实跑）

`--format markdown` 的**原始产物**（未经修改）：

```markdown
## Test Gap Report — pallets/flask #6168

- changes analysed: **3** / 7
- gaps: **1 high**, 2 medium, 0 low
- average score: **86.2** / 100
- not scored: 4 change(s), see report JSON

**src/flask/helpers.py::raise_any** — Risk: HIGH (GapScore 100/100, confidence MEDIUM)
Changed behavior : exception behaviour added: `BaseExceptionGroup`
Problem          : no test reaches this change
Suggested input  : an input that makes the new `BaseExceptionGroup` branch run

**src/flask/sessions.py::get_expiration_time** — Risk: MEDIUM (GapScore 79.3/100, confidence MEDIUM)
Changed behavior : return expression changed: `datetime.now(timezone.utc) + app.permanent_session_lifetime` -> `datetime.now(UTC) + app.permanent_session_lifetime`
Problem          : no test reaches this change

**tests/test_appctx.py::test_robust_teardown** — Risk: MEDIUM (GapScore 79.3/100, confidence MEDIUM)
Changed behavior : a branch was removed
Problem          : no test reaches this change
```

两点值得注意：

- **没有 "mapping coverage" 这一行**。它只在 C 跑过时才出现；这次 C 被跳过（无 testmon 数据库），所以整行都不打印，而不是显示 0%。
- `confidence MEDIUM` 是因为缺 C 的映射（`no_mapping`）。报告里出现降级标记**不是故障，是设计**。

> 这份产物是 E 用自己的渲染器生成的，可以直接当评论贴。F 若要定制排版，读 `gap_report.json` 自己渲染即可，字段见 §4。

---

## 9. 验收清单（F 自查）

- [ ] 能读到 `gap_report.json`，`schema_version == "1.0"`
- [ ] 退出码 0 / 2 / 3 三种情况都处理了（不是只判断"非 0 即失败"）
- [ ] 评论里展示了 `confidence` 和缺失的 `inputs`
- [ ] `gaps=[]` 时不说"没有问题"，而是说"未发现缺口"并给出 `changes_scored`
- [ ] 缺口条数做了上限
- [ ] 评论在 check 失败之前贴出
- [ ] E 崩溃时有人知道（@ 负责人，而不是静默）

---

## 10. 需要我配合的地方

- 想改评论格式 → 先看 §4，改渲染即可，不必动 E
- 想加字段 → 提出来，E 加字段是向后兼容的（只增不改）
- 想调阈值/权重 → 改 `gap-scoring/weights.py`，不要改 `scoring.py` 的公式

### PR 级一行摘要（已提供）

想在 check 名称、commit status 或评论标题里放一句话，直接用：

```python
from report import status_line
status_line(report)   # report 就是 gap_report.json 读出来的 dict
```

三种形态（都是纯文本，不含 markdown，CI 能直接显示）：

| 情况 | 输出 |
| --- | --- |
| 有缺口 | `Test gaps: 1 high, 2 medium, 0 low (avg 86.2/100, 3/7 changes scored)` |
| 分析了但没缺口 | `No potential test gaps in 1 scored change(s) (avg 43.55/100)` |
| 完全没数据 | `No analysable changes (7 change(s) in this PR)` |

**注意第三种**：它不会说"没有问题"，而是明说"没有可分析的变更"——这正是 §5 强调的诚实性。
