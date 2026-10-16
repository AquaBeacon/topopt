# 開発ガイド

造形用トポロジー最適化・構造解析。

## セットアップ

python -m pip install -e "topopt[all,test,mcp]"。パッケージは topopt/、モデルと検証根拠はその隣に置きます。cd topopt の後 python -m pytest。外部ソルバはREADMEを参照してください。

Pythonの依存を追加する場合は、このリポジトリ専用の仮想環境を推奨します。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

## 入口

- [topopt/pyproject.toml](topopt/pyproject.toml)
- [docs/mcp.md](docs/mcp.md)
- [osmo_tripod](osmo_tripod)

## 検証

ビルド・実機・外部ツールの確認手順はREADMEを参照してください。移行検証だけで実機動作・製造可能性を合格扱いにしません。

## データと関連リポジトリ

このディレクトリがGitリポジトリのルートです。親の AquaBeacon-OSS は複数リポジトリの置き場です。横断開発では各リポジトリを同じ親ディレクトリに配置し、[全体の入口](https://github.com/AquaBeacon/workspace-tools)から関連設計へ進みます。単体checkoutでも本書のローカル入口を使用できます。

生データ・生成物・SDK・仮想環境は手元に保持し、公開対象を.gitignoreで区別します。追跡済みのファイルは.gitignoreだけでは除外されないので、`git status --short`と`git ls-files`を確認します。

ライセンスは[Apache 2.0](LICENSE)、第三者部分は[THIRD_PARTY.md](THIRD_PARTY.md)。

## 公開リポジトリ

GitHub: https://github.com/AquaBeacon/topopt

```sh
git clone https://github.com/AquaBeacon/topopt.git
```

公開ブランチはmainです。新設リポジトリは公開対象だけを初期履歴にし、過去の開発履歴と実験ログはローカルに保管します。
