# PR / git Change Analyzer（Member 1）交接文档

> 面向两位下游同学：**Member 2（Behavioral Change Detection）** 和 **Member 3（测试依赖映射）**。
> 第一部分讲清“这个文件夹里有什么、怎么跑、输出长什么样”；
> 第二部分讲清“拿到 `output.json` 之后，行为变更检测具体怎么用，能直接照抄的代码在哪”。

仓库位置：`CS5351-TestPilot-main/`，本模块位于 `CS5351-TestPilot-main/pr-analyzer/`。
约定：**所有代码、注释、README 一律英文**；只有本文档是中文，方便组内交接。

---

## 0. 五分钟上手（TL;DR）

```powershell
$py = "C:\Users\kunkunji\anaconda3\envs\se\python.exe"
cd "C:\Users\kunkunji\anaconda3\envs\se\CS5351-TestPilot-main\pr-analyzer"

# 1) 不联网自检：内置新旧源码，打印 add / delete / modify + change_category
& $py main.py --demo-compare

# 2) 跑单元测试（63 个，全部离线，不需要网络）
& $py -m unittest discover -s tests

# 3) 真实 PR 全流程：镜像源码 -> 提取实体 -> 新旧对比 -> 识别 rename -> 写 output.json
$env:GITHUB_TOKEN = "ghp_xxx"      # 可选；不设也能跑，但限额 60 次/小时
& $py main.py --repo pallets/flask --pr 6168
# 结果默认写入 pr-analyzer/output.json
```

退出码：`0` 成功，`1` 未预期异常，`2` 参数/输入错误（含 GitHub 401/404、限流放弃、下载全失败）。

---

## 第一部分：文件夹结构、文件作用、怎么测试与使用

### 1.1 整体结构

```
CS5351-TestPilot-main/
├── README.md                     # 团队协作手册（Git 用法、仓库地址），不是本模块文档
├── .gitignore                    # 仓库级忽略规则（注意：不含 .env）
├── pytest-testmon/               # 老师提供的 testmon 源码，我们只读、用来对齐机制
└── pr-analyzer/                  # ★ Member 1 的模块
    ├── analyzer.py               # 核心库：diff 解析 / 噪音过滤 / GitHub 拉取 / AST / 对比 / 流水线
    ├── main.py                   # CLI 入口：参数解析、离线 demo、写 output.json、退出码
    ├── requirements.txt          # 依赖清单
    ├── .env.example              # 环境变量示例（复制成 .env 后填 token）
    ├── .gitignore                # 模块级忽略：.env、__pycache__、out/、output.json
    ├── README.md                 # 模块英文文档（字段含义、API 表、限制）—— 和本文互补
    ├── tests/                    # 单元测试，全部离线（假 session，不联网）
    │   ├── test_ast_entities.py        # 实体抽取 + 三种指纹
    │   ├── test_compare_entities.py    # add/delete/modify 与四类 change_category
    │   ├── test_diff_lines.py          # hunk 内逐行新旧行号
    │   ├── test_pr_sources.py          # 源码镜像（布局、manifest、路径安全、断点续传）
    │   └── test_analyze_pr.py          # 端到端流水线 + find_renames
    ├── out/                      # 【生成物】源码镜像目录，默认 out/pr_<N>/{base,head,manifest.json}
    ├── output.json               # 【生成物】默认输出文件（样例为 pallets/flask#6168 的结果）
    └── docs/                     # （本文件所在位置，位于仓库根 docs/ 下）
```

`out/` 与 `output.json` 都已在 `pr-analyzer/.gitignore` 中忽略，**不要提交**。

### 1.2 各文件职责

| 文件 | 作用 | 什么时候看它 |
| --- | --- | --- |
| `analyzer.py` | 全部核心逻辑。顶部 160 行 docstring 是**接口契约**（Schema 2.0、行号约定、指纹约定、下游用法） | 想改行为、想确认字段语义时先读 docstring |
| `main.py` | 命令行入口。负责参数、互斥校验、四种运行模式（本地 diff 报告 / 单文件 AST 演示 / 新旧对比演示 / 真实 PR） | 只想跑结果时只用它 |
| `requirements.txt` | 运行依赖 | 换机器时 `pip install -r requirements.txt` |
| `.env.example` | 只列 `GITHUB_TOKEN`（私有仓库必需，公开仓库可选） | 首次配置 token |
| `tests/` | 63 个断言级测试，不需要网络、不需要 token | 改完代码先跑它 |
| `out/pr_<N>/` | 每个 PR 的 base/head 完整源码 + `manifest.json`（每文件 sha256/字节/行数/是否二进制/错误） | 想离线复现分析、或做跨文件比对时 |

### 1.3 依赖与环境

- Python **3.10+**（本机 `se` 环境是 3.10.21）。
- 关键依赖：`tree-sitter==0.21.3` + `tree-sitter-languages==1.10.2`（**必须钉在 0.21.x**，0.22+ 会报 `Language version mismatch`）、`requests`、`unidiff`（可选，缺了就用手写 diff 解析器）、`python-dotenv`（可选）、`PyGithub`（仅为兼容保留，当前实现用 `requests` 直连 REST）。
- 若 `tree-sitter-languages` 装不上，代码会自动回退到 `tree-sitter-language-pack`，两者 API 相同。
- 本机特殊情况：`se` 环境的 `pip` 因临时目录权限被沙箱拒绝而无法直接装包，当前是用“手工解 wheel 到 `Lib\site-packages`”的方式装的；换机器正常 `pip install -r requirements.txt` 即可。

### 1.4 四种运行模式

| 命令 | 是否联网 | 输出 | 用途 |
| --- | --- | --- | --- |
| `main.py --demo` | 否 | 终端 | 演示“二进制/锁文件/文档过滤 + hunk 分块 + 行号范围” |
| `main.py --demo-ast [--demo-lang java]` | 否 | 终端 | 演示单个文件抽出的 class/function/method 及其三种指纹 |
| `main.py --demo-compare` | 否 | 终端 | 演示 base/head 对比与 `change_category` 判定（最直观） |
| `main.py --repo owner/name --pr N` | 是 | `output.json` | **真实 PR 全流程**，下游要的就是这份 JSON |
| `main.py --repo . --base HEAD~1 --head HEAD` | 否 | 终端/`-o` | 本地仓库两个 commit 的 diff 报告（Schema 1.0，与 AST 流水线是两套输出） |

常用参数：

```text
--repo owner/name        仓库名（也接受完整 URL 或 git@ 形式）
--pr N                   PR 编号
--analyze                强制走 AST 流水线（给了 --repo owner/name --pr 时自动开启）
--mirror-dir DIR         源码镜像目录，默认 out/pr_<N>
--force                  强制重新下载镜像（默认断点续传，已存在则跳过）
--min-similarity 0.9     跨文件 rename 判定的相似度阈值（0~1）
-o, --output FILE        输出文件，默认 output.json
--summary-only           只打印 summary，不写文件
-v, --verbose            打印调试日志
```

### 1.5 怎么测试

```powershell
$py = "C:\Users\kunkunji\anaconda3\envs\se\python.exe"
cd "C:\Users\kunkunji\anaconda3\envs\se\CS5351-TestPilot-main\pr-analyzer"

& $py -m py_compile analyzer.py main.py            # 语法检查
& $py -m unittest discover -s tests -v             # 63 个测试
& $py main.py --demo-compare                       # 端到端目视检查
```

在线自检（需要网络，可选 token）：

```powershell
& $py main.py --repo pallets/flask --pr 6168
# 预期：exit 0，12 个变更文件 / 6 个被分析 / 5 个被跳过 / 7 条 change / 0 个 rename
```

### 1.6 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `HTTP 401` | token 无效或过期 | 重新生成，写入 `.env` 的 `GITHUB_TOKEN` |
| `HTTP 403 / rate limit` | 未登录只有 60 次/小时 | 配置 token（5000 次/小时），或等 `X-RateLimit-Reset` |
| `HTTP 404` | 仓库私有 / PR 号写错 / 文件在该 revision 不存在 | 确认仓库与 PR 号，私有仓库必须带 token |
| `unsupported language`（出现在 `skipped`） | 该文件不是 `.py`/`.java` | 正常跳过，不计入变更 |
| `noise: docs` / `lock-or-license` / `binary-asset` | 噪音过滤规则命中 | 正常跳过 |
| `Cannot infer the language` | 直接调用 `compare_sources()` 时没给 `file_path` | 传 `file_path="x.py"` 或 `language="python"` |
| `Language version mismatch` | tree-sitter 版本 > 0.21 | 降回 `tree-sitter==0.21.3` |

---

## 第二部分：Behavioral Change Detection（Member 2）如何使用本模块输出

### 2.1 数据流中的位置

```
Member 1  pr-analyzer  ──output.json──▶  Member 2  Behavioral Change Detection  ──▶  Member 3  测试依赖映射
          “改了什么”                      “行为变没变、变了多少”                    “哪些测试要跑”
```

Member 1 只回答**结构层面**的事实：哪个文件的哪个实体（class/function/method）发生了哪类结构变化、在哪几行、旧版叫什么、是不是重命名。
Member 2 在此之上判断**语义/行为层面**的影响；Member 3 再把“受影响实体”映射到“覆盖它们的测试”。

**不要**让 Member 2 重新解析 diff 或源码——`output.json` 已经把行号、实体、类别、指纹都算好了。

### 2.2 输出结构（Schema 2.0）

顶层：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | string | `"2.0"`（AST 流水线）；diff 报告模式是 `"1.0"`，别混用 |
| `mode` | string | `"pr_ast"` |
| `repo` | object | `owner` / `name` / `base_sha` / `head_sha` / `pr_number` |
| `mirror` | object | `out_dir` + `counts{files,downloaded,kept,failed}`，指向本次用的源码镜像 |
| `summary` | object | `files_changed` / `files_analyzed` / `files_skipped` / `changes` / `renames` / `by_change_type` / `by_change_category` / `warnings` |
| `files` | array | 每个被分析文件一条：`file_path` / `language` / `is_test_file` / `changes[]` |
| `changes` | array | **扁平列表**，就是上面 `files[].changes` 的并集，下游最常用 |
| `skipped` | array | `{path, reason}`：噪音、非 Python/Java、解析失败 |
| `warnings` | array | 字符串，例如镜像部分文件下载失败 |

单条 change（**下游唯一需要消费的粒度**）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `entity_type` | string | `class` / `function` / `method`（小写） |
| `entity_name` | string | 实体短名，如 `calculate_total`（嵌套方法只给短名，靠 `line_start` 区分） |
| `change_type` | string | `add` / `delete` / `modify` / `rename` |
| `change_category` | string \| null | `Signature change` / `Logic change` / `Documentation change` / `Rename / Move`；`add`/`delete` 为 `null` |
| `line_start` / `line_end` | int | 1-based 闭区间；`modify`/`add`/`rename` 用**新版**行号，`delete` 用**旧版**行号 |
| `summary` | string | 人读的一句话，如 `AST structural change in method body` |
| `file_path` | string | 仓库相对 POSIX 路径（rename 时是**新路径**） |
| `old_path` / `new_path` / `old_qualname` / `new_qualname` / `similarity` | 额外 | 仅 `rename` 记录有 |

> 契约：**每条记录固定包含前 7 个键**（`entity_type`、`entity_name`、`change_type`、`change_category`、`line_start`、`line_end`、`summary`），`file_path` 由所在 `files[]` 提供；`changes[]` 里每条额外带 `file_path`。后续版本**只增字段、不改已有字段含义**。

### 2.3 `change_category` 的判定规则（决定 Member 2 要不要深挖）

对比时分别计算三个指纹（都是对 AST 子树做归一化后的 sha256 前缀）：

- `signature_hash`：类型名 + 参数列表 + 返回类型（Python 还含默认值、`async`、装饰器）。
- `body_hash`：函数/方法体（已剔除 docstring 与注释）。
- `docstring_hash`：docstring 文本。

判定优先级：**签名变化 > 函数体变化 > 仅文档变化**。

| 类别 | 条件 | 对行为的意义 | Member 2 该做什么 |
| --- | --- | --- | --- |
| `Signature change` | `signature_hash` 变 | 接口变了：调用方可能编译/运行失败，或语义按新参数改变 | **最高优先级**：找所有调用方，标记破坏性变更 |
| `Logic change` | 签名没变、`body_hash` 变 | 行为几乎一定变了 | 交给 Member 3 跑覆盖该实体的测试；需要更细粒度时再对 `node_text` 做语句级 diff |
| `Documentation change` | 只有 `docstring_hash` 变 | 运行行为不变 | **直接跳过**，这是最大的省时点 |
| `Rename / Move` | 跨文件配对成功（`node_hash` 完全一致，或相似度 ≥ 阈值） | 名字/位置变了，行为通常不变 | 更新依赖映射的“实体键”，**不要**当成 delete+add 重跑全部测试 |
| `add` | 新版有、旧版无 | 新行为，没有历史基线 | 标记“需要新测试”，而不是“需要重跑旧测试” |
| `delete` | 旧版有、新版无 | 行为被移除 | 查调用方是否还引用；相关测试应删除或标记失效 |

注意两个边界：

1. Python 的装饰器变化（如新增 `@property`）算 `Signature change`，因为签名把装饰器计入。
2. Java 的 Javadoc 不在 AST 里，**改注释不会产生任何记录**——这与“行为未变”的结论一致，但意味着 Java 侧拿不到 `Documentation change`。

### 2.4 可直接运行的消费示例

```python
import json
from collections import Counter

def load_behaviour_workload(path="pr-analyzer/output.json"):
    """Member 2 的入口：把 output.json 过滤成“需要做行为分析”的变更集合。"""
    with open(path, encoding="utf-8") as fh:
        report = json.load(fh)

    assert report["schema_version"] == "2.0", "this is the AST pipeline output"

    workload = []
    for change in report["changes"]:
        # 1) rename：行为没变，只需迁移依赖映射，交给 Member 3 的 key 重写逻辑
        if change["change_type"] == "rename":
            continue
        # 2) 纯文档：AST 已经证明运行行为不变
        if change["change_category"] == "Documentation change":
            continue
        # 3) 其余进入行为分析
        workload.append({
            "key": f'{change["file_path"]}::{change["entity_name"]}@{change["line_start"]}',
            "file_path": change["file_path"],
            "entity_name": change["entity_name"],
            "entity_type": change["entity_type"],
            "change_type": change["change_type"],
            "change_category": change["change_category"],
            "lines": (change["line_start"], change["line_end"]),
            "priority": "high" if change["change_category"] == "Signature change" else "medium",
        })
    return workload, report["summary"]


if __name__ == "__main__":
    items, summary = load_behaviour_workload()
    print("files changed:", summary["files_changed"])
    print("behaviour workload:", len(items))
    print(Counter(i["change_category"] for i in items))
```

跑在仓库自带的 flask 样例上，输出是：

```
files changed: 12
behaviour workload: 7
Counter({'Logic change': 6, 'Signature change': 1})
```

### 2.5 建议 Member 2 的输出契约（给 Member 3 用）

```json
{
  "entity_key": "src/flask/helpers.py::raise_any@676",
  "file_path": "src/flask/helpers.py",
  "line_start": 676,
  "line_end": 679,
  "change_category": "Logic change",
  "behaviour_changed": true,
  "severity": "medium",
  "reason": "body AST changed; signature unchanged",
  "callers": ["src/flask/app.py::Flask.handle_user_exception"]
}
```

`entity_key` 用 `file_path::entity_name@line_start` 最稳（`output.json` 里的 change 记录没有 `qualname`，同名嵌套方法靠行号区分；rename 时用 `new_path::new_qualname`）。
如果需要分层级的全限定名（如 `Account.deposit`）：用 `extract_entities(parse_to_ast(source, "python"))`，它返回的实体字典里带 `qualname`；rename 记录本身也带 `old_qualname` / `new_qualname`。

### 2.6 与 Member 3（测试依赖映射）的衔接

- **Join key**：`file_path`（仓库相对 POSIX 路径）+ `line_start/line_end`（新版行号）。Member 3 用这两项去 testmon 的覆盖率数据里找“哪些测试执行过这段区间”。
- 如果 Member 3 仍用 `pytest-testmon` 的 checksum 体系：本模块的 `body_hash` **不能**直接替代 testmon 的 checksum（算法不同），但可以当“内容是否变化”的快速判据；真正的依赖映射仍以行号区间为准。
- `rename` 记录必须迁移映射：把旧 `(old_path, old_qualname)` 的历史依赖复制到 `(new_path, new_qualname)`，否则 Member 3 会误判“全新代码、无测试覆盖”。

### 2.7 验收清单（Member 2 自查）

- [ ] 只读 `output.json`，不再解析源码/diff。
- [ ] `Documentation change` 一律跳过（可用 flask 样例验证：改 docstring 不会出现在 `changes` 里）。
- [ ] `Signature change` 全部标为高优先级。
- [ ] `rename` 不当成 delete+add，且能更新下游 key。
- [ ] `add` 输出“需要新增测试”，而不是“需要重跑现有测试”。
- [ ] 对 `skipped` 里的 `unsupported language` 不报错（噪音文件本来就不该进分析）。
- [ ] 遇到 `schema_version != "2.0"` 直接报错退出，避免把 diff 报告的 1.0 结构当 AST 输出读。

---

## 附：本模块对外 API 速查

| API | 用途 |
| --- | --- |
| `analyze_pr(repo_name, pr_number, *, token=None, out_dir=None, force=False, min_similarity=0.9)` | 全流程，返回可直接 `json.dump` 的 dict |
| `get_pr_source_files(...)` | 只做源码镜像，返回 manifest |
| `parse_to_ast(code, language, *, source_path=None)` | 单文件解析成 `ASTDocument` |
| `extract_entities(ast_doc)` | 抽实体（含三种指纹、行号、字节区间） |
| `compare_entities(old_entities, new_entities)` | 单文件新旧对比，返回 changes |
| `compare_sources(old_src, new_src, *, file_path)` | 上面两步的便捷封装 |
| `find_renames(old_by_path, new_by_path, *, min_similarity=0.9)` | 跨文件 rename 配对 |
| `flatten_affected_scopes(report)` | 从 diff 报告（1.0）里取 `path::qualname` 列表，供旧流程使用 |

更细的字段与实现说明见 `pr-analyzer/README.md`（英文）与 `analyzer.py` 顶部 docstring。
