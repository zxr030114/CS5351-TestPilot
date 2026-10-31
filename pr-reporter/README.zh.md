# F 模块：PR 报告与验收评估——中文使用说明

F 是附加在 A–E 旁边的独立模块，主入口为 `pr_reporter.py`。它负责串联已有分析、生成报告、按需发布 GitHub PR 评论，以及运行固定场景的验收评估。

## 一、先看哪个文件

| 文件 | 用途 |
| --- | --- |
| `sample-reports/gap_report.zh.html` | 浏览器打开即可阅读的中文演示报告 |
| `sample-reports/gap_report.zh.md` | 可编辑、可复制的中文演示报告 |
| `sample-reports/evaluation.zh.md` | 中文验收评估结果及适用范围 |
| `sample-reports/gap_report.json` | 原始结构化报告，供程序读取 |
| `docs/course-alignment.zh.md` | 课件要求对照、两个非功能需求和风险登记 |

中文报告翻译自已保存的本地运行结果，没有重新运行分析，也没有修改分数、证据、提交标识或耗时。JSON 的字段名继续采用原有英文接口，避免影响 A–E 对接。当前主程序默认生成英文报告；本次提供的是额外的中文阅读版本。

## 二、环境与安装

需要 Python **3.10 或更高版本**、Git，以及同一仓库内的 A–E 目录。本次使用 Python 3.12.14 验证。

### 当前电脑：直接使用已经创建的环境

在仓库根目录打开 PowerShell，运行：

```powershell
.\pr-reporter\.venv\Scripts\python.exe .\pr-reporter\pr_reporter.py demo --out .\pr-reporter\out\demo
```

当前电脑系统默认的 `python` 版本较旧，建议直接使用上述虚拟环境路径。

### 其他电脑：首次安装

先确认下面的 `python` 是 3.10 及以上版本，再在仓库根目录运行：

```powershell
python --version
python -m venv pr-reporter/.venv
.\pr-reporter\.venv\Scripts\python.exe -m pip install -r pr-reporter/requirements.txt
.\pr-reporter\.venv\Scripts\python.exe -m unittest discover -s pr-reporter/tests -v
```

Linux/macOS 将解释器路径换为 `pr-reporter/.venv/bin/python`。

## 三、一条命令运行完整本地演示

```powershell
.\pr-reporter\.venv\Scripts\python.exe .\pr-reporter\pr_reporter.py demo --out .\pr-reporter\out\demo
```

它会依次：

1. 创建临时 Git 仓库，并提交新旧两个版本的取款函数。
2. 在旧版本执行真实 pytest 测试，使用 testmon 采集依赖数据库。
3. 调用 A 识别代码实体变化、B 检测返回精度变化。
4. 调用 C 查找历史相关测试、D 提取测试断言。
5. 调用 E 评分，再由 F 输出 JSON、Markdown、HTML。

演示中，返回值从保留两位小数改为一位，但现有测试只验证整数输入。预期结果为 **43.55 分、低风险、置信度高**。

这个命令不访问 GitHub、不调用模型，也不发布评论。新旧版本在临时仓库中处理，不切换用户工作区的提交，不改写用户原始 testmon 数据库。

### 输出文件

| 文件 | 含义 |
| --- | --- |
| `changes.json` | A 的代码实体变化报告 |
| `behavior.json` | B 的行为变化证据，附有仓库路径和模块路径 |
| `mapping.json` | C 的历史测试依赖结果；有可用数据库时生成 |
| `adequacy.json` | D 从完整新版本测试源码提取的断言证据 |
| `gap_report.json` | E 的分数、证据，加上 F 的运行标识和耗时 |
| `gap_report.md` | 适合 PR 评论的报告 |
| `gap_report.html` | 不依赖脚本的离线 HTML 报告 |

## 四、已有 A–E 报告时怎么用

### 1. 直接读取 E 的最终报告

将命令中的输入路径替换为实际文件：

```powershell
.\pr-reporter\.venv\Scripts\python.exe pr-reporter/pr_reporter.py report --report pr-reporter/sample-reports/gap_report.json --out pr-reporter/out/report
```

### 2. 从 A–D 的四份报告开始

下面的命令可以直接使用仓库已有样例：

```powershell
.\pr-reporter\.venv\Scripts\python.exe pr-reporter/pr_reporter.py score --changes gap-scoring/fixtures/changes.json --behavior gap-scoring/fixtures/behavior_with_path.json --mapping gap-scoring/fixtures/mapping_result.json --adequacy gap-scoring/fixtures/adequacy.json --out pr-reporter/out/fixture
```

缺少可选的 B/C/D 输入时，报告会明确说明分析降级。`--top 5` 控制最多展示五个缺口；没有可评分变化时，不会输出“测试充分”的结论。

## 五、分析真实项目的两个本地提交

```powershell
.\pr-reporter\.venv\Scripts\python.exe pr-reporter/pr_reporter.py run --repo-dir . --base HEAD~1 --head HEAD --collect-baseline --testmon-root mapping-demo --pytest-target test_payment.py --out pr-reporter/out/local
```

| 参数 | 含义 |
| --- | --- |
| `--repo-dir` | 被分析项目的本地 Git 仓库 |
| `--base` | 修改前提交，例如 `HEAD~1` 或完整 SHA |
| `--head` | 修改后提交，例如 `HEAD` 或完整 SHA |
| `--collect-baseline` | 在独立临时克隆中执行旧版本测试，重新采集 testmon 数据 |
| `--testmon-root` | 相对于被分析仓库的 pytest 根目录 |
| `--pytest-target` | 相对于 pytest 根目录的测试文件或 node ID，可重复指定 |
| `--out` | 输出目录 |
| `--timeout` | 单次子进程超时秒数，默认 120 |

上面的命令只使用 payment 演示测试作为基线。分析其他项目时，应调整测试根目录、测试目标和项目依赖。

**运行边界：**仅执行旧版本测试；新版本的业务代码与测试源码只做静态解析。默认关闭第三方 pytest 插件自动加载，只显式加载 testmon。需要额外插件或外部服务的项目，应配置相应运行环境。本工具不是代码执行隔离系统。

### 使用已有数据库

```powershell
.\pr-reporter\.venv\Scripts\python.exe pr-reporter/pr_reporter.py run --repo-dir . --base BASE_SHA --head HEAD_SHA --db mapping-demo/.testmondata --db-base-sha BASE_SHA --testmon-root mapping-demo --out pr-reporter/out/local
```

`BASE_SHA`、`HEAD_SHA` 是需要替换的占位符。`--db-base-sha` 表示调用方声明数据库对应的基线提交；工具不能独立证明数据库确实来自该提交，报告会记录这一边界。既不采集也不提供数据库时，结果会标明缺少测试映射数据。

## 六、发布或更新 GitHub PR 评论

```powershell
.\pr-reporter\.venv\Scripts\python.exe pr-reporter/pr_reporter.py report --report gap_report.json --out pr-reporter/out/published --publish --repo-name OWNER/REPOSITORY --pr 12 --expected-head FULL_HEAD_SHA
```

需要将仓库、PR 编号、报告路径和提交 SHA 替换为实际值。报告中的身份必须与发布目标一致，程序还会查询 GitHub 上当前 PR 的 head，拒绝发布已过期的分析。

发布需要运行环境中已有 `GITHUB_TOKEN`；GitHub Actions 可使用其内置令牌。程序只更新当前身份发布的、带 TestPilot 专属标记的评论；正文没有变化时不写入，不修改其他人的评论。

**普通演示和报告生成都不会发送评论。只有显式添加 `--publish` 才会请求发布。**

默认只提示潜在缺口。添加 `--fail-on-high` 后，程序先保存报告并完成请求的评论发布，再在存在高风险缺口时返回退出码 3。

| 退出码 | 含义 |
| --- | --- |
| 0 | 报告或评估正常完成 |
| 1 | 上游失败通知、未预期错误或发布失败 |
| 2 | 输入、依赖、超时或基线执行问题 |
| 3 | 显式高风险门禁未通过，或固定评估标准未通过 |

上游失败时可使用 `report --failure-file failure.txt --out ...`，生成“分析失败、无法给出充分性结论”的说明。随后成功运行会清理自身旧的失败标记。

## 七、GitHub Actions 工作流

配置位于 `.github/workflows/testpilot-f.yml`，主要完成：

1. 在只读权限的作业中运行 F 测试、固定评估和本地完整演示。
2. 对 PR 使用完整新旧提交快照进行分析。
3. 上传 JSON、Markdown、HTML 和测量结果；失败时也保留可用产物。
4. 使用单独具备评论写权限的作业更新同仓库 PR 的评论。

来自 fork 的 PR 只运行分析并提供产物，不自动发评论。仓库变量 `TESTPILOT_FAIL_ON_HIGH=true` 可启用高风险门禁。

分析与发布作业使用基线分支中的工具代码，因此需先把 F 模块及工作流合入基线分支，再验证这两项功能。当前已检查 YAML 并运行本地命令，**尚未进行线上 Actions 验证，也没有发布真实 PR 评论**。

## 八、运行固定验收评估

```powershell
.\pr-reporter\.venv\Scripts\python.exe pr-reporter/pr_reporter.py evaluate --out pr-reporter/out/evaluation
```

评估合同位于 `benchmarks/contract.json`，预先固定场景、标签、比较基线、阈值、重复次数和验收标准。输出记录合同 SHA256，便于确认是否更改过评估条件。

六个合成场景包括：三个存在缺口的场景、两个对照场景、一个真实标签未知的场景。未知标签不参与准确性统计，但需要展示不确定性。

指标包括精确率、召回率、特异度和本地评分/渲染 p95 耗时。比较对象是明确规定的“存在历史相关测试就认为足够”的启发式，**不是 testmon 原生的测试缺口检测器**；testmon 本来不预测断言缺口。

这些结果仅说明固定验收样例的表现，不能作为真实项目的准确率。评分/渲染耗时不包含 Git、pytest、网络；完整演示另行记录基线测试和总耗时。

## 九、模块文件分工

| 文件 | 作用 |
| --- | --- |
| `pr_reporter.py` | 主入口、命令分派和真实本地演示 |
| `integration.py` | A–E 串联、版本隔离、路径与证据身份适配 |
| `rendering.py` | E 报告校验、内容转义、限量 Markdown 和离线 HTML |
| `github_client.py` | GitHub REST、PR 身份检查和评论创建/更新 |
| `evaluation.py` | 固定场景评估、混淆矩阵、计时和验收判断 |
| `tests/test_reporter.py` | 报告、失败、模拟 API、门禁及真实本地集成测试 |

## 十、如何解读结果及当前局限

- **风险分数**表示潜在缺口的优先级；**置信度**表示输入和证据对该结果的支持程度，两者不同。
- 存在历史依赖不等于实际执行了变化分支；存在相关断言不等于能区分新旧行为。
- 本模块不执行新版本测试、不自动生成测试、不进行变异测试，也没有静态冷启动映射。
- 当前 F 完整串联主要支持 Python；本地重命名保守表示为删除加新增。
- A/B/D 的分析局限仍然存在；模块顶层暂不支持评分的变化会保留为未评分项。
- 六个验收样例不能替代独立标注的真实 PR 数据和用户反馈。
- 三轮 sprint、站会、燃尽图与成员实际工时需要团队真实维护。

中文版是阅读与交接材料，不改变主程序或 A–E 的接口。
