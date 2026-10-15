# Classic Pattern Explorer 1.0

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md)

Classic Pattern Explorer 是一款可在桌面窗口或命令行中检索文言文（汉文，Literary Sinitic）原文的工具。界面支持韩语、日语、简体中文、繁体中文和英语。切换界面语言不会转换或改动原文文本。

## Windows 可执行文件

下载 [cpe-gui.exe](https://github.com/cobari/CPE-release/releases/download/v1.0/cpe-gui.exe) 后双击即可运行，无需安装 Python 或 uv。Windows 可执行文件使用 Microsoft Edge WebView2 Runtime。

## 从源码运行

请安装 Python 3.11 或更高版本以及 `uv`，然后在当前目录下运行：

```bash
uv sync --locked
uv run --locked cpe gui
```

传入 UTF-8 文本文件或文件夹路径即可直接打开：

```bash
uv run --locked cpe gui path/to/texts
```

在 macOS 上可打开 `cpe-gui.command`，在 Linux 上可运行 `cpe-gui.sh`。首次启动会自动安装运行时依赖。Windows 使用 Edge WebView2，macOS 使用 WebKit，Linux 使用 Qt。窗口会记住上次选择的文本文件夹。

在设置（Settings）对话框中可选择界面语言和检索来源。本地文本文件使用 UTF-8 编码；选择文件夹时会递归读取其中的 `.txt` 和 `.md` 文件。文本文件可附带名为 `NAME.txt.manifest.toml` 的附属配置文件（sidecar），用于记录书名（title）、版本（edition）和年代（date）。访问在线文献源需要网络连接。

## 命令行

```bash
uv run --locked cpe --help
uv run --locked cpe match patterns/mvp.toml path/to/texts --out results
uv run --locked python itkc_search.py --help
```

检索命令支持导出 CSV、JSONL 以及运行元数据。内置的模式定义保留了其文献出处引用。应用程序包已包含现有的 CLI 命令及其依赖项，所有列出的命令均可直接使用。

## 发行说明

本仓库包含运行时程序包、翻译目录、页面资源、启动脚本、运行时依赖锁文件以及许可证文件。测试代码、开发依赖、构建工具、智能体指令、研究文档、标注基准数据（gold data）、本地语料库以及开发仓库的 Git 历史记录均不包含在内。

当前程序包版本为 `1.0.0`，对应的 Git 标签为 `v1.0`。`release.json` 记录了精确的源码快照信息。后续版本将作为经审校的运行时快照导出，私有开发历史不会合并到本仓库。

本项目基于 MIT 许可证（`LICENSE`）发布。这是一个与韩国古典翻译院（ITKC）无关的个人项目，不附带任何原文语料库、现代译文或数据库响应数据。
