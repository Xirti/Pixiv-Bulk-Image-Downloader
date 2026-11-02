本地试用候选：v1.1.4-r3，大动图下载与转换。

# MOKU Lite R3

基于 v1.1.4-r1（8ac6d6f）移植 main R12 的共用业务修复。保留 Lite 原有 HTML、基础 CSS、主题切换、资料目录和登录凭据命名。

构建：./build-portable.ps1；打包：./make-release.ps1。
配置：edition_profile.py。历史与采集篮位于 %LOCALAPPDATA%/MOKU/library。

大动图预算可通过启动前的环境变量调整，修改后重启程序：

| 变量 | 默认值 | 范围 |
|---|---:|---:|
| `MOKU_UGOIRA_MAX_MIB`：帧包与成品文件预算 | 512 MiB | 40–4096 MiB |
| `MOKU_UGOIRA_TEMP_MIB`：解压帧与转换暂存预算 | 2048 MiB | 128–16384 MiB |
| `MOKU_UGOIRA_TIMEOUT_SECONDS`：每件动图转换时限 | 600 秒 | 30–3600 秒 |

这三个参数分别约束文件大小、临时空间与时间；增大文件预算后也需为临时目录和保存目录预留空间。GIF 保留单帧内存预算，MP4 导出使用本机 PATH 中的 FFmpeg。
