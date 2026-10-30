# 论文阅读总结（E 模块）

> **写作边界**：本总结基于公开摘要、DOI 页面与检索到的二手描述整理，**未逐篇精读全文**。
> 标 ⚠️ 的地方是需要到原文核对的具体数据或作者信息；引用前请务必核对，不要把这份总结当作最终引用源。
> 完整清单（20 条）见 [`related-work.zh.md`](related-work.zh.md)，本文只挑与 E 关系最紧的八篇。

---

## 一、三篇必读

### 1. Inozemtseva & Holmes, *Coverage is not strongly correlated with test suite effectiveness*, ICSE 2014

| | |
| --- | --- |
| **问题** | 覆盖率高的测试套件，是不是真的更能发现缺陷？ |
| **做法** | 在多个开源 Java 项目上，比较不同的覆盖率准则（语句/分支等）与测试有效性指标（以变异杀伤率衡量）之间的关系 ⚠️ 具体项目数与统计方法需核对 |
| **结论** | 覆盖率与有效性**只是弱相关**；换一种覆盖率准则，结论也差不多 |
| **对 E 的意义** | **这是 E 存在的理论前提。** 如果覆盖率就够，D 的 `assertion_evidence_found` 可以直接当结论，E 没有存在必要 |
| **写进报告可以这么说** | "我们不把覆盖率当作充分性的判据：已有实证表明覆盖率与测试有效性之间只有弱相关 [Inozemtseva & Holmes 2014]，因此 E 把'是否被断言验证'而不是'是否被执行'作为主信号。" |

### 2. Zhang & Mesbah, *Assertions are strongly correlated with test suite effectiveness*, ESEC/FSE 2015

| | |
| --- | --- |
| **问题** | 既然覆盖率不好用，那什么指标更能解释测试的有效性？ |
| **做法** | 以**断言数量**为核心指标，与测试有效性（同样以变异杀伤衡量）做相关性分析 ⚠️ 具体模型与系数需核对 |
| **结论** | 断言数与有效性**强相关**，比覆盖率更能解释有效性 |
| **对 E 的意义** | 支撑两件事：① 打分的权重重心放在 adequacy（0.55）而不是结构项（0.45）；② 证据复核要盯着"断言到底检查了什么"，而不是"有没有断言" |
| **注意** | 这篇讲的是**断言数量**，E 用的是**断言质量**（能不能观测到改动）。引用时不要说成同一件事——E 的增量正在于此 |

### 3. *Prioritizing Test Gaps by Risk in Industrial Practice: An Automated Approach and Multimethod Study*, IEEE TSE 2025（DOI `10.1109/TSE.2025.3556248`）

| | |
| --- | --- |
| **问题** | 工业界发现了一堆"未被测试覆盖的改动"（test gap），但数量太多，怎么排优先级？ |
| **做法** | 为每个 test gap 计算**风险分**（由多个指标组合而成），并在真实工业环境中用多种方法评估 ⚠️ 具体指标构成与评估设计需核对原文 |
| **结论** | 风险化排序在实践中可行且被接受 |
| **对 E 的意义** | **最接近的同题工作。** E 的"行为风险 × 变更重要度 × 覆盖缺口 + 规则加成"是同类思路的一个轻量、可解释实现 |
| **E 的差异（写 related work 时必须点出）** | ① 行为级而非行级（用 B 的四类行为）；② 用断言证据而非覆盖率；③ 对 D 的结论做二次复核；④ 显式输出置信度 |
| ⚠️ **作者列表** | 检索时被反爬拦截，未能确认；请用 DOI 到 IEEE Xplore 查全 |

---

## 二、五篇略读

| 文献 | 一句话 | 用在 E 的哪里 |
| --- | --- | --- |
| **Barr, Harman, McMinn, Shahbaz, Yoo**, *The Oracle Problem in Software Testing: A Survey*, IEEE TSE 2015 | "判断测试是否通过"本身就是难题（oracle 问题） | 解释文案必须用 "Potential" 而非断定缺失；也是"证据可能假阳性"的理论依据 |
| **Yoo & Harman**, *Regression Testing Minimization, Selection and Prioritization: A Survey*, STVR 2012 | 测试最小化/选择/排序三者的关系与权衡 | E 的定位：在"选择"（C）之后做"排序"（priority），不是替代 C |
| **Just, Jalali, Inozemtseva, Ernst, Holmes, Fraser**, *Are mutants a valid substitute for real faults?*, FSE 2014 | 变异体能否代表真实缺陷 | 回答"为什么不做变异测试"：成本高，且 E 的目标是**提示**不是证明 |
| **Johnson, Song, Murphy-Hill, Bowdidge**, *Why Don't Software Developers Use Static Analysis Tools to Find Bugs?*, ICSE 2013 | 开发者不用静态分析的主因是误报与不可信 | 置信度模型（2.4）与措辞红线（"Potential"）的直接依据 |
| **van Deursen, Moonen, van den Bergh, Kok**, *Refactoring Test Code*, XP 2001 | test smell 的经典分类（含断言相关坏味道） | 解释文案里描述"断言太弱"的词汇来源 |

> 另外两条非论文但重要的"工业证据"：
> - **Teamscale 的 Test Gaps 功能文档**：明确写了"只把非平凡改动算作改动"，否则改变量名会被误判为未测试——这正是 E 跳过 `Documentation change` 的同一条理由。
> - **Meta 的 Predictive Test Selection**（ICSE-SEIP 2019）：工业界用 ML 做测试选择；E 选择规则化是为了**可解释**，这是有意识的取舍，不是能力不足。

---

## 三、引用位置表

写课程报告时，按这张表把文献放到对应章节：

| 报告章节 | 引哪篇 | 支撑什么论点 |
| --- | --- | --- |
| Introduction / 动机 | Inozemtseva & Holmes 2014 | "看起来测了"是个真问题 |
| Related Work | TSE 2025、Teamscale 文档 | 已有同题工作，我们不是重复造轮子 |
| Related Work | Yoo & Harman 2012 | E 在测试选择流水线中的位置 |
| Design：为什么用风险打分 | TSE 2025、Felderer & Schieferdecker 2014 | 风险化排序有先例 |
| Design：为什么 adequacy 权重更高 | Zhang & Mesbah 2015 | 断言比覆盖更能解释有效性 |
| Design：为什么要证据复核 | Barr et al. 2015、Zhang & Mesbah 2015 | status 不等于验证 |
| Design：为什么措辞要留余地 | Johnson et al. 2013 | 误报会摧毁工具信任 |
| Discussion：局限 | Just et al. 2014 | 没有做变异测试，是成本取舍 |
| Future Work | Meta Predictive Test Selection | ML 路线是另一种可能 |

---

## 四、可直接改写的一段 Related Work

> 一百多字，改改就能放进报告：

现有研究表明，代码覆盖率与测试有效性之间只有弱相关（Inozemtseva & Holmes, 2014），而断言数量与测试有效性则呈强相关（Zhang & Mesbah, 2015）。这说明"测试是否执行到改动"不足以判断改动是否被验证。工业界的 Test Gap Analysis 进一步把这类"改动后仍未被覆盖的代码区域"作为独立指标，并已有工作为其设计风险评分以支持优先级排序（IEEE TSE, 2025）。本模块沿用这一思路，但做了三点调整：以行为级变化而非行级覆盖为输入；以断言证据而非覆盖率作为充分性判据；并针对静态证据的假阳性风险，显式输出置信度并对上游结论做二次复核。

---

## 五、引用前必须核对的东西

| 项 | 状态 | 怎么核 |
| --- | --- | --- |
| TSE 2025 那篇的**作者列表** | ⚠️ 未确认 | IEEE Xplore 搜 DOI `10.1109/TSE.2025.3556248` |
| *Did we test our changes?*（Eder 等）的年份/会议 | ⚠️ 未确认 | Semantic Scholar 页面 |
| Inozemtseva & Holmes 的具体统计量（项目数、相关系数） | ⚠️ 未记录 | 原文 §4 |
| Zhang & Mesbah 的具体模型与系数 | ⚠️ 未记录 | 原文 §3 |
| Just et al. 2014 的结论强度（"mutants 能替代多少真实缺陷"） | ⚠️ 凭印象，未记数字 | 原文摘要 |

**原则**：报告里可以只说结论方向（"弱相关""强相关"），不要写没核对过的具体数字。写错一个数字比少写一句更伤。

---

## 六、如果时间只够读一篇

读 **Inozemtseva & Holmes 2014**。

因为它是 E 的立论基础：如果覆盖率足够，整个模块没有存在必要。把这篇的**研究设计**看懂（他们怎么定义"有效性"、怎么控制变量），比记住结论更有用——答辩时老师大概率会问"你怎么证明你的指标比覆盖率好"，那时你需要的是方法论，不是结论。
