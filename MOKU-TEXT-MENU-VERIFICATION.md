# 文本右键操作：验证记录

规范 `MOKU-TEXT-MENU-SPEC.md`；基线 `38d06f2`，实现 `0db1ac5`。本地源码修改，未打包或推送。

## 原因与实现

当前锁定的 pywebview 6.2.1 在 Windows WebView2 非调试模式下设置 `AreDefaultContextMenusEnabled=False`，窗口创建又默认 `text_select=False`。页面没有拦截 `contextmenu` 或禁止文字选择的样式。两项失败回归分别复现菜单关闭和文字不能选择。

在同步 `before_load` 生命周期、WebView2 初始化完成后的 UI 线程开启原生菜单，启用正文选择。菜单只保留剪切、复制、粘贴、全选、删除、撤销和重做等原生编辑命令，保留其启用／禁用状态；空白处不弹导航菜单。事件只订阅一次，不开放调试、开发工具、刷新、后退、打印或网页搜索，不添加剪贴板桥接、轮询或网页脚本。

技术依据：[pywebview 文本选择参数](https://pywebview.flowrl.com/api/)、[微软 WebView2 菜单开关与原生菜单过滤](https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/context-menus)。实际版本行为以本机锁定依赖代码和原生探针为准。

## 验证

- `python -B -m unittest discover -s tests -p test_desktop_client.py`：修改前明确失败 `native context menus are disabled in the desktop host`、`display text cannot be selected`；修改后 19 项通过，覆盖菜单命令和禁用状态、空白菜单、重复订阅、非调试运行等。
- 最终完整应用回归：484 项，483 通过、1 跳过（本机目录符号链接不可用），108.557 秒。
- `python -B tests/text_context_menu_native_probe.py`：真实 WebView2 隐藏窗口确认菜单启用、开发工具关闭；输入框、textarea、只读框、普通正文四类文本可选择；真实 .NET 菜单项集合可被过滤；重新加载后设置仍保留。只访问临时本机测试服务，使用临时配置目录，没有修改系统剪贴板或账户。
- 探针验证的是初始化、选择、菜单集合互操作和重载，不是弹出菜单的实际点击或剪切／粘贴执行。隐藏窗口无法通过模拟鼠标生成原生弹出菜单，因此未将其记为通过；此实验分支和相关调试代码已移除。
- `git diff --check` 通过。登录、网络、下载、历史、前端交互和默认清晰度逻辑未改动。
- 预防：新增非调试桌面初始化回归，避免仅在浏览器或调试模式测试而遗漏发布模式的默认限制；无需架构调整。

## Standards

独立审查：硬性违规 0，可行动代码异味 0；最高严重度：无。同步 UI 生命周期设置菜单，订阅有防重复保护，过滤保留原生命令状态，没有业务改动或新增剪贴板权限。

## Spec

独立审查：遗漏 0，越界 0，错误实现 0；最高严重度：无。输入与普通文字支持原生文本操作，只读与禁用规则未绕过，重载有效，调试与额外导航菜单保持关闭。验证边界明确，未伪称自动点击原生剪贴板菜单。

汇总：Standards 0 项；Spec 0 项。
