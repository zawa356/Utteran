# G-09 — 開発専用`--next-ui-v1`起動・GUIプロセス排他・復旧境界

更新: 2026-11-11 JST（開発版`0.1.49`、未リリース、Draft PR）。UI-3 [#21](https://github.com/zawa356/Utteran/issues/21) / Epic [#18](https://github.com/zawa356/Utteran/issues/18)。

> **これは製品のv1接続ではない。** `--next-ui-v1`は起動・認証・排他・同期APIの安全性を確かめるための**一時的な開発モード**。
> 現行の新UI（S3-A/B1）はまだ旧`/api/jobs`を呼ぶため、このモードでは**旧投入が409**になり、文字起こしは開始できない（legacy fallbackは作らない）。
> 実ジョブ実行・B4 ready/smoke・実Setup完了・S3-B2・Windows WebView2実機は**受入対象外**（後続B4/G-07、最終統合ゲート）。

## 1. 3つの起動モード

| 起動 | 画面 | API | v1 DB | GUIロック |
|---|---|---|---|---|
| `utteran-gui`（引数なし） | 旧UI（`/`） | 旧`/api/*`のみ | 作らない | 取得する |
| `utteran-gui --next-ui` | `/next/`（Vue） | 旧`/api/*`のみ（従来どおり） | 作らない | 取得する |
| `utteran-gui --next-ui-v1`（**開発専用・暫定**） | `/next/`（Vue）、旧`/`も到達可 | 旧`/api/*`＋同一originの`/api/v1`（B5/B7/B8/B9） | 開発用rootにだけ作る | 取得する |

- 判定は`argv[1:]`の**厳密一致**（`utteran_gui.instance_guard.launch_mode`）。`--next-ui-v1 extra`・`--next-ui --next-ui-v1`・`--next-ui-v1=`・大文字違い等は
  **既定の旧UI**になり、v1へ丸めない（`--next-ui`の既存判定と同じ規則）。設定・環境変数・migrationでv1が選ばれることはない。
- 既定・`--next-ui`の`create_app()`引数は従来と同一（`test_native_startup_avoids_host_inline_style_only_for_next_ui`）で、`/api/v1`は404、v1 DBは作られない。
- `--next-ui-v1`のウィンドウタイトルは`utteran [dev: next-ui-v1 - not a product acceptance mode]`、起動ログに固定stage`next_ui_v1_dev_mode`（値なし）を出す。
  Vue画面・Design Freezeは変更していない。

**暫定フラグの廃止条件:** 新UIのv1投入接続（B4 ready/smokeの後）が完成し、owner承認の下で`--next-ui`自体がv1を使う形（または既定切替S6）に統合された時点で削除する。
それまでは開発者の検証用に限り、配布物・手順書の利用者向け導線には載せない。

## 2. 開発用data rootの条件（`--next-ui-v1`のみ）

`UTTERAN_DATA_ROOT`はportable配布でも使うため、**設定されているだけでは開発用と判定しない**。次をすべて満たさなければ固定codeで起動を拒否する（`validate_dev_data_root`）。

| 条件 | 拒否code |
|---|---|
| `UTTERAN_DISTRIBUTION`が空でない（portable等の配布識別）または凍結実行ファイル（`sys.frozen`） | `dev_root_distribution` |
| `UTTERAN_DATA_ROOT`が未設定・空・空白 | `dev_root_required` |
| 絶対パスでない | `dev_root_not_absolute` |
| 既存のディレクトリでない（**作成しない**） | `dev_root_missing` |
| ファイルシステムroot、home、homeの祖先、既定のユーザーデータ領域（`platformdirs`の`utteran`のdata/config/cache/log、`utteran-gui`のconfig、`~/.utteran`）と一致・内包・その内側 | `dev_root_reserved` |
| 空でなく、正しいmarkerが無い（markerの改変・ディレクトリ・symlinkを含む） | `dev_root_not_marked` |
| 検査中のI/O失敗 | `dev_root_unavailable` |

- **marker方針**: 初回は**既存の空ディレクトリ**にだけ固定内容のmarker`.utteran-dev-data-root`（JSON: `kind`/`purpose`/`schema_version`のみ、path・時刻・利用者名なし）を
  排他作成（`O_EXCL`）する。2回目以降はmarkerが完全一致するrootだけを許可する。既存データのあるディレクトリは採用も初期化も削除もしない。
- **別名**: rootは`os.path.realpath`で正規化（symlink・junction/reparse point・`..`を解決）し、その正規形を`UTTERAN_DATA_ROOT`へ書き戻してから後続の解決・子プロセスへ渡す。
  ロックはroot内の1ファイルに対するOSロックなので、別名経由でも同じファイルを指し二重取得できない（POSIXはsymlink、Windowsはjunctionで試験）。
- 検査とロック取得が成功する**前**には、FastAPI app・B5/B7/B8/B9 DB・migration・dispatch・GUIログ設定のいずれも作らない（拒否時に変わるのは、空ディレクトリへのmarker作成の可能性だけ）。
- 拒否時は標準エラー（凍結版Windowsでは同じ固定文をMessageBox）に`[code]`と固定文だけを出し、終了code 2。path・token・argvは出さない。

## 3. 同一data rootのGUIプロセス排他

- **方式**: `<data root>/gui-instance.lock`への非ブロッキング排他ロック（POSIX `fcntl.flock(LOCK_EX|LOCK_NB)`、Windows `msvcrt.locking(LK_NBLCK, 1)`でbyte 0）。
  ファイルの存在では判断しない。**OSがプロセス終了時（クラッシュ・強制killを含む）に自動解放**する。lockファイルは通常終了時もunlinkしない（unlinkすると2つの保持者が併存し得るため）。
- Windowsでは、終了したプロセスのbyte-range lockの解放が「システム資源次第で遅れることがある」と文書化されている。強制終了の直後に再起動すると
  一時的に`gui_already_running`になり得る（数秒後の再起動で解消）。試験は再取得を最大15秒待つ。
- descriptorは継承不可（PEP 446＋`O_CLOEXEC`）。子プロセス（worker・setup child）がロックを延命しないことを、`close_fds=False`の子を残したまま親をkillする実プロセス試験で確認。
- 既定レイアウト（`UTTERAN_DATA_ROOT`なし）のロックは既定のユーザーデータ領域（`platformdirs.user_data_dir("utteran")`、control storeの親と同じ場所）に置く。
  別rootは互いに妨げない。依存追加なし、既定起動に重いimportなし（標準ライブラリ＋`utteran_paths`のみ）。
- **3モードすべて**がAPI構築・socket bind・ログ設定の前に取得し、uvicorn停止とv1 runtime停止が終わるまで保持する。
  同一rootでの「旧×v1」「v1×v1」「旧×旧」「`--next-ui`×v1」の2つ目は`gui_already_running`で拒否され、競合側はファイルを1つも変えない（実プロセス試験、Linux＋Windows CI）。
- **意図した挙動変更**: 同じdata rootでGUIを2つ起動すると、2つ目は起動を拒否される（従来は旧GUIを2つ起動できた）。単独起動・旧API・旧画面の挙動は変えない。
- **保護範囲（誇張しない）**: 守るのは**この版以降の協調するGUIプロセス同士**だけ。CLI（`utteran`）、古いバイナリ、ロックに参加しない外部プロセス、
  クラッシュ後に残った子プロセスは排除しない。「全OSプロセスで単一writer」ではない。v1 DB内の実行権は従来どおりSQLiteの単一heavy slotとfencing tokenが守る。
  CLIをロック配下にする拡張は範囲外。

同一プロセス内の旧queue排他（`OperationQueue.close_for`）は維持する。両者の関係:

| 排他 | 範囲 | 守るもの |
|---|---|---|
| `close_for`（B7） | 同一Pythonプロセス | v1 activate中は旧queueへ新規投入しない |
| GUIインスタンスロック（G-09） | 同一data rootの協調GUIプロセス間 | 別GUIの旧queue/v1が同じrootで同時に動かない |
| SQLite heavy slot＋fencing（B6/B7） | 同じcontrol DBを開く全プロセス | v1 job/setupの二重dispatch |

## 4. ポート先取り・v1構築・同一origin認証

1. `bind_loopback_socket()`を**`create_app()`より前**に実行（全モード共通化。旧モードでも順序以外は同一）。
2. socketの実アドレスから`v1_authority="127.0.0.1:<実port>"`を作り、`create_app(enable_next_ui, enable_v1_settings, enable_v1_jobs, enable_v1_setup, enable_v1_events, v1_authority)`。
   同じsocketをuvicornへ渡し再bindしない（予約したportを失うTOCTOUなし）。
3. 認証は既存B5 sub-appのsession/Host/Origin middlewareをそのまま使う（独自実装・緩和なし）。`/launch?session=`のcookie発行と`/next/`へのredirect、nonce付きCSP、
   D&D再bindは`--next-ui`と同じ。`/api/v1`は同じloopback originに1 mount。
4. 実loopback HTTPで確認: 正しいHost・cookieでv1 GET 200、session欠如401、Host不一致403、Origin不一致403、cookieだけのPOSTは`origin_required` 403、
   Origin一致またはsession headerのPOSTはguard通過、SSE`/api/v1/events`はcookieで200（`text/event-stream`）・cookieなし401。
5. v1有効時、`POST /api/jobs`・`POST /api/wizard/jobs`・`POST /api/models/actions`は**環境probe・設定保存などの副作用より前に**409（`V1_OWNS_SLOT`）。
   旧`GET /api/queue`・取消・旧画面`/`は到達可能。**旧画面へ遷移できても旧投入は409のまま**（仕様）。
6. v1のread-only API（jobs/queue/setup-operations/sync/presets）に到達し、activate直後のqueueは`dispatch: paused`。

## 5. 起動失敗・停止・rollback

- `activate()`の順序（旧queue idle確認→所有tokenでclose→DBを開く前にbackup→layout migration→recovery paused/held→dispatcher開始）は不変。旧job/historyの自動移行なし。
- `activate()`は`create_app()`の**最後の手順**（直後は`return`のみ）。route登録・mount・StaticFiles・middlewareの失敗はすべてactivate前に起き、
  旧queueのclose・backup・migration・dispatcher開始は行われない（routeはruntimeを遅延参照する。PR #46レビューP2で変更）。
- `JobRuntime.shutdown()`は例外を投げないが成功も装わない: `rolled_back`／`not_active`／`active_attempt`／`ownership_lost`／`shutdown_failed`（backup失敗等）を返し、
  拒否は固定code（`v1_runtime_shutdown_refused`）でログする。`active_attempt`・`ownership_lost`・`shutdown_failed`では旧queueを再開しない（fail closed）。
  利用者に「強制rollback」操作は出さない。ブラウザを旧画面へ戻してもv1は自動`deactivate()`しない。
- 停止順序: WebView終了（通常・`Ctrl+C`・WebView作成失敗）→uvicorn停止（lifespan shutdownがv1 runtimeを止める）→lifespanが止めなかったruntimeを停止
  （uvicorn起動失敗時など、再試行は5秒）→ロック解放。v1ではuvicornのjoin上限を45秒（runtime停止の30秒を含む）、旧モードは従来の5秒。
  app構築の成功直後から（uvicornの`Config`/`Server`/threadの構築を含む）すべての経路が同じ後始末を通る（PR #46レビューP2）。
- **ロックを解放するのは停止が証明できたときだけ**（PR #46レビューP1）: サーバースレッドが止まり、かつv1 runtimeが無いか`state != active`（rollback完了）。
  `active_attempt`・`ownership_lost`・`shutdown_failed`ではv1の実行主体がまだ仕事を持ち得るため、ロックを明示解放せず
  （`gui_shutdown_incomplete reason=v1_runtime`）、プロセス終了時のOS解放に任せる。旧GUIはv1のSQLite fencingに参加しないため。
  サーバーが期限内に止まらなければロックを明示解放せず、プロセス終了時のOS解放に任せる（`gui_shutdown_incomplete`）。
- 起動前段の失敗（data root・ロック・socket bind・app構築・DB不正）では、作った資源（socket・ロック）を解放してから例外を伝える。不正DBのbyteは変えない。
- **OS強制終了**: cleanupの実行を前提にしない。OSがロックを解放し、次回起動の`activate()`がbackup→recovery（paused/held、確認できないownerは`recovery_required`）で安全性を確保する。
  実プロセスをkillした直後に同じrootでv1を再起動し、`dispatch: paused`・pre-v1 backup作成・旧投入409を確認。
- migrationは一方向（v3/v4/v5→v6と事前backupは既存試験で再確認）。**実DB復元・Windows ACL実機・secure delete・private DB保持期間はG-15等の別ゲート**。

## 6. 試験（合成fixtureのみ）

| 区分 | 試験 |
|---|---|
| フラグ | `tests/test_g09_instance_guard.py`（厳密一致11例）、`--next-ui`の既存判定 |
| data root | 同上: 未設定・空・相対・不在・ファイル・配布識別・凍結・予約領域・home・FS root・未markの既存データ・改変marker・symlink marker、marker作成と再利用、symlink・junction別名で同一ロック |
| ロック | 同上: 同一root排他・再取得・継承不可・lock非削除・別root独立 |
| 実プロセス | `tests/test_g09_startup_guard.py`: 6組合せの同一root拒否と無変更、別root並行、強制kill＋生存子で再取得、v1 activate中のkill後の再起動 |
| 起動・認証 | 同上: 実uvicorn＋実loopback HTTPでv1起動E2E（上記§4）、旧2モードはv1 404・DBなし・ロック保持 |
| 失敗/停止 | 同上: 拒否時にapp・ログ・DBを作らない、bind失敗、app構築失敗、create_app内の任意の失敗がactivate前に起きること（route登録・StaticFiles・bundle）、activateが最後の手順であること、uvicorn `Config`/`Server`/thread構築失敗、不正DB、WebView作成失敗、Ctrl+C、uvicorn起動失敗、停止しないサーバー、**shutdown拒否（3コード）でロックを保持**、`shutdown()`の結果code |
| 既存 | `test_backend_foundation_e2e.py`（再起動で`active_attempt`を返し旧queueは閉じたまま、を追補）、B7/B8/B9、UI-2 launcher |

**非対象（明記）:** 実モデル・GPU ASR、B4 smoke、製品S3-B2、実ユーザーDB restore、Windows手動WebView2/DPI/IME。CI成功を実機受入とは呼ばない。

## 7. 後続ゲート

G-09（本書、0.1.49）→ **B4 ready/smoke** → **新UIのv1投入接続**（S3-A/B1を`/api/v1`へ、409の解消） → **v1上のS3-B2** → 最終統合ゲート（Windows WebView2実機）。
