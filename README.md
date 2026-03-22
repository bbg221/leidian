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

## 仓库

```text
git@github.com:bbg221/leidian.git
```

## 许可

游戏逻辑代码以仓库内约定为准；若使用 Kenney 等第三方素材，请保留对应许可证文件（如 `assets` 中的说明）。
