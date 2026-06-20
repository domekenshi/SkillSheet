# profile-editor（プロフィール編集 GUI）

`profile.json`（構造化マスター）をブラウザのフォームで編集し、保存すると
`profile.md` を自動生成するローカルツール。**Python 標準ライブラリのみ・依存ゼロ・ビルド不要**。

## 使い方

```sh
python3 tools/profile-editor/server.py
```

- 自動でブラウザが開きます（開かなければ http://localhost:8765 を手動で開く）。
- フォームで 基本情報 / スキルサマリ / 技術スキル / 担当可能工程 / プロジェクト経歴 /
  個人開発 / 資格 / 公開実績 / チームワーク を編集。
- 上部のボタン：
  - **保存**：`profile.json` を更新し、`profile.md` を再生成。
  - **経験年数を再計算**：各案件の「メイン技術」設定 × 期間 × 現在日付から経験年数を自動計算
    （本人申告値が下限。「利用のみ」の技術は対象外）。再計算後に「保存」で確定。
- 終了は実行中ターミナルで `Ctrl+C`。
- ポート変更：`PROFILE_EDITOR_PORT=9000 python3 tools/profile-editor/server.py`

## データの流れ

```
profile.json（マスター・GUIで編集）── 保存 ──▶ profile.md（自動生成・直接編集しない）
                                              └─▶ generate-skillsheet などのスキルが参照
```

## ファイル

- `server.py` … ローカル HTTP サーバ（API ＋ index.html 配信）
- `index.html` … 編集フォーム（素の HTML/JS、ビルド不要）
- `profile_io.py` … profile.json の読み書き・profile.md 生成・経験年数の再計算ロジック

## 注意

- `profile.json` / `profile.md` は個人情報を含むため `.gitignore` 済み（コミットされない）。
- `profile.md` は生成物。手で編集しても次回保存で上書きされるため、編集は GUI（または `profile.json`）で行う。
- ローカル専用（127.0.0.1 バインド）。外部公開しないこと。
