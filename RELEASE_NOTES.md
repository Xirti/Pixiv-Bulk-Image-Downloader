MOKU v1.1.5 · Glass / Lite

同一版本，两种界面：
- `Glass`：花里胡哨版 · 动效 MAX，液态玻璃、宽边折射、实时点阵、鼠标联动标题和日夜扫屏。低配置慎入 😂
- `Lite`：简洁界面，轻装使用。两版共用本轮下载、合成与网络优化，保留各自的资料目录和登录凭据。

这次重点：
- 大动图帧包分段读写，默认文件预算提升至 512 MiB，降低大文件下载与转换的内存开销。
- GIF 支持双帧并行量化与重复画面合并；MP4 减少中间图片转换，按 CPU 和内存余量调整线程，提供可选 NVENC 与 CPU 回退。
- 动图按作品保存，支持暂停、继续及调整剩余任务格式，保留已完成进度。
- 多窗口任务按版本更新，移除任务清理旧断点，完善重新登录和丢失响应后的恢复。
- 复用 HTTPS 连接，临时中断自动重连；响应头、代理隧道和图片读取支持及时取消。

Windows 10/11 x64。完整解压，Glass 打开 `MOKU-Glass-Lab/MOKU-Glass-Lab.exe`，Lite 打开 `MOKU/MOKU.exe`。MP4 导出使用本机 PATH 中的 FFmpeg。

源码：[`main` · Glass](https://github.com/Xirti/Pixiv-Bulk-Image-Downloader/tree/main) / [`lite-backup` · Lite](https://github.com/Xirti/Pixiv-Bulk-Image-Downloader/tree/lite-backup)。
