# E 模块相关文献清单

> 建立：2026-10-30　检索方式：web search + 出版方/DBLP 页面核对
> 用途：设计依据、related work、局限性论证；后续可用于论文阅读总结
> 维护：每新增一条，注明「对应 E 的任务编号」和「能支撑什么论点」

---

## 0. 先看这三篇（如果只读三篇）

| # | 文献 | 一句话 | 为什么是必读 |
| --- | --- | --- | --- |
| 1 | Inozemtseva & Holmes, *Coverage is not strongly correlated with test suite effectiveness*, ICSE 2014 | 覆盖率与测试有效性只是弱相关 | **E 存在的理论前提**：D 报"覆盖到"不等于"验证了" |
| 2 | Zhang & Mesbah, *Assertions are strongly correlated with test suite effectiveness*, ESEC/FSE 2015 | 断言数与有效性**强**相关，比覆盖率更能解释有效性 | **E 的打分重心**：为什么把 adequacy 放在 0.55 的权重上 |
| 3 | *Prioritizing Test Gaps by Risk in Industrial Practice*, IEEE TSE 2025 | 工业界给每个 test gap 算风险分并排序 | **最接近的同题工作**，必须作为 related work 对比 |

---

## 1. 同题研究：Test Gap Analysis（最直接的一条线）

这是工业界和学术界都已存在的概念，E 不是凭空发明的。

| 文献/资料 | 要点 | 对应 E | 怎么用 |
| --- | --- | --- | --- |
| **Teamscale 文档：Test Gaps 视角** （[docs.teamscale.com](https://docs.teamscale.com/reference/ui/testgaps.md)） | test gap = 最近一次修改后仍未被测试执行的代码区域；静态（仓库）+ 动态（覆盖率）结合；**只把"非平凡改动"算作改动**，否则改变量名会被误判为未测试 | 2.2 规则、`Documentation change` 跳过 | 支撑"纯文档/重命名不计缺口"这条规则——工业工具也这么做 |
| **Prioritizing Test Gaps by Risk in Industrial Practice: An Automated Approach and Multimethod Study**, IEEE TSE 2025，DOI `10.1109/TSE.2025.3556248` | 为每个 test gap 计算风险分并排序，工业级落地 + 多方法评估 | 2.1 / 2.2 / 2.3 | **核心 related work**；E 的"风险 × 重要度 × 缺口"三因子与之同构 |
| **Did we test our changes? Assessing alignment between tests and development in practice**（Eder, Hauptmann 等） | 实测"改动与测试的吻合度" | §1 动机 | 用来说明问题的普遍性 |
| **Test-Gap-Analyse**（XP Days 2015 讲义，德语） | 工业实践讲解：如何在进 release 前发现未测试的改动 | 动机 | 说明这是真实工程痛点，不是课程虚构 |

> ⚠️ **需要核对的引用信息**：TSE 2025 那篇的作者列表我未能从公开页面确认（DBLP/Semantic Scholar 当时被反爬或限流），写作时请到 IEEE Xplore 用 DOI 查全。Eder 那篇的发表年份/会议也建议再核一次。

### 1.1 E 与这条线的差异（写 related work 时的关键句）

工业界的 TGA 主要靠**覆盖率**判断"测没测到"，E 多了一层：

1. **行为级**（B 的四类行为变化）而非行级——同样一行改动，"改异常类型"和"改注释"风险差一个数量级；
2. **断言证据**（D）而非仅执行——覆盖到但没断言，在 E 里是 Level 2 / deficit 0.70，仍算缺口；
3. **证据复核**（2.6）——连 D 的"有断言"都要再查是强证据还是精度盲区。

这三点正好是 §2、§3 两节文献支撑的。

---

## 2. 覆盖 ≠ 充分（E 的核心论点）

| 文献 | 要点 | 对应 E |
| --- | --- | --- |
| Inozemtseva & Holmes, ICSE 2014，DOI `10.1145/2568225.2568271` | 大规模实测：覆盖率与变异杀伤率的相关性弱 | 2.6 的立论 |
| Zhu, Hall, May, *Software unit test coverage and adequacy*, ACM Computing Surveys 1997，DOI `10.1145/267580.267590` | 覆盖准则与"充分性"的经典区分 | §7.2 等级模型 |
| Papadakis, Kintis, Zhang, Jia, Le Traon, Harman, *Mutation Testing Advances: An Analysis and Survey*, Advances in Computers 2019 | 变异测试作为更强充分性标准的综述 | 未来工作（Could 级） |
| Just, Jalali, Inozemtseva, Ernst, Holmes, Fraser, *Are mutants a valid substitute for real faults?*, FSE 2014，DOI `10.1145/2635868.2635929` | 变异体能否代表真实缺陷 | 说明"我们为什么没直接上变异测试"（成本） |

> 用法：报告里解释"为什么不用覆盖率当分数"时，直接引 Inozemtseva & Holmes。

---

## 3. 断言强度与 oracle 问题（解释 D 的 status 为什么乐观）

这一节是 **2.6 evidence 复核** 的直接依据。

| 文献 | 要点 | 对应 E |
| --- | --- | --- |
| Zhang & Mesbah, ESEC/FSE 2015，DOI `10.1145/2786805.2786858` | 断言数量与测试有效性**强**相关，且比覆盖率更能解释 | 2.6：把"断言质量"当主信号 |
| Kudrjavets, Nagappan, Ball, *Assessing the relationship between software assertions and faults*, ISSRE 2006，DOI `10.1109/ISSRE.2006.14` | 断言密度与缺陷率的经验关系（工业数据） | 2.1 权重标定的旁证 |
| Barr, Harman, McMinn, Shahbaz, Yoo, *The Oracle Problem in Software Testing: A Survey*, IEEE TSE 2015 | 判定"测试是否通过"本身有多难 | §6 解释文案必须留有余地（"Potential" Test Gap） |
| van Deursen, Moonen, van den Bergh, Kok, *Refactoring Test Code*, XP 2001 | test smell 的经典分类（含 "Assertion Roulette" 等） | 3.x 解释里描述"断言弱"的词汇来源 |

> **可以直接写进报告的一句**：D 的 `assertion_evidence_found` 只说明语法上存在匹配的断言，而 Zhang & Mesbah 的工作表明断言与有效性相关的前提是断言**检查了被改动的效果**；E 的 2.6 正是把这个前提变成可执行的检查。

---

## 4. 变更影响分析与测试选择（A / C 阶段）

E 不实现这一段，但要理解上游，也要能解释为什么上游会不完整。

| 文献 | 要点 | 对应 |
| --- | --- | --- |
| Arnold & Bohner, *Impact Analysis — Towards a Framework for Comparison*, ICSM 1993 | 变更影响分析的奠基框架 | A 的概念来源 |
| Ren, Shah, Tip, Ryder, Chesley, *Chianti: A Tool for Change Impact Analysis of Java Programs*, OOPSLA 2004，DOI `10.1145/1028976.1029012` | 方法级变更影响分析 | A/B 的实体粒度 |
| Gligoric, Eloussi, Marinov, *Practical Regression Test Selection with Dynamic File Dependencies*（Ekstazi）, ISSTA 2015 | 文件级动态依赖做测试选择 | C 的方法族 |
| Legunsen, Harifi, Shi, Lu, Gligoric, *An Extensive Study of Static Regression Test Selection in Modern Software Evolution*, FSE 2016，DOI `10.1145/2950290.2950361` | 静态 RTS 的大规模评估：安全性与开销 | C 的局限（`selection_complete=false`） |
| Yoo & Harman, *Regression Testing Minimization, Selection and Prioritization: A Survey*, STVR 2012，DOI `10.1002/stvr.430` | 三者关系的权威综述 | E 的定位：E 属于 prioritization 一侧 |
| Machalica, Samylkin, Porth, Chandra, *Predictive Test Selection*, ICSE-SEIP 2019 | Meta/Facebook 用 ML 预测哪些测试值得跑 | 对比对象：工业用 ML，E 用可解释规则 |
| (本项目自带) `pytest-testmon` | 基于覆盖率与 checksum 的测试选择 | C 的实现 |

> 用法：E 的 join 覆盖率检查（`join.low_coverage`）可以引 Legunsen 等关于 RTS 安全性/完整性的讨论。

---

## 5. 风险驱动的测试与优先级（E 的公式）

| 文献 | 要点 | 对应 E |
| --- | --- | --- |
| Felderer & Schieferdecker, *A taxonomy of risk-based testing*, STTT 2014 | 风险驱动测试的系统分类 | 2.1：为什么用"风险 × 重要度" |
| Rothermel, Untch, Chu, Harrold, *Prioritizing Test Cases for Regression Testing*, IEEE TSE 2001 | 覆盖率类优先级准则的经典比较 | 2.3 的排序思路（按分数降序） |

---

## 6. 误报抑制与解释生成（3.x + 置信度）

| 文献 | 要点 | 对应 E |
| --- | --- | --- |
| Johnson, Song, Murphy-Hill, Bowdidge, *Why Don't Software Developers Use Static Analysis Tools to Find Bugs?*, ICSE 2013 | 开发者不用静态分析的主因：误报与不可信 | 2.4 置信度、6.1 措辞红线 |
| *Bridging the Gap: Using GPT-4 to Summarize and Explain Static Analysis Warnings at Scale*（IEEE，2025 前后，⚠️ 作者/年份待核） | 用 LLM 把告警翻译成人话并可规模化 | 3.x 的"未来可扩展方向" |
| van Deursen 等 2001（见 §3） | test smell 术语 | 3.x 文案 |

> E 的解释生成目前是**模板化**的（可复现、零成本、不幻觉），LLM 版本列为 Could 级。这条对比可以写进 report 的 future work。

---

## 7. 设计决策 ↔ 文献对照表（写报告直接用）

| E 的设计决策 | 支撑文献 | 属于"借鉴"还是"差异" |
| --- | --- | --- |
| 只有非文档/非重命名改动才计缺口 | Teamscale TGA 文档 | 借鉴 |
| 风险分 = 行为风险 × 变更重要度 × 覆盖缺口 | TSE 2025 风险化 test gap；Felderer 风险测试分类 | 借鉴 + 三因子具体化 |
| 用断言证据而非覆盖率作主判据 | Inozemtseva & Holmes 2014；Zhang & Mesbah 2015 | 借鉴 |
| 对 D 的 status 做二次复核 | oracle 问题（Barr 2015）+ 断言强度（Zhang 2015） | **差异/增量** |
| "Potential Test Gap" 而非 "missing test" | Johnson 2013（误报导致不信任） | 借鉴 |
| 置信度与分数分离 | Johnson 2013 | **差异/增量**（工业 TGA 少见显式置信度） |
| 规则化而非 ML | Predictive Test Selection（ML 路线）对比 | 差异（可解释性优先） |

---

## 8. 后续可补的方向

- **断言强度的可量化指标**：目前 E 只用"弱/中/强"三档，是否有更细的度量可引？
- **PR 评论可读性**：有没有关于"自动评论如何写才被采纳"的实证研究？
- **LLM 解释的评估方法**：如果 E 以后接 LLM 生成解释，需要引用评估协议。
- **测试缺口与缺陷的关联**：TGA 类工作是否报告了"缺口 → 漏到线上的缺陷"的量化关系？这会是很强的 motivation。

---

## 9. 引用管理建议

- 现在这份清单是"人读版"，等确定要引哪 8–10 篇后，建议用 Zotero/BibTeX 管理，避免作者名、页码出错。
- 课程报告通常不需要 DOI，但写上更稳；E 的报告里建议至少给 **标题 + 作者 + 会议/期刊 + 年份**。
- ⚠️ 标记的两条（TSE 2025 作者列表、Eder 等年份）**提交前必须核对**，不要直接引用我这版的转述。
