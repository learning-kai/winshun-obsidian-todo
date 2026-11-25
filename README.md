# Obsidian 待办便签（Win顺 插件）

与 Obsidian 的 `Tasks.md`（以及社区插件 `888bik.obsidian-quicknote`）**实时双向同步**的桌面待办便签，内置极简桌面倒计时磁贴。纯 Python 3 标准库实现（`tkinter` + 系统原生 API），零第三方依赖、零联网、不修改其他插件源码。

## 用途与特性

### 1. Obsidian 桌面待办便签
- **无损双向同步**：直接读写配置好的 Obsidian Vault 内的待办文件（默认 `Tasks.md`）。兼容标准 Markdown `- [ ]` / `- [x]` 任务以及 `888bik.obsidian-quicknote` 的 `- [ ] [YYYY-MM-DD HH:MM] 内容 <!-- source: Win顺 -->` 格式；勾选、编辑、删除只修改对应行，完整保留 YAML frontmatter、标题、代码块、缩进、行尾风格与其他笔记内容。
- **外部修改自动刷新**：在 Obsidian 或其他工具改动 `Tasks.md` 后，桌面便签约 0.8 秒内自动同步更新。
- **暖纸极简桌面磁贴**：
  - 无系统标题栏，支持 Windows 11 原生圆角描边与高分屏 DPI 自适应，提供「暖纸极简（浅/深）」与「跟随 Win顺 配色」两种视觉风格。
  - 静止时仅保留左上角淡色文档标题与右上角淡色 `×`；悬停磁贴时柔和展开工具栏（`＋`、`🔍`、`📌`、`↗`、`⚙`、`▾`）。
  - 按文档顺序渲染 `##` / `###` 分组标题，支持 `**粗体**`、`` `代码` ``、`#标签` 及中文冒号前缀自动加粗，使用纯标准库抗锯齿圆角复选框。
  - 单击复选框即时勾选/取消完成；双击文本或点 `✎` 行内编辑；点 `✕` 删除并提供 6 秒撤销条。
  - 支持 8 方向边缘/四角缩放、置顶、不透明度滑块、字号调节、折叠为标题栏与位置尺寸记忆。
- **全局快捷键与托盘菜单**：
  - 默认 `Ctrl+Alt+B` 一键显示 / 隐藏桌面便签（优先通过 Win顺 宿主 `register_hotkeys` 注册，可在设置中更换或清空）。
  - Win顺 系统托盘右键菜单提供「Obsidian 待办」快捷子菜单。

### 2. 极简桌面倒计时磁贴
- **即搜即开**：在 Win顺 搜索框输入 `倒计时 25`、`倒计时 40 复习`、`djs 1.5h`、`timer 90s` 或 `倒计时 10:30` 回车立即弹出独立桌面倒计时磁贴。
- **三种每像素透明形态**：基于 Windows `UpdateLayeredWindow` + GDI+ `PARGB` 渲染真抗锯齿与柔和环境投影，悬停可一键循环切换：
  - **方形水彩卡片**：整卡水彩水位渐变 + 底部 2.5px 渐变细线
  - **圆形渐变表盘**：双色平滑渐变环 + 柔和光晕珠点 + 12 刻度
  - **极简胶囊条**：紧凑低遮挡悬浮条 + 迷你进度环
- **轻量化交互与柔润提示音**：悬停滚动鼠标滚轮可 `±1 分钟` 微调时长；点击数字暂停/继续；倒计时结束时播放内置定制的柔润绒音风铃 `chime.wav`（纯 Python 合成的 44.1 kHz 立体声原创无版权 WAV，也可在设置中关闭或自行替换）。

## 关键字与示例

| 命令 / 输入 | 行为 |
|---|---|
| `otodo` / `便签` / `清单` / `待办便签` / `bq` / `td` | 查看未完成/已完成待办、开关桌面便签或打开 `Tasks.md` |
| `otodo 整理周报` | 第一项直接添加到 `Tasks.md`（预览真实写入行），下方同时列出匹配的已有待办 |
| `otodo 关键词` | 筛选已有待办；`Enter` 勾选完成/取消、`Ctrl+Enter` 显示便签、`Shift+Enter` 打开 `Tasks.md` |
| `otodo 显示` / `otodo 隐藏` | 置顶「显示 / 隐藏 Obsidian 待办便签」项 |
| `倒计时 25` / `djs 40 复习` / `timer 90s` | 启动倒计时并弹出桌面倒计时磁贴（纯数字默认分钟，支持 `h`/`m`/`s`/`mm:ss` 与标签） |
| `倒计时`（空格） | 查看运行中的倒计时（`Enter` 暂停/继续、`Ctrl+Enter` 重置、`Shift+Enter` 结束）或快捷预设 |

便签窗口内快捷键：`Ctrl+N` 新建、`Ctrl+F` 搜索筛选、`F5` / `Ctrl+R` 立即同步、`Ctrl+O` 打开 `Tasks.md`、`Esc` 关闭浮层或取消编辑。

## 与 888bik.obsidian-quicknote 协同使用

1. 在「Win顺 → 设置 → 插件 → Obsidian 待办便签」中，将 **Obsidian Vault 目录** 设为与 `888bik.obsidian-quicknote` 相同的 Vault 根目录（例如 `C:\Users\YourName\ObsidianVault`）。
2. 将 **待办文件** 保持一致（默认 `Tasks.md`）。
3. 首次打开便签时也可直接点击便签中的「选择 Obsidian Vault 文件夹…」进行关联。

## 权限说明（最小权限原则）

本插件仅申请以下 4 项必要权限，**不申请** `network`、`processes`、`clipboard`、`notifications`、`system:settings` 或 `elevated`：

| 权限 | 为何需要 | 对应实现位置 |
|---|---|---|
| `files:read` | 读取已配置的 Obsidian Vault 内的待办 Markdown 文件（默认 `Tasks.md`），并在后台检测外部修改以刷新桌面便签。 | `markdown_store.py` |
| `files:write` | 将用户在便签或搜索框执行的勾选、新增、编辑、删除与撤销操作无损写回该待办 Markdown 文件。 | `markdown_store.py` |
| `ui` | 显示桌面待办便签与独立倒计时磁贴窗口，并通过 Win顺 宿主 `register_hotkeys` 注册显隐快捷键。 | `sticky_window.py`、`countdown_tile.py`、`layered_countdown.py`、`main.py` |
| `winshun:open` | 点击「打开 Tasks.md」时通过 Win顺 的 `open_item` 工具使用系统默认应用（如 Obsidian）安全打开该文件，无需 `processes` 权限。 | `main.py` |

> **安全与系统 API 说明**：
> - 插件零联网、不收集或上传任何笔记数据、不下载或执行任何外部程序。
> - `runtime.sandbox` 为 `normal` 以便读写用户指定的 Obsidian Vault；`markdown_store.py` 严格校验相对路径，拒绝绝对路径、`..` 路径穿越、指向 Vault 外部的符号链接 / junction 以及超过 20 MB 或非普通文件目标。
> - 代码中的 `ctypes` 调用仅限 Windows 系统自带图形/窗口接口（`user32.dll`、`dwmapi.dll`、`gdi32.dll`、`gdiplus.dll`、`shcore.dll`），用于实现原生无边框圆角、高分屏 DPI 缩放及倒计时磁贴的每像素透明分层渲染。
> - `chime.wav` 为原创纯数学合成的 44.1 kHz 16-bit 立体声柔润提示音，通过标准库 `winsound` 异步播放。

## 运行依赖

- Windows 10 / 11 x64
- Win顺（建议 0.33.0 及以上，0.44.0 已实测）
- Python 3（附带标准库 `tkinter`，无需安装第三方 pip 包）

## 数据位置

- **待办内容**：仅保存在你配置的 Obsidian Vault 内的待办 Markdown 文件（默认 `Tasks.md`）。
- **界面状态**：仅将窗口位置、尺寸、置顶、不透明度、字号、折叠、筛选模式、倒计时形态与备选 Vault 路径保存在 Win顺 分配的数据目录 `WS_DATA_DIR`（`%LOCALAPPDATA%\WinShun\plugins\learning-kai.obsidian-todo.data\state.json`）。

## 安装与测试

从 Win顺「设置 → 插件 → 插件库」直接搜索安装，或下载 Release 中的 `.wsplugin` 包安装：

```powershell
ws plugin install .\learning-kai.obsidian-todo-1.3.1.wsplugin
ws plugin enable learning-kai.obsidian-todo
```

运行自动化单元测试：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m unittest discover -s tests -v
```

## 许可证

MIT License，见 [LICENSE](LICENSE)。
