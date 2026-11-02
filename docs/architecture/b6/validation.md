# 検証結果と未検証事項

## 環境・基点

- Linux container、Python 3.11.18（uv `.venv`）、uv 0.11.32。モデル・GPU・network不要。
- baseline: 開始基点B5 HEAD `5abb23d62f5fd061c8f5d88ea25d0c761fb133d2`の**未変更作業ツリー**で、
  同一依存（`uv sync --extra dev --extra gui --locked`）・同一環境にて計測。
- failed/error/skipped node集合の比較: [test-comparison.json](test-comparison.json)。

## 結果（ローカル実測）

| 項目 | baseline（B5 HEAD） | B6最終 |
|---|---|---|
| `pytest -m "not requires_model"` | 944 passed / 2 failed / 8 skipped（計954） | 1005 passed / 2 failed / 8 skipped（計1015） |
| failed集合 | `test_gui_processes::test_profile_manifest_detects_current_and_stale_dependencies`、`...detects_venv_move_without_disabling_profile` | 同一（新規0、解消0） |
| error集合 | なし | なし |
| skipped集合 | Windows専用5、Windows PowerShell起動2、git-filter-repo未導入1（計8） | 同一 |
| `tests/test_b6_control.py` | — | 61 passed（3回連続実行でflakeなし） |
| mypy | 9 errors（`processes.py` Windows API、既知） | 同一9件、B6新規0（100 files） |
| ruff check / format --check | 成功 | 成功 |
| `uv lock --check` | 成功 | 成功（version行のみ変更） |
| B1/B3/B4/B5 schema `--check` | 成功 | 成功（bytes不変） |
| B6 schema `--check` | — | 成功 |
| `uv build --wheel` | — | `utteran-0.1.35-py3-none-any.whl`、`utteran_control/*`と`utteran_contracts/control*.py`を含む |
| 独立wheel venv 3.11.18 | — | B6+B1契約+B5+paths+version 200 passed |
| 独立wheel venv 3.12.3 | — | 同200 passed |
| `public_history_scan --worktree` | 80 findings / blocking 2 | 82 findings / blocking 2（同一の既存音声fixture2件）。増分2件は合成sentinel pathのnon-blocking `test-placeholder`/`data-reference` |
| `check_powershell_bom.py` | `scripts/verify_uninstaller.ps1` BOM欠落で失敗 | 同一（既存、本PR対象外） |

既知failed 2件は、B5記録の「既知3件」の部分集合（この環境ではもう1件が再現しない）。
`--fail-on-findings`はbaselineでも既存fixture 2件によりrc=1で、B6による新規blockingはない。
BOM欠落とpublic scanのrc=1はlinux-quality jobではmypy失敗後の後続stepのため従来から未到達と見られる。
いずれも[Issue #27](https://github.com/zawa356/Utteran/issues/27)の範囲として扱い、本PRで修正しない。

PR #28レビュー修正後の実測（B6 80件、全体1024 passed / 2 failed / 8 skipped、wheel各220件）は
[pr28-review-fixes-validation](pr28-review-fixes-validation.md)を参照。上表は修正前（`a037857`）の記録値。

## テスト観点（`tests/test_b6_control.py`）

1. Schema/DTO: 決定的生成・`--check` CLI・store出力/例のschema適合、unknown field・schema_version・
   naive/非UTC datetime・自由文reason拒否、public schemaにnonce/fencing/PIDなし。
2. Durability: reopen、不変history/outbox/replay（trigger）、空/破損/未知version/必須table欠落の
   byte不変拒否、`create=False`、open後削除で再作成しない、unsafe path、POSIX 0600/0700とsymlink拒否、
   8 thread同時初回作成、B5と別path。
3. Atomicity: commit前SQLITE_FULL、応答喪失（commit後）、実`max_page_count`満杯、busy、readonly、
   `os._exit`によるcommit前後process crash→再起動後の整合とreplay。
4. Concurrency: 2 thread・2 process CAS、同key 6並行、異body 409、replay先行、単一active slotの
   アプリ/DB二重拒否、stale fencing、cancel/complete race両順序。
5. Outbox: seq連番・allowlist列・再open後非巻戻し、160件を37件pageで欠落/重複なし、filter gap、
   cursor不正/別epoch/未来seq/rotate後expired、未commit不可視、snapshot+watermark、並行writer下25回整合。
6. Recovery: 判定matrix、queued→held順序維持・paused・setup/blocked flag、verified loss→interrupted、
   unknown/PID reuse/起動証拠なし/child生存→recovery_required（slot保持・二重起動拒否）、
   二度呼出し不変、clock jump、Verifier例外、current owner、lease欠落、attempt数不変（dispatch 0）。
7. Privacy: sentinel/nonceがprivate table以外・snapshot・event・view・例外・logに出ない、
   token系key/HF token形式値の拒否（保存0）、破損行のvalue-freeエラー。
8. Compatibility: `utteran_control`/schemaのimportでutteran・utteran_gui・utteran_settings・fastapi・
   torch等を読まない。`create_app()`既定でcontrol DB未作成・`/api/v1/events`/`sync`は404・
   `utteran_control`未import。既存全テストのfailed/error/skipped集合不変。
9. Backup: online backup 0600、既存先拒否、restore後epoch更新で旧cursor expired。

## CI（GitHub Actions）

[Draft PR #28](https://github.com/zawa356/Utteran/pull/28)、HEAD `802ba04`、
[run 41390499258](https://github.com/zawa356/Utteran/actions/runs/41390499258)（pull_request）。
**workflow全体は赤**（既知課題のみ）。「CI緑」ではない。

| job | 結果 | 内容 |
|---|---|---|
| windows-tests | failure | pytest **1011 passed / 4 skipped**（B5時点951/3 + B6 60 passed、POSIX権限test 1件skip）。PowerShell parse成功。headless起動stepが既知のInno Setup（ISCC.exe）不在で失敗 |
| contracts-python-compatibility (3.11) | success | 契約・境界・B5・B6 411 passed、B1/B4/B5/B6 schema `--check`成功 |
| contracts-python-compatibility (3.12) | success | 同411 passed |
| linux-quality | failure | lock・B1〜B6 schema・ruff・format成功。mypyが既知9件（`processes.py`）で停止し、後続のLinux pytest/BOM/public scanは未実行 |

linux-qualityで未実行となったLinux全体pytest・BOM・public scanは、ローカル実測（上表）で代替確認した。

## 未検証（明示）

- Windows実機のACL、PyInstaller/portable/installer同梱、実GPU。Windows ActionsはPOSIX権限testをskip。
- 実子processを用いたowner/child生存確認、PID+start time取得、Job Object連携、VM kill injection（B7/U3）。
- 長期運用でのDBサイズ増加（GCなし）、大量operation時のsnapshot性能（paginationなし）。
- network filesystem・hard link非対応filesystem上の動作。
