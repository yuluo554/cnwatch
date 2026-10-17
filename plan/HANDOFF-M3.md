# HANDOFF-M3：规则引擎

> 上游：M2（抓取与去重）已完成。本文件是 M3 的启动包，库内路径一律相对路径。
> 启动命令（用户侧）：`/goal 读取 "<项目绝对路径>/plan/HANDOFF-M3.md" 继续完成任务`

## 一、当前进度与证据

### M2 已完成（2026-10-17）

| 项 | 证据 |
|---|---|
| `watchlist` 七个子命令 | `src/cnwatch/watchlist/`；`tests/test_watchlist.py`（25 项）+ `tests/test_cli.py` |
| `fetch` 全链路 | `src/cnwatch/fetch/`；`tests/test_fetch.py`（32 项）+ `tests/test_fetch_replay.py`（11 项） |
| 配置单一来源 | `fetch/config.py` 解析 `data/README.md` 第四节；`fetch --show-config` 回显解析结果 |
| 离线回放 | `data/snapshots/announce-day-{15,16}.json` → `fetch --snapshots-dir`，20 只 / 20 请求 / 13 条基线 |
| 运行台账 | `fetch_runs`（含 `source_kind`/`pages`/`dropped`/`baseline_created`）、`fetch_failures` |
| Schema | v2；v1 库自动 `ALTER TABLE` 升级，已有行不丢（`tests/test_db.py`） |
| 反向样例 | `data/fixtures/negative/`（`<em>`、缺 id、空标题、总数不符、坏 CSV 行、缺列） |
| 测试 | 双解释器各 **194** 项通过 |

真实证据（可复跑）：

```bash
rm -rf rt
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt watchlist import \
  --file data/fixtures/watchlist-sample.csv
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt fetch \
  --snapshots-dir data/snapshots --today 2026-10-16
# 首次：baseline established, 13 stored / requests 20 (first 20, pages 0, retries 0)
# 再跑：new 0  already known 13
```

### 尚未发生的事（不得写进 DoD 打勾）

- **GitHub 远端仍不存在，CI 一次都没跑过。** 所有 CI 类结论只能写
  "本地等价校验（未接远端，CI 待首推验证）"。`.github/workflows/ci.yml` 的
  "Offline replay smoke" 步骤只在本地用同一串命令手跑过。
- **没有做过一次真实联网抓取。** M2 的 `UrllibTransport` 路径未经真实请求验证；
  回放证明了链路与去重，不证明站点当前的响应形态。M6 前应真实跑一次并留档。
- `classify` / `report` / `archive` / `notify` / `run` 仍返回退出码 3。
- NFR-04「50 只 < 60s」只有估算（1s/只 × 50 ≈ 50s），没有真实基准。

## 二、下一棒待办（M3）

按 `plan/06-里程碑.md` 的 M3 DoD 与 `plan/04-模块详设.md` 模块 3 执行。

1. **`classify` 子命令**：读 `announcements` 中尚未分类（或 `rules_version` 过期）的行，
   用 `src/cnwatch/resources/rules/rules-v1.json` 判定，写 `classifications`。
2. **26 类规则**：官方类别按 `priority` 升序、同优先级按 `id` 字典序取第一个命中；
   其余命中记 `also_matched`（只进日志）。
3. **四补丁**：诉讼·仲裁 / 减持 / 回购 / 质押，独立扫描，全部命中进 `tags[]`，
   固定顺序；`neutralizers` 必须生效（"超额配股权"不是配股）。
4. **风险分级**：所有命中项取最高（high > mid > low）；`unclassified` 也要有风险级。
5. **真值比对**：`data/fixtures/truth/classify-v1.json` 86 条逐条比对，
   差异必须点名 `announcement_id`。

### M3 必须踩准的四条已定口径

| 口径 | 值 | 出处 |
|---|---|---|
| 分类只用标题 + code | 不用正文、不用 `announcementType` 数值码 | `data/README.md` S12 / K12 |
| 26 类必须全 | 与 `data/snapshots/new-notice.js` 逐项相等 | 规则包 + 自检 |
| 四补丁只进 tags | 不伪造官方类别 | `data/README.md` 第四节 |
| 规则包禁手改 | 改规则 = 改生成器 + 整目录重生成 | `tools/gen_rules.py --check` |

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
| K12 | `announcementType` 数值码不参与分类 | `rules-v1.json` 的 `match.announcement_type_used=false` |

**M3 新增（建议沿用）**

| 编号 | 口径 | 理由 |
|---|---|---|
| K13 | 日期按**交易所时区 UTC+8** 换算，不用 UTC | 冻结 2026-10-15 有 922/1097 条在 UTC 下会落到前一天（M2 实测） |
| K14 | `fetch_runs` 游标只认 `source_kind='watchlist'` 且 `status in (complete, degraded)` | 全市场兜底不得推进清单游标；失败运行不得留下盲区 |
| K15 | 请求计数拆成 first / pages / retries | "请求次数 == 清单长度"必须仍然字面可验 |

## 四、本机环境坑（M2 新增实测）

1. **`py -0p` 两条通道**：3.8.8 与 3.12.10。用 `py -3.12` / `py -3.8`，不要用 `python`。
2. **`tools/` 不是包**：命令行跑要带 `PYTHONPATH=tools`（测试里 `_run_tool` 已处理）。
3. **源码态必须 `PYTHONPATH=src`**；`pip install --target` 到中立目录后不要留在该目录再跑，
   否则会误用 `.tmp-install/cnwatch` 而不是源码。
4. **Windows 控制台 + 中文**：落盘一律 `encoding="utf-8"`、`newline="\n"`；
   控制台断言优先看退出码，不要只靠中文 stderr 文字。
5. **`tests/test_no_stubs.py` 会扫测试文件里的禁用词**：写测试名/注释时别用
   "placeholder"、"NotImplementedError"、"TODO: implement"；上次就是测试名里带了
   一个被扫描词导致红灯。
6. **回放不要真睡**：CLI 已为 `--snapshots-dir` 注入 `NullSleep`；自己写新回放测试时
   用 `RecordingSleep(clock=FakeMonotonic())`，否则全市场回放要空等 44s。
7. **删除临时目录前先 `cd` 到仓库根**；失败重试一次（Git Bash 目录占用）。

## 五、DoD 逐项 checklist（M3）

- [ ] 26 类集合与 `new-notice.js` 快照逐项相等（集合断言，不是计数）
- [ ] 真值台账 86 条逐条命中；差异点名 `announcement_id`
- [ ] 四补丁正例命中、反例不命中（反例含 `neutralizers` 场景）
- [ ] 风险冲突取高；`unclassified` 有显式风险级与计数
- [ ] `announcementType` 不出现在判定路径（AST/源码断言）
- [ ] 同一输入双跑，`classifications` 逐行一致
- [ ] 规则包版本写进 `classifications.rules_version`
- [ ] 规则包缺失/26 类不全 → 退出码 2 且不写任何分类结果
- [ ] 双解释器测试通过（本地等价校验，未接远端）
- [ ] 计划文档（06/07/04）与实测同步回写
- [ ] `plan/HANDOFF-M4.md` 已落盘并提交

## 六、关键命令速查

```bash
# 测试（两个解释器都要跑）
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.12 -m pytest -q
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.8  -m pytest -q

# M1 三道数据门禁（离线，不联网）
PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_snapshots.py --check
PYTHONPATH=tools PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_rules.py --check
PYTHONPATH=tools PYTHONDONTWRITEBYTECODE=1 py -3.12 tools/gen_truth.py --check

# M2 离线回放（28 秒内跑完，含两次运行）
rm -rf rt
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt watchlist import \
  --file data/fixtures/watchlist-sample.csv
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt fetch \
  --snapshots-dir data/snapshots --today 2026-10-16
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt fetch \
  --snapshots-dir data/snapshots --today 2026-10-16   # 第二次必须 new 0

# 口径回显
PYTHONPATH=src py -3.12 -m cnwatch --home ./rt fetch --show-config

# CLI 冒烟
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.12 -m cnwatch --home ./rt selftest

# 安装面验证（中立目标目录，不入仓）
rm -rf .tmp-install .tmp-run
py -3.12 -m pip install --no-deps --no-build-isolation --target .tmp-install .
find .tmp-install/cnwatch -name "*.py" | wc -l          # 应含 fetch/ 与 watchlist/
find .tmp-install/cnwatch/resources -type f             # 两个 json 都在
```

## 七、M4 预告（不要提前做）

M4 才动 `report`。M3 阶段**不要**写任何渲染代码，也不要在 `classify` 里预计算
"新增/消失"——那是 `report_baseline_items` 的职责。M3 只需保证：

- `classifications` 每行有确定的 `category / tags_json / risk_level / rules_version`；
- 未分类条目**照常落库**（`category='unclassified'`），不得跳过——
  跳过会让日报的"未分类计数"永远为 0。

## 八、交接检查（本棒收尾时自查）

- [x] 所有 DoD 的 ✅ 都能指向具体测试名或命令输出
- [x] 没做过的事写明"未做"（真实联网抓取、CI）
- [x] 测试计数与文档一致（194 / 194）
- [x] `plan/HANDOFF-M3.md` 已落盘并提交
