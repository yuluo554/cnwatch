# HANDOFF-M2：抓取与去重

> 上游：M1（数据先行）已完成。本文件是 M2 的启动包，库内路径一律相对路径。
> 启动命令（用户侧）：`/goal 读取 "<项目绝对路径>/plan/HANDOFF-M2.md" 继续完成任务`

## 一、当前进度与证据

### M1 已完成

| 项 | 证据 |
|---|---|
| 股票主数据快照 | `data/snapshots/szse_stock.json`（6261 条）、`bj_stock.json`（604 条） |
| 26 类枚举快照 | `data/snapshots/new-notice.js`（逐字节切片，26 个枚举） |
| 公告响应快照 | `data/snapshots/announce-day-2026-10-{15,16}.json`（1097 / 1313 条） |
| 标定语料 | `category-samples-*.json`（每类 6 页真实标题）、`keyword-samples-*.json`（四补丁） |
| 量级台账 | `volume-ledger-2026-09-17_2026-10-17.json`（26 类 + 5 关键词） |
| sha256 登记 | `data/snapshots/MANIFEST.json`（8 个文件） |
| 映射台账 | `data/fixtures/stock-master.csv`（6261 行）、`watchlist-sample.csv`（20 只） |
| 规则包 | `src/cnwatch/resources/rules/rules-v1.json`（26 类 + 4 补丁 + 风险表 + 季节性） |
| 真值台账 | `data/fixtures/truth/{fetch,classify}-v1.json` |
| 生成器 | `tools/gen_snapshots.py`、`tools/gen_rules.py`、`tools/gen_truth.py`（均带 `--check`） |
| 测试 | `tests/test_m1_data.py`（39 项 M1 门禁，含独立复算），双解释器各 **97** 项通过 |

### 尚未发生的事（不得写进 DoD 打勾）

- GitHub 远端仍不存在，CI 一次都没跑过。所有 CI 类结论只能写
  "本地等价校验（未接远端，CI 待首推验证）"。
- `fetch` / `watchlist` / `classify` 仍返回退出码 3，**M1 没有写任何业务抓取代码**。

## 二、下一棒待办（M2）

按 `plan/06-里程碑.md` 的 M2 DoD 与 `plan/04-模块详设.md` 模块 1/2 执行。

1. **`watchlist` 子命令**：`import / add / remove / edit / list / search / sync-master`。
   `sync-master` 读 `data/fixtures/stock-master.csv`（或 M1 快照）建立 `code → orgId`；
   分组只允许 `持仓 / 观察 / 风险`（K3）。
2. **`fetch` 主链路**：逐只请求、`pageSize=30` 翻页、`totalAnnouncement` 对账、
   `<em>` 清洗、失败隔离与退避。
3. **配置注入**：限频 1s、退避 1/2/4s×3、pageSize 30、窗口规则**不得写死在业务代码**，
   由 `fetch/config.py` 从 `data/README.md` 第四节的单一来源读取/常量集中注入（K7）。
4. **离线回放**：用 `data/snapshots/announce-day-*.json` 冻结字节驱动完整链路。
5. **运行摘要**：请求次数、翻页次数、失败清单点名、`unclassified` 不在此阶段。

### M2 必须踩准的四条实测口径

| 口径 | 值 | 出处 |
|---|---|---|
| 多股票参数无效 | 必须逐只请求 | `data/README.md` 约束 1 |
| 增量窗口 | 上次成功日期 − 1 天 → 今天 | 约束 2 |
| `pageSize` 上限 | 30 | 约束 4 |
| `column` | 固定 `szse`（沪/深/北通用） | 约束 5 |

## 三、既定口径清单（K 编号，动了会打挂测试）

| 编号 | 口径 | 落点 |
|---|---|---|
| K1 | 退出码 0/1/2/3 语义固定；3 是临时"未就绪" | `src/cnwatch/exit_codes.py` + `tests/test_cli.py` |
| K2 | `announcement_id` 是唯一主键，禁止可空唯一键 | `src/cnwatch/db.py` + `tests/test_db.py` |
| K3 | 分组只允许 `持仓/观察/风险` | DB CHECK + CLI 校验 |
| K4 | `--home` > `CNWATCH_HOME` > cwd；不隐式写用户主目录 | `src/cnwatch/paths.py` |
| K5 | 编码守卫只改已知 ASCII-like 编码，cp936/gbk 不动 | `src/cnwatch/encoding.py` |
| K6 | 自检必须可证伪：资源缺失/规则包不全时返回非 0 | `src/cnwatch/selftest.py` |
| K7 | 所有数值（限频、退避、pageSize、窗口）只在 `data/README.md` 第四节定义 | 全项目 |
| K8 | 快照入库标 `-text`，sha256 与 manifest 同步提交 | `.gitattributes` + `data/snapshots/MANIFEST.json` |
| K9 | 真值台账只存输入与期望，禁止 `observed_*` | `plan/05` §三 + `tests/test_m1_data.py` |
| K10 | 生成器不读时钟、不随机；重生成逐字节一致 | `tools/gen_*.py --check` |
| K11 | 规则包冻结、禁手改；由生成器整目录重生成 | `tools/gen_rules.py` |
| K12 | `announcementType` 数值码不参与分类 | `rules-v1.json` 的 `match.announcement_type_used=false` |

## 四、本机环境坑（M1 新增实测）

1. **`py -0p` 两条通道**：3.8.8 与 3.12.10。用 `py -3.12` / `py -3.8`，不要用 `python`。
2. **`tools/` 不是包**：`gen_truth.py` 通过 `sys.path` 逻辑依赖 `gen_rules` 的匹配函数，
   命令行跑要带 `PYTHONPATH=tools`（测试里 `_run_tool` 已处理）。
3. **Windows 控制台 + 中文**：落盘一律 `encoding="utf-8"`、`newline="\n"`；
   工具写文件用 `write_bytes(text.encode("utf-8"))`，避免 3.8 的 `write_text(newline=)` 限制。
4. **测试会临时改写仓内文件**（`test_snapshot_check_is_falsifiable` 等），
   `finally` 里恢复；中断测试后先跑一次 `tools/gen_snapshots.py --check` 确认仓干净。
5. **控制台输出可能吞字**：断言中文不要只靠 `stderr` 文字，优先看退出码。
6. **删除临时目录前先 `cd` 到仓库根**；失败重试一次（Git Bash 目录占用）。

## 五、DoD 逐项 checklist（M2）

- [ ] 请求次数 == 清单长度（翻页另计）
- [ ] 同一响应回放两次，行数不变
- [ ] 跨日重叠窗口：重复 0
- [ ] 注入 5xx：重试次数正确、失败清单点名、其余完整
- [ ] 含 `<em>` 样本入库无标签
- [ ] 声明总数与实取不符 → 该股失败，不产生半份结果
- [ ] 首次运行基线语义生效（新增 = 0 且不刷屏）
- [ ] `fetch` 不读系统时钟做"今天"判定（可注入日期）
- [ ] 数值只从单一来源注入，业务代码无字面量（静态断言）
- [ ] 双解释器测试通过（本地等价校验，未接远端）
- [ ] 本文件 `plan/HANDOFF-M3.md` 已落盘并提交

## 六、关键命令速查

```bash
# 环境
py -3.12 --version
py -3.8 --version

# 测试（两个解释器都要跑）
PYTHONDONTWRITEBYTECODE=1 py -3.12 -m pytest -q
PYTHONDONTWRITEBYTECODE=1 py -3.8  -m pytest -q

# M1 三道数据门禁（离线，不联网）
PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_snapshots.py --check
PYTHONPATH=tools PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_rules.py --check
PYTHONPATH=tools PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_truth.py --check

# CLI 冒烟（源码态必须带 PYTHONPATH=src）
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.12 -m cnwatch --home ./rt selftest

# 安装面验证（中立目标目录，不入仓）
rm -rf .tmp-install .tmp-run
py -3.12 -m pip install --no-deps --no-build-isolation --target .tmp-install .
find .tmp-install/cnwatch/resources -type f   # 必须同时有 official-categories.json 与 rules/rules-v1.json
```

> 以上命令在 M1 收尾时均实跑过。`tools/gen_snapshots.py --capture` 会联网（约 200 次
> 请求、~4 分钟），只有需要重抓快照时才用；**日常与 CI 只用 `--check`**。

## 七、M3 预告（不要提前做）

M3 才动 `classify`。M2 阶段**不要**在业务代码里实现标题匹配，也不要读
`rules-v1.json` 做判定；M2 只需保证 `announcements` 表里的标题是干净的
（无 `<em>`、已去空白归一），以及 `classifications` 表结构不被破坏。
