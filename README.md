# 大肥鱼的小屋（astrbot_plugin_fish_house）v1.9.2

> 这是 **v1.9.2 早期版本**的源码存档：7 段视频槽位 + 叠化播放 + WebUI 面板 + 独立公共页。
> 后续的画布/素材库版本不在这个仓库里。
>
> License: MIT

把大肥鱼做成一个**视频桌宠**：休息时循环休息视频，回消息时拿手机，干活时去电脑，干完活再回休息。视频按顺序播放，片段之间叠化，不会多个视频同时抢帧。

## 一、打开

- AstrBot 管理面板 → 插件 → 大肥鱼的小屋 → 打开面板。
- 面板最上面是 **7 个视频槽位**，默认已经装好一套内置视频，打开公共链接就能直接看。
- 想换素材，点槽位的「上传视频」或「用地址」替换即可。
- 独立公共链接（不需要登录面板）：

```text
http://服务器地址:6185/fish_house/index.html
```

面板顶部会显示当前服务器的实际链接，点“复制链接”即可。

> 页面使用 AstrBot 自带的 public 静态目录，插件启动 / 保存设置时会自动把页面和视频同步过去。如果 `dashboard/dist` 不存在，面板会提示同步失败。

## 二、7 段视频

插件自带一套默认素材（`assets/videos/`），第一次加载时会自动装进数据目录并填满槽位：

| 槽位 | 用途 | 内置文件 |
| --- | --- | --- |
| `rest` | 休息循环，啥也不干时播放 | `rest.mp4`（休息状态） |
| `phone_pickup` | 拿起手机 | `phone_pickup.mp4` |
| `phone_loop` | 玩手机 / 回消息循环 | `phone_loop.mp4` |
| `phone_putdown` | 放下手机 | `phone_putdown.mp4` |
| `computer_go` | 从休息处去电脑 | `computer_go.mp4` |
| `computer_loop` | 玩电脑 / 干活循环 | `computer_loop.mp4` |
| `computer_rest` | 干完活从电脑回休息 | `computer_rest.mp4` |

在面板里给每个槽位替换视频。每段下方有「播放有声」勾选，默认全开，可以只让其中几段出声。支持：

- MP4 / WebM / M4V / MOV；
- 单个最大 200MB（可配置）；
- 也可以“用地址”填 http(s) 链接或 `/fish_house/...` 公共路径；
- 视频保存在 `data/plugin_data/astrbot_plugin_fish_house/videos/`，同时复制到公共目录。

建议 7 段使用相同分辨率、帧率、机位；转场视频的首尾帧与对应状态衔接。想要透明桌宠，请导入 **带 alpha 的 WebM**；MP4 不支持透明背景。

## 三、状态机

### 休息

- `rest` 循环播放。

### 回消息

```text
rest → phone_pickup → phone_loop → phone_putdown → rest
```

- 每一段都**播放到视频末尾**才进入下一段，不按固定秒数硬切；
- `phone_loop` 只播一遍，播完自动放下手机；
- **Bot 的回复会等桌宠真正拿起手机（phone_pickup 播完、进入 phone_loop）后才发出**；
- 已经在手机状态时回复会立即发出；
- 如果已经在电脑状态，**不会**去拿手机，消息直接在电脑前发出。

### 去电脑干活

任务类请求（「打开抖音 / 查天气 / 搜资料 / 整理文件」等）会**先拿手机看一眼，再走到电脑，到了才动手**：

```text
rest
→ phone_pickup      ← 拿起手机看消息
→ phone_loop
→ phone_putdown
→ computer_go       ← 放手机、走向电脑
→ computer_loop     ← 到电脑后才开始实际任务
```

- 用户消息本身是任务时，在 **LLM 开始处理前**插件就先让桌宠拿手机、去电脑，
  并**等它真正走到电脑旁边**才把请求放行；所以打开抖音、浏览器、执行工具都不会早于桌宠到位；
- 模型主动调用 `house_work_start` 时也一样：这个工具会等整段“拿手机→走过去”动画结束才返回；
- 已经在电脑旁边时不额外等待；正在拿手机时会等它把手上的手机放下再走过去；
- `computer_loop` 循环，直到任务结束；
- **干活时不会被打断**：模型每次请求、每个工具执行都会重置/暂停空闲计时，工具跑完再重新计时；
- 一轮活干完后，`idle_seconds`（默认 60 秒）内没有新动作就自动 `computer_rest → rest`；
- **直接说「回去休息 / 下班 / 回窝」会先原地回复你，消息发出后立即回休息**，不依赖模型是否记得调用 `house_work_end`，也不会闪出“拿手机”动画；
- 长任务用 `house_work_ping` 续期；它会把“已结束”状态撤销，重新算干活中；
- 安全兜底：万一任务异常一直不结束，超过 `max_work_seconds`（默认 30 分钟）会强制回休息；
- 不再和记账绑定：查资料、逛网页、整理文件、写报告、刷视频都可以算干活。

### 干完活

```text
computer → （等 idle_seconds）→ computer_rest → rest
phone → phone_putdown → rest
```

## 四、叠化与播放顺序

- 任意时刻只有**一个视频在播放**；
- 每个片段必须播完（`ended`）才会播下一个，不按固定秒数切；
- 循环片段（休息 / 手动电脑）被状态切换打断时，先暂停在当前帧，再叠化到下一个片段；
- 叠化时长默认 400ms，可在面板调整；
- 衔接时**画面和声音一起渐变**：新片段音量 0→1、旧片段 1→0，和叠化同步，不会突然切换声音；
- 过渡缺失时会自动跳过该段，例如没有 `phone_pickup` 就直接进入 `phone_loop`。

## 五、公共页与活动气泡

公共页覆盖整个窗口：

- 视频铺满浏览器窗口，`object-fit: cover`；
- 底部一个胶囊状态提示（离底边留了距离，不贴边），显示 Bot 正在做什么；
- 默认带声音；浏览器拦下有声自动播放时会先静音播，点右上角喇叭就能开声音（选择会记住）；
- 面板里每段视频都有「播放有声」勾选，可以逐段决定哪段出声（比如只让休息视频有环境音，其他静音）；
- 页面不再提供全屏按钮，需要全屏用浏览器 F11 或 OBS 浏览器源即可；
- 气泡由 Bot 写入，例如：
  - 帮主人查资料
  - 我去逛一下抖音
  - 在整理今天的账单
  - 在电脑前写报告

Bot 工具：

```text
house_activity(activity, ttl_seconds)   写底部气泡
house_work_start(activity)              派桌宠去电脑，走到之后才返回，再开始实际操作
house_work_ping(activity)               长任务续期，避免一分钟自动休息
house_work_end()                        任务结束，到电脑后歇一分钟再回休息
```

也可以手动：

```text
/小屋 说 帮主人查资料
/小屋 干活 我去逛一下抖音
/小屋 干完
```

或在面板「活动气泡」里直接发送。

## 六、手动触发

聊天：

```text
/小屋            查看状态
/小屋 干活       去电脑
/小屋 休息       回休息
/小屋 回消息     触发一次拿手机
```

HTTP：

```http
POST /api/v1/plugins/extensions/astrbot_plugin_fish_house/event
{"action": "reply"}
{"action": "work_start"}
{"action": "work_end"}
{"action": "rest"}
{"action": "bubble", "text": "帮主人查资料"}
```

需要 AstrBot 面板的 Authorization / API Key。公共页只读 `state.json`，不需要登录。

### 给机器人调用

在 AstrBot 的 API Key 页面创建一个 Key，然后：

```bash
curl -X POST "http://服务器:6185/api/v1/plugins/extensions/astrbot_plugin_fish_house/event" \
  -H "Authorization: Bearer <API Key>" \
  -H "Content-Type: application/json" \
  -d '{"action":"work_start"}'
```

任务结束：

```bash
curl ... -d '{"action":"work_end"}'
```

```text
reply      回消息（手机动画）
work_start 去电脑（任何任务）
work_end   干完回休息
rest       强制回休息
```

桌宠现在默认和 Bot 接在一起；上面的 HTTP 接口保留给外部任务或手动触发。

## 七、配置

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | true | 启用插件 |
| `public_prefix` | fish_house | 公共链接路径 |
| `crossfade_ms` | 400 | 叠化毫秒 |
| `trigger_on_reply` | true | Bot 发消息时触发手机 |
| `auto_reply` | true | Bot 发消息时自动播放手机动画（可关闭，完全由接口驱动） |
| `idle_seconds` | 60 | 一轮活结束后，空闲多久回休息；工具执行期间暂停计时 |
| `max_work_seconds` | 1800 | 连续干活安全上限，超过强制回休息 |
| `max_upload_mb` | 200 | 单视频上传上限 |

面板里的叠化设置会写入 `data/plugin_data/astrbot_plugin_fish_house/config.json`。

## 八、自动备份

每次以下操作前，插件会自动把 `config.json`、`state.json` 和全部视频复制到：

```text
data/plugin_data/astrbot_plugin_fish_house/backups/<时间>-<原因>/
```

- 插件重载/更新前：`before-update`
- 替换某个槽位视频前：`before-replace-<slot>`
- 清空某个槽位前：`before-clear-<slot>`
- 恢复备份前：`before-restore`

只保留最近 20 个备份。面板「备份与恢复」可以选一个备份直接还原槽位和视频。内置默认视频和 `assets/videos/` 里的一致，不会重复塞进备份。

> 更新插件本身不会碰 `plugin_data`；真正会丢数据的是清空槽位或手动删除 `videos/`。现在清空槽位也会先备份。

## 九、数据与公共文件

```text
data/plugin_data/astrbot_plugin_fish_house/
  config.json      槽位与设置
  state.json       当前状态（含模式、气泡、时间戳）
  videos/          上传的视频
  uploads/         临时上传分片

astrbot/dashboard/dist/fish_house/
  index.html
  app.js
  style.css
  config.json      公共页读取
  state.json       公共页轮询
  videos/          公共视频
```

修改视频或设置后会自动同步；也可以在面板点“重新同步公共页”。

## 十一、导出插件

面板「导出插件」会生成：

```text
/fish_house/astrbot_plugin_fish_house.zip
```

zip 内是 `astrbot_plugin_fish_house/` 源码目录，包含插件代码、配置 schema、面板和公共页；**不包含**上传的视频、用户配置和备份。拿到别的 AstrBot 后解压到 `data/plugins/` 并重载即可。

## 十二、注意

- 公共页每段视频可在面板单独勾选「播放有声」，改完约 8 秒内公共页自动生效，不用刷新；
- 浏览器自动播放：公共页打开就自动播放；默认带声音，被浏览器拦下时先静音播，点右上角喇叭开声音；
- 视频衔接：新片段叠在旧片段上面淡入，旧片段保持不透明垫底，不会闪白/闪黑；
- 公共页默认透明背景，适合 OBS 浏览器源；
- 视频不要并行播放，否则首尾帧会错乱；
- 如果 AstrBot 更新覆盖了 `dashboard/dist`，重载插件即可重新同步；
- 需要用 `http://服务器:6185/fish_house/index.html` 访问，不要加 `/api` 前缀。

