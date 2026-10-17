# HANDOFF-M4：本地日报

> 上游：M3（规则引擎）已完成。本文件是 M4 的启动包，库内路径一律相对路径。
> 启动命令（用户侧）：`/goal 读取 "<项目绝对路径>/plan/HANDOFF-M4.md" 继续完成任务`

## 一、当前进度与证据

### M3 已完成（2026-10-17）

| 项 | 证据 |
|---|---|
| `classify` 子命令 | `src/cnwatch/classify/`（`text/rules/engine/repository/service`）；`cnwatch classify [--since] [--resources]` |
| 26 类 | `test_classify_engine.py::test_rule_package_category_set_equals_the_frozen_enum`（与 `new-notice.js` 切片集合相等） |
| 真值台账 86 条 | `test_every_truth_row_matches_and_divergence_names_the_id`，实测 **0 差异**（78 正例 / 5 反例 / 3 未分类） |
| 四补丁 | 正例由 `keyword-samples` 快照驱动；反例 5 条 + `超额配股权` 中性化，各一条测试 |
| 风险分级 | `high > mid > low`；取"胜出类别 + 补丁标签"（见下方口径，**别改**） |
| 未分类 | 照常落库 `category='unclassified'`，有显式风险级与计数 |
| 诊断日志 | `out/logs/classify-<ver>.log` **追加**写，每次运行一段带时间戳表头；空跑不会抹掉上一次的诊断 |
| 双跑一致 | `test_two_runs_on_a_copy_of_the_same_database_agree`、`test_relabelling_the_same_rows_twice_produces_identical_rows` |
| 规则包失败 | 缺失/截断/换 id/四补丁不全 → exit 2，**连 home 都不创建** |
| `announcementType` | AST 门禁（Attribute/Name/Constant 三面）；变异注入即变红 |
| 测试 | 双解释器各 **253** 项通过 |

真实证据（可复跑）：

```bash
rm -rf rt
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt watchlist import \
  --file data/fixtures/watchlist-sample.csv
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt fetch \
  --snapshots-dir data/snapshots --today 2026-10-16
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt classify
# pending 13  classified 13  risk high 0 mid 4 low 9  unclassified 5
# also matched (log only): category_zj_szsh 2
# database: announcements 13, classifications 13
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt classify   # 第二次：pending 0
```

### 尚未发生的事（不得写进 DoD 打勾）

- **GitHub 远端仍不存在，CI 一次都没跑过。** 新增的 "Classify smoke" 步骤只在本地
  用同一串命令手跑过（`selftest=0 / report=3 / import→fetch→classify→classify` 全绿）。
  所有 CI 类结论只能写"本地等价校验（未接远端，CI 待首推验证）"。
- **没有做过一次真实联网抓取。** M2 的 `UrllibTransport` 仍未经真实请求验证；M6 前应真实跑一次并留档。
- `report` / `archive` / `notify` / `run` 仍返回退出码 3。
- NFR-04「50 只 < 60s」仍只有估算。

## 二、下一棒待办（M4）

按 `plan/06-里程碑.md` 的 M4 DoD 与 `plan/04-模块详设.md` 模块 4 执行。

1. **`report` 子命令**：`out/reports/<date>/daily.md` + `daily.html`，单一装配源供两格式共用。
2. **双视图**：按股票分组、按类别分组；两视图从同一个 `list[Entry]` 分区函数输出，
   `set(A ids) == set(B ids)` 由结构保证。
3. **首次基线**：`report_baseline_items` 无前一日数据 → `baseline_created=1`，新增 = 0 且写明"首次运行"。
4. **对比**：有基线时 新增 = 今日 − 昨日，消失 = 昨日 − 今日；消失只计数与列清单，不写 `removed_at`。
5. **渲染层零判定**：不重算类别/风险/新增，只读既有字段；AST 门禁禁止 `report/` import `classify` 的匹配函数。
6. **零外链**：无 `<link>`、外链 `<script>/<img>/<iframe>`、`@import`、外部字体；条目 `<a href>` 放行。
7. **双跑逐字节一致**：同输入 + 同 `--generated-at` = 逐字节相同（Markdown 与 HTML 各测一次）。

### M4 必须踩准的口径

| 口径 | 值 | 出处 |
|---|---|---|
| 风险排序键 | `(risk_rank, announce_date desc, announcement_id asc)`，定义一次、两视图复用 | `plan/04` 4.5 |
| 首次运行 | 静默建基线，新增 = 0 | `data/README.md` 第四节 |
| 免责声明 | 逐字存在，页脚固定 | `plan/04` 4.4 |
| 未分类计数 | 读 `classifications` 里 `category='unclassified'` 的行，不重算 | M3 已保证这些行存在 |
| `also_matched` | **不读**。它只进 `out/logs/classify-<ver>.log` | M3 / `plan/04` 3.4 |

### M3 留下的一个口径，改动前先读这段

风险级 = max（**胜出类别** + 补丁标签），**不含** `also_matched` 里的落选类别。

86 条台账里有 8 条会因"所有命中类别取高"翻档（如"…第一季度报告（更正版）"经补充更正从 low 变 mid）。
若 M4 或后续想改这个读法，**必须先重新生成 `classify-v1.json` 并同步 04/06/07**，
否则 `test_every_truth_row_matches_and_divergence_names_the_id` 会红。

## 三、既定口径清单（K 编号，动了会打挂测试）

| 编号 | 口径 | 落点 |
|---|---|---|
| K1 | 退出码 0/1/2/3 语义固定；3 是临时"未就绪" | `src/cnwatch/exit_codes.py` + `tests/test_cli.py` |
| K2 | `announcement_id` 是唯一主键，禁止可空唯一键 | `src/cnwatch/db.py` + `tests/test_db.py` |
| K3 | 分组只允许 `持仓/观察/风险` | DB CHECK + `watchlist.models.validate_group` |
| K4 | `--home` > `CNWATCH_HOME` > cwd；不隐式写用户主目录 | `src/cnwatch/paths.py` |
| K5 | 编码守卫只改已知 ASCII-like 编码，cp936/gbk 不动 | `src/cnwatch/encoding.py` |
| K6 | 自检必须可证伪：资源缺失/规则包不全时返回非 0 | `src/cnwatch/selftest.py` |
| K7 | 所有数值（限频、退避、pageSize、窗口、回看、超时）只在 `data/README.md` 第四节定义 | `fetch/config.py` 解析该表 + AST 门禁 |
| K8 | 快照入库标 `-text`，sha256 与 manifest 同步提交 | `.gitattributes` + `data/snapshots/MANIFEST.json` |
| K9 | 真值台账只存输入与期望，禁止 `observed_*` | `plan/05` §三 + `tests/test_m1_data.py` |
| K10 | 生成器不读时钟、不随机；重生成逐字节一致 | `tools/gen_*.py --check` |
| K11 | 规则包冻结、禁手改；由生成器整目录重生成 | `tools/gen_rules.py` |
| K12 | `announcementType` 数值码不参与分类 | `classify` 的 AST 门禁 + `rules-v1.json` 的 `match.announcement_type_used=false` |
| K13 | 日期按**交易所时区 UTC+8** 换算，不用 UTC | `clock.exchange_date_from_epoch_ms` |
| K14 | `fetch_runs` 游标只认 `source_kind='watchlist'` 且 `status in (complete, degraded)` | `fetch/repository.py` |
| K15 | 请求计数拆成 first / pages / retries | `FetchSummary` |
| K16 | 分类风险取"胜出类别 + 补丁标签"，落选类别只进日志 | `classify/engine.py` + `test_risk_uses_the_winning_category_not_every_match` |
| K17 | 规则包先全量校验再开库；坏包 exit 2 且零写入 | `cli.cmd_classify` 的调用顺序 |
| K18 | 未分类条目照常落库，不跳过 | `classify/service.py` + `test_unclassified_rows_are_stored_not_skipped` |

## 四、本机环境坑（M2/M3 新增实测）

1. **`py -0p` 两条通道**：3.8.8 与 3.12.10。用 `py -3.12` / `py -3.8`，不要用 `python`。
2. **`tools/` 不是包**：命令行跑要带 `PYTHONPATH=tools`（测试里 `_run_tool` 已处理）。
3. **源码态必须 `PYTHONPATH=src`**；`pip install --target` 到中立目录后不要留在该目录再跑。
4. **Windows 控制台 + 中文**：落盘一律 `encoding="utf-8"`、`newline="\n"`；
   **`Path.write_text(newline=...)` 在 3.8 不存在**，要显式 `open(..., newline="\n")`（M3 踩过）。
5. **`tests/test_no_stubs.py` 会扫禁用词**：别在实现/测试里写 "placeholder"、
   "NotImplementedError"、"TODO: implement"。M3 的 SQL 变量名 `placeholders` 就被扫红过一次。
6. **回放不要真睡**：CLI 已为 `--snapshots-dir` 注入 `NullSleep`。
7. **删除临时目录前先 `cd` 到仓库根**；失败重试一次（Git Bash 目录占用）。
8. **`py -3.12 -c` 里写多行替换脚本容易静默不生效**：替换后打印 `found:`/`changed:`
   再跑测试，否则"变异后仍全绿"会被误读成"门禁不灵敏"（M3 实际踩过）。

## 五、DoD 逐项 checklist（M4）

- [ ] Markdown + 单文件 HTML 均生成到 `out/reports/<date>/`
- [ ] 双视图 id 集合相等（结构保证 + 集合断言）
- [ ] 首次运行新增 = 0 且有"首次运行/已建基线"说明
- [ ] 两日对比逐条一致（新增/消失按 `report_baseline_items` 复算）
- [ ] HTML 零外链（出口常驻断言 + 测试）
- [ ] 双跑逐字节一致（Markdown 与 HTML 各一次）
- [ ] 渲染层零判定（AST 门禁：`report/` 不 import `classify` 匹配函数）
- [ ] 免责声明逐字存在
- [ ] 摘要含 high/mid/low 与**未分类计数**（读库，不重算）
- [ ] 双解释器测试通过（本地等价校验，未接远端）
- [ ] 计划文档（04/06/07）与实测同步回写
- [ ] `plan/HANDOFF-M5.md` 已落盘并提交

## 六、关键命令速查

```bash
# 测试（两个解释器都要跑）
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.12 -m pytest -q
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.8  -m pytest -q

# M1 三道数据门禁（离线，不联网）
PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_snapshots.py --check
PYTHONPATH=tools PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_rules.py --check
PYTHONPATH=tools PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_truth.py --check

# M2+M3 离线全链路（抓取 → 分类 → 再分类）
rm -rf rt
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt watchlist import \
  --file data/fixtures/watchlist-sample.csv
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt fetch \
  --snapshots-dir data/snapshots --today 2026-10-16
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt classify
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt classify   # 必须 pending 0

# 规则包失败面（必须 exit 2 且不建 home）
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt2 classify --resources /nonexistent; echo $?

# 口径回显 / CLI 冒烟
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt fetch --show-config
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.12 -m cnwatch --home ./rt selftest

# 安装面验证（中立目标目录，不入仓）
rm -rf .tmp-install
py -3.12 -m pip install --no-deps --no-build-isolation --target .tmp-install .
find .tmp-install/cnwatch -name "*.py" | wc -l          # 应含 classify/ fetch/ watchlist/
find .tmp-install/cnwatch/resources -type f             # 两个 json 都在
```

## 七、M5 预告（不要提前做）

M5 才动 `archive` / `notify`。M4 阶段**不要**写 PDF 下载、文本抽取、任何通知渠道。
M4 只需保证：

- `report_runs` 的 `markdown_path` / `html_path` / `item_count` / `baseline_created` /
  `failed_stocks` / `content_sha256` 都被正确填写；
- `report_baseline_items` 每个报告日写入当日 id 集合（供下一日对比）；
- 渲染失败不写半份产物（先渲染到内存，再落盘）。

## 八、交接检查（本棒收尾时自查）

- [x] 所有 DoD 的 ✅ 都能指向具体测试名或命令输出
- [x] 没做过的事写明"未做"（真实联网抓取、CI）
- [x] 测试计数与文档一致（253 / 253）
- [x] 口径歧义（风险取"胜出类别"）已写进 04 与本文档，改动代价写明
- [x] `plan/HANDOFF-M4.md` 已落盘并提交
