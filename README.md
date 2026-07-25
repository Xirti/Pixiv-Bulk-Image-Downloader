# MOKU - Pixiv Tag Gallery

<p align="center">
  <img src="assets/moku-icon.png" width="148" alt="MOKU 猫娘图标">
</p>

欢迎光临 MOKU 的小窝，请随便坐喵。

MOKU 是一只住在 Windows 里的 Pixiv 搜图猫娘。把 tag、作者名或作品 ID 告诉她，喜欢的作品很快就会被找回来；看中了便放进采集篮，挑好以后再整整齐齐地一起下载。界面跑在 pywebview 和 WebView2 上，后端乖乖待在本机回环地址，负责 Pixiv 请求、预览图片、文件夹选择和下载写入。

当前源码版本：**1.0.10** 喵。

想直接使用，就去 [MOKU Releases](https://github.com/Xirti/MOKU/releases) 把最新的 Windows x64 压缩包抱回家喵。记得把完整 ZIP 解压出来，再运行 `MOKU.exe`；若只拎走一个 EXE，它会因为找不到同伴而没法工作的。喜欢亲手打包的话，也可以在源码目录运行 `make-release.ps1`。

悄悄说明一下：MOKU 是独立项目，与 Pixiv 没有隶属关系喵。

<img width="1855" height="990" alt="MOKU 首页" src="https://github.com/user-attachments/assets/e9d431d0-d55a-40b0-8963-94764838f306" />

## 搜图小本领

- Pixiv tag 搜索和有范围限制的历史日期窗口都会用喵。
- 多标签严格 AND 搜索也难不倒她。用 `;` 或 `；` 分开标签，例如 `cat;night city`；标签里的空格会好好留在原处。
- `pid:123456` 可以精确查作者，`author:name` 可以精确查作者名；半角和全角冒号都认得喵。
- 可选排除 AI 生成作品，也能筛选插画、漫画和动图作品。
- 有公开全年龄、R-18、全部类型三种内容范围。R-18 需要先在桌面模式连接 Pixiv 账号。
- 每页会摆好 36 个结果，前方三页的数据也会提前备妥，翻页时就能轻快一点。
- 结果缓存会留下当前页附近的六页；离得太远的旧页面和临时预览授权会及时收拾干净。
- 只预取结果数据，不会偷偷下载还没打开页面的缩略图；搜索预览也会认真使用 `no-store`。

<img width="1770" height="677" alt="MOKU 搜索页" src="https://github.com/user-attachments/assets/14cede70-7876-4c5e-b9cb-87ae750c1af2" />

## 采集篮和预览

看到喜欢的作品，先放进采集篮里慢慢挑就好喵。采集篮支持任意数量的作品，最多选择 1,000 张图片；作品页和大图查看器采用窗口化加载，不会一下把几百张图片和控件全塞进页面。

下载任务会按图片数量安排请求，可以把作品分别收进各自的文件夹，也可以让同一 tag、作者或作品上下文共用一个文件夹。单个作品支持多页预览和按页选择，只把亲手勾好的内容交给下载队列喵。

大图预览会缓存临时授权，授权则会跟着登录状态和缓存窗口一起管理。失效的预览会自己尝试恢复，重复请求会合并；即使失败，也会留下清楚的占位提示，不让破图图标挡住你的点击喵。

R-18G 暂不支持。MOKU 不会替你修改 Pixiv 年龄设置，也不会绕过账号权限。

## 网络和代理

网络诊断会乖乖等你亲手点下按钮才开始喵。它会并行检查 Pixiv 网站和图片 CDN，不会带上 Pixiv 会话。

MOKU 不改 Windows 代理设置，不启动 VPN，也不扫描本机端口。它可以使用当前 Windows 用户已经启用的本地 HTTP 代理，TUN 也能工作；没有可用代理时，就直接走网络。

内置后端启动时会先选择网络，真正访问 Pixiv 前还会再确认一次。接受的 HTTP 代理只限 `127.0.0.1`、`localhost` 和 `::1`；远程 `HTTP_PROXY` / `ALL_PROXY` 不会被 Python 的默认代理逻辑偷偷带回来。

<img width="1807" height="960" alt="MOKU 网络指南" src="https://github.com/user-attachments/assets/6684e171-2bb4-49c0-9216-b6ece2cbb55d" />

## 桌面登录

账号连接只在桌面模式开放，跟着下面四小步就好喵：

1. MOKU 会在第二个 WebView2 窗口打开 Pixiv 官方登录页。
2. 密码、验证码和两步验证都留在 Pixiv 页面里处理。
3. 页面到达 HTTPS Pixiv 首页后，MOKU 只接受经过严格检查的 Secure、HttpOnly `PHPSESSID`。
4. 勾选保持登录时，会把会话交给当前 Windows 用户的凭据管理器保存。

MOKU 不记录 Cookie，也不会把 Pixiv 会话发给图片 CDN 或其他域名。外部 Edge 自动化、CDP、远程调试端口和 `/ajax/user/self` 阻塞式探测都不在登录流程里。

本机会话在 Cookie 过期前可能还显示为已连接；等真实 Pixiv 请求拒绝它时，再来连接一次就好喵。

## 安全边界

- HTTP 服务只绑定 `127.0.0.1`。
- API 请求需要回环客户端、回环 `Host`、非跨站 fetch 上下文，以及空缺或同源的 `Origin`。
- `/api/health` 是唯一允许无请求头的握手接口；真正的同源客户端会拿到进程级请求 token，其他 API GET 都需要它。图片 URL 使用另外的高熵能力令牌。
- 写操作只接受有大小边界的 JSON 对象。
- Pixiv API 流量只允许访问批准的 Pixiv HTTPS 主机。
- 图片流量只允许访问 `i.pximg.net`，不会带账号 Cookie。
- 用户提供的下载路径必须是绝对路径。
- 断开连接后，R-18 页面、图片令牌和作品缓存都会清理。

这一条要竖起耳朵认真记住喵：请不要把后端绑定到 `0.0.0.0`，也不要把它暴露给局域网或互联网。若要做这种部署，需要重新设计认证、TLS、文件范围和完整威胁模型。

## 从源码唤醒 MOKU

先准备好这些东西喵：

- Windows 10 或 Windows 11
- Python 3.12
- Microsoft Edge WebView2 Runtime

把运行依赖安装好：

```powershell
python -m pip install -r requirements.lock
```

接着唤醒桌面主程序：

```powershell
python moku_app.py
```

也可以运行 `MOKU启动.vbs` / `MOKU启动.bat`。PowerShell 启动器支持先启动并复用本机后端：

```powershell
powershell -ExecutionPolicy Bypass -File .\launch-moku.ps1 -Mode Desktop
```

`Browser` 模式适合公开浏览和前端调试，登录入口会在这个模式里保持关闭。

## 给 MOKU 做检查

运行原生 UI 探针前，先喂好开发依赖：

```powershell
python -m pip install -r requirements-dev.lock
```

再运行单元与集成测试：

```powershell
python -m unittest discover -s tests -v
```

测试会仔细检查多标签分页、结果和图片令牌缓存、未打开页面的缩略图、离线指南、匿名网络诊断、嵌入式后端代理初始化、WebView2 Cookie、DNS 重绑定、同源防护、请求体边界、下载完整性、冻结资源和启动器契约，一项也不会落下喵。

## 把 MOKU 装进便携包

轻轻运行这条命令：

```powershell
powershell -ExecutionPolicy Bypass -File .\build-portable.ps1
```

打包好的文件会待在这里喵：

```text
dist\MOKU\MOKU.exe
dist\MOKU\SHA256.txt
dist\MOKU\BUILD_MANIFEST.json
```

构建脚本会在 PyInstaller 前后计算输入指纹，跑完整测试，并执行冻结服务探针。schema 3 的 `BUILD_MANIFEST.json` 会把源码、构建输入和便携目录里的每个文件及目录绑在一起，还会拒绝链接、未声明文件和非 Windows x64 内容。清单里只有哈希和相对路径，不会写入本机绝对路径、账号信息或 Cookie。

### 当前发布版

当前源码版本是 `1.0.10`。便携版使用带哈希锁的 Python 3.12 依赖构建，测试通过后才会继续冻结；服务、文件夹选择、文件写入、官方登录窗口、使用指南和网络探针都会挨个跑一遍喵。真实 Pixiv 探针需要当前网络能访问 Pixiv 和图片 CDN。

权威的 EXE 和 ZIP 哈希会放在 Release 里的 `SHA256SUMS.txt`。生成的哈希不会写回源码，免得构建指纹和自己互相咬尾巴。`SHA256.txt` 只包含 `MOKU.exe` 的单向指纹和文件名，不会泄露账号、Cookie、路径或身份信息。

构建脚本还会确认冻结后端代际是 `exe-sha256:<MOKU.exe hash>`，生成第三方许可说明，清理探针日志，再写出最终的 `SHA256.txt`。

## 分发提醒

MOKU 1.0.10 已准备好作为 Windows x64 便携 ZIP 发布。请解压完整的 `MOKU` 文件夹，再运行 `MOKU.exe`；Windows 版程序没有 Authenticode 签名，SmartScreen 可能显示未知发布者提示，运行前请用 `SHA256SUMS.txt` 核对 ZIP。

发布前请再检查一下，别让日志、下载内容、Windows 凭据管理器数据、运行时描述文件、构建缓存或临时 WebView2 配置目录跟着溜进发布包喵。

## 许可证

MOKU 披着 [MIT License](LICENSE) 的小披风出门喵。

## 服务与版权提醒

Pixiv 作品归各自创作者所有。请好好遵守 Pixiv 当前条款、适用法律和创作者授权，没有得到许可时，不要再次分发下载的作品喵。
