# SalesMind 前端

Vue 3 + TypeScript + Vite + Plotly.js。经营工作台、数据页和规则分析对话已连接真实 MySQL 查询接口。

## 联调启动

在项目根目录的独立终端运行 SSH 隧道（已经运行时不用重复启动）：

```powershell
.venv\Scripts\python.exe scripts/dev_tunnel.py
```

另一个终端启动 API：

```powershell
.venv\Scripts\python.exe -m uvicorn salesmind.main:app --host 127.0.0.1 --port 8000
```

前端终端：

```powershell
cd frontend
npm ci
npm run dev
```

访问 http://127.0.0.1:5173 。Vite 开发代理将 `/api` 和 `/health` 转发至本机 8000，可用 `SALESMIND_DEV_BACKEND_URL` 修改目标。`npm run build` 输出静态资源；生产部署需要另行配置反向代理，Vite preview 不包含开发代理。

## 已接通的接口

`GET /api/v1/sales/overview?start_month=2026-03&end_month=2026-08&region_id=2`

- 月份参数必须同时提供或同时省略；省略时按数据库完整水位选择最近六个月。
- 支持 1–24 个连续完整月；区域省略表示所有区域。
- 仅统计 COMPLETED 订单，以订单所属区域为准，排除取消与退款订单。
- 返回 MySQL 来源、查询时间、数据版本、水位、维度、当前期与去年同期聚合，以及精确金额总额。
- 金额总额为十进制元字符串，聚合单元为整数分字符串；前端校验金额、订单数和请求范围一致性。
- 错误返回 422（范围无效）、404（区域不存在）、503（连接不可用）或 504（查询超时）。失败时显示错误，不回退到固定演示结果。

销售接口必须登录后携带 Access Token。首次打开页面显示用户名、密码和图片验证码登录页；有效账号均可查看全部销售数据。Access Token 有效 30 分钟，Refresh Token 有效 7 天并由 Redis 管理。配置、Cookie 与部署说明见 [登录模块](../AUTH.md)。

联调实测 2026-03 至 2026-08：销售额 4,782,185.61 元、毛利 1,673,824.69 元、已完成订单 1,750 笔。当前 MySQL 保存的是项目生成的样例订单；真实查询不代表生产业务数据。

## 可用功能与边界

工作台和数据页切换时间或区域会重新请求接口，趋势、排名、同比与导出基于查询结果。接口不读取用户提供的原始 SQL，不写入业务数据。

分析对话采用本地规则解析，支持明确月份、连续月份、最近完整月、区域追问、趋势和排名；支持的问题同样请求 MySQL。没有日期会要求澄清，不支持的预测与归因会明确提示。大模型、Agent、SSE 和后端分析会话存储尚未接入；登录设备会话已存 Redis。

暂停或取消仅中止客户端读取，并不承诺取消已提交的数据库查询；服务端查询有超时限制。继续时按同一范围重新查询，不是恢复 Agent 检查点。

分析会话和报告按登录用户隔离保存于浏览器 localStorage（`salesmind.user.<id>.v1`），结果包含 MySQL 数据快照、版本和查询时间。Token 不写入 localStorage。旧演示存储保留但不会混入登录账号。`src/data/fixture.json` 仍用于旧记录、规则解析期间配置和离线单元测试；接口失败时不作为业务结果回退。

Plotly 图表懒加载，加载失败保留数值表格。金额以整数分计算，仅显示时转换为元。

## 验证

```powershell
npm test
npm run build
```

前端还验证登录恢复、Token 自动刷新、并发合并、退出失败处理和本地账号隔离。前端测试覆盖金额精度、同比覆盖、日期解析、区域追问、真实接口映射、取消信号、请求范围、失败不回退及不一致金额拒绝。后端 `tests/test_sales_overview.py` 验证真实 SQL 聚合、订单状态、时间边界、空区域、范围约束、登录保护和超时。
