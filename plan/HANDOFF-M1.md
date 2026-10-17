# HANDOFF-M1：数据先行

> 上游：M0（计划与骨架）已完成。本文件是 M1 的启动包，库内路径一律相对路径。
> 启动命令（用户侧）：`/goal 读取 "<项目绝对路径>/plan/HANDOFF-M1.md" 继续完成任务`

## 一、当前进度与证据

### M0 已完成

| 项 | 证据 |
|---|---|
| 计划文档 02–07 | `plan/02-需求解读与边界.md` … `plan/07-交付对标.md` |
| CLI 契约与退出码 | `src/cnwatch/cli.py`、`src/cnwatch/exit_codes.py`、`tests/test_cli.py` |
| SQLite schema v1 | `src/cnwatch/db.py`（`SCHEMA_SQL`）、`tests/test_db.py` |
| 路径解析（`--home` > env > cwd） | `src/cnwatch/paths.py`、`tests/test_paths.py` |
| 编码守卫（不劫持 cp936） | `src/cnwatch/encoding.py`、`tests/test_encoding.py` |
| 可证伪自检 | `src/cnwatch/selftest.py`、`tests/test_selftest.py` |
| 随包 26 类枚举表 | `src/cnwatch/resources/official-categories.json` |
| 无桩哨兵守门 | `tests/test_no_stubs.py` |
| 安装面验证 | `pip install --target` 到中立目录 → 包内 `resources/` 存在，自检 pass |
| 双解释器 | 3.8 与 3.12 各 50 项通过（本地等价校验，未接远端，CI 待首推验证） |

### 尚未发生的事（不得写进 DoD 打勾）

- GitHub 远端不存在，CI 一次都没跑过。所有 CI 类结论只能写"本地等价校验（未接远端，CI 待首推验证）"。
- 未做任何真实网络请求（M1 才做，且要冻结响应字节）。

## 二、下一棒待办（M1）

按 `plan/05-数据计划.md` §五 的 M1-1 … M1-10 执行。建议顺序：

1. **M1-1 股票主数据快照**：抓 `szse_stock.json` 与 `bj_stock.json`，裁剪到
   `code/pinyin/category/orgId/zwjc`，写 `data/snapshots/`，登记 sha256。
2. **M1-2 26 类枚举**：抓 `new-notice.js`，只保留枚举定义片段。
3. **M1-3 / M1-4 公告响应**：抓单日与相邻两日全市场响应，按窗口分页取全；
   用 `totalAnnouncement` 与实际条数对账，不符不落盘。
4. **M1-5 映射台账**：由快照生成 `data/fixtures/stock-master.csv`；
   必须包含三个反例：`300750→GD165627`、`601398→jjxt0000019`、`002594→gshk0001211`。
5. **M1-6 / M1-7 规则包**：写 `tools/gen_rules.py` 产出
   `src/cnwatch/resources/rules/rules-v1.json`；26 类齐全，每类至少一条真实标题命中；
   0 量类（年报/一季报/监事会）显式标季节性。
6. **M1-8 真值台账**：`tools/gen_truth.py` 产出 `data/fixtures/truth/classify-v1.json`；
   只能有输入与期望，禁止 `observed_*`。
7. **M1-9 质押补测**：30 天 `searchkey=质押` 计数，写回 `data/README.md`。
8. **M1-10 勘误同步**：`data/README.md` 与实测逐项对齐。

## 三、既定口径清单（K 编号，动了会打挂测试）

| 编号 | 口径 | 落点 |
|---|---|---|
| K1 | 退出码 0/1/2/3 语义固定；3 是临时"未就绪" | `src/cnwatch/exit_codes.py` + `tests/test_cli.py` |
| K2 | `announcement_id` 是唯一主键，禁止可空唯一键 | `src/cnwatch/db.py` + `tests/test_db.py` |
| K3 | 分组只允许 `持仓/观察/风险` | DB CHECK + CLI 校验 |
| K4 | `--home` > `CNWATCH_HOME` > cwd；不隐式写用户主目录 | `src/cnwatch/paths.py` |
| K5 | 编码守卫只改已知 ASCII-like 编码，cp936/gbk 不动 | `src/cnwatch/encoding.py` |
| K6 | 自检必须可证伪：资源缺失时返回非 0 | `src/cnwatch/selftest.py` |
| K7 | 所有数值（限频、退避、pageSize、窗口）只在 `data/README.md` 第四节定义 | 全项目 |
| K8 | 快照入库标 `-text`，sha256 与 manifest 同步提交 | `.gitattributes` + `data/snapshots/MANIFEST.json` |
| K9 | 真值台账只存输入与期望，禁止 `observed_*` | `plan/05` §三 |
| K10 | 生成器不读时钟、不随机；重生成逐字节一致 | `plan/05` §四 |
| K11 | 规则包冻结、禁手改；由生成器整目录重生成 | `plan/05` §四 |
| K12 | `announcementType` 数值码不参与分类 | `plan/00` D13 |

## 四、本机环境坑（只写实测过的）

1. **`py -0p` 有两条通道**：3.8.8 与 3.12.10。跑命令用 `py -3.12` / `py -3.8`，
   不要用 `python`（Windows 商店占位符风险）。
2. **Git Bash 下 `cd` 后不要依赖上一条命令的 CWD**：本会话曾出现 `pwd` 显示 D 盘但
   `rm` 报 "Device or resource busy"（目录被占用）。清理临时目录前先 `cd` 到仓库根，
   并在删除失败时重试一次。
3. **测试与脚本一律加 `PYTHONDONTWRITEBYTECODE=1`**，避免 pyc 损坏导致的随机失败。
4. **中文输出**：本机控制台是中文环境；脚本落盘一律显式 `encoding="utf-8"`，
   行尾 `newline="\n"`（3.8 不支持 `Path.write_text(newline=...)`）。
5. **pip 可能被代理污染**：需要装包时加 `NO_PROXY="*" no_proxy="*"` 与清华镜像。
6. **`git bash` 的路径改写**：以 `/` 开头的参数可能被 MSYS 转写；涉及绝对路径时
   用 `D:/...` 形式，不用 `/d/...` 作为命令参数。

## 五、DoD 逐项 checklist（M1）

- [ ] 快照三件套（股票主数据、枚举、公告响应）入仓且登记 sha256
- [ ] `.gitattributes` 对快照标 `-text`；克隆后 sha256 一致
- [ ] 快照字段与 `data/README.md` S2/S3 逐字段一致；条数对账通过
- [ ] `data/fixtures/stock-master.csv` 覆盖样例清单全部代码
- [ ] 三个 `orgId` 反例在台账中且与实测一致
- [ ] `rules-v1.json` 26 类齐全；每类至少 1 条真实标题命中
- [ ] 0 量类（年报/一季报/监事会）显式标季节性，不造假样本
- [ ] 真值台账无 `observed_*`；每条期望可由输入独立复算
- [ ] `tools/gen_*.py --check` 重生成逐字节一致
- [ ] `data/README.md` 勘误同步（质押计数、任何实测不符处）
- [ ] 双解释器测试通过（本地等价校验，未接远端）
- [ ] 本文件 `plan/HANDOFF-M2.md` 已落盘并提交

## 六、关键命令速查

```bash
# 环境
py -0p
py -3.12 --version
py -3.8 --version

# 测试（两个解释器都要跑）
PYTHONDONTWRITEBYTECODE=1 py -3.12 -m pytest -q
PYTHONDONTWRITEBYTECODE=1 py -3.8  -m pytest -q

# CLI 冒烟（源码态必须带 PYTHONPATH=src；安装后才可裸用 python -m / cnwatch）
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.12 -m cnwatch --help
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 py -3.12 -m cnwatch --home ./rt selftest

# 安装面验证（中立目标目录，不入仓）
rm -rf .tmp-install .tmp-run
py -3.12 -m pip install --no-deps --no-build-isolation --target .tmp-install .
```

> 所有命令在本轮 HANDOFF 写完时都实跑过（`--help`、`selftest`、`pytest` 双版本、
> `pip install --target`）。**源码态 `python -m cnwatch` 必须先 `pip install -e .`
> 或设 `PYTHONPATH=src`**——`src/` 布局不会从仓库根自动可见，这一点本轮实测踩过。
> `--home ./rt` 只创建 `out/`，不触碰用户主目录。

## 七、M2 预告（不要提前做）

M2 才动 `watchlist` 与 `fetch`。M1 阶段**不要**在业务代码里写死任何限频/退避数值——
它们只在 `data/README.md` 第四节出现，M2 由 `fetch/config.py` 读取并注入。
