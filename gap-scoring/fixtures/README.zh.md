# E 模块测试夹具（fixtures）

这里是 2026-10-30 在 `work\` 里**实际跑通** C/D 链路后拿到的真实产物，用来开发 E 和做回归测试。

| 文件 | 来源 | 说明 |
| --- | --- | --- |
| `changes.json` | A（Member 1） | 由 C 里回显的 change 对象反推构造，四份数据才能凑成一条完整链路 |
| `behavior.json` | B（Member 2） | 原始输出，**不含 `file_path`** |
| `behavior_with_path.json` | B + 人工注入 | 注入了 `payment.py` 的版本，D 能正确消费 |
| `mapping_result.json` | C（Member 3） | 1 个变更 → 3 个测试，`status=matched`，`selection_complete=true` |
| `adequacy.json` | D（Member 4） | 上面两份跑出来的分析结果 |
| `payment_base.py` | `git show HEAD` | 改动前源码（`round(...,2)`） |
| `payment_head.py` | 工作区 | 改动后源码（`round(...,1)`） |

## 这份数据讲的什么故事

`withdraw` 的返回精度从 2 位改成 1 位：

```
- return round(balance - amount,2)
+ return round(balance - amount,1)
```

B 识别出 1 条 `RETURN_BEHAVIOR_CHANGE`，C 匹配到 3 个历史相关的测试，D 判定 `assertion_evidence_found`——**看起来"测得好好的"**。

但翻开证据：

| 测试 | 行 | 断言 | strength |
| --- | --- | --- | --- |
| `test_normal_withdraw` | 7 | `withdraw(100, 30) == 70` | `potential_match` |
| `test_insufficient_balance` | 11 | `pytest.raises(ValueError, ...)` | `weak_or_mismatched` |
| `test_negative_amount` | 16 | `pytest.raises(ValueError, ...)` | `weak_or_mismatched` |

**唯一的数值断言是整数 70**——`round(70.0, 2)` 和 `round(70.0, 1)` 都得 70，这个测试根本区分不了新旧实现。D 自己的 `recommendations` 也明说了："an integer-only example cannot establish rounding adequacy."

> 这正是 E 存在的意义：**顶层 `status` 会骗人，真相在 `evidence[].strength` 和 `recommendations` 里**。

## 典型场景（`scenarios/`）

`scenarios/` 下每个目录是一组自洽的四份报告，用来固定"不同风险类别应该得多少分"。由 `tools/make_scenarios.py` 生成，输出已提交，测试运行时不依赖生成脚本。

| 场景 | 分数 | 等级 | 置信度 | 说明 |
| --- | --- | --- | --- | --- |
| `00-precision-blind`（即本目录） | 43.55 | LOW | HIGH | 精度改动，只有整数断言 → 证据复核降级 |
| `01-exception-uncovered` | 96.25 | HIGH | HIGH | 新增异常、无测试到达 → 异常规则 + 关联测试不可达 |
| `02-boundary-no-assertion` | 79.92 | MEDIUM | HIGH | 边界 18→21，测试执行到但没断言 |
| `03-new-branch-covered` | 0.00 | NONE | HIGH | 新分支被断言覆盖 → 抑制为 0，不进报告 |
| `04-documentation-only` | 0.00 | NONE | HIGH | 只改 docstring → 直接不打分 |
| `05-mapping-unresolved` | 79.30 | MEDIUM | LOW | C 无法解析映射 → 两个警号叠加 |

预期值写在 `scenarios/expected.json`，由 `tests/test_scenarios.py` 逐条比对。**这些数字是手写的、不是代码算出来的**，所以校准一旦漂移，测试会失败而不是悄悄通过。

> 05 场景的置信度是 LOW 而不是 MEDIUM：C 在 `unresolved` 时同时会把 `selection_complete` 置为 false，两个警号叠加（`mapping_unresolved` + `selection_incomplete`）。这是真实 C 的行为，不是 fixture 编的。

## 复现命令

```powershell
$root = "D:\Desktop\港城课程\CS5351\project"
$repo = "$root\CS5351-TestPilot"
$env:PYTHONPATH = "$root\pyshim;$root\deps;$repo\pytest-testmon;$repo\pr-analyzer"
$env:TMP = "$root\.tmp3"; $env:TEMP = $env:TMP
New-Item -ItemType Directory -Force -Path $env:TMP | Out-Null

# 1) 建 testmon 依赖库（在基线版本上）
cd "$root\work\mapping-demo"
python -m pytest -p testmon.pytest_testmon --testmon -v test_payment.py

# 2) 改 payment.py：round(...,2) → round(...,1)

# 3) C：生成映射
cd "$root\work"
python mapping-demo/map_changes.py --local-file mapping-demo/payment.py --base HEAD `
  --db mapping-demo/.testmondata --test-root mapping-demo `
  --output mapping-demo/mapping_result.json

# 4) B：生成行为报告
cd "$repo\pr-analyzer"
python main.py --behavior-old "$root\work\out\payment_base.py" `
  --behavior-new "$root\work\out\payment_head.py" -o "$root\work\out\behavior.json"

# 5) D：分析
cd "$root\work"
python adequacy-analyzer/test_adequacy_analyzer.py `
  --behavior out/beh_payment.py.json --mapping mapping-demo/mapping_result.json `
  --test-root . --output out/res_payment.py.json
```

## 三个必须知道的坑

**① `file_path` 必须是"模块相对路径"，不是仓库相对路径**

| 传给 D 的 `file_path` | D 的结果 |
| --- | --- |
| `payment.py` | ✅ `assertion_evidence_found`，3 个候选测试 |
| `mapping-demo/payment.py` | ❌ `no_candidate_test`，0 个候选 |

原因是 D 拿 `file_path` 去匹配 `from payment import withdraw` 里的模块名。**A 给的是仓库相对路径**，所以 E 必须自己做这层转换（找到 import root）。

**② B 不输出 `file_path`**，多文件 PR 必须由调用方按文件注入，否则 D 会按函数名后缀乱匹配。

**③ testmon 不用装**，把 `pytest-testmon` 目录加进 `PYTHONPATH`、用 `-p testmon.pytest_testmon` 显式加载插件即可（`pip install` 在这台机器上会卡在编译）。

## 这份 fixture 的局限

- 只有一个变更、一个文件、一种行为类型（`RETURN_BEHAVIOR_CHANGE`）
- 没有覆盖 `NEW_BRANCH` / `BOUNDARY_CHANGE` / `EXCEPTION_BEHAVIOR_CHANGE`
- 没有覆盖 C 的 `file_fallback` / `needs_new_tests` / `unresolved` 状态

后续补齐见 `TASKS.zh.md` 的 5.2（5 个典型场景 fixture）。
