# Selfie AI Studio v1.2.3

Pinokio版 Stable Diffusion WebUI Forge を前提にした初期実用テスト版です。

## v1.2.3 モデル確認の自動化
- Project / Character適用時、Forgeモデル一覧が未取得なら自動で静かに取得
- 自動取得に成功した場合は「モデル未確認」確認を省略して、そのまま照合
- 自動取得に失敗した場合だけ従来の確認を表示
- 現在Forgeモデルも同時に取得し、モデル選択欄へ反映
- 生成は自動開始しない
- 外部API追加なし（ForgeローカルAPIのみ）
- 画像・モデル・LoRAの移動 / 削除 / 上書きなし

## v1.2.2 History Project紐付け修正
- Projectを開いた際のWorkspace保存を修正
  - 誤って存在しない `repo.save()` を呼んでいた箇所を `repo.save_all()` へ修正
- Project / Character IDをランタイム状態として先に保持
  - Workspace保存に失敗しても、その起動中のHistory紐付けは維持
- 生成ボタンを押した瞬間のProject / Character IDをスナップショット
  - バックグラウンド生成中に状態が変わっても、開始時のProjectへ登録
- 生成JSONにも `project_id` / `character_id` を保存
  - 将来の復旧・診断に利用可能
- 生成完了ステータスに紐付けProject名を表示
- 既存の「未設定」Historyは推測で勝手に書き換えない
- 画像ファイルの移動・削除・上書きなし
- API追加なし

## v1.2.1 History実用性改善
- History右側に選択画像のプレビューを追加
  - Tk標準機能のみ使用し、Pillow/API依存なし
- Projectを開いたIDをStudio実行中にも保持し、生成Historyへの紐付けを安定化
- Character IDも同様に保持
- Historyのモデル保存を改善
  - 生成タブ選択モデル
  - 現在のForgeモデル表示
  - Forge APIの現在モデル
  の順にフォールバック
- 一覧の評価を記号付き表示へ変更
  - ○ 未評価 / ★ 採用 / △ 仮採用 / ● 作業中 / × 不採用
- DB内の正式な評価値は変更しないため既存データ互換
- 画像の移動・削除・上書きなし
- API追加なし

## v1.2 History・採用管理 基礎版
- `History` DBを共通Dataへ追加
- Studioから新規生成した画像を自動でHistory登録
- 現在開いているProject / Characterへ自動紐付け
- 画像ファイルは従来のForge出力先に残し、勝手に移動・コピーしない
- Historyには画像パス / SHA-256 / Prompt / Negative / Model / LoRA / Sampler / Steps / CFG / Size / 日時を保存
- SHA-256または画像パスで重複登録を防止
- 評価：未評価 / 採用 / 仮採用 / 作業中 / 不採用
- 自由タグ・メモを保存
- Project / 評価 / キーワードで絞り込み
- 既存のStudio生成画像をHistory DBへ一括登録可能
- Historyから生成設定を生成タブへ復元可能
- History画像が外部で移動・削除された場合もDBは勝手に削除しない
- API追加なし。管理処理は完全ローカル

## v1.1 共通データ永続化
- Character / Project / Prompt Library / Preset / Workspace をアプリ本体から分離
- 初回起動時に共通データ保存先を選択
  - NAS上の任意フォルダを選択可能
  - 保存先は今後のStudioバージョンでも共通利用
- 選択したルート配下に `Data / Backups / Logs` を作成
- 保存先パスはWindowsの `%LOCALAPPDATA%\Selfie AI Studio\bootstrap.json` に保持
  - 新バージョンを別フォルダへ展開しても同じDataを参照
- Forge設定とLoRAお気に入りもアプリ本体から分離し、バージョン更新で消えない構成へ変更
- 初回のみ旧版 `studio_data` からコピー移行可能
  - 旧版データは削除・移動・上書きしない
- バージョン変更時、共通Dataを `Backups` へZIPバックアップ
- 保存先が利用できない場合、勝手に別場所へ切り替えず選択を求める
- NASへのモデル・画像コピー機能はまだ行わない

## v1.0.1 Project開始導線改善
- 「Projectを開く」後、自動で生成タブへ移動
- 生成タブ上部に「現在のProject」表示を追加
- Project / Character / Model / LoRA / 保存先を一行で確認可能
- Project反映後も画像生成は自動開始しない
- 既存のCharacter安全適用ロジックはそのまま維持

## v1.0 Project Manager 基礎版
- Project専用タブ
- Projectの新規登録 / 編集
- Project名 / Character / 保存先 / 状態 / メモ を保存
- 検索 / 状態絞り込み / お気に入り
- 保存先はフォルダ選択ダイアログから指定可能
- Projectを開くと、紐付けCharacterの設定を生成タブへ復元
- Character側の安全なモデル確認 / LoRA不足処理をProjectからも再利用
- Workspaceに現在Project / Characterをローカル記録
- Projectを開いても自動生成は開始しない
- 採用画像DB / 最近生成のProject紐付け / Preset連携は次段階へ分離

## v0.9.5 モデル候補承認の学習
- Character適用時、保存モデルとForge側候補が同名系で完全一致しない場合の確認を改善
- 候補を「はい」で承認した場合
  - その候補を今回の適用に使用
  - Character DBの保存モデル名をForge側の正式表記へ更新
  - 次回以降は完全一致になり、同じ候補確認を繰り返さない
- 「いいえ」の場合はCharacter DBを変更しない
- モデルのコピー / 移動 / 削除は行わない
- 自動生成は行わない

## v0.9.4 Character適用安全化
- Character一覧へ「状態」を追加
  - 準備完了 / 要確認 / 一部不足 / モデル不足 / 未確認
- 「Character適用時にモデルもForgeへ切り替える」チェックを追加
  - 初期値OFF
  - ON時のみForgeモデルを切り替える
- 保存モデルがForge側に無い場合
  - 警告を表示
  - モデルをスキップして他設定だけ適用、またはキャンセルを選択
- 同名モデルだがハッシュ等が異なる候補がある場合
  - 候補を表示し、使用するか確認
- 不足LoRAは勝手に代替せずスキップし、ステータスに表示
- モデルやLoRAのコピー / 移動 / 削除は一切行わない
- Character適用だけでは画像生成を開始しない

## v0.9.3 Sampler取得・復元修正
- Characterの「現在の生成設定を取得」でSamplerが空になる問題を修正
- 原因：生成タブの実体 `self.sampler` に対し、Character側が誤って `self.sampler_var` を参照していた
- Characterから生成タブへ適用する際のSampler復元も同時修正
- Model / LoRA / Prompt / Negative / Steps / CFG / Size の挙動は変更なし
- 自動生成は行わない

## v0.9.2 Characterモデル取得修正
- 「現在の生成設定を取得」でモデル欄が空になる問題を修正
- Forge/UI側の現在モデル表示を優先して取得
- 取得できない場合はStudioのモデル選択欄へフォールバック
- 取得後ステータスにモデル名を表示
- LoRA / Prompt / Negative / Sampler / Steps / CFG / Size の既存挙動は変更しない
- 自動生成・自動Forgeモデル切替は引き続き行わない

## v0.9.1 Character入力省力化
- 「現在の生成設定を取得」を追加
  - Model / LoRA / Prompt / Negative / Sampler / Steps / CFG / Width / Height を生成タブから一括取得
  - Character名は保持
  - 取得だけでは保存・生成しない
- 「PNGから取得」を追加
  - 既存のローカルPNGメタデータ解析を再利用
  - Prompt / Negative / Model / LoRA / Sampler / Steps / CFG / Size をCharacterフォームへ反映
  - API・外部通信なし
- 「現在の生成設定で更新」を追加
  - 選択中Characterを現在の生成設定で更新
  - Character名 / お気に入り / メモ / マスター参照は保持
- 自動生成・自動Forgeモデル切替は行わない

## v0.9 Character Manager 基礎版
- Character専用タブ
- キャラクターの新規登録 / 編集
- 名前 / 推奨モデル / LoRA / 基本Prompt / 基本Negative を保存
- Sampler / Steps / CFG / Width / Height をキャラごとに保存
- 検索 / お気に入り
- ダブルクリックまたはボタンで生成タブへ設定を復元
- LoRAはStudioのmanaged LoRAへ復元
- Character適用時は自動生成しない
- Modelは選択欄へ反映するが、Forgeへのモデル切替は自動実行しない
- 採用画像 / マスター比較 / 自動Character推測は次段階へ分離

## v0.8.5.1 起動修正
- Pillow（PIL）依存を撤廃
- PNGメタデータ読込をPython標準ライブラリのみで実装
- `tEXt` / `zTXt` / `iTXt` をローカル解析
- 追加インストール不要
- API・外部通信なし

## v0.8.5 PNGメタデータ取込
- Prompt Libraryに「PNGを開く」を追加
- Forge / AUTOMATIC1111系PNGの `parameters` を完全ローカルで読込
- Prompt / Negative Prompt を自動入力
- Model / Sampler / Schedule / Steps / CFG / Seed / Size / VAE を表示
- Prompt内の `<lora:名前:Weight>` を検出して表示
- 内容確認後に「保存」を押す方式。自動登録・自動生成はしない
- 生成情報が無い画像は明示
- API・外部通信は使用しない
- 既存画像を変更・上書きしない
- ドラッグ＆ドロップは次段階へ分離

## v0.8 Prompt Library 基礎版
- Prompt Library専用タブ
- Prompt / Negative の手入力登録
- 名前 / カテゴリ / タグで整理
- 全文検索
- カテゴリ絞り込み
- お気に入り登録 / お気に入りのみ表示
- ダブルクリックまたはボタンで生成タブへ呼び出し
- `studio_data/prompt_library.json` にローカル保存
- Studio Coreの原子的JSON保存・`.bak` バックアップを利用
- Prompt Libraryから読み込んでも自動生成は開始しない
- PNGメタデータ取込 / JSON一括インポートは次段階へ分離

## v0.7.1 LoRA同期（修正版）
- v0.7配布時に `app.py` がv0.6のまま残っていたビルド不具合を修正
- タイトル・実装・READMEの3点を自動検証してからZIP化
- LoRAはStudio内部状態でON/OFF管理
- 同一LoRAは1件のみ保持、Weight変更は更新
- Prompt欄からLoRAタグを除去し、生成直前だけ内部合成

## v0.6 Studio Core
- Character DB (`studio_data/characters.json`)
- Project DB (`studio_data/projects.json`)
- Preset DB (`studio_data/presets.json`)
- Workspace DB (`studio_data/workspace.json`)
- 一意ID付きの追加・更新API
- JSONの原子的保存（temp → 検証 → backup → replace）
- 直前版を `.bak` として保持
- Core状態確認タブ
- 既存モデル / LoRA / outputs は移動・削除・変更しない
- NASへの自動書き込みはまだ行わない

## v0.5で追加したもの
- LoRA専用タブ
- Forgeローカル `models\Lora` の自動スキャン
- LoRA検索
- 複数選択
- Weight指定
- `<lora:名前:Weight>` を生成Promptへ自動追加
- LoRAお気に入り登録 / お気に入りのみ表示
- お気に入りは `lora_favorites.json` に保存
- LoRA操作だけでは画像生成を開始しない安全設計

## v0.4.1で修正したもの
- Windows起動ランチャーを最小構成に変更
- `Start_Selfie_AI_Studio.cmd` を追加
- `start_windows.bat` も同じ単純な起動方式へ変更
- 起動できない時用に `Start_Diagnostic.cmd` を追加
- ランチャーをASCII + CRLFで保存し、cmd.exeの文字コード・改行影響を避ける

## v0.4で追加したもの
- 「Studio履歴」タブ
- Studioから生成した画像とJSON設定を自動で一覧化
- 履歴から Prompt / Negative / Steps / CFG / Size / Sampler / Model を生成タブへ復元
- 履歴画像をWindowsで開く
- 生成直後にStudio履歴を自動更新
- ダブルクリック起動を安定させるため、GUI用Pythonランチャーを優先

## v0.3で追加したもの
- 現在のForgeモデル表示
- Studioからモデルを切り替える「モデル適用」ボタン
- モデル切替後にForgeの現在モデルを再取得して確認
- モデル一覧取得時に現在モデルを可能な範囲で自動選択

## v0.2までの機能
- Pinokio版Forgeの既定パスを自動入力
- Forgeフォルダ構成の環境診断
- Stable-diffusion / LoRA / VAE のファイル数確認
- Forge API接続確認
- Forge APIからモデル一覧取得
- Sampler一覧取得
- txt2img生成
- 最近の生成画像を最大100件表示
- NASモデル保管場所の登録と一覧表示
- 外部ライブラリ不要（Python標準ライブラリのみ）

## 重要
v0.3でもNAS内ファイルを移動・削除・上書きしません。
NAS→Forgeへの自動コピーはまだ無効です。
モデル管理の安全性を実機確認した後に追加します。

## 起動
`Start_Selfie_AI_Studio.cmd` をダブルクリックしてください。

起動できない場合は、Pythonの確認をこちらと一緒に行います。

## Forge既定パス
C:\pinokio\api\stable-diffusion-webui-forge.git\app

## Forge API
既定:
http://127.0.0.1:7860

Forgeが起動しているのに「未接続」になる場合、Forge側でAPI有効化が必要です。
その設定は環境に合わせて一緒に確認します。

## 次の予定
- NAS→Forgeモデルの安全なワンクリック配置
- お気に入り
- モデルカード/サムネイル
- LoRA管理
- プロジェクト/採用管理
- サブPC生成サーバー対応
