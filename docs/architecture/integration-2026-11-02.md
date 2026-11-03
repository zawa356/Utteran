# Phase A〜B6 main統合・GitHub Pages統合記録

更新日: 2026-11-02 JST。**これは開発コードのmain統合であり、リリースや実機受入完了ではありません。**

## GitHub上の統合実績

| 領域 | PR | main merge commit |
|---|---|---|
| Phase A + B1 + B2 | [#30](https://github.com/zawa356/Utteran/pull/30) | `7318a2fc21dfd05f099803e7855ee09fa7d1778d` |
| B3 Profile Worker | [#24](https://github.com/zawa356/Utteran/pull/24) | `cf84f337d5a624afc088cf3557bc4ef7ca10cca7` |
| B4 Capability Inventory | [#25](https://github.com/zawa356/Utteran/pull/25) | `417e3bea3c806ced98e3aef9aa1e92258820088b` |
| B5 Preset/Policy | [#26](https://github.com/zawa356/Utteran/pull/26) | `8a3f175ffc50fe586cae33ae02270c0978910345` |
| B6 Durable Store/Outbox | [#28](https://github.com/zawa356/Utteran/pull/28) | `c6365555234d04421b73e72687eb8219854de623` |
| 日本語・英語 Astro/Starlight サイト | [#29](https://github.com/zawa356/Utteran/pull/29) | `c79de4a88f70666224c7805735ecc6a33d7d5876` |

既存のstacked PRはbaseを順次`main`へ変更して、**merge commit**方式で統合。強制push、squash/rebaseは行っていない。独立のUI監査Draft [#23](https://github.com/zawa356/Utteran/pull/23)は未統合のまま。統合元ブランチの削除も行っていない。

## 状態・残るゲート

- mainの開発バージョンは`0.1.35`、**未リリース**。公開リリースやタグは変更していない。
- B5設定APIは明示opt-in、B6 control storeも既定では使わない。現行GUI/CLI・非永続キューは従来のまま。B7〜B9、GUI刷新U0〜U3、実モデル/GPU/Windows実機受入は未完。
- [#27](https://github.com/zawa356/Utteran/issues/27)で追跡: Linux mypy 9件、Windows PowerShell headless検査のInno Setup前提、PID-file競合、BOM検査失敗、後続CIの未実行step。main統合はこれらの解消・CI合格を意味しない。
- POSIX以外のDB ACL、private DBの無期限保持/完全消去/バックアップ方針、実process復旧も未受入。既存課題はcloseしない。
- 0.1.35のlock/hash互換性問題と公開履歴scanの既存fixture findingも、解消を主張しない。

## GitHub Pages

- **2026-11-02追補（現在地）:** Settings→Pages有効化後、[Website run 41394884715](https://github.com/zawa356/Utteran/actions/runs/41394884715)（main `3fd187d`、attempt 2）が**success**。`https://zawa356.github.io/Utteran/`・`/Utteran/en/`はHTTP 200を確認（B7作業時）。`/Utteran/favicon.svg`は404で、独立した軽微なサイト不具合として別途扱う。以下の「未有効・公開未確認」は公開前の履歴。
- Astro/Starlight日英12ページは[site専用CI](https://github.com/zawa356/Utteran/actions/runs/41392127702)で成功。
- mainでの[Pages build/deploy run 41394333383](https://github.com/zawa356/Utteran/actions/runs/41394333383)では**buildとartifact uploadは成功**、deployは404 `Ensure GitHub Pages has been enabled` で失敗。**公開成功は未確認**。
- リポジトリ管理者が[Settings → Pages](https://github.com/zawa356/Utteran/settings/pages)で Sourceを`GitHub Actions`に設定し、失敗したworkflowを再実行した後、`/Utteran/`と`/Utteran/en/`の表示・リンク・言語切替を実機ブラウザで受入。
- 現時点で公開済みサイトを前提にREADMEのリンクやGitHub AboutのWebsiteを設定しない。成功確認後に更新する。

## 文書同期と次工程

- README日英はB5/B6の「main統合済み・未リリース」に訂正。過去PR提出時の検証記録や当時のDraft状態は**歴史的記録として保持**し、後から書き換えず、この統合記録で現在地を明示。
- 変更履歴・AISTATE・Phase A/B5/B6・UI UX計画の現在地を同期。Issue #17/#18の状態を同期。文書更新も専用PRでmainへ反映する。
- 次の機能実装はB7から**main HEAD基点**（2026-11-02追補: B7はmain `3fd187d`基点でDraft実装、PR #32で`4fbb4d1`としてmain統合。B8はmain `4fbb4d1`基点でDraft、開発版0.1.37・未リリース。[B7](b7/README.md) / [B8](b8/README.md)）。CI改善は#27として独立し、実受入は各Issueで追跡する。
