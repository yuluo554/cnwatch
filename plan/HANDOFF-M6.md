# HANDOFF-M6：发布

> 上游：M5（归档与提醒）已完成。本文件是 M6 的启动包，库内路径一律相对路径。
> 启动命令（用户侧）：`/goal 读取 "<项目绝对路径>/plan/HANDOFF-M6.md" 继续完成任务`

## 一、当前进度与证据

### M5 已完成（2026-10-17）

| 项 | 证据 |
|---|---|
| `archive` 子命令 | `src/cnwatch/archive/`（`config/models/transport/extract/repository/service`）；`cnwatch archive [--announcement-id] [--code] [--category] [--risk] [--all-high] [--text] [--downloaded-at] [--show-config]` |
| 归档路径 | `data/pdf/<code>/<date>/<announcement_id>.PDF`；后缀（`.PDF`）与页数、摘要长度、默认抽取开关都从 `data/README.md` 第四节解析 |
| 五个字段 | `archive_records` 的 `local_path / byte_size / downloaded_at / text_status / text_excerpt` 全部来自本次真实写入（`test_the_record_carries_path_size_and_time`） |
| 幂等 | 记录存在且文件大小相符 → 不下载、不写第二行；文件缺失/大小不符 → 重下并更新**同一行**。`--text` 补抽只读本地文件 |
| 落盘前三检 | status 200 / 非空 / 以 `%PDF-` 开头；错误页不会被记成一次成功下载（`test_an_error_page_is_not_recorded_as_a_download`） |
| 抽取顺序 | PyMuPDF → `pdftotext` → 不可用；PyMuPDF 延迟导入，核心安装零依赖 |
| 依赖缺失 | `require_extractor` 在第一次下载、创建第一个目录**之前**拒绝：退出码 2 + `cnwatch[pdf]`/poppler 两条安装路径；连 home 都不创建 |
| 抽取失败 | `ExtractionFailed` / 空结果 / 压缩后为空 → `text_status=unavailable` + 空摘要 + 退出码 0（不伪造摘要） |
| 摘要纪律 | `archive/` 不 import `classify`（AST 门禁）；抽取内容含"质押/减持/诉讼"也不改动 `classifications` 行 |
| `notify` 子命令 | `src/cnwatch/notify/`（`config/models/channels/repository/service`）；`cnwatch notify [--channel] [--date] [--dry-run] [--webhook-url] [--email-*] [--show-config]` |
| 默认关闭 | 默认渠道 `none` 从第四节 `通知默认值` 行解析，不写在 argparse；`none` 时 CLI 先返回：不建目录、不开库、不构造渠道对象 |
| 零外部调用 | `test_the_default_channel_makes_zero_external_calls`（transport.post / 桌面命令 / smtplib 三条外呼路径全部替换成会报错的桩 + home 未被创建）；正向对照 `test_the_zero_call_test_can_actually_catch_a_call` |
| 失败隔离 | 失败只写 `out/logs/notify.log` 并退出 1；报告 sha256 与库内行数前后一致；`notify/repository.py` 无任何写语句（源码扫描） |
| `data/pdf/` 不入库 | `/data/pdf/` 在 `.gitignore`（行为断言 `git check-ignore` + `git ls-files data/pdf` 为空），并有正向对照"冻结快照不被忽略" |
| 口径表读取单点 | 新增 `src/cnwatch/policy.py`；`fetch/config.py` 与 `archive/config.py` 都基于它（K7 的落点见 §三） |
| 退出码 | `archive` 0 正常 / 1 有下载失败（其余照常落盘）/ 2 无筛选、无库、无匹配、缺抽取依赖、坏时间戳；`notify` 0 关闭·无内容·dry-run·已发送 / 1 发送失败 / 2 用法错误 |
| 测试 | 双解释器各 **376** 项通过（`test_archive.py` 44 项、`test_notify.py` 39 项）；7 处新门禁做过变异实测，全部变红 |

真实证据（可复跑，离线回放）：

```bash
rm -rf rt
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt watchlist import \
  --file data/fixtures/watchlist-sample.csv
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt fetch \
  --snapshots-dir data/snapshots --today 2026-10-15
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt fetch \
  --snapshots-dir data/snapshots --today 2026-10-16
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt classify
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt report \
  --date 2026-10-16 --generated-at 2026-10-16T12:00:00Z
# items 5  high 0 mid 0 low 5  unclassified 4

# 真实网络下载（只下两份，限频 1s）
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt archive --code 601318 \
  --downloaded-at 2026-10-16T12:00:00Z --data-dir data
# 2 requested  archived 2  failed 0  bytes 264316
# 1225606984  94871 B ；1225607661 169445 B
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt archive --code 601318 --text \
  --downloaded-at 2026-10-16T12:00:00Z --data-dir data
# 2 requested  archived 0  already present 2  text extracted 2（复用本地文件，不重下）
PYTHONPATH=src py -3.8  -m cnwatch --home ./rt archive --code 300750 --text \
  --downloaded-at 2026-10-16T12:00:00Z --data-dir data
# 2 archived  139705 B / 139750 B（3.8 通道走 PyMuPDF 后端）

# 提醒：默认关闭 → 零外部调用；dry-run 组装但不发
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt notify --date 2026-10-16 --data-dir data
# channel=none → "no external call was made"，退出 0
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt notify --channel desktop \
  --date 2026-10-16 --dry-run --data-dir data
# 当日 5 条 + 免责声明，零外部调用
```

真实证据（真实联网，`rt-real/`）：

```bash
rm -rf rt-real
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt-real watchlist import \
  --file data/fixtures/watchlist-sample.csv
time PYTHONPATH=src py -3.12 -m cnwatch --home ./rt-real fetch
# fetch  window 2026-10-10..2026-10-17  (watchlist)
# first run: baseline established, 51 announcements stored
# stocks 20 (ok 20, failed 0)  requests 20 (first 20, pages 0, retries 0)
# result: complete      real 0m22.071s   ← 20 只 / 22.07s，即 1.1s/只
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt-real classify
# pending 51  classified 51 ；risk mid 8 / low 43 ；unclassified 16
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt-real report
# report 2026-10-17  items 23  high 0 mid 1 low 22  unclassified 3
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt-real archive \
  --announcement-id 1225608903 --text --data-dir data
# 1 archived  152999 B  text=extracted（excerpt 400 字符，真实中文正文）
```

- **`UrllibTransport.post` 已对真实接口验证**（20 请求 / 20 只 / 0 失败 / 0 重试，7 天窗口 51 条）。
- 真实 10-15 与 10-16 的自选股条数分别为 **8 / 5**，与 M1 冻结快照的子集完全一致。
- real PDF 直连复核：`finalpage/2026-10-13/1225601360.PDF` → 200 `application/pdf` **92,643 B**（与 S5 同值）。
- **NFR-04 首次有了真实基准**：20 只 22.07s（1.1s/只）→ 50 只 ≈ 55s，仍在 <60s 口径内但余量只剩 ~8%。

产物尺寸（离线两日）：`daily.md` 4793 / 5708 B；`daily.html` 9252 / 10702 B。

### 尚未发生的事（不得写进 DoD 打勾）

- **GitHub 远端仍不存在，CI 一次都没跑过。** 所有 CI 类结论只能写
  "本地等价校验（未接远端，CI 待首推验证）"。
- **真实桌面通知 / 真实邮件 / 真实 webhook 各投递 0 次**：没有可用接收端，
  默认关闭也不投递。已做的只有"注入失败"与"注入成功"两种离线断言。
- `archive --all-high` 未在真实全库上跑过（离线测试覆盖了筛选与批量计数）。
- `run`（端到端编排）仍未实现，仍返回退出码 3（见 `plan/06` M5 偏差）。
- 无 tag、无 Release、无 `plan/RELEASE-M6.md`；`README.md` 的状态行仍是开发态措辞。

## 二、下一棒待办（M6）

按 `plan/06-里程碑.md` 的 M6 DoD 与 `plan/07-交付对标.md` §五 执行。

1. **首推与 CI 四矩阵**：建远端 → push → ubuntu/windows × 3.8/3.12 全绿；
   `push 数 = run 数`（tag push 独立计一条）留档 run id。
2. **脱敏扫描**：真实用户名、本机绝对路径（Windows 盘符绝对路径、POSIX 家目录路径）、
   Cookie、真实邮箱 0 命中。建议命令：

   ```bash
   # 本机绝对路径：任何 "盘符:" 或 /Users/xxx、/home/xxx
   git grep -nIE '[A-Za-z]:[\\/]|/Users/[A-Za-z]|/home/[A-Za-z]' -- . ':!plan/HANDOFF-M6.md'
   # 真实邮箱：允许 example.invalid / example.com 这类保留域名占位
   git grep -nIE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+' -- . | grep -vE 'example\.(invalid|com)'
   # Cookie / 凭据
   git grep -nIE 'Cookie|Set-Cookie|Bearer |password=' -- . ':!plan/HANDOFF-M6.md'
   ```

   已知且**允许**的占位：测试与文档里的 `*@example.invalid`、`smtp.example.invalid`、
   `http://example.invalid/hook`（RFC 2606 保留域名，永不可解析），以及
   `.github/workflows/ci.yml` 里的 `actions/checkout@v4`、`actions/setup-python@v5`
   （Actions 的 `@ref` 语法，不是邮箱；扫描时按 `uses:` 行排除）。
   本文件自身不会被当发布物扫描，但历史提交照样能查：`git log -p --all | grep -nE '...'`。
3. **干净 clone 复核**：新目录 `git clone --no-local` + 新 venv + README 逐条跑通；
   三个 `gen_*.py --check` 全绿。
4. **tag + Release**：tag 打在 CI 绿的那个提交上；Release 附 release zip + sha256。
5. **附件回下载对账**：真下载 Release 附件 → sha256 对账 → 中立目录解压冒烟
   （`pip install --target` 到仓库外目录，`cnwatch selftest` + 三条命令）。
6. **`plan/RELEASE-M6.md`**：事实分栏留档（本地实测 / CI run id / 附件 sha256 / 未做项）。
7. **README 状态行转正**：M6 行 ✅；命令、extras、附件名与实物一致；局限披露与实测一致。

### M6 必须踩准的口径

| 口径 | 值 | 出处 |
|---|---|---|
| CI 结论写法 | 未接远端时只写"本地等价校验（未接远端，CI 待首推验证）" | 本文档 §一 |
| tag 位置 | CI 绿的那个提交 | `plan/07` §5.4 |
| 附件名 | 与 README 声明一致（M6 定名后三处同步） | `plan/07` §5.1 |
| 脱敏 | 仓库无 PDF、无 SQLite、无日志、无真实持仓、无本机路径/用户名/邮箱/Cookie | `plan/07` §5.3 |
| 免责三处 | README / CLI `--help` / 日报页脚，单点常量 `src/cnwatch/disclaimer.py` | K22 |
| NFR-04 基准 | 20 只 22.07s（1.1s/只）→ 50 只 ≈55s；发布说明里用这个数，不用估算 | `data/README.md` §六之三 |

### M5 留下的口径，改动前先读

1. **归档幂等判据是"记录存在且文件大小相符"**：同态不写第二行；坏文件自愈更新同一行。
   不要为了"重新抽文本"改成删除+重插——那会产生重复行的观感，也绕开了 FR-40 的"不批量预下载"。
2. **`text_status` 只有两态**：`unavailable` 同时表示"没请求"和"抽不出"。空结果**不得**记 `extracted`。
3. **抽取依赖缺失是前置拒绝**（exit 2，零写入）。这个顺序（先校验、后开库/建目录）是 K17 的同一纪律。
4. **`notify` 默认渠道从 `data/README.md` 第四节解析**。要让默认变成可投递的渠道，先改文档再改代码。
5. **`archive` 的筛选项只覆盖 `watchlist`**；`--announcement-id` 才能点到市场兜底行。
6. **`policy.py` 是第四节口径表的唯一读取点**；给新模块加数值时改文档 + 在该模块建类型化视图，
   不要在业务代码里写 `3`、`400`、`.PDF` 这类字面量（M5 的门禁已覆盖 `archive/`）。
7. **`report` 的三条老口径未变**：只读 `source_kind='watchlist'`、`announcements INNER JOIN classifications`、
   `content_sha256` 记的是 Markdown。

## 三、既定口径清单（K 编号，动了会打挂测试）

| 编号 | 口径 | 落点 |
|---|---|---|
| K1 | 退出码 0/1/2/3 语义固定；3 现在只剩 `run` 一个用户 | `src/cnwatch/exit_codes.py` + `tests/test_cli.py` |
| K2 | `announcement_id` 是唯一主键，禁止可空唯一键 | `src/cnwatch/db.py` + `tests/test_db.py` |
| K3 | 分组只允许 `持仓/观察/风险` | DB CHECK + `watchlist.models.validate_group` |
| K4 | `--home` > `CNWATCH_HOME` > cwd；不隐式写用户主目录 | `src/cnwatch/paths.py` |
| K5 | 编码守卫只改已知 ASCII-like 编码，cp936/gbk 不动 | `src/cnwatch/encoding.py` |
| K6 | 自检必须可证伪 | `src/cnwatch/selftest.py` |
| K7 | 所有数值只在 `data/README.md` 第四节定义 | **`src/cnwatch/policy.py`（表读取单点）** + `fetch/config.py`、`archive/config.py`、`notify/config.py` + AST 门禁 |
| K8 | 快照入库标 `-text`，sha256 与 manifest 同步提交 | `.gitattributes` + `data/snapshots/MANIFEST.json` |
| K9 | 真值台账只存输入与期望，禁止 `observed_*` | `plan/05` §三 + `tests/test_m1_data.py` |
| K10 | 生成器不读时钟、不随机；重生成逐字节一致 | `tools/gen_*.py --check` |
| K11 | 规则包冻结、禁手改 | `tools/gen_rules.py` |
| K12 | `announcementType` 数值码不参与分类 | `classify` 的 AST 门禁 + `rules-v1.json` 的 `announcement_type_used=false` |
| K13 | 日期按交易所时区 UTC+8 换算 | `clock.exchange_date_from_epoch_ms`；`report`/`notify` 默认日同源 |
| K14 | `fetch_runs` 游标只认 `source_kind='watchlist'` 且 `status in (complete, degraded)` | `fetch/repository.py` |
| K15 | 请求计数拆成 first / pages / retries | `FetchSummary` |
| K16 | 分类风险取"胜出类别 + 补丁标签" | `classify/engine.py` + `test_risk_uses_the_winning_category_not_every_match` |
| K17 | 先全量校验再开库；坏输入 exit 2 且零写入 | `cli.cmd_classify` / `cli.cmd_archive` 的调用顺序 |
| K18 | 未分类条目照常落库，不跳过 | `classify/service.py` |
| K19 | 渲染层零判定：`report/` 不 import `classify` | `tests/test_report.py` AST 门禁 + 行为测试 |
| K20 | HTML 零外链；守卫在 `render_html` 出口常驻 | `report/html.py` |
| K21 | 日报只读 `source_kind='watchlist'` | `report/repository.py::rows_for_date` |
| K22 | 免责声明单点定义，三处共用 | `src/cnwatch/disclaimer.py` |
| K23 | 归档幂等 = 记录在且文件大小相符；坏态自愈不产生重复行 | `archive/service.py::_is_intact` + `test_re_archiving_the_same_id_downloads_nothing` |
| K24 | `text_status` 两态；空抽取记 `unavailable`，不得记 `extracted` | `archive/service.py::_extract` + `test_an_empty_extraction_is_unavailable_not_a_success` |
| K25 | 抽取依赖缺失 → 下载前 exit 2 + 安装提示 + 零写入 | `archive/service.require_extractor` + 两个 CLI 测试 |
| K26 | 归档筛选项只覆盖 `watchlist`；`--announcement-id` 例外 | `archive/repository.py::targets` + `test_filters_are_watchlist_scoped_but_an_explicit_id_is_not` |
| K27 | 通知默认渠道从第四节解析；`none` 时零外部调用（先返回，不建目录不开库） | `notify/config.py` + `cli.cmd_notify` + 两条对照测试 |
| K28 | 通知层无写路径；失败只进 `out/logs/notify.log` 且退出 1 | `notify/repository.py` 源码扫描 + `test_a_failed_send_leaves_the_report_and_the_database_untouched` |
| K29 | 归档落盘前三检：200 / 非空 / `%PDF-` 开头 | `archive/transport.py::PdfDownloader.fetch` + `test_an_error_page_is_not_recorded_as_a_download` |
| K30 | 时间戳单点：`clock.parse_instant` + `PinnedTimestamp`（`--generated-at` / `--downloaded-at` 同源） | `src/cnwatch/clock.py` |

## 四、本机环境坑（M5 新增实测）

1. **本机两个解释器的抽取后端不同**：3.8 通道装了 PyMuPDF（`fitz` + `pymupdf`），3.12 通道没有，
   只有系统 `pdftotext`（本机由 Git for Windows 的 mingw64 提供，落在 PATH 上）。
   写"两种后端都跑通"的证据要指明是哪个解释器跑的；3.8 会打印 SWIG 的 `DeprecationWarning`，属噪声。
2. **真实接口可用**（2026-10-17 复核）：`POST hisAnnouncement/query` 与
   `GET static.cninfo.com.cn/finalpage/...` 均 200；`robots.txt` 仍是 404（S9 的结论不变）。
   真实抓取 20 只 22.07s，比估算的 20s 略慢，50 只余量只有 ~8%。
3. **`git check-ignore --no-index -q -- <path>` 是验证忽略规则的正确方式**：文件不必存在，
   退出码 0 = 被忽略、1 = 未被忽略，可以直接当断言用（比 grep `.gitignore` 强）。
4. **`py -3.12 - <<'EOF'` 的 heredoc 替换脚本要打印 `FOUND/CHANGED`**（HANDOFF-M5 §四.8 的同一坑）：
   M5 用同一套写法做变异实测时，7 处替换全部先确认 `FOUND: True` 再看红。
5. **`pathlib` 的 `/` 与 `%` 优先级**：`home / "x" / "y%s" % suffix` 会把 `%` 作用在整个 Path 上并抛
   `TypeError`；格式化要先加括号。M5 写测试时踩过一次。
6. **跑真实抓取前先删 scratch home**：`fetch` 会写 `fetch_runs` 游标，第二次运行就变成增量窗口，
   两次运行的条数不可比。要留档就每次 `rm -rf rt-real` 重来。
7. 其余 M2–M4 的坑仍有效：`PYTHONPATH=src` 源码态、`py -3.12`/`py -3.8` 不要用 `python`、
   落盘一律 `encoding="utf-8"` + `newline="\n"`、删临时目录前先 `cd` 出仓库根、
   `file:` URL 不能直接用浏览器打开（要起 `py -3.12 -m http.server`）。

## 五、DoD 逐项 checklist（M6）

- [ ] 历史 = 工作树（`git status` 干净、全部提交在 HEAD）
- [ ] 干净 clone + 全新 venv + README 逐条跑通
- [ ] 脱敏扫描 0 命中（用户名、本机路径、Cookie、邮箱）
- [ ] CI 四矩阵全绿，push 数 = run 数（按事件计）
- [ ] `git tag` + GitHub Release（附 release zip + sha256）
- [ ] Release 附件真下载 → sha256 对账 → 中立目录解压冒烟
- [ ] `plan/RELEASE-M6.md` 事实分栏留档
- [ ] README 状态行转正；许可、命令、附件名三类与实物一致
- [ ] 三处免责声明目视复核一致（README / CLI `--help` / 日报页脚）

## 六、关键命令速查

```bash
# 测试（两个解释器都要跑）
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.12 -m pytest -q     # 376 项
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.8  -m pytest -q     # 376 项

# M1 三道数据门禁（离线，不联网）
PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_snapshots.py --check
PYTHONPATH=tools PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_rules.py --check
PYTHONPATH=tools PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_truth.py --check

# 离线全链路（M2–M5）
rm -rf rt
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt watchlist import --file data/fixtures/watchlist-sample.csv
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt fetch --snapshots-dir data/snapshots --today 2026-10-16
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt classify
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt report --date 2026-10-16 --generated-at 2026-10-16T12:00:00Z
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt archive --code 601318 --downloaded-at 2026-10-16T12:00:00Z --data-dir data
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt notify --date 2026-10-16 --data-dir data   # 默认关闭

# 真实联网抓取（限频 1s/只，20 只约 22s）
rm -rf rt-real
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt-real watchlist import --file data/fixtures/watchlist-sample.csv
time PYTHONPATH=src py -3.12 -m cnwatch --home ./rt-real fetch

# 口径回显
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt fetch   --show-config
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt archive --show-config --data-dir data
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt notify  --show-config --data-dir data
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt selftest

# 忽略规则（不需要文件存在）
git check-ignore -v --no-index -- data/pdf/600519/2026-10-16/1.PDF   # 命中 = 已忽略

# 安装面验证（中立目标目录，不入仓）
rm -rf .tmp-install
py -3.12 -m pip install --no-deps --no-build-isolation --target .tmp-install .
find .tmp-install/cnwatch -name "*.py" | wc -l     # 应含 archive/ notify/ classify/ fetch/ report/ watchlist/
find .tmp-install/cnwatch/resources -type f        # 两个 json 都在

# 发布（M6）
git tag -a v0.1.0 -m "cnwatch 0.1.0"
gh release create v0.1.0 --notes-file plan/RELEASE-M6.md
gh release view v0.1.0 --json assets,isDraft
```

## 七、交接检查（本棒收尾时自查）

- [x] 所有 DoD 的 ✅ 都能指向具体测试名或命令输出
- [x] 没做过的事写明"未做"（真实通知投递 0 次、`--all-high` 未真实批跑、CI 未首推、`run` 未实现）
- [x] 测试计数与文档一致（376 / 376）
- [x] M5 新确立的口径（K23–K30）已写进 `plan/04` §5、`data/README.md` §六之三 与本文档
- [x] 变异实测有记录（7 处门禁逐个变红）
- [x] `plan/HANDOFF-M6.md` 已落盘并提交
