# 雷电 · Leidian

纵版射击游戏（Python + pygame），多关卡、Boss、僚机、呼叫支援（闪电风暴）等。

## 环境要求

- Windows（推荐）
- Python **3.12** 或 **3.13**（需能安装 pygame 轮子）
- [pygame](https://www.pygame.org/) 2.5+

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

或在项目根目录双击 **`run_game.bat`**（自动使用 `.venv` 若存在）。

## 资源说明

游戏贴图、激光等放在 **`assets/`** 目录下（含 `Enemies/`、`Lasers/` 等）。若仓库中未包含大体积素材，请自备资源或从发布包中解压到 `assets/`。

## 打包为 exe

1. 安装依赖（含打包工具）：

   ```bash
   pip install -r requirements.txt -r requirements-build.txt
   ```

2. 双击或在命令行执行 **`build_exe.bat`**，生成 **`dist\leidian.exe`**（单文件）。

3. （可选）代码签名：见 `scripts\sign_leidian.ps1` 与 `sign_after_build.bat`。

> Windows 11「智能应用控制」可能拦截未签名的 exe；可临时关闭/改为警告，或使用 `run_game.bat` 直接运行源码。

## 操作说明

- **移动**：WASD
- **射击**：空格（僚机与主炮同发）
- **呼叫支援**：M / 左 Ctrl — 闪电清屏 / Boss 扣当前血量约 1/3

## 局域网双人（新增）

支持一个主机 + 一个客户端，双方在各自电脑上看到两架飞机并分别控制。

### 推荐：自动发现配对（无需手输 IP）

两台电脑都执行同一条命令：

```bash
python main.py --lan auto
```

程序会在局域网自动发现对方，并自动分配主机/客户端后进入游戏。

### 备用：手动指定主机/客户端

1. 主机电脑启动：

   ```bash
   python main.py --lan host --bind-ip 0.0.0.0 --port 28990
   ```

2. 客户端电脑启动（把 `192.168.x.x` 改成主机局域网 IP）：

   ```bash
   python main.py --lan client --host-ip 192.168.x.x --port 28990
   ```

3. 操作方式：
   - 两边都用 **WASD/方向键 + 空格**。
   - 主机端为 P1，客户端端为 P2。

> 说明：这是联机基础版（主机权威同步），先保证双人同屏协作可用。后续可以继续把完整关卡/Boss/掉落等玩法也同步到联机模式。

当前联机版已支持：
- 双人独立移动与开火（P1 主机本地，P2 客户端远程）
- 刷怪、击毁计分、过关推进
- 双人分别受击掉命（HUD 显示双方生命）
- 特殊机掉落、双人拾取强化（枪型/僚机）
- Boss 战、Boss 子弹、支援闪电（M/左 Ctrl）

## 仓库

```text
git@github.com:bbg221/leidian.git
```

## 许可

游戏逻辑代码以仓库内约定为准；若使用 Kenney 等第三方素材，请保留对应许可证文件（如 `assets` 中的说明）。
