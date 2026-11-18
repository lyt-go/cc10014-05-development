# 数仓分层治理平台

面向数据入库、清洗转换、分层建模、质量校验与血缘追踪的分层数仓治理平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 原始入库 | `raw_ingest` | 入库批次 | 批次编号、来源系统、文件格式 |
| 字段映射 | `column_mapping` | 映射规则 | 规则编号、源字段名、目标字段名 |
| 清洗策略 | `cleanse_rule` | 清洗策略 | 策略编号、目标字段、空值处理 |
| 去重配置 | `dedup_config` | 去重配置 | 配置编号、业务键列、排序字段 |
| 贴源层管理 | `ods_layer` | 贴源表 | 表编号、来源系统、表名称 |
| 明细层管理 | `dwd_layer` | 明细表 | 表编号、父层来源、表名称 |
| 汇总层管理 | `dws_layer` | 汇总表 | 表编号、统计粒度、汇总维度 |
| 维度建模 | `dim_model` | 维度模型 | 模型编号、维度名称、自然键列 |
| 快照管理 | `snapshot_mgr` | 数据快照 | 快照编号、目标数据集、快照时间 |
| 分区治理 | `partition_mgr` | 分区策略 | 策略编号、目标表名、分区字段 |
| 质量稽核 | `quality_check` | 稽核规则 | 规则编号、校验表名、校验字段 |
| 血缘追踪 | `lineage_track` | 血缘链路 | 链路编号、上游表名、下游表名 |
| 补数调度 | `backfill_job` | 补数任务 | 任务编号、目标表名、起始日期 |
| 表级比对 | `table_compare` | 比对任务 | 任务编号、左表名称、右表名称 |
| 结构注册 | `schema_registry` | 结构定义 | 定义编号、表名称、字段名称 |
| 数据发布 | `data_publish` | 发布版本 | 版本编号、数据集名称、发布范围 |
| 转换编排 | `transform_dag` | 转换节点 | 节点编号、输入表名、输出表名 |
| 冷热归档 | `cold_archive` | 归档策略 | 策略编号、目标表名、冷数据阈值 |

## 清洗规则执行报告

清洗策略支持按 `执行顺序` 整组执行待清洗原始数据行，并把结果沉淀成清洗规则执行报告：

- 每条规则在报告里保留 `命中数`、`空值命中数`、`正则命中数` 与 `替换值`；
- 同字段多版本冲突先判定优先策略（执行顺序小者优先，同顺序按版本高者优先），
  报告同时保留“按版本顺序列出替换值”和“按命中范围列出替换值”两种历史片段；
- 规则顺序、DWD刷新标记与报告在同一个事务里落库，任何一步失败整体回滚，
  避免出现“报告已保存、明细仍是旧清洗口径”；
- 报告生成超过超时阈值即重试（默认 5 秒、最多 2 次，可在请求里覆盖），
  超限后返回失败原因，不会留下半份报告；
- 策略登记/升级时按规则口径校验阈值范围、条件与标准，违规的替换值不会入库。

相关接口：

| 接口 | 说明 |
| --- | --- |
| `POST /api/cleanse_rule/execute` | 按目标数据集整组清洗并生成报告 |
| `POST /api/cleanse_rule/{id}/execute` | 从单条策略出发串起同数据集规则整组清洗 |
| `GET /api/cleanse_rule/reports` | 报告列表（可按数据集/报告编号检索） |
| `GET /api/cleanse_rule/reports/{报告编号}` | 报告详情：规则命中数、替换值、冲突判定、两种历史视图 |
| `GET /api/cleanse_rule/reports/{报告编号}/rows` | 报告对应的清洗结果行 |
| `GET /api/cleanse_rule/reports/export` | 导出报告清单 CSV |

前端页面：清洗策略页可发起整组清洗，明细层列表展示 `刷新状态` / `最近清洗报告`，
两处都能进入 `/cleanse_rule/reports/:reportNo` 报告详情页复核结果。

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数仓与数据湖
