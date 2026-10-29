# 游戏服务监控

## 架构

PVE 独立采集器使用现有只读探针，通过受限 SSH 读取各游戏服务数据，并直接 HTTPS 上报同一个 CF Worker。运行时不访问旧 gsmon 站点，也不使用它的代理。云服游戏关联已接入的服务器 UUID；内网游戏关联 PVE UUID，不新增虚拟机服务器条目。

`/games#/games` 显示游戏列表和详情，服务器详情顶部的“本机游戏”可过滤关联项目。一个服务器可以关联多个游戏。页面沿用后台登录，游戏接口始终要求登录，即使主机概览设为公开也不公开游戏统计。

## 配置与部署

1. 保留原项目的 `API_SECRET`、D1、Durable Object、域名及 Cron 设置。
2. 执行 `migrations/0001_games.sql`，只增加 `game_services` 和 `game_history` 表，不修改现有服务器表。
3. 将游戏注册到 `game_services`，id、显示名称、server_id 和在线统计口径由操作者指定，不接受上报方修改归属。
4. 为 Worker 配置独立的 `GAME_REPORT_SECRET`，至少32字符。采集器私有配置使用同一 secret；SSH私钥和生产数据库密码不得放入仓库。
5. 构建 `npm run build:frontend`，执行 `npm run test:all`、Worker dry-run，发布代码与静态资源。
6. `collectors/game_collector.py` 读取私有JSON配置：`url`、`report_secret` 和 `games` 数组。每个游戏包含 `id`、`host`、`user`、`command`、`key`、`source`。key为本机私钥绝对路径。命令仅来自运维配置，不接受网页输入。
7. 先运行 `--dry-run --once`，再开启独立 systemd 服务。采集器默认30秒一轮，完整统计每300秒；采集耗时会延长实际周期。

本站部署脚本从 `/root/.config/cf-server-monitor` 读取现有凭据，从 `/root/.config/cf-game-monitor` 读取采集清单，不包含凭据内容。`ops/provision_games.py` 是部署专用清单注册工具；`ops/release_games.py` 使用官方上传接口，保存旧 Worker 后发布并保留已有域名及计划任务。正式发布前刷新 Wrangler OAuth。

## 数据与鉴权

- `POST /games/update`：JSON body `{games:[{id,sampled_at,metrics}]}`，秒级时间戳。
- `X-Game-Timestamp` 为当前秒，`X-Game-Signature` 为 `HMAC-SHA256(secret, timestamp + "\n" + 原始UTF-8 body)` 的hex结果。
- 请求和样本时间允许120秒偏差；流式读取上限32KiB；一次至多20个已注册游戏，禁止重复id。
- 每游戏单独比较采样时间，重复重传和旧数据不能覆盖新状态；最新状态和历史在D1 batch事务中写入。
- `GET /api/games`：已登录用户读取注册游戏和最新状态。
- `GET /api/games?id=<id>&hours=1|6|24|168`：读取在线曲线，时间桶内取峰值，保留7天历史，每小时清理。
- 180秒无新采样显示过期。采集失败一律清空有效在线状态，不把旧值冒充正常。失败细节不从生产探针原样上报，避免暴露配置和凭据。
- 在线口径分别标注会话、连接或近5分钟活跃指标；不相加为总玩家数。-1、缺失和过期显示“—”，只有确认的0才显示0。
- 今日峰值按北京时间换日，来源口径改变时重置；可在首次迁移时按相同口径继承旧峰值。
- 进程、端口和在线统计用于运维观测，不能代替真实客户端登录和玩法验收。

## 运行与回滚

正式采集器服务 `cf-game-monitor.service`；配置 `/etc/cf-game-monitor/config.json`（0600），SSH密钥位于受限的外部目录，状态 `/var/lib/cf-game-monitor/state.json`。服务只新增监控负载，不重启游戏。

回滚先停止 `cf-game-monitor.service`，恢复上一版 Worker/静态资源；新增D1表可保留，无需删除任何原表。9.11原采集文件保留用于回滚，替代链路验证成功后仅停止并禁用其 `gsmon` 服务。若需恢复旧采集，重新 enable/start 该服务。

新增密钥只允许对应监控命令，卸载时按 `cf-game-monitor-pve` 公钥标记删除新增授权，不能整份覆盖后续授权文件。CT114和云服相应 authorized_keys 在变更前有备份。

## 验证

`npm run test:games` 覆盖真实SQLite事务、鉴权、过期、重复/乱序上报和采集失败；原测试通过 `npm run test:all` 执行。线上验收应比较9个真实探针、鉴权接口、浏览器桌面与手机、历史曲线以及原7台服务器的指标和WebSocket。游戏级通知渠道尚未配置，本版本不声称已实现故障消息推送。

现有外部主题有独立路由；游戏入口使用内置前端的 `/games` 路径。Worker在外部主题页面增加游戏导航，保留原服务器主题。

systemd 单元模板见 `collectors/cf-game-monitor.service`，安装后执行 `systemctl daemon-reload` 与 `systemctl enable --now cf-game-monitor`。首次迁移保留既有峰值，CF在线曲线从本次接入开始积累。


## 公网优选入口

备用优选地址：`https://monitor-fast.jinli90.com/games#/games`。它使用本账户的精确Workers路由和两个已测试的Cloudflare边缘IP解析，复用同一Worker、数据库及后台账号。原 `monitor.jinli90.com` 自定义域名及探针上报地址保持原配置。

DNS为两个A记录：`104.18.0.1`、`104.16.10.1`，DNS-only，TTL120秒。多A记录提供多个连接候选，浏览器如何排序与重试取决于客户端；不代表严格主备切换，也不是按运营商自动分流。当前实测覆盖家里PVE、36.138.222.18、175.27.233.72及Windows云服浏览器，不能保证所有公网更快，也不是Cloudflare中国网络加速。

这是基于实际HTTPS验证的直接边缘IP接入方式。Cloudflare官方常规Workers路由文档要求proxied DNS，此入口的DNS-only配置应作为备用实验入口，后续若Cloudflare路由/证书行为变化应恢复标准代理模式。原入口继续保留，不在未经复测的情况下覆盖。

运维脚本 `ops/edge_entry.py` 只操作独立域名及其自己创建的DNS记录，凭据与记录ID在外部私有目录。`python3 ops/edge_entry.py status` 读取状态；`prefer` 使用预先测试的双IP；`revert` 恢复新入口为Cloudflare标准代理解析并删除脚本创建的额外A记录，不修改原入口。没有启用SaaS、付费套餐或第三方优选域名。

新域名首次使用需重新登录，账号密码沿用原站；Cookie和浏览器本地存储按域名隔离。原站登录状态不自动跨域共享。
