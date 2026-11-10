# MOKU 发布签名

发布签名用于识别 Xirti 发布的原版与检测组件篡改，不限制 MIT 允许的源码修改和再分发。它不能阻止控制本机的管理员修改可执行文件或移除自检。

## 普通用户

- 启动完整性检查在本机完成，无需网络、激活或额外账号。
- 更新先验证 Ed25519 发布签名，再核对压缩包与全文件清单。验证失败只取消本次更新，当前版本继续使用。
- 配置、登录凭据、缓存、日志和下载作品不属于签名范围。可以移动完整便携目录。
- 程序组件损坏时，从项目主页下载完整包覆盖程序组件；保留个人数据目录。
- 签名元数据有效期只影响获取更新，不会让已安装程序到期停用。系统时间错误可能导致更新检查失败。

## 首次建立信任

从未签名的旧版过渡时，手动下载首个签名版，并通过可信渠道核对公钥指纹或包摘要。旧版自身不能证明新公钥的归属，首次信任不能靠新包自行声明建立。

源码 `release_trust.py` 仅包含 Ed25519 公钥与指纹。公钥编译进 EXE，不从 GitHub 返回内容或可替换的 JSON 文件中导入。

## 维护者私钥

在仓库之外指定一个尚不存在的独立子目录：

```powershell
python -B sign-release.py init --key '<项目外私钥目录>\MOKU发布签名\release-ed25519.dpapi' --register
```

工具创建独立目录，只授予当前 Windows 用户和 SYSTEM 访问权限。私钥以 DPAPI CurrentUser 加密后写盘；不会输出私钥或密码，也不把它们写进源码和打包清单。调用签名库时私钥仍短暂存在进程内存中；同一账号下的恶意软件不属于这个存储保护的防御范围。

**DPAPI 文件不是跨设备备份。** 重装系统、丢失 Windows 用户密钥或迁移电脑前，先在本地终端交互导出密码加密的 PKCS#8 备份：

```powershell
python -B sign-release.py export-backup --key '<私钥文件>' --backup '<离线介质>\moku-release-backup.pem'
python -B sign-release.py import-backup --key '<新的独立目录>\release-ed25519.dpapi' --backup '<备份文件>'
```

密码至少 16 字符，终端隐藏输入。备份与密码分开保管。不要把私钥、备份或密码发到聊天、Issue、仓库、CI 日志。不要删除或覆盖旧密钥。

## 发布流程

```powershell
./build-portable.ps1 -ReleaseKey '<私钥文件>'
./make-release.ps1 -SkipBuild -ReleaseKey '<私钥文件>'
```

顺序固定：构建 → 可选 Windows 代码签名 → 计算文件摘要 → 签署 schema-4 清单 → 验证 → 压缩 → 签署发布元数据。

发布目录中的 ZIP、`MOKU-update.json`、`MOKU-update.sig.json` **必须作为同一个 GitHub Release 的资产上传**。保留其他说明和摘要文件。签名后不要改文件或重新压缩；需要改动就提高版本号并重新构建、签名。此工具不会自动上传、推送或发布。

`MOKU-update.json` 绑定仓库、Windows x64 平台、版本、ZIP 文件名、大小、SHA-256、清单 SHA-256 和一年有效期。不同用途的签名有独立前缀，不能把清单签名拿来充当发布元数据签名。

客户端拒绝缺失、伪造、过期、未知密钥的签名。它按 stable/beta 分别记录已见最高版本，拒绝退回更低版本或替换同一版本的包；安装前再次检查。更新失败的本地事务恢复仍允许还原上一版。该记录不是防管理员修改的硬件计数器，也不是完整 TUF 实现。

## Windows 发布者签名

Ed25519 发布签名不需要付费证书，但不会让 Windows 显示受信任的发布者。要获得系统认可的发布者身份，需要有效的代码签名证书或相应服务；自签名证书不能自动得到其他用户电脑的信任。

证书已在当前用户证书存储中、有可用私钥且安装 Windows SDK 后：

```powershell
./build-portable.ps1 -ReleaseKey '<私钥文件>' -CodeSignThumbprint '<证书指纹>' -TimestampUrl 'https://<证书服务商的 RFC3161 时间戳地址>'
./make-release.ps1 -SkipBuild -ReleaseKey '<私钥文件>'
```

`sign-windows.ps1` 使用 SHA-256 与 RFC3161 时间戳，要求 SignTool 验证通过、签名证书指纹匹配且有时间戳；失败中止构建。只签署项目自己的 EXE，第三方 DLL 保留原签名，并由完整文件清单覆盖。未来新增自有 DLL 时应加入签名列表。

没有证书时仍可生成有 Ed25519 保护的版本，构建输出会明确说明 Windows 发布者签名未配置。云签名服务需要按服务商的密钥访问方式额外接入；当前参数适配 Windows 证书存储，不包含供应商专用认证。

## 密钥轮换与泄露

正常轮换：在可信环境生成新密钥，把新旧公钥都加入 `release_trust.py` → 用旧密钥发布过渡版 → 用户升级后用新密钥签下一版。跨过过渡版的旧客户端需要手动安装。这不是从网络静默接受新公钥。

旧发布密钥泄露时，不能只靠同一密钥签名的公告恢复信任。通过独立可信渠道通知、核对新指纹，并手动安装可信恢复版。当前工具单密钥签署，不把第二个公钥冒充成第二重签名，也没有自动吊销服务。

## 开发与 CI

```powershell
./build-portable.ps1 -Development
./make-release.ps1 -SkipBuild -Development
```

开发构建明确使用 schema 3，不强制启动自检，输出在 `release/development-v...`；正式更新器拒绝这种未签名包。开发标志编译进 EXE，不能通过更换清单或设置运行时环境变量关闭正式版的完整性检查。CI 只构建这类开发资产，不接触发布私钥。

公开资料：[项目主页](https://github.com/Xirti/Pixiv-Bulk-Image-Downloader)、[Microsoft SignTool](https://learn.microsoft.com/windows/win32/seccrypto/signtool)、[cryptography Ed25519](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/)。
