# HANDOFF-M5：归档与提醒

> 上游：M4（本地日报）已完成。本文件是 M5 的启动包，库内路径一律相对路径。
> 启动命令（用户侧）：`/goal 读取 "<项目绝对路径>/plan/HANDOFF-M5.md" 继续完成任务`

## 一、当前进度与证据

### M4 已完成（2026-10-17）

| 项 | 证据 |
|---|---|
| `report` 子命令 | `src/cnwatch/report/`（`models/labels/assembly/markdown/html/repository/service`）；`cnwatch report [--date] [--generated-at] [--resources]` |
| 双产物 | `out/reports/<date>/daily.md` + `daily.html`，同目录、无时间戳 |
| 单一装配源 | `assembly.build_document` → `ReportDocument`；两格式只做排版 |
| 双视图 | 一个 `_partition` 函数出两个视图；`test_both_views_carry_the_same_id_set` 断言集合相等**且**逐组计数相等 |
| 排序键单点 | `models.Entry.sort_key` = `(risk_rank, -date_ordinal, announcement_id)`，两视图复用 |
| 首次基线 | 无更早基线 → `baseline_created=1`，`added=0`，两格式都写“首次运行，已建立基线” |
| 两日对比 | `added 5 / removed 8`，测试侧从快照独立重算集合差；消失只列清单，不写 `removed_at` |
| 零外链 | `html.assert_no_external_resources` 在 `render_html` **内部**调用；五种注入全部变红 |
| 零判定 | AST 门禁禁 `report/` import `classify`；行为测试：库存 `low`、标题含“质押”仍打印 `low` |
| 双跑一致 | 同 `--generated-at` 两次运行，两格式 sha256 不变 |
| 免责声明 | `src/cnwatch/disclaimer.py` 单点常量；README / CLI `--help` / 日报页脚三处共用 |
| 来源隔离 | 只读 `source_kind='watchlist'`；`fetch --all` 的全市场行不进日报（FR-18） |
| 未打标条目 | INNER JOIN 会让未打标行缺席两视图；报告在异常节给出计数并提示先跑 `classify` |
| 退出码 | 0 正常 / 1 抓取有失败股票（日报仍生成并标注不完整）/ 2 无库、该日无数据且无基线、该日行未打标 |
| 测试 | 双解释器各 **295** 项通过（`test_report.py` 43 项） |

真实证据（可复跑）：

```bash
rm -rf rt
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt watchlist import \
  --file data/fixtures/watchlist-sample.csv
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt fetch \
  --snapshots-dir data/snapshots --today 2026-10-16
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt classify
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt report \
  --date 2026-10-15 --generated-at 2026-10-15T12:00:00Z
# items 8  high 0 mid 4 low 4  unclassified 1
# baseline: first run, established for 2026-10-15; added 0
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt report \
  --date 2026-10-16 --generated-at 2026-10-16T12:00:00Z
# items 5  high 0 mid 0 low 5  unclassified 4
# baseline 2026-10-15: added 5, removed 8
```

产物尺寸：`daily.md` 4793 / 5708 B；`daily.html` 9252 / 10702 B（CSS 内联，零外链）。

### 尚未发生的事（不得写进 DoD 打勾）

- **GitHub 远端仍不存在，CI 一次都没跑过。** 所有 CI 类结论只能写
  "本地等价校验（未接远端，CI 待首推验证）"。
- **没有做过一次真实联网抓取。** `UrllibTransport` 仍未经真实请求验证；M6 前应真实跑一次并留档。
- **没有真实下载过一份 PDF。** M5 的 `UrllibTransport` 复用路径同样未经真实请求。
- `archive` / `notify` / `run` 仍返回退出码 3。
- NFR-04「50 只 < 60s」仍只有估算。

## 二、下一棒待办（M5）

按 `plan/06-里程碑.md` 的 M5 DoD 与 `plan/04-模块详设.md` 模块 5 执行。

1. **`archive` 子命令**：`data/pdf/<code>/<date>/<announcement_id>.PDF`；`archive_records` 记
   `local_path` / `byte_size` / `downloaded_at` / `text_status` / `text_excerpt`。
2. **文本抽取**：PyMuPDF → `pdftotext` → `unavailable`；只抽前 3 页，摘要长度单点定义。
3. **依赖缺失**：用户显式要求抽取但两者都不可用 → 退出码 2 + 安装提示，**不写空摘要**。
4. **幂等**：同一 `announcement_id` 重复归档不重复下载、不重复写行。
5. **`notify` 子命令**：`desktop` / `email` / `webhook` 三选一，默认 `none`；默认关闭时**零外部调用**。
6. **失败隔离**：通知发送失败只进运行日志，不影响日报与 DB，退出码 1。
7. **`data/pdf/` 不被 git 跟踪**（`.gitignore` 已有，补测试断言）。

### M5 必须踩准的口径

| 口径 | 值 | 出处 |
|---|---|---|
| 归档路径 | `data/pdf/<code>/<date>/<announcement_id>.PDF`（后缀大写） | `plan/04` 5.2 |
| 抽取范围 | 前 3 页 | `data/README.md` 第四节 |
| 抽取失败 | `text_status=unavailable`，摘要为空，退出码 0 | `plan/04` 5.3 |
| 依赖缺失 | 退出码 2 + 安装提示，不写空摘要 | `plan/04` 5.3 |
| 通知默认 | `none`，零外部调用 | `data/README.md` 第四节 |
| 通知失败 | 只写日志，不影响日报与 DB，退出码 1 | `plan/04` 5.4 |
| 摘要用途 | **不得**作为分类依据 | `plan/04` 5.3 |

### M4 留下的口径，改动前先读

1. **日报只读 `source_kind='watchlist'`。** 若 M5 想让归档产物也进日报，先改这条并补测试。
2. **`announcements INNER JOIN classifications`。** 未打标行不进日报；M5 若要"归档后回填分类"，
   必须走 `classify`，不能在归档层写 `classifications`。
3. **`content_sha256` 记的是 Markdown 摘要。** 若 M5 让日报带归档状态，需决定是否同步改这个字段。
4. **免责声明单点** `src/cnwatch/disclaimer.py`。通知正文若含免责，从这里取，不要复制字符串。

## 三、既定口径清单（K 编号，动了会打挂测试）

| 编号 | 口径 | 落点 |
|---|---|---|
| K1 | 退出码 0/1/2/3 语义固定；3 是临时"未就绪" | `src/cnwatch/exit_codes.py` + `tests/test_cli.py` |
| K2 | `announcement_id` 是唯一主键，禁止可空唯一键 | `src/cnwatch/db.py` + `tests/test_db.py` |
| K3 | 分组只允许 `持仓/观察/风险` | DB CHECK + `watchlist.models.validate_group` |
| K4 | `--home` > `CNWATCH_HOME` > cwd；不隐式写用户主目录 | `src/cnwatch/paths.py` |
| K5 | 编码守卫只改已知 ASCII-like 编码，cp936/gbk 不动 | `src/cnwatch/encoding.py` |
| K6 | 自检必须可证伪：资源缺失/规则包不全时返回非 0 | `src/cnwatch/selftest.py` |
| K7 | 所有数值（限频、退避、pageSize、窗口、回看、超时、抽取页数）只在 `data/README.md` 第四节定义 | `fetch/config.py` 解析该表 + AST 门禁 |
| K8 | 快照入库标 `-text`，sha256 与 manifest 同步提交 | `.gitattributes` + `data/snapshots/MANIFEST.json` |
| K9 | 真值台账只存输入与期望，禁止 `observed_*` | `plan/05` §三 + `tests/test_m1_data.py` |
| K10 | 生成器不读时钟、不随机；重生成逐字节一致 | `tools/gen_*.py --check` |
| K11 | 规则包冻结、禁手改；由生成器整目录重生成 | `tools/gen_rules.py` |
| K12 | `announcementType` 数值码不参与分类 | `classify` 的 AST 门禁 + `rules-v1.json` 的 `match.announcement_type_used=false` |
| K13 | 日期按**交易所时区 UTC+8** 换算，不用 UTC | `clock.exchange_date_from_epoch_ms`；`report` 默认日同源 |
| K14 | `fetch_runs` 游标只认 `source_kind='watchlist'` 且 `status in (complete, degraded)` | `fetch/repository.py` |
| K15 | 请求计数拆成 first / pages / retries | `FetchSummary` |
| K16 | 分类风险取"胜出类别 + 补丁标签"，落选类别只进日志 | `classify/engine.py` + `test_risk_uses_the_winning_category_not_every_match` |
| K17 | 规则包先全量校验再开库；坏包 exit 2 且零写入 | `cli.cmd_classify` 的调用顺序 |
| K18 | 未分类条目照常落库，不跳过 | `classify/service.py` + `test_unclassified_rows_are_stored_not_skipped` |
| K19 | 渲染层零判定：`report/` 不 import `classify`；报告只读既有字段 | `report/` AST 门禁 + `test_a_stored_low_stays_low_even_when_the_title_would_grade_high` |
| K20 | HTML 零外链；守卫在 `render_html` 出口常驻 | `report/html.py` + `test_the_guard_runs_inside_render_html` |
| K21 | 日报只读 `source_kind='watchlist'` | `report/repository.py::rows_for_date` + `test_market_sweep_rows_stay_out_of_the_report` |
| K22 | 免责声明单点定义，三处共用 | `src/cnwatch/disclaimer.py` |

## 四、本机环境坑（M2/M3/M4 新增实测）

1. **`py -0p` 两条通道**：3.8.8 与 3.12.10。用 `py -3.12` / `py -3.8`，不要用 `python`。
2. **`tools/` 不是包**：命令行跑要带 `PYTHONPATH=tools`（测试里 `_run_tool` 已处理）。
3. **源码态必须 `PYTHONPATH=src`**；`pip install --target` 到中立目录后不要留在该目录再跑。
4. **Windows 控制台 + 中文**：落盘一律 `encoding="utf-8"`、`newline="\n"`；
   **`Path.write_text(newline=...)` 在 3.8 不存在**，要显式 `open(..., newline="\n")`。
5. **`tests/test_no_stubs.py` 会扫禁用词**：别在实现/测试里写 "placeholder"、
   "NotImplementedError"、"TODO: implement"。M3 的 SQL 变量名 `placeholders` 就被扫红过一次。
6. **回放不要真睡**：CLI 已为 `--snapshots-dir` 注入 `NullSleep`。
7. **删除临时目录前先 `cd` 到仓库根**；失败重试一次（Git Bash 目录占用）。
8. **`py -3.12 -c` 里写多行替换脚本容易静默不生效**：替换后打印 `found:`/`changed:`
   再跑测试。M4 实际踩过：删 `html.py` 里一行守卫时，第一次替换 `found: False` 却仍报绿，
   改成按行号 `del lines[361]` 才真正生效。
9. **`file:` URL 不能直接用浏览器打开**（IAB 拒绝）。要看日报效果，用
   `py -3.12 -m http.server` 起本地服务再访问 `http://127.0.0.1:<port>/<date>/daily.html`。
10. **`tab.screenshot({fullPage:true})` 的拼接可能重复内容**：用
    `document.querySelectorAll('h1').length` 确认 DOM 没重复，再判断是截图拼接问题。

## 五、DoD 逐项 checklist（M5）

- [ ] 归档路径/大小/时间记录正确（`archive_records` 三字段）
- [ ] 重复归档同一 id 幂等（不重复下载、不重复写行）
- [ ] 抽取依赖缺失 → 退出码 2 + 安装提示，不写空摘要
- [ ] 抽取失败 → `text_status=unavailable`，摘要为空，退出码 0
- [ ] 通知默认 `none` 时零外部调用（注入可断言的 transport/spy）
- [ ] 通知失败不影响日报与 DB，退出码 1，错误只进 `out/logs/`
- [ ] `data/pdf/` 未被 git 跟踪（测试断言 `.gitignore` + 目录不出现于 `git ls-files`）
- [ ] 双解释器测试通过（本地等价校验，未接远端）
- [ ] 计划文档（04/06/07）与实测同步回写
- [ ] `plan/HANDOFF-M6.md` 已落盘并提交

## 六、关键命令速查

```bash
# 测试（两个解释器都要跑）
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.12 -m pytest -q
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.8  -m pytest -q

# M1 三道数据门禁（离线，不联网）
PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_snapshots.py --check
PYTHONPATH=tools PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_rules.py --check
PYTHONPATH=tools PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_truth.py --check

# M2+M3+M4 离线全链路
rm -rf rt
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt watchlist import \
  --file data/fixtures/watchlist-sample.csv
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt fetch \
  --snapshots-dir data/snapshots --today 2026-10-16
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt classify
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt report \
  --date 2026-10-15 --generated-at 2026-10-15T12:00:00Z
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt report \
  --date 2026-10-16 --generated-at 2026-10-16T12:00:00Z

# 日报双跑逐字节一致
sha256sum rt/out/reports/2026-10-16/daily.md rt/out/reports/2026-10-16/daily.html
# 重跑同一条 report 命令后应完全相同

# 口径回显 / CLI 冒烟
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt fetch --show-config
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.12 -m cnwatch --home ./rt selftest

# 安装面验证（中立目标目录，不入仓）
rm -rf .tmp-install
py -3.12 -m pip install --no-deps --no-build-isolation --target .tmp-install .
find .tmp-install/cnwatch -name "*.py" | wc -l     # 应含 classify/ fetch/ report/ watchlist/
find .tmp-install/cnwatch/resources -type f        # 两个 json 都在

# 看日报效果（file: URL 不能直接用浏览器打开）
cd rt/out/reports && py -3.12 -m http.server 8765 --bind 127.0.0.1
# 浏览器访问 http://127.0.0.1:8765/2026-10-16/daily.html
```

## 七、M6 预告（不要提前做）

M6 才动发布：GitHub 公开仓库、CI 四矩阵、tag、Release、发布留档。M5 阶段**不要**写 CI 配置变更、
不要建远端、不要打 tag。M5 只需保证：

- `archive_records` 五个字段都被正确填写；
- 通知默认关闭时**零外部调用**（可注入 spy 断言）；
- 归档与通知的失败都只降级、不污染日报与 DB。

## 八、交接检查（本棒收尾时自查）

- [x] 所有 DoD 的 ✅ 都能指向具体测试名或命令输出
- [x] 没做过的事写明"未做"（真实联网抓取、真实 PDF 下载、CI）
- [x] 测试计数与文档一致（295 / 295）
- [x] M4 新确立的口径（watchlist 来源隔离、INNER JOIN、content_sha256 含义、免责单点）已写进 04 与本文档
- [x] `plan/HANDOFF-M5.md` 已落盘并提交
