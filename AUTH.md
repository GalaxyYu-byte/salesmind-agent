# 登录模块

首版支持用户名、密码、图片验证码登录及当前设备退出。直接查询现有 `agent_app.app_user`，不创建或修改账号，不需要数据库迁移。所有启用账号均可访问全部销售数据；角色、区域和指标字段暂不参与数据范围限制。

## 本机启动

被忽略的 `.env` 中配置独立随机密钥和本机 Cookie 开关：

```dotenv
SALESMIND_AUTH_JWT_SECRET=<至少32字节的随机值>
SALESMIND_AUTH_COOKIE_SECURE=false
SALESMIND_AUTH_ACCESS_SECONDS=1800
SALESMIND_AUTH_REFRESH_SECONDS=604800
```

本机联调已生成密钥并写入 `.env`，不会打印或提交该密钥。缺少密钥时登录接口返回 503，没有硬编码或每次重启生成的备用密钥。

保持 SSH 隧道运行，再启动 API 和前端：

```powershell
.venv\Scripts\python.exe scripts/dev_tunnel.py
# 独立终端运行；已有隧道不重复启动。
.venv\Scripts\python.exe -m uvicorn salesmind.main:app --host 127.0.0.1 --port 8000
# 另一个终端：
cd frontend
npm run dev
```

访问 http://127.0.0.1:5173 。`start-dev.bat` 仍用于启动前后端，SSH 隧道需先运行。旧的 `SALESMIND_ENABLE_DEVELOPMENT_QUERIES` 开关不再绕过登录。

账号密码遵循原有初始化配置，本次真实联调用 `demo_director`。兼容已有 `pbkdf2_sha256$迭代次数$盐$摘要` 哈希；后续创建密码可用 `core.security.hash_password`，它使用随机盐和 600,000 次 PBKDF2。没有注册、找回密码、用户管理或默认后门账号。

## 接口

| 方法与路径 | 请求 | 结果 |
|---|---|---|
| GET `/api/v1/auth/captcha` | 无 | `captcha_id`、PNG data URL、`expires_in:120` |
| POST `/api/v1/auth/login` | `username`、`password`、`captcha_id`、`captcha_code` | Access Token、有效秒数、用户信息；设置 Refresh Cookie |
| POST `/api/v1/auth/refresh` | 浏览器自动携带 Refresh Cookie | 新 Access Token；轮换 Refresh Cookie |
| POST `/api/v1/auth/logout` | 浏览器自动携带 Refresh Cookie | 204，撤销当前设备会话并删除 Cookie |
| GET `/api/v1/auth/me` | Bearer Access Token | 用户 ID、用户名、显示名、角色 |
| GET `/api/v1/sales/overview` | Bearer Access Token及销售范围参数 | 真实 MySQL 销售汇总 |

所有 POST 请求要求 `X-SalesMind-CSRF: 1`；有 Origin 时必须属于 `SALESMIND_AUTH_ALLOWED_ORIGINS`。部署采用同源反向代理，没有开放跨域 CORS。所有认证与销售响应包含 `Cache-Control: no-store`。登录失败不区分未知账号、错误密码和停用账号；验证错误不回显提交的密码，连接异常不包含底层凭据。

## Token 与 Redis

Access Token 为 HS256 JWT，固定校验算法、issuer、audience、Token 类型与必要声明。通常有效 30 分钟；会话剩余不足 30 分钟时缩短有效期。每次请求还检查 Redis 会话和用户启用状态，密码哈希变化会使该用户的旧会话不可用。

Refresh Token 为设备会话 UUID 加 48 字节随机凭证，Redis 只保存凭证的 SHA-256 哈希、用户 ID、密码指纹和绝对截止时间。会话从登录开始最多 7 天；刷新不延长截止时间或 TTL。Lua 原子比较旧哈希后轮换，重复使用旧 Refresh Token 返回 401。每台设备独立会话，当前设备退出立即使其 Access 与 Refresh Token 都失效，不影响其他设备。

Cookie 使用 HttpOnly、SameSite=Strict、路径 `/api/v1/auth`，没有宽泛 Domain。生产默认 Secure=true，并要求 HTTPS；只有本机 HTTP 联调设置 false。Refresh Token 不出现在 JSON、localStorage 或页面 JavaScript 中。

验证码为 4 位字母数字，忽略大小写，2 分钟有效；Redis 存验证码 ID 绑定的摘要，Lua 在验证时一次性消费，正确或错误尝试均不能复用。获取验证码每 IP 每分钟最多 30 次；登录每 IP 每 5 分钟最多 30 次、每账号每 5 分钟最多 10 次。开发代理下 IP 可能合并为同一本机来源；生产代理需按可信来源规划限流，不能盲目信任客户端 X-Forwarded-For。

## 前端

登录页成功后才挂载工作台和发起销售请求。Access Token 只放内存，刷新页面通过 Cookie 恢复登录；请求遇到 401 时合并刷新并最多重试一次。支持 Web Locks 的浏览器还会跨标签串行化 Cookie 的登录、刷新和退出。刷新失效返回登录页，连接暂时失败保留用户状态并显示错误。

会话与报告按用户 ID 保存为 `salesmind.user.<id>.v1`，Token 不持久化。旧演示存储键保留但不混入登录账号。缓存属于本机浏览器数据，不等于后端持久化或加密存储。

## 验证

```powershell
.venv\Scripts\python.exe -m pytest tests/test_auth.py tests/test_sales_overview.py tests/test_health.py tests/test_config.py tests/test_database.py tests/test_migrations.py -q
cd frontend
npm test
npm run build
```

后端测试使用真实 SQLite 用户查询及内存 Redis 的 Lua 执行，覆盖验证码、Cookie、JWT、轮换重放、并发刷新、限流、停用账号、改密撤销和多设备隔离。前端测试覆盖恢复登录、自动刷新、并发合并、取消信号、重试边界、服务故障和账号本地缓存隔离。另已通过 Vite 代理对现有 MySQL、Redis 和样例账号进行真实 HTTP 联调。

实现依据：[PyJWT 文档](https://pyjwt.readthedocs.io/en/stable/usage.html)和 [Cookie 属性文档](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie)。
