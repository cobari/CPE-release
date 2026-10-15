# Classic Pattern Explorer 1.0

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md)

Classic Pattern Explorer 是一款可在桌面視窗或命令列中檢索文言文（漢文，Literary Sinitic）原文的工具。介面支援韓語、日語、簡體中文、繁體中文和英語。切換介面語言不會轉換或改動原文文本。

## Windows 執行檔

下載 [cpe-gui.exe](https://github.com/cobari/CPE-release/releases/download/v1.0/cpe-gui.exe) 後按兩下即可執行，不需安裝 Python 或 uv。Windows 執行檔使用 Microsoft Edge WebView2 Runtime。

## 從原始碼執行

請安裝 Python 3.11 或更新版本以及 `uv`，然後在此資料夾下執行：

```bash
uv sync --locked
uv run --locked cpe gui
```

傳入 UTF-8 文字檔或資料夾路徑即可直接開啟：

```bash
uv run --locked cpe gui path/to/texts
```

在 macOS 上可開啟 `cpe-gui.command`，在 Linux 上可執行 `cpe-gui.sh`。首次啟動會自動安裝執行階段相依套件。Windows 使用 Edge WebView2，macOS 使用 WebKit，Linux 使用 Qt。視窗會記住上次選擇的文字資料夾。

在設定（Settings）對話方塊中可選擇介面語言與檢索來源。本機文字檔使用 UTF-8 編碼；選擇資料夾時會遞迴讀取其中的 `.txt` 與 `.md` 檔案。文字檔可附帶名為 `NAME.txt.manifest.toml` 的附屬設定檔（sidecar），用於記錄書名（title）、版本（edition）與年代（date）。存取線上文獻來源需要網路連線。

## 命令列

```bash
uv run --locked cpe --help
uv run --locked cpe match patterns/mvp.toml path/to/texts --out results
uv run --locked python itkc_search.py --help
```

檢索命令支援匯出 CSV、JSONL 以及執行中繼資料。內建的模式定義保留了其文獻出處引用。應用程式套件已包含現有的 CLI 命令及其相依項目，所有列出的命令均可直接使用。

## 發行說明

本儲存庫包含執行階段套件、翻譯目錄、頁面資源、啟動腳本、執行階段相依鎖定檔以及授權條款檔案。測試程式碼、開發相依套件、建置工具、代理程式指令、研究文件、標註基準資料（gold data）、本機語料庫以及開發儲存庫的 Git 歷史記錄均不包含在內。

目前套件版本為 `1.0.0`，對應的 Git 標籤為 `v1.0`。`release.json` 記錄了精確的原始碼快照資訊。後續版本將作為經審校的執行階段快照匯出，私有開發歷史不會合併至本儲存庫。

本專案依 MIT 授權條款（`LICENSE`）釋出。這是一個與韓國古典翻譯院（ITKC）無關的個人專案，不附帶任何原文語料庫、現代譯文或資料庫回應資料。
