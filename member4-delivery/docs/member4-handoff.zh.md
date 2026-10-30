# Member 4：Test Adequacy Analyzer 交付与使用说明

本模块根据你提供的第四位成员分工，以及小组仓库现有接口实现。它是一个可运行、可测试的静态分析原型，覆盖 pytest AST 解析、assert 提取、pytest.raises 检测、行为变化与断言匹配四部分。

## 1. 交付文件

```text
adequacy-analyzer/
  test_adequacy_analyzer.py   核心分析器和命令行入口
  demo.py                    对接成员 2 与现有 payment 测试的离线演示
  README.md                  英文接口说明、运行方法与局限
  .gitignore                 忽略演示生成物
  tests/test_adequacy.py      20 项单元及集成测试
docs/member4-handoff.zh.md    本说明
sample-reports/
  behavior.json              演示生成的成员 2 行为报告
  adequacy.json              演示生成的成员 4 分析报告
```

解压后，将 `adequacy-analyzer` 文件夹复制到小组仓库根目录，与 `pr-analyzer`、`mapping-demo` 平级。中文说明和样例报告可作为交接附件。代码、注释和模块 README 使用英文，与仓库约定保持一致。

本次仅在本地准备代码，未推送、提交或修改 GitHub 上的仓库。

## 2. 与前三位成员的关系

```text
Member 1：PR / git 变化分析
       ↓
Member 2：behavior_changes，指出潜在行为变化
       ↓                         ↓
Member 3：testmon 测试依赖映射    Member 4：理解每项行为变化
       ↓                         ↑
       selected_tests ───────────┘
                                 ↓
                    解析候选测试，提取断言，输出证据和缺口
```

成员 4 不重写成员 1 的 diff 分析，也不修改成员 3 的 testmon 数据库。它读取成员 2 的独立行为报告，以及成员 3 的可选映射报告。成员 1 的 `output.json` 不能直接充当 `--behavior`，需要先经过成员 2。

接口使用现有 `behavior_changes`、`function`、`type`、`new_exception_type`、`old_boundary`、`new_boundary` 等字段。若处理多个模块，建议调用方给每条行为变化补充 `file_path`，减少同名函数误匹配。

## 3. 如何运行

使用 Python 3.10 及以上版本；本次在 Python 3.12.14 下验证。系统默认 Python 3.9 无法运行成员 2 中使用 `ast.Match` 的部分，所以联调时要选择正确解释器。成员 4 自身没有第三方依赖，也不需要 API key。

在小组仓库根目录执行：

```sh
python -m unittest discover -s adequacy-analyzer/tests -v
python adequacy-analyzer/demo.py
```

演示应输出：

```text
{"behavior_changes": 3, "tests_analyzed": 4, "with_assertion_evidence": 3}
NEW_BRANCH: assertion_evidence_found
EXCEPTION_BEHAVIOR_CHANGE: assertion_evidence_found
RETURN_BEHAVIOR_CHANGE: assertion_evidence_found
```

结果保存在 `adequacy-analyzer/out/`。

消费真实的成员 2 / 3 报告时：

```sh
python adequacy-analyzer/test_adequacy_analyzer.py --behavior behavior.json --mapping mapping_result.json --test-root . --output adequacy_result.json
```

这里 `--test-root .` 是小组仓库或被分析项目的根目录，必须与映射报告中测试 node ID 的路径起点一致。它不一定等于创建 testmon 数据库时的 pytest root。省略 `--mapping` 会检查目录内所有默认命名的测试文件。

## 4. 核心算法怎么讲

1. 用 `ast.parse` 读取测试源码，识别 `test_*` 函数、异步测试和 `Test*` 类中的测试方法。
2. 提取 `assert` 的表达式、行号和关联调用。支持直接断言、导入别名，以及简单的“调用结果赋值后再比较”。无关断言不会因为在同一个测试函数内就被归给目标调用。
3. 提取 `pytest.raises` 的异常类型、异常元组、可选 `match`，并检查目标调用是否在对应的 with 代码块中。也支持 `pytest.raises(Error, callable, ...)`。
4. 结合成员 2 的行为类型，寻找结果比较或预期异常的静态证据。结合成员 3 的测试列表缩小范围，输出测试 node ID、断言行号、证据强度和补测建议。

## 5. 如何理解结果

| 状态 | 含义 |
| --- | --- |
| assertion_evidence_found | 找到了语法层面相关的比较或异常断言 |
| weak_assertions | 仅有真值检查，或断言形式、异常类型不符合变化类型 |
| no_matching_assertion | 测试调用了目标函数，但没有关联到对应断言 |
| no_candidate_test | 提供或筛选后的测试中没有识别到目标调用 |
| unsupported_behavior | 行为类型暂不支持，或缺少函数身份 |

**这些状态不是“测试充分/不充分”的数学证明。** `runtime_adequacy_proven` 始终为 false。`input_complete` 只说明输入读取和映射选择是否完整，不能解释成覆盖充分。

例如原有 payment 测试 `assert withdraw(100, 30) == 70` 的确检查了返回值，但当变化涉及保留一位还是两位小数时，这个整数例子不足以区别新旧实现。分析器会保留这条断言证据，并建议添加能区分精度的小数输入；目前不会自动证明期望值是否正确。

## 6. 验证范围与尚未实现的部分

本次通过 20 项离线测试，覆盖别名、赋值追踪、无关断言、嵌套 helper 排除、异常上下文、错误异常类型、异常移除、参数化 node ID 筛选、缺失测试、语法错误和真实成员 2 API 联调；演示和命令行入口也已跑通。

演示使用仓库已有的 payment 测试，以及明确构造的新旧 withdrawal 源码，**不是 pytest-testmon 的真实 PR 分析结果**。成员 3 的输出兼容性用接口测试数据验证，本次没有采集真实 testmon 数据库，也没有完成真实 PR → 行为分析 → 依赖映射 → 充分性分析的整条运行。因此汇报时应将它称为静态证据原型。

当前不展开 fixture、helper 内部调用、参数化用例的具体取值，也不解析复杂对象方法分派、动态导入或复杂数据流。尚未执行候选测试、验证分支覆盖率或进行 mutation testing。后续可用这些运行证据提升“充分性”判断的可信度。
