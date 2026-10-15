# Classic Pattern Explorer 1.0

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md)

Classic Pattern Explorer는 데스크톱 창 또는 명령줄(CLI)에서 한문(Literary Sinitic) 원문을 검색하는 도구입니다. 인터페이스는 한국어, 일본어, 중국어 간체, 중국어 번체, 영어를 지원하며, 인터페이스 언어를 변경해도 원문 텍스트는 변환되지 않습니다.

## Windows 실행 파일

[cpe-gui.exe](https://github.com/cobari/CPE-release/releases/download/v1.0/cpe-gui.exe)를 내려받아 두 번 클릭하면 실행됩니다. Python이나 uv를 설치할 필요가 없습니다. Windows 실행 파일은 Microsoft Edge WebView2 Runtime을 사용합니다.

## 소스에서 실행하기

Python 3.11 이상과 `uv`를 설치한 뒤, 이 폴더에서 다음 명령을 실행하세요:

```bash
uv sync --locked
uv run --locked cpe gui
```

UTF-8 텍스트 파일이나 폴더 경로를 전달하면 바로 열 수 있습니다:

```bash
uv run --locked cpe gui path/to/texts
```

macOS에서는 `cpe-gui.command`를 열고, Linux에서는 `cpe-gui.sh`를 실행하세요. 첫 실행 시 런타임 의존성이 설치됩니다. Windows는 Edge WebView2, macOS는 WebKit, Linux는 Qt를 사용합니다. 프로그램 창은 마지막으로 선택한 텍스트 폴더를 기억합니다.

설정(Settings) 대화상자에서 인터페이스 언어와 검색 소스를 선택할 수 있습니다. 로컬 텍스트 파일은 UTF-8 인코딩을 사용하며, 폴더를 지정하면 하위 경로의 `.txt` 및 `.md` 파일을 재귀적으로 불러옵니다. 각 텍스트 파일에는 제목(title), 판본(edition), 연대(date) 정보를 담은 `NAME.txt.manifest.toml` 사이드카 파일을 함께 둘 수 있습니다. 온라인 소스 검색에는 네트워크 연결이 필요합니다.

## 명령줄 인터페이스 (CLI)

```bash
uv run --locked cpe --help
uv run --locked cpe match patterns/mvp.toml path/to/texts --out results
uv run --locked python itkc_search.py --help
```

검색 명령은 CSV, JSONL 및 실행 메타데이터를 내보냅니다. 기본 포함된 패턴 정의 파일에는 출처 인용 정보가 유지되어 있습니다. 애플리케이션 패키지에는 기존 CLI 명령과 관련 의존성이 모두 포함되어 있어 안내된 모든 명령을 그대로 사용할 수 있습니다.

## 배포 구성

이 저장소에는 런타임 패키지, 번역 카탈로그, 페이지 리소스, 실행 스크립트(런처), 런타임 의존성 잠금 파일, 라이선스 파일이 포함되어 있습니다. 테스트 코드, 개발 의존성, 빌드 도구, 에이전트 지침, 연구 문서, 골드 데이터(검증용 정답 데이터), 로컬 코퍼스 및 개발 저장소의 Git 이력은 제외되어 있습니다.

패키지 버전은 `1.0.0`이며, 대응하는 Git 태그는 `v1.0`입니다. `release.json`에는 정확한 소스 스냅샷 정보가 기록되어 있습니다. 이후 버전 또한 검토를 거친 런타임 스냅샷 형태로 내보내지며, 비공개 개발 이력은 이 저장소에 병합되지 않습니다.

MIT 라이선스(`LICENSE`)로 배포됩니다. 이 프로젝트는 한국고전번역원(ITKC)과 무관한 개인 프로젝트이며, 원문 코퍼스나 현대어 번역문, 데이터베이스 응답 데이터는 포함되어 있지 않습니다.
