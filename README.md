# 矿大校园网自动登录（Windows）

连接中国矿业大学校园 Wi-Fi 后，用本机 Chrome 自动打开认证页面、填写自己的账号密码并提交。支持登录 Windows 桌面后自动运行，以及掉线后重试。

面向本项目使用的 `CUMT_Stu` / `10.2.5.251` 认证环境，不保证适用于其他校区、其他学校或改版后的页面。不是学校官方客户端。仅用于自己的授权账号，不绕过校园网认证或终端限制。

## 功能与边界

- 优先通过 Playwright 控制 Chrome 登录，浏览器失败时保留 HTTP 请求方式作为备用；备用方式在原使用环境中曾失败，不能保证可用。
- 默认每 20 秒检查连通性，连续失败 3 次后改为每 5 分钟重试。
- 登录流程会读取认证页的在线账号并进行归属判断；网络已通且明确是其他账号时，不主动挤掉对方。
- **开机自启指登录 Windows 桌面后启动**，不是未解锁时自动运行。
- Wi-Fi 需要由 Windows 自动连接，本程序负责连接后的网页认证。
- 当前实现不能保证在认证页读不到在线账号时严格证明账号归属；连通性正常也不等同于本账号已验证。
- 不提供 iPad/iPhone 后台自动认证。

## 1. 环境准备

需要 Windows、Python 3.10 或更高版本，以及已安装的 Google Chrome。先自行安装所需软件到合适位置，本仓库不自动安装系统软件。

下载仓库并解压，或使用 Git：

```powershell
git clone https://github.com/Wjtouo/cumt-net.git
cd cumt-net
```

项目路径尽量保持固定，配置开机任务后不要直接移动文件夹。

### Python 选择规则

`.bat` 与 `.vbs` 启动器使用相同的选择规则：

1. 优先使用环境变量 `CUMT_PYTHON` 指定的 `python.exe` 完整路径（无须在变量值中加引号）。
2. 否则使用当前用户目录下已存在的 `Programs\Python\Python310` 安装。
3. 最后使用 PATH 中的 `python.exe` / `pythonw.exe`。

自定义 Python 安装位置或使用虚拟环境时，可在 Windows“编辑账户的环境变量”中设置 `CUMT_PYTHON`，指向你实际安装位置下的 `python.exe`，确保同目录有 `pythonw.exe`。设置后重新打开终端；开机任务在下次登录后读取新变量。

安装项目依赖（在项目目录运行）：

```powershell
.\run_python.bat -m pip install --target .\libs -r .\requirements.txt
```

依赖安装在当前项目的 `libs` 文件夹，不需要运行 `playwright install`；程序使用你已安装的 Chrome。更换 Python 版本时，需要重新建立与该版本匹配的 `libs` 依赖，不能直接复用旧版本的二进制依赖。

## 2. 填写个人配置

首次使用时，将 `config.example.json` 复制成 `config.json`，已有个人配置时请勿覆盖：

```powershell
if (!(Test-Path .\config.json)) { Copy-Item .\config.example.json .\config.json }
notepad .\config.json
```

填写 `account`（校园网账号）和 `password`（密码），并按实际认证页面选择 `provider`：`校园联通`、`校园移动`、`校园电信`、`校园用户` 或 `校园其他`。

账号通常填写认证页输入框中的账号，不自行追加运营商后缀。其他参数通常保持模板默认值即可：`portal_host` 是认证服务器地址，`interval` 是检查间隔秒数。`custom_login_*` 属于高级备用功能，初次使用留空。

**隐私说明：**`config.json` 中的密码在本机明文保存，本项目没有加密凭据。`.gitignore` 已排除个人配置、整个 `logs` 文件夹、交接文档、依赖和缓存。日志、截图及 `logs/captured_request.txt` 可能包含账号、网络标识甚至完整登录密码，不要直接发到 Issues 或上传到其他地方。不要使用 `git add -f` 强制提交这些文件；`.gitignore` 不能清除已存在于 Git 历史中的秘密。

## 3. 首次验证

先在 Windows 连接校园 Wi-Fi，并在网络设置中勾选“自动连接”。然后：

```powershell
# 查看状态，不发送登录请求
.\run_python.bat cumt_net.py --status

# 检查一次，必要时自动登录
.\run_python.bat cumt_net.py
```

也可以双击 `show_status.bat` 查看状态，双击 `browser_login.bat` 立即执行一次浏览器登录。已联网时通常直接退出；需要排查页面时使用：

```powershell
$env:PYTHONPATH = "$PWD\libs"
.\run_python.bat browser_login.py --show
```

如需在前台持续检查并自动重连：

```powershell
.\run_python.bat cumt_net.py --daemon
```

按 Ctrl+C 结束前台守护。双击 `start_daemon.vbs` 可无窗口运行，但请避免重复启动多个守护进程。

## 4. 设置开机自动登录

推荐使用 Windows“任务计划程序”创建任务，下面的设置同时适用于电池供电：

1. 打开“任务计划程序”→“创建任务”，名称填写 `CUMT_AutoLogin`。
2. “常规”选择当前 Windows 用户，并选“只在用户登录时运行”。
3. “触发器”新增“登录时”，限定当前用户。
4. “操作”新增“启动程序”：程序填写 `wscript.exe`；参数填写 `start_daemon.vbs` 的**完整路径并加双引号**；“起始于”填写项目文件夹完整路径。
5. “条件”取消“只有在计算机使用交流电源时才启动此任务”，并取消切换为电池时停止任务。
6. “设置”启用“允许按需运行任务”。不要把“仅在有网络连接时启动”作为前提，让守护程序自行等待网络就绪。
7. 保存任务；如已有手动启动的守护进程，先按下节方法停止，再右键任务→“运行”，检查日志是否新增守护启动记录。

启动脚本会启动后台 Python 后退出，所以任务显示“就绪”不代表后台 Python 已停止。任务计划程序的“结束”也不能保证停止该 Python 子进程。

### 停用与停止

在任务计划程序中禁用 `CUMT_AutoLogin` 可以阻止下次登录自动启动。若要同时停止当前运行的后台程序，在任务管理器“详细信息”中显示“命令行”列，**仅结束命令行包含本项目 `cumt_net.py --daemon` 的 `pythonw.exe` 进程**，不要结束其他项目的 Python 进程。

### 验收

重启电脑并登录桌面，先不要手动认证；确认自动连接校园 Wi-Fi，等待认证后实际打开网页，再检查 `logs/login.log` 中的启动、登录与在线账号核对记录。已保留的校园网会话可能让电脑无需再次认证，所以一次重启能上网不一定证明这次发生了自动登录。

## 常见问题

- **连上 Wi-Fi 但不能上网：**先手动打开 `http://10.2.5.251/`，确认当前认证页面和运营商；检查 `config.json`。不在校园网覆盖内时无法完成认证。
- **缺少 Playwright / greenlet 或 DLL 加载失败：**确认安装依赖与运行脚本使用同一个 Python；重新建立匹配该版本的 `libs`。
- **找不到 Chrome：**先确认本机已安装 Chrome。脚本默认使用 Chrome，不会自动下载浏览器。
- **拔电后没有自动登录：**检查任务计划程序中的电源条件，以及 Wi-Fi 是否自动连接。
- **已显示在线但无法确认自己的账号：**查看认证页实际在线账号；程序不会主动替换已识别出的其他在线账号。请勿把“有网”当作账号归属的唯一证据。
- **认证页改版：**查看本地截图及日志，可能需要调整 `browser_login.py` 中的表单选择规则。截图和日志公开前必须脱敏。
- **注销后暂时无法认证：**本地使用中曾出现网络重新获取地址的等待期；脚本会等待认证服务器可达并在失败后重试，不要反复快速提交。

## 文件说明

- `cumt_net.py`：状态检测、守护循环及备用 HTTP 登录。
- `browser_login.py`：Chrome 表单登录。
- `config.example.json`：不含账密的配置模板。
- `requirements.txt`：项目依赖版本。
- `run_python.bat`：统一选择 Python。
- `start_daemon.vbs`：无窗口启动守护程序。
- `browser_login.bat` / `show_status.bat`：双击登录或查看状态。

本仓库不包含真实账号密码、个人交接文档、运行日志、截图、抓取的登录请求或打包依赖。
