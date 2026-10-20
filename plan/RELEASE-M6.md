# RELEASE-M6：`v0.1.0` 发布留档

> 本工具仅聚合公开信息，不构成投资建议。

- 里程碑：M6（发布），DoD 见 `plan/06-里程碑.md` M6 与 `plan/07-交付对标.md` §五
- 口径：本文件只写**已经发生**的事实；每一步都给出可复跑的命令或可点开的 run 链接。
- 版本说明：`cnwatch-0.1.0.zip` 内是本文件在 tag 时刻的修订。
- **对齐纪律（2026-10-20 起）**：tag 与发布时的 `main` HEAD **相等**，不再留"发布后对账提交"。
  代价必须讲清：zip 是本文件所在的树打的，**所以 zip 自身的大小与 sha256 不能写进本文件**
  ——写了就改内容，改的又是它自己（循环）。因此**当前附件的大小与 sha256 的权威来源是 Release 说明**
  （annotated tag 的对象消息里也写了一副本）；本文件只写**不随自身内容变化的不变量**
  （附件名、前缀、blob 数、`.sha256` 格式）与各时点的历史值。
- **历次重指**：`70e96f6`（2026-10-17 首发；5 个 M6 收尾提交落在 tag 之后，附件因此不含它们）
  → `9a79e66`（2026-10-20 第一次对齐，数值见 §四.2）
  → **本次（2026-10-20 收尾重发，数值见 Release 说明）**。§四 与 §四.2 是各时点的历史事实，均原样保留。

---

## 一、发布物

| 项 | 值 |
|---|---|
| 版本 | `0.1.0`（`pyproject.toml`） |
| tag | `v0.1.0`（annotated；**指向发布时的 `main` HEAD，两者相等**；tag 对象 id 与其消息见 Release 说明） |
| tag 指向的提交 | 见 Release 说明（本文件在 zip 内，写自身 tag 所指提交的哈希会循环，见文首「对齐纪律」） |
| Release | <https://github.com/yuluo554/cnwatch/releases/tag/v0.1.0>，2026-10-17T11:20:12Z 创建，非草稿、非预发布、标记 Latest（2026-10-20 两次重发只替换附件与更新说明，Release 对象与创建时间未变） |
| 附件 | `cnwatch-0.1.0.zip`（**大小见 Release 说明**）、`cnwatch-0.1.0.zip.sha256`（84 B；格式 `<64 位小写 hex><两个空格>cnwatch-0.1.0.zip<LF>`，`sha256sum -c` 可自校验） |
| `cnwatch-0.1.0.zip` sha256 | **见 Release 说明**（本文件在 zip 内，写自身哈希会循环） |
| 附件内容 | `git archive` 的 tag 快照，前缀 `cnwatch-0.1.0/`：114 个文件 = tag 的 114 个 blob，逐 blob sha1 相等；含 `src/ tests/ tools/ plan/ data/`、`README.md`、`LICENSE`、`pyproject.toml`、`.github/`、`.gitattributes`、`.gitignore`。不含 `.git/`、不含运行期产物（`out/`、`data/pdf/`、`*.db`） |
| 许可 | MIT（`LICENSE`，与 README「License」节一致） |

附件名三处一致：README「Quick start」的下载命令、本文件、Release 资产列表。
发布产物不入库（仓库只留 tag 与留档）：本地副本与 §四 的下载核对目录都在仓库之外。

## 二、发布前本地实测

两个解释器、三道数据门禁、干净克隆、脱敏，全部在 tag 之前跑过：

| 项 | 命令 | 结果 |
|---|---|---|
| 3.12 全量测试 | `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.12 -m pytest -q` | **376 passed** |
| 3.8 全量测试 | `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.8 -m pytest -q` | **376 passed**（5 条 PyMuPDF SWIG `DeprecationWarning`，属噪声） |
| 快照门禁 | `py -3.12 tools/gen_snapshots.py --check` | `snapshots OK: canonical bytes, manifest sha256, shapes and ledgers agree` |
| 规则包门禁 | `PYTHONPATH=tools py -3.12 tools/gen_rules.py --check` | `rules OK: 26 categories, 4 patches, byte-identical` |
| 真值门禁 | `PYTHONPATH=tools py -3.12 tools/gen_truth.py --check` | `truth OK: 2 ledgers byte-identical` |
| 干净克隆 + 新 venv | `git clone --no-local <repo> clone1` → `py -3.12 -m venv .venv` → `pip install -e ".[dev]"` → `pytest -q` | **376 passed**；安装的是 `cnwatch-0.1.0` |
| 脱敏扫描（本机路径） | 盘符绝对路径与 POSIX 家目录路径形状 | 全部命中都是**非本机路径**：`http://`/`https://`、`plan/HANDOFF-M1.md` 的写样式示例、`plan/HANDOFF-M6.md` 里带 `xxx` 占位的扫描式样本身；**无本机绝对路径** |
| 脱敏扫描（本机用户名） | 本机用户名与工作目录关键词逐个 grep（模式本身不落盘） | **0 命中** |
| 脱敏扫描（邮箱） | 邮箱形状，排除 RFC 2606 保留域名与 Actions 的 `uses:` 行 | 仅 `actions/checkout@v4`、`actions/setup-python@v5`（Actions 的 `@ref`，非邮箱）；其余为 `*@example.invalid` 等保留域名 |
| 脱敏扫描（凭据） | 登录态与凭据类关键词 | 命中全部是「不使用 Cookie」的策略表述与 SMTP 口令取自命令行参数（非硬编码）；**无凭据** |
| 入库边界 | 被跟踪文件的后缀白名单检查 | **0 命中**；`git ls-files data/pdf` 为空 |
| 忽略规则 | `git check-ignore -v --no-index -- data/pdf/600519/2026-10-16/1.PDF` | 命中 `/data/pdf/` 规则 |
| 免责三处 | `README.md`、`cnwatch report --help`、`out/reports/<date>/daily.html` 页脚 | 均为 `cnwatch/disclaimer.py` 的单点常量 `本工具仅聚合公开信息，不构成投资建议。`；由 `test_readme_states_the_disclaimer_and_the_data_source`、`test_report_help_states_the_disclaimer`、`test_the_disclaimer_is_present_verbatim_in_both_formats` 三条测试钉住 |

> 上表的扫描模式**故意只写形状、不写字面量**：把本机用户名或路径关键词抄进留档，
> 等于把要扫掉的东西写进仓库，下一次审计会命中留档自身。可复跑的完整命令留在
> `plan/HANDOFF-M6.md` §二（那份文件的定位是交接说明，不是发布物）。

出网前修掉的一个缺陷（否则首推即四腿同红）：

- `.github/workflows/ci.yml` 的 console-script smoke 仍按 M0 的预期断言 `cnwatch report` 退出码为 `3`。
  M4 已实现 `report`，空 home 下它退出 `2`。已改为：`run` 断言 `3`（唯一仍在 stage gate 后的命令）、
  `report` 断言 `2`（无库=不可用输入）；并补一条 M4/M5 离线 smoke（report/notify/archive），
  因为在此之前 CI 从未执行过这三条已交付的命令。本地按 CI 步骤逐条复跑通过后才推送。

## 三、CI 留档（push 数 = run 数）

工作流 `.github/workflows/ci.yml`：`ubuntu-latest`/`windows-latest` × Python `3.8`/`3.12`，四腿并行。
（2026-10-20 起为 `ubuntu`/`windows` × `3.12` 两腿——见 §七 第 5 条；下表与下面两张耗时表是当时的记录。）

| # | 事件 | 对象 | run id | 结果 |
|---|---|---|---|---|
| 1 | push | `main` `347d2fa`（CI 接线修复） | [39027648189](https://github.com/yuluo554/cnwatch/actions/runs/39027648189) | ✅ 四腿全绿 |
| 2 | push | `main` `70e96f6`（README + 本文件，= tag 提交） | [39027987628](https://github.com/yuluo554/cnwatch/actions/runs/39027987628) | ✅ 四腿全绿 |
| 3 | push | tag `v0.1.0`（独立计一条） | [39028170301](https://github.com/yuluo554/cnwatch/actions/runs/39028170301) | ✅ 四腿全绿 |
| 4 | push | `main` `bf665c1`（发布后留档补齐） | [39028669714](https://github.com/yuluo554/cnwatch/actions/runs/39028669714) | ✅ 四腿全绿 |

- 上表截至 `main` = `bf665c1`：**4 条 push → 4 条 run**。`70e96f6` 与 `bf665c1` 的差异只在 `plan/` 文档。
  （本条是重发前的历史记录：2026-10-20 起 `v0.1.0` 与发布时的 `main` HEAD 相等，见文首「对齐纪律」
  与 Release 说明；此后每条 push 仍恰好一条 run，可用 `gh run list` 逐条核对。）
- 之后每次文档 push 各产生一条 run（不变量：一条 push 恰好一条 run），可用 `gh run list` 逐条核对。
- tag push 由 GitHub 作为独立 run 触发（该 run 的 `headBranch` 是 `v0.1.0`，不是 `main`），
  所以"push 数 = run 数"按事件计成立，没有把 tag 与分支挤进同一条 run。
- 发布后的文档提交（含本条补齐）同样一条 push 对应一条 run，可用 `gh run list --limit 10`
  逐条核对；不变量是"每条 push 恰好一条 run"。

同一两条分支推送的耗时（run 内四腿）：

| 腿 | run 2 | run 3 |
|---|---|---|
| ubuntu-latest / py3.12 | ✅ 18s | ✅ 20s |
| ubuntu-latest / py3.8 | ✅ 34s | ✅ 33s |
| windows-latest / py3.12 | ✅ 136s | ✅ 67s |
| windows-latest / py3.8 | ✅ 110s | ✅ 98s |

## 四、附件真下载核对

全部在仓库外的中立目录（`_m6scratch/recheck/`）完成，命令按顺序如下。

| 步骤 | 命令 | 结果 |
|---|---|---|
| 真下载 | `gh release download v0.1.0 --repo yuluo554/cnwatch --dir .` | 取回两个附件（793,117 B / 84 B） |
| sha256 对账 | `sha256sum cnwatch-0.1.0.zip` 与 §一 记录值比较 | `562b70e8…43f1`，**MATCH** |
| 校验文件自校验 | `sha256sum -c cnwatch-0.1.0.zip.sha256` | `cnwatch-0.1.0.zip: OK` |
| 逐 blob 完整性 | `git ls-tree -r -z v0.1.0` 的 114 个 blob 与 zip 内解压内容比 `blob sha1` | 114 个文件、**extra 0 / missing 0 / 内容不一致 0** |
| README 流程冒烟 | 解压 → `py -3.12 -m venv .venv` → `pip install -e .` → `cnwatch selftest` | `result: pass`，退出 0 |
| 中立目标安装 | `py -3.12 -m pip install --no-deps --no-build-isolation --target <仓库外目录> .` | `cnwatch/` 下 53 个 `.py`；`resources/` 两个 json 均在（含 `rules/rules-v1.json`） |
| 中立安装冒烟 | `PYTHONPATH=<target> py -3.12 -m cnwatch --home <仓库外 home> …` 逐条运行 | `selftest` → `result: pass`；`watchlist import` → `added 20`；`fetch --snapshots-dir … --today 2026-10-16` → `baseline established` / `result: complete`；`classify` → `announcements 13, classifications 13`；`report --date 2026-10-16` → 两个文件落盘；`notify` → `channel=none` + `no external call was made`；`archive --show-config` → `suffix: .PDF` / `default_extract: off` |

注意：上表的"逐 blob 完整性"只比 zip 与 tag；解压目录里若出现 `build/`、`src/cnwatch.egg-info/`、
`cnwatch.db`，那是本次 `pip install -e .` 与 `selftest` 的产物，不在 zip 内（zip 只有 114 个文件）。

### 4.1 README 逐条跑通（全部在 Release 附件解压出的树里执行）

解压后新 venv `pip install -e .`，随后按 README 原文逐条执行（home 用 `./rt`、`./rt-market`、`./rt-real`，
均在被解压的树内、仓库之外）。退出码按 README「Exit codes」表核对：

| README 命令 | 预期 | 实得 | 末行摘要 |
|---|---|---|---|
| `cnwatch selftest` | 0 | 0 | `result: pass` |
| `PYTHONPATH=src python -m cnwatch selftest` | 0 | 0 | `result: pass` |
| `cnwatch --home ./rt selftest` | 0 | 0 | `result: pass` |
| `watchlist import` | 0 | 0 | `imported: added 20, updated 0, unchanged 0, rejected 0` |
| `watchlist add --code 300750` | — | 2 | 该码已在样例清单内 → 提示 `--update`（文档化行为，非缺陷） |
| `watchlist edit --code 300750 --group 风险` | 0 | 0 | `updated 300750: group=风险` |
| `watchlist remove --code 300750` | 0 | 0 | `removed 300750 宁德时代` |
| `watchlist list` / `--group 持仓` / `search --query gzmt` / `list --audit` | 0 | 0 | 均返回目标行；`--audit` 显示 `edit` 前后值 |
| `watchlist sync-master` | 0 | 0 | `master 6261 codes; checked 20, refreshed 0, marked missing 0` |
| `cnwatch fetch`（真实联网） | 0 | 0 | `stocks 20 (ok 20, failed 0) requests 20 (first 20, pages 0, retries 0)`；**19.7s**，基线 51 条 |
| `fetch --snapshots-dir … --today 2026-10-16` | 0 | 0 | `result: complete` |
| `fetch --show-config` | 0 | 0 | 回显第四节口径（interval 1.0s / page_size 30 …） |
| `fetch --all --date 2026-10-16`（用冻结快照回放，零网络） | 0 | 0 | `window 2026-10-16..2026-10-16 (market)`；**1313 条 / 44 页**，来源标 market |
| `fetch --all --date 2026-10-15`（同上） | 0 | 0 | 1097 条，与 M1 冻结快照同值 |
| `classify` / `classify --since 2026-10-16` | 0 | 0 | `database: announcements 11, classifications 11` |
| `report --date 2026-10-16 --generated-at …` | 0 | 0 | `daily.md` 与 `daily.html` 落盘 |
| `archive --show-config` | 0 | 0 | `suffix: .PDF` / `default_extract: off` |
| `archive --code 601318`（真实下载） | 0 | 0 | `requests 2 (retries 0)`；**94,871 B + 169,445 B**（与 M5 记录同值） |
| 再次 `archive --code 601318` | 0 | 0 | `already archived` ×2，**不重下**（幂等） |
| `archive --code 601318 --text` | 0 | 0 | `text extracted 2`；从本地文件补抽，未重下 |
| `archive --announcement-id 1225604248`（真实下载） | 0 | 0 | **92,960 B**，落 `data/pdf/601000/2026-10-15/`（点到市场兜底行 = K26 例外） |
| `archive --risk high` / `--all-high` | 2 | 2 | 当日无 high 行 → 明确用法错误 + 提示先 fetch/classify |
| `archive`（无筛选） | 2 | 2 | 提示需要 `--announcement-id/--code/--category/--risk/--all-high` |
| `notify`（默认） | 0 | 0 | `disabled: the channel is none; no external call was made` |
| `notify --channel desktop --dry-run` | 0 | 0 | `dry run: composed but not sent` |
| `notify --channel webhook`（`example.invalid`） | 1 | 1 | `getaddrinfo failed`；失败只进 `out/logs/notify.log` |
| `notify --channel email`（`smtp.example.invalid`） | 1 | 1 | 同上；两个接收端都是 RFC 2606 保留域名，永不可解析 |

结论：README 里出现的每条命令都在这棵树上执行过，退出码与文档表一致；
需要出网的四条（`fetch`、`archive --code`、`archive --announcement-id`，以及被 `fetch` 覆盖的请求路径）
走了真实接口，其余全部离线。`fetch --all` 用冻结快照回放，没有为验证 README 再发一轮全市场请求。

### 4.2 中间态复核（2026-10-20 第一次对齐：tag `70e96f6` → `9a79e66`）

用与 §四 相同的流程重跑一遍（命令与结果如下）。**下表是中间态**，已被同日收尾重发取代
（当前值见 Release 说明）；保留它是因为"每个时点发生过什么"本身是事实。

| 步骤 | 命令 | 结果 |
|---|---|---|
| tag 对齐 | `git rev-list -n1 v0.1.0` 与 `git rev-parse HEAD` 比较；`git log --oneline v0.1.0..HEAD` | 两者相等（`9a79e66`）；**落后提交 0 条**（输出为空） |
| 远端 tag | `git ls-remote origin refs/tags/v0.1.0 refs/tags/v0.1.0^{}` | tag 对象 `85eaebd9554d55a4afdd6bd7ca70fc92d9ea458d` → 提交 `9a79e6633ba1a0ecee13165fa98117c771d14cb1` |
| 重打附件 | `git archive --format=zip --prefix=cnwatch-0.1.0/ -o cnwatch-0.1.0.zip v0.1.0` | 799,714 B；135 条目 = 114 文件 + 21 目录，全部以 `cnwatch-0.1.0/` 开头 |
| `.sha256` | `<64 位 hex><两个空格>cnwatch-0.1.0.zip<LF>`（与首发时的 84 B 文件同格式） | 84 B，`sha256sum -c` → `cnwatch-0.1.0.zip: OK` |
| 真下载 | `gh release download v0.1.0 -R yuluo554/cnwatch --pattern 'cnwatch-0.1.0.zip*'` | 取回两个附件（799,714 B / 84 B） |
| sha256 对账 | `sha256sum cnwatch-0.1.0.zip` 与本地重打值、GitHub 资产 digest 比较 | `519f4fa1f8aab28ffdfc426acc81ad70628b97bcbf80641ed408d1bad853a035`，**三方一致** |
| 校验文件自校验 | `sha256sum -c cnwatch-0.1.0.zip.sha256` | `cnwatch-0.1.0.zip: OK` |
| 本地重打 vs 下载件 | `cmp <重打件> <下载件>` | **逐字节相同** |
| 逐 blob 完整性 | `git ls-tree -r v0.1.0` 的 114 个 blob 与 zip 解压内容比 `blob sha1` | 114 个文件、**extra 0 / missing 0 / 内容不一致 0** |
| CI | tag push 触发的 run [39353002458](https://github.com/yuluo554/cnwatch/actions/runs/39353002458) | ✅ 四腿全绿（该 run 用的是 tag 提交上的旧 `ci.yml`） |

## 五、未做项（不得写成已完成）

- **`run`（端到端编排）未实现**，仍返回退出码 `3`，产出一律为空。已交付的六条命令
  （`watchlist`/`fetch`/`classify`/`report`/`archive`/`notify`）可各自独立运行；
  stage gate 的文案不再指向已完成的里程碑；`plan/06` M5 偏差与 `plan/07` §六第 5 条同一登记。
- **真实桌面通知 / 真实邮件 / 真实 webhook 投递次数为 0**：没有可用接收端，
  默认渠道 `none` 也不投递。已有的是离线断言（注入失败、注入成功、零外部调用两种对照）。
- **`archive --all-high` 未在真实全库上批跑**：离线测试覆盖了筛选与批量计数，
  真实数据上只跑过 `--code` 与 `--announcement-id`。
- **无 PyPI 发布**：`v0.1.0` 只以源码附件 + tag 形式发布，`pip install cnwatch` 不可用。
- **`announcementType` 数值码映射仍未核对**，因此不进判定路径（README「Known limitations」同一条）。
- **未做真实整轮 50 只抓取**：NFR-04 的 50 只 ≈55s 是由 20 只实测值线性外推，不是 50 只实测。

## 六、真实基准（发布说明引用同一数值）

- NFR-04：真实联网 `fetch` 在**发布物**上再测一次 20 只 = **19.7s**（`real 0m19.706s`，20 请求 0 失败 0 重试，
  7 天窗口 51 条基线），与 M5 的 22.07s 同量级；两次都不含任何估算 →
  50 只 ≈ **49–55s**，仍在 `<60s` 口径内，但余量只有 8%–18%。**未做真实 50 只整轮**，
  所以这两个数是 20 只实测的线性外推，不是 50 只实测。
- 数据源实测与六条硬约束见 `data/README.md`；接口、PDF 直连、26 类枚举的来源
  与「无显式 robots 规则 ≠ 授权，因此自律限频」的记录同在该文件 §三。

## 七、2026-10-20 收尾重发：相对首发的内容差异

首发（2026-10-17）之后 `main` 上又落了 5 个 M6 收尾提交与 1 个二次迭代提交，附件一直按 tag 打，
**都没有进过附件**。本次把 tag 与发布线对齐后重打，附件相对首发的差异是：

| # | 差异 | 位置 |
|---|---|---|
| 1 | README 走查行数修正 `26 -> 27` 及其后 4 个台账/脱敏提交 | `README.md`、`plan/`、留档 |
| 2 | README Status 表新增一行：`run` 仍未实现（`exit 3`，不写任何东西） | `README.md` |
| 3 | `license` 改 SPDX 表达式 + `license-files`（**需 `setuptools>=77`**），删掉已弃用的 `License ::` classifier → 构建元数据零 warning | `pyproject.toml` |
| 4 | CI 的 `actions/checkout`/`actions/setup-python` 升到 `v7`，补 `permissions: contents: read` 与 `concurrency` | `.github/workflows/ci.yml` |
| 5 | 解释器下限 `>=3.8` → **`>=3.12`**；CI 矩阵 4 腿 → **2 腿**（`ubuntu`/`windows` × 3.12）；README 同步 | `pyproject.toml`、`.github/workflows/ci.yml`、`README.md` |

第 5 条的原因不是偏好，是工具链现状：**PEP 639 的 SPDX `license = "MIT"` 与 `license-files` 需要 setuptools ≥ 77，
而 setuptools 76 起已不支持 Python 3.8**（3.8 上最后一版是 75.3.4）——
两者不可兼得（3.8 已 EOL），所以这次同时抬了下限。第 3 与第 5 条是同一决定的两面。

`run` 仍是占位符（**本次没有实现它**），退出码语义未变。
本次重发的本地实测与干净环境复核数值，记录在 `update/03-执行记录.md`（该目录不入库）。
