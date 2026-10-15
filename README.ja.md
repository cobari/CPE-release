# Classic Pattern Explorer 1.0

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md)

Classic Pattern Explorer は、デスクトップウィンドウまたはコマンドラインから漢文（Literary Sinitic）の原文を検索するツールです。インターフェースは韓国語、日本語、簡体字中国語、繁体字中国語、英語に対応しています。インターフェース言語を変更しても、原文テキスト自体は変換されません。

## Windows 実行ファイル

[cpe-gui.exe](https://github.com/cobari/CPE-release/releases/download/v1.0/cpe-gui.exe) をダウンロードし、ダブルクリックして起動してください。Python や uv のインストールは不要です。Windows 実行ファイルは Microsoft Edge WebView2 Runtime を使用します。

## ソースから実行する

Python 3.11 以降と `uv` をインストールし、このフォルダから以下を実行してください：

```bash
uv sync --locked
uv run --locked cpe gui
```

UTF-8 のテキストファイルまたはフォルダを指定して直接開くこともできます：

```bash
uv run --locked cpe gui path/to/texts
```

macOS では `cpe-gui.command` を開き、Linux では `cpe-gui.sh` を実行してください。初回起動時にランタイム依存関係がインストールされます。Windows は Edge WebView2、macOS は WebKit、Linux は Qt を使用します。ウィンドウは選択されたテキストフォルダを記憶します。

設定（Settings）ダイアログでインターフェース言語と検索ソースを選択できます。ローカルのテキストファイルは UTF-8 を使用し、フォルダを指定すると `.txt` および `.md` ファイルを再帰的に読み込みます。各テキストには、書名（title）・版本（edition）・年代（date）を記述した `NAME.txt.manifest.toml` サイドカーファイルを添えることができます。オンラインソースへのアクセスにはネットワーク接続が必要です。

## コマンドライン

```bash
uv run --locked cpe --help
uv run --locked cpe match patterns/mvp.toml path/to/texts --out results
uv run --locked python itkc_search.py --help
```

検索コマンドは CSV、JSONL、および実行メタデータを出力します。同梱されているパターン定義には出典の引用情報が保持されています。アプリケーションパッケージには既存の CLI コマンドとその依存関係が含まれているため、記載されているすべてのコマンドをそのまま利用できます。

## 配布内容

このリポジトリには、ランタイムパッケージ、翻訳カタログ、ページリソース、ランチャー、ランタイム依存関係のロックファイル、およびライセンスが含まれています。テスト、開発用依存関係、ビルドツール、エージェント指示書、研究ドキュメント、ゴールドデータ、ローカルコーパス、および開発リポジトリの Git 履歴は除外されています。

パッケージバージョンは `1.0.0`、対応する Git タグは `v1.0` です。`release.json` には正確なソーススナップショットが記録されています。以降のバージョンもレビュー済みのランタイムスナップショットとしてエクスポートされ、非公開の開発履歴はここにはマージされません。

MIT ライセンス（`LICENSE`）の下で公開されています。本プロジェクトは韓国古典翻訳院（ITKC）とは無関係の個人プロジェクトです。原文コーパス、現代語訳、データベースのレスポンスデータは同梱されていません。
