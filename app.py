from __future__ import annotations

import hashlib
import json
import pathlib
import time
import uuid

import pandas as pd
import streamlit as st

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:  # App still runs for manual entry when the optional SDK is absent.
    genai = None
    types = None
    GENAI_AVAILABLE = False

from sake_brix_core import (
    COLUMNS_29,
    calculate_statistics,
    connect_db,
    export_records,
    get_jst_now,
    init_db,
    normalize_import_records,
    restore_records,
    save_measurement,
)

# -----------------------------------------------------------------------------
# Page and database startup
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="日本酒 糖度測定＆総合スペック管理ツール",
    page_icon="🍶",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    @media (max-width: 768px) {
        h1 { font-size: 1.5rem !important; }
        h2 { font-size: 1.25rem !important; }
        h3 { font-size: 1.1rem !important; }
        .stTextInput input, .stNumberInput input, .stSelectbox, .stTextArea textarea {
            font-size: 16px !important;
        }
        .stButton button { width: 100%; font-size: 1rem !important; padding: 0.6rem !important; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

BASE_DIR = pathlib.Path(__file__).resolve().parent
DB_NAME = BASE_DIR / "sake_brix.db"
SPEC_OPTIONS = [
    "未選択", "純米酒", "特別純米酒", "純米吟醸", "吟醸酒",
    "純米大吟醸", "大吟醸", "本醸造", "普通酒", "その他",
]
MODEL_DEFAULT = "gemini-3.8-flash"

try:
    DB_STARTUP_STATUS = init_db(DB_NAME)
except Exception as exc:
    st.error(
        "データベースの初期化・移行を安全に完了できなかったため、アプリを停止しました。\n\n"
        f"詳細: {exc}\n\n"
        "元のDBファイルと自動バックアップを削除せず、まずDBファイルのコピーを保管してください。"
    )
    st.stop()

if DB_STARTUP_STATUS.get("migrated"):
    st.sidebar.success(
        f"DB移行完了: {DB_STARTUP_STATUS.get('migrated_count', 0)}件\n"
        f"バックアップ: {DB_STARTUP_STATUS.get('backup_path')}"
    )

# -----------------------------------------------------------------------------
# Session state defaults. All widgets use their keys as the source of truth.
# Callbacks update keys before the next render to avoid stale form values.
# -----------------------------------------------------------------------------
FORM_TEXT_DEFAULTS = {
    "input_brewery": "",
    "input_name": "",
    "input_by": "",
    "input_lot": "",
    "input_state": "",
    "input_rice": "",
    "input_yeast": "",
    "input_memo": "",
}
FORM_OTHER_DEFAULTS = {
    "input_spec": "未選択",
    "candidate_select_box": 0,
    "selected_candidate": {},
    "ai_candidates": [],
    "current_image_hash": None,
    "last_inference_time": None,
    "save_notice": None,
    "db_notice": None,
    "restore_notice": None,
}
for key, default in {**FORM_TEXT_DEFAULTS, **FORM_OTHER_DEFAULTS}.items():
    if key not in st.session_state:
        st.session_state[key] = default

for i in range(1, 7):
    st.session_state.setdefault(f"chk_m{i}", False)
    st.session_state.setdefault(f"val_m{i}", 0.0)

NUMERIC_FORM_DEFAULTS = {
    "chk_alc": False, "val_alc": 15.0,
    "chk_smv": False, "val_smv": 0.0,
    "chk_acid": False, "val_acid": 1.3,
    "chk_amino": False, "val_amino": 1.1,
    "chk_rp": False, "val_rp": 60.0,
}
for key, default in NUMERIC_FORM_DEFAULTS.items():
    st.session_state.setdefault(key, default)


def _clean_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _candidate_float(value):
    if value is None or value == "":
        return None
    try:
        result = float(value)
        return result if pd.notna(result) else None
    except (TypeError, ValueError):
        return None


def set_form_from_candidate(candidate: dict) -> None:
    """Set real widget keys before widgets are rendered for the current run."""
    text_map = {
        "input_brewery": "brewery",
        "input_name": "name",
        "input_by": "by_year",
        "input_lot": "lot_date",
        "input_state": "processing_state",
        "input_rice": "rice_used",
        "input_yeast": "yeast",
    }
    for widget_key, candidate_key in text_map.items():
        st.session_state[widget_key] = _clean_text(candidate.get(candidate_key))

    category = _clean_text(candidate.get("spec_category"))
    st.session_state["input_spec"] = category if category in SPEC_OPTIONS else ("その他" if category else "未選択")

    numeric_map = {
        "alcohol": ("chk_alc", "val_alc", 15.0, 0.0, 30.0),
        "smv": ("chk_smv", "val_smv", 0.0, -30.0, 30.0),
        "acidity": ("chk_acid", "val_acid", 1.3, 0.0, 5.0),
        "amino_acid": ("chk_amino", "val_amino", 1.1, 0.0, 5.0),
        "rice_polished": ("chk_rp", "val_rp", 60.0, 0.0, 100.0),
    }
    for candidate_key, (check_key, value_key, default_value, minimum, maximum) in numeric_map.items():
        value = _candidate_float(candidate.get(candidate_key))
        if value is not None and not minimum <= value <= maximum:
            value = None  # Ignore implausible AI readings instead of breaking a number_input.
        st.session_state[check_key] = value is not None
        st.session_state[value_key] = value if value is not None else default_value


def apply_selected_candidate_callback() -> None:
    candidates = st.session_state.get("ai_candidates", [])
    index = st.session_state.get("candidate_select_box", 0)
    if not candidates:
        st.session_state["selected_candidate"] = {}
        return
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = 0
    index = min(max(index, 0), len(candidates) - 1)
    selected = candidates[index]
    st.session_state["selected_candidate"] = selected
    set_form_from_candidate(selected)
    st.session_state["db_notice"] = None
    st.session_state["candidate_apply_notice"] = f"候補 {index + 1} をフォームへ反映しました。内容を確認してください。"


def reset_entry_form_state() -> None:
    """Called from the submit callback before widgets are recreated.

    Notice keys are intentionally not reset here; the success/error message
    must survive into the next render.
    """
    for key, default in FORM_TEXT_DEFAULTS.items():
        st.session_state[key] = default
    st.session_state["input_spec"] = "未選択"
    st.session_state["candidate_select_box"] = 0
    st.session_state["selected_candidate"] = {}
    st.session_state["ai_candidates"] = []
    st.session_state["current_image_hash"] = None
    st.session_state["last_inference_time"] = None
    for i in range(1, 7):
        st.session_state[f"chk_m{i}"] = False
        st.session_state[f"val_m{i}"] = 0.0
    for key, default in NUMERIC_FORM_DEFAULTS.items():
        st.session_state[key] = default


def save_new_record_callback() -> None:
    """Read submitted form values, save atomically, and reset only on success."""
    brewery = _clean_text(st.session_state.get("input_brewery"))
    name = _clean_text(st.session_state.get("input_name"))
    if not brewery or not name:
        st.session_state["save_notice"] = ("error", "蔵元名と銘柄名は必須です。入力値は保持されています。")
        return

    measurements: list[float | None] = []
    try:
        for i in range(1, 7):
            if st.session_state.get(f"chk_m{i}", False):
                value = float(st.session_state.get(f"val_m{i}", 0.0))
                if not 0.0 <= value <= 50.0:
                    raise ValueError(f"測定{i}の糖度は0.00〜50.00の範囲で入力してください。")
                measurements.append(value)
            else:
                measurements.append(None)
        stats = calculate_statistics(measurements)
    except Exception as exc:
        st.session_state["save_notice"] = ("error", f"糖度測定値を確認してください: {exc} 入力値は保持されています。")
        return

    optional_values = {
        "alcohol": float(st.session_state["val_alc"]) if st.session_state.get("chk_alc") else None,
        "smv": float(st.session_state["val_smv"]) if st.session_state.get("chk_smv") else None,
        "acidity": float(st.session_state["val_acid"]) if st.session_state.get("chk_acid") else None,
        "amino_acid": float(st.session_state["val_amino"]) if st.session_state.get("chk_amino") else None,
        "rice_polished": float(st.session_state["val_rp"]) if st.session_state.get("chk_rp") else None,
    }
    # Avoid saving out-of-range values even if state was modified outside widgets.
    limits = {
        "alcohol": (0, 30), "smv": (-30, 30), "acidity": (0, 5),
        "amino_acid": (0, 5), "rice_polished": (0, 100),
    }
    for field, value in optional_values.items():
        if value is not None and not limits[field][0] <= value <= limits[field][1]:
            st.session_state["save_notice"] = ("error", f"{field} の入力値が許容範囲外です。入力値は保持されています。")
            return

    record = {
        "id": str(uuid.uuid4()),
        "recorded_at": get_jst_now(),
        "brewery": brewery,
        "name": name,
        "by_year": _clean_text(st.session_state.get("input_by")) or None,
        "lot_date": _clean_text(st.session_state.get("input_lot")) or None,
        "spec_category": st.session_state.get("input_spec", "未選択"),
        "processing_state": _clean_text(st.session_state.get("input_state")) or None,
        "brix": stats["brix"],
        **{f"m{i}": measurements[i - 1] for i in range(1, 7)},
        **stats,
        **optional_values,
        "rice_used": _clean_text(st.session_state.get("input_rice")) or None,
        "yeast": _clean_text(st.session_state.get("input_yeast")) or None,
        "memo": _clean_text(st.session_state.get("input_memo")) or None,
    }
    try:
        saved_id = save_measurement(DB_NAME, record)
        st.session_state["save_notice"] = (
            "success",
            f"保存しました: {brewery} — {name} / 平均糖度 {stats['mean']:.2f}°Bx (ID: {saved_id[:8]}…)" ,
        )
        # The callback runs before the new render, so assigning widget keys here is safe.
        reset_entry_form_state()
    except Exception as exc:
        st.session_state["save_notice"] = ("error", f"保存に失敗しました: {exc} 入力値は保持されています。")




def delete_record_callback() -> None:
    record_id = st.session_state.get("selected_record_id")
    if not record_id:
        st.session_state["db_notice"] = ("error", "削除対象が選択されていません。")
        return
    conn = connect_db(DB_NAME)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("DELETE FROM measurements WHERE id = ?", (record_id,))
        if cursor.rowcount != 1:
            raise RuntimeError("対象レコードが見つからないか、すでに削除されています。")
        conn.commit()
        st.session_state["db_notice"] = ("success", "レコードを削除しました。")
        st.session_state["confirm_delete_record"] = False
        st.session_state["selected_record_id"] = ""
    except Exception as exc:
        conn.rollback()
        st.session_state["db_notice"] = ("error", f"削除エラー: {exc}")
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Sidebar: AI settings and JSON backup
# -----------------------------------------------------------------------------
def get_secret(name: str, fallback=None):
    try:
        return st.secrets.get(name, fallback)
    except Exception:
        return fallback

if "api_key_input" not in st.session_state:
    st.session_state["api_key_input"] = get_secret("GEMINI_API_KEY", "") or ""
if "model_name_input" not in st.session_state:
    st.session_state["model_name_input"] = get_secret("GEMINI_MODEL", MODEL_DEFAULT) or MODEL_DEFAULT

st.sidebar.title("⚙️ 設定・API管理")
api_key = st.sidebar.text_input("Gemini API Key", type="password", key="api_key_input", placeholder="APIキーを入力")
model_name = st.sidebar.text_input("Geminiモデル名", key="model_name_input", help="利用可能なモデル名を指定してください。")

client = None
if not GENAI_AVAILABLE:
    st.sidebar.warning("google-genai がインストールされていないため、AI解析は無効です。手入力・DB機能は利用できます。")
elif _clean_text(api_key):
    try:
        client = genai.Client(api_key=_clean_text(api_key))
        st.sidebar.caption("APIクライアントを初期化しました。通信成功は接続テストで確認してください。")
    except Exception as exc:
        st.sidebar.error(f"APIクライアント初期化エラー: {exc}")
else:
    st.sidebar.info("Secrets またはサイドバーからAPIキーを設定してください。")

if st.sidebar.button("🔌 API接続テスト", key="api_connection_test"):
    if client is None:
        st.sidebar.error("APIキーまたは google-genai SDK が利用できません。")
    else:
        try:
            with st.spinner("APIへ接続しています…"):
                client.models.generate_content(model=_clean_text(model_name) or MODEL_DEFAULT, contents="Reply with OK.")
            st.sidebar.success(f"API通信成功 ({get_jst_now()})")
        except Exception as exc:
            st.sidebar.error(f"API通信に失敗しました: {exc}")

st.sidebar.markdown("---")
st.sidebar.subheader("📦 データバックアップ")
try:
    export_payload = json.dumps(export_records(DB_NAME), ensure_ascii=False, indent=2, allow_nan=False)
    st.sidebar.download_button(
        "💾 JSONバックアップをダウンロード",
        data=export_payload.encode("utf-8"),
        file_name=f"sake_brix_backup_{time.strftime('%Y%m%d')}.json",
        mime="application/json",
        key="download_json_backup",
    )
except Exception as exc:
    st.sidebar.error(f"JSONバックアップの作成に失敗しました: {exc}")

# -----------------------------------------------------------------------------
# Main navigation
# -----------------------------------------------------------------------------
st.title("🍶 日本酒 糖度測定＆総合スペック管理ツール")
menu = st.radio(
    "メニュー",
    ["📝 新規測定・入力", "📊 保存データ一覧・検索・編集", "⚖️ データ比較", "🔄 バックアップ・復元"],
    horizontal=True,
    key="main_menu",
)

# -----------------------------------------------------------------------------
# Screen 1: entry + image analysis
# -----------------------------------------------------------------------------
if menu == "📝 新規測定・入力":
    st.header("📝 新規測定・スペック入力")

    notice = st.session_state.pop("save_notice", None)
    if notice:
        (st.success if notice[0] == "success" else st.error)(notice[1])
    candidate_notice = st.session_state.pop("candidate_apply_notice", None)
    if candidate_notice:
        st.success(candidate_notice)

    with st.expander("📷 ラベル撮影・画像アップロード（AI自動解析）", expanded=True):
        st.write("スマートフォンのカメラ撮影、またはフォトライブラリからラベル画像を選択してください。")
        uploaded_image = st.file_uploader(
            "ラベル画像 (JPEG / PNG)", type=["jpg", "jpeg", "png"], key="label_image"
        )
        if uploaded_image is not None:
            image_bytes = uploaded_image.getvalue()
            image_hash = hashlib.sha256(image_bytes).hexdigest()
            if st.session_state.get("current_image_hash") != image_hash:
                st.session_state["current_image_hash"] = image_hash
                st.session_state["ai_candidates"] = []
                st.session_state["selected_candidate"] = {}
                st.session_state["candidate_select_box"] = 0
                st.session_state["last_inference_time"] = None
            st.image(image_bytes, caption="アップロードされたラベル", use_container_width=True)

            if st.button("🤖 このラベルをAIで解析する", key="analyze_label"):
                if client is None:
                    st.error("有効なGemini APIクライアントがありません。APIキーとSDKを確認してください。")
                else:
                    # JSON Schema follows the Python Gen AI SDK's JSON Schema option.
                    nullable_string = {"anyOf": [{"type": "string"}, {"type": "null"}]}
                    nullable_number = {"anyOf": [{"type": "number"}, {"type": "null"}]}
                    response_schema = {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "brewery": nullable_string,
                                "name": nullable_string,
                                "spec_category": nullable_string,
                                "processing_state": nullable_string,
                                "by_year": nullable_string,
                                "lot_date": nullable_string,
                                "alcohol": nullable_number,
                                "smv": nullable_number,
                                "acidity": nullable_number,
                                "amino_acid": nullable_number,
                                "rice_polished": nullable_number,
                                "rice_used": nullable_string,
                                "yeast": nullable_string,
                            },
                            "required": [
                                "brewery", "name", "spec_category", "processing_state", "by_year",
                                "lot_date", "alcohol", "smv", "acidity", "amino_acid",
                                "rice_polished", "rice_used", "yeast",
                            ],
                            "additionalProperties": False,
                        },
                    }
                    prompt = (
                        "画像に実際に写っている日本酒ラベルの情報を読み取ってください。"
                        "画像にラベルが複数ある場合はラベルごとに個別のオブジェクトを返してください。"
                        "不明または読み取れない項目はnullにしてください。推測で値を補わないでください。"
                    )
                    started = time.perf_counter()
                    try:
                        try:
                            config = types.GenerateContentConfig(
                                response_mime_type="application/json",
                                response_json_schema=response_schema,
                                thinking_config=types.ThinkingConfig(thinking_level="low"),
                            )
                        except Exception:
                            try:
                                config = types.GenerateContentConfig(
                                    response_mime_type="application/json",
                                    response_json_schema=response_schema,
                                )
                            except Exception:
                                try:
                                    config = types.GenerateContentConfig(
                                        response_mime_type="application/json",
                                        response_schema=response_schema,
                                    )
                                except Exception:
                                    config = types.GenerateContentConfig(response_mime_type="application/json")
                                    st.warning("使用中のSDKがJSON Schema設定に対応していないため、JSON形式指定のみで解析します。google-genaiを更新してください。")
                        response = client.models.generate_content(
                            model=_clean_text(model_name) or MODEL_DEFAULT,
                            contents=[
                                types.Part.from_bytes(data=image_bytes, mime_type=uploaded_image.type or "image/jpeg"),
                                prompt,
                            ],
                            config=config,
                        )
                        response_text = getattr(response, "text", None)
                        if not response_text:
                            raise ValueError("モデルから空の応答が返されました。")
                        parsed = json.loads(response_text.strip())
                        if isinstance(parsed, dict):
                            parsed = [parsed]
                        if not isinstance(parsed, list):
                            raise ValueError("AIの応答がJSON配列ではありません。")
                        candidates = [
                            item for item in parsed
                            if isinstance(item, dict) and (_clean_text(item.get("brewery")) or _clean_text(item.get("name")))
                        ]
                        st.session_state["ai_candidates"] = candidates
                        st.session_state["candidate_select_box"] = 0
                        st.session_state["last_inference_time"] = round(time.perf_counter() - started, 2)
                        if candidates:
                            st.session_state["selected_candidate"] = candidates[0]
                            # The form is rendered later in this same run, so these assignments occur first.
                            set_form_from_candidate(candidates[0])
                            st.success(f"解析完了: {len(candidates)}件 ({st.session_state['last_inference_time']:.2f}秒)。候補を確認してください。")
                        else:
                            st.session_state["selected_candidate"] = {}
                            st.info("ラベル候補を検出できませんでした。手入力してください。")
                    except Exception as exc:
                        st.error(f"AI解析に失敗しました: {exc}")

    candidates = st.session_state.get("ai_candidates", [])
    if candidates:
        st.markdown("---")
        st.subheader("🎯 検出されたラベルの選択")
        candidate_labels = [
            f"{i + 1}. {c.get('brewery') or '蔵元不明'} — {c.get('name') or '銘柄不明'} ({c.get('spec_category') or '特定名称不明'})"
            for i, c in enumerate(candidates)
        ]
        st.selectbox(
            "反映するラベルを選択",
            options=list(range(len(candidates))),
            format_func=lambda idx: candidate_labels[idx],
            key="candidate_select_box",
        )
        st.button(
            "📥 選択した候補をフォームに反映",
            on_click=apply_selected_candidate_callback,
            key="apply_candidate_button",
        )
        if st.session_state.get("last_inference_time") is not None:
            st.caption(f"直近の解析時間: {st.session_state['last_inference_time']:.2f}秒")

    st.markdown("---")
    with st.form("entry_form", clear_on_submit=False):
        st.subheader("Step 1: 蔵元名・銘柄名")
        col1, col2 = st.columns(2)
        with col1:
            st.text_input("蔵元名 *", key="input_brewery")
        with col2:
            st.text_input("銘柄名・商品名 *", key="input_name", help="例: 〇〇 特別純米 無濾過生原酒")

        st.subheader("Step 2: 糖度の実測値（最大6回・0.01°Bx単位）")
        st.caption("実測した枠にだけチェックを入れてください。数値0.00も、チェックした場合だけ実測値として扱います。")
        measurement_columns = st.columns(3)
        for i in range(1, 7):
            with measurement_columns[(i - 1) % 3]:
                st.checkbox(f"測定 {i} を有効にする", key=f"chk_m{i}")
                # Keep enabled at all times: widgets inside a form do not rerun dynamically.
                st.number_input(
                    f"測定 {i} (°Bx)", min_value=0.0, max_value=50.0,
                    step=0.01, format="%.2f", key=f"val_m{i}",
                )

        st.subheader("Step 3: スペック詳細・ロット情報")
        col3, col4, col5 = st.columns(3)
        with col3:
            st.selectbox("特定名称", SPEC_OPTIONS, key="input_spec")
        with col4:
            st.text_input("製法・状態", key="input_state", help="例: 無濾過生原酒、直汲み、火入れ 等")
        with col5:
            st.text_input("醸造年度 (BY)", key="input_by", placeholder="例: 2025BY")
        st.text_input("製造年月 / ロット", key="input_lot", placeholder="例: 2026.04")

        st.subheader("Step 4: 詳細スペック（未入力はNULL保存）")
        st.caption("チェックを入れた項目だけ保存します。チェック操作に伴うフォーム再描画を必要としないよう、数値欄は常に編集できます。")
        col6, col7, col8 = st.columns(3)
        with col6:
            st.checkbox("アルコール度数を記録", key="chk_alc")
            st.number_input("アルコール度数 (%)", min_value=0.0, max_value=30.0, step=0.1, format="%.1f", key="val_alc")
            st.checkbox("酸度を記録", key="chk_acid")
            st.number_input("酸度", min_value=0.0, max_value=5.0, step=0.1, format="%.1f", key="val_acid")
        with col7:
            st.checkbox("日本酒度を記録", key="chk_smv")
            st.number_input("日本酒度", min_value=-30.0, max_value=30.0, step=0.5, format="%.1f", key="val_smv")
            st.checkbox("アミノ酸度を記録", key="chk_amino")
            st.number_input("アミノ酸度", min_value=0.0, max_value=5.0, step=0.1, format="%.1f", key="val_amino")
        with col8:
            st.checkbox("精米歩合を記録", key="chk_rp")
            st.number_input("精米歩合 (%)", min_value=0.0, max_value=100.0, step=1.0, format="%.1f", key="val_rp")
            st.text_input("使用米", key="input_rice")
        st.text_input("使用酵母", key="input_yeast")
        st.text_area("テイスティングメモ・感想", key="input_memo")

        st.form_submit_button(
            "💾 測定データを保存する",
            type="primary",
            on_click=save_new_record_callback,
        )

# -----------------------------------------------------------------------------
# Screen 2: database list, search, edit, delete
# -----------------------------------------------------------------------------
elif menu == "📊 保存データ一覧・検索・編集":
    st.header("📊 保存データ一覧・検索・編集")
    db_notice = st.session_state.pop("db_notice", None)
    if db_notice:
        (st.success if db_notice[0] == "success" else st.error)(db_notice[1])

    search_query = st.text_input("🔍 蔵元名・銘柄名・メモを部分一致検索", key="search_query")
    start_time = time.perf_counter()
    escaped = search_query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    pattern = f"%{escaped}%"
    conn = connect_db(DB_NAME)
    try:
        if search_query:
            where = "(COALESCE(brewery, '') LIKE ? ESCAPE '!' OR COALESCE(name, '') LIKE ? ESCAPE '!' OR COALESCE(memo, '') LIKE ? ESCAPE '!')"
            params = (pattern, pattern, pattern)
            total_count = conn.execute(f"SELECT COUNT(*) FROM measurements WHERE {where}", params).fetchone()[0]
            df = pd.read_sql_query(
                f"SELECT * FROM measurements WHERE {where} ORDER BY recorded_at DESC LIMIT 100", conn, params=params
            )
        else:
            total_count = conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
            df = pd.read_sql_query("SELECT * FROM measurements ORDER BY recorded_at DESC LIMIT 100", conn)
    except Exception as exc:
        st.error(f"検索エラー: {exc}")
        total_count = 0
        df = pd.DataFrame()
    finally:
        conn.close()
    elapsed = time.perf_counter() - start_time
    st.caption(f"検索処理 {elapsed:.4f}秒 / 該当総数 {total_count}件 / 表示 {len(df)}件（最大100件）")
    if total_count > 100:
        st.info("現在は先頭100件を表示しています。全件が表示されているわけではありません。")

    if df.empty:
        st.info("該当するデータがありません。")
    else:
        display_columns = [
            col for col in ["recorded_at", "brewery", "name", "brix", "mean", "spec_category", "processing_state", "alcohol", "smv", "acidity"]
            if col in df.columns
        ]
        st.dataframe(df[display_columns], use_container_width=True, hide_index=True)

        id_to_row = {str(row["id"]): row for _, row in df.iterrows()}
        available_ids = list(id_to_row.keys())
        # Reset the selected widget value before creating the widget if filtering
        # removed the previously selected record.
        if st.session_state.get("selected_record_id") not in available_ids:
            st.session_state["selected_record_id"] = available_ids[0]
        selected_id = st.selectbox(
            "編集・削除するレコード",
            options=available_ids,
            format_func=lambda record_id: (
                f"{id_to_row[record_id]['recorded_at']} | {id_to_row[record_id]['brewery']} — "
                f"{id_to_row[record_id]['name']} ({id_to_row[record_id]['mean']}°Bx)"
            ),
            key="selected_record_id",
        )
        if st.session_state.get("last_selected_record_id") != selected_id:
            # Do not leave a confirmation checked when the target record changes.
            st.session_state["confirm_delete_record"] = False
            st.session_state["last_selected_record_id"] = selected_id
        target = id_to_row[selected_id]

        col_delete, col_edit = st.columns(2)
        with col_delete:
            st.checkbox("このレコードを削除することを確認", key="confirm_delete_record")
            st.button(
                "🗑️ 選択したレコードを削除",
                key="delete_record_button",
                disabled=not st.session_state.get("confirm_delete_record", False),
                on_click=delete_record_callback,
            )

        with col_edit:
            edit_mode = st.toggle("📝 選択レコードを編集", key="edit_mode")

        if edit_mode:
            suffix = hashlib.sha256(str(selected_id).encode("utf-8")).hexdigest()[:10]
            def row_missing(key):
                value = target.get(key)
                return value is None or pd.isna(value)
            def row_text(key):
                return "" if row_missing(key) else str(target[key])
            def row_float(key, default=0.0):
                return default if row_missing(key) else float(target[key])

            with st.form(f"edit_form_{suffix}"):
                st.subheader(f"編集: {target['brewery']} — {target['name']}")
                ec1, ec2 = st.columns(2)
                with ec1:
                    e_brewery = st.text_input("蔵元名 *", value=row_text("brewery"), key=f"edit_{suffix}_brewery")
                    e_name = st.text_input("銘柄名 *", value=row_text("name"), key=f"edit_{suffix}_name")
                    e_by = st.text_input("醸造年度 (BY)", value=row_text("by_year"), key=f"edit_{suffix}_by")
                    e_lot = st.text_input("製造年月 / ロット", value=row_text("lot_date"), key=f"edit_{suffix}_lot")
                    current_spec = row_text("spec_category") or "未選択"
                    e_spec = st.selectbox("特定名称", SPEC_OPTIONS, index=(SPEC_OPTIONS.index(current_spec) if current_spec in SPEC_OPTIONS else 0), key=f"edit_{suffix}_spec")
                    e_state = st.text_input("製法・状態", value=row_text("processing_state"), key=f"edit_{suffix}_state")
                with ec2:
                    e_rice = st.text_input("使用米", value=row_text("rice_used"), key=f"edit_{suffix}_rice")
                    e_yeast = st.text_input("使用酵母", value=row_text("yeast"), key=f"edit_{suffix}_yeast")
                    e_memo = st.text_area("メモ", value=row_text("memo"), key=f"edit_{suffix}_memo")

                st.markdown("**スペック数値（チェックなしはNULL）**")
                en1, en2, en3 = st.columns(3)
                numeric_edit_cfg = [
                    ("alcohol", "アルコール度数 (%)", 30.0, 0.1, 15.0),
                    ("smv", "日本酒度", 30.0, 0.5, 0.0),
                    ("acidity", "酸度", 5.0, 0.1, 1.3),
                    ("amino_acid", "アミノ酸度", 5.0, 0.1, 1.1),
                    ("rice_polished", "精米歩合 (%)", 100.0, 1.0, 60.0),
                ]
                e_numeric: dict[str, float | None] = {}
                for idx, (field, label, maxval, step, default_num) in enumerate(numeric_edit_cfg):
                    container = [en1, en2, en3][idx % 3]
                    with container:
                        existing = None if row_missing(field) else float(target[field])
                        check_key = f"edit_{suffix}_has_{field}"
                        value_key = f"edit_{suffix}_{field}"
                        st.checkbox(f"{label}を記録", value=(existing is not None), key=check_key)
                        st.number_input(
                            label,
                            min_value=(-30.0 if field == "smv" else 0.0), max_value=maxval,
                            step=step, value=(existing if existing is not None else default_num),
                            format="%.1f", key=value_key,
                        )
                        e_numeric[field] = float(st.session_state.get(value_key, default_num)) if st.session_state.get(check_key, existing is not None) else None

                st.markdown("**糖度測定値（有効な値が1つ以上あれば統計値を再計算）**")
                st.caption("すべて未チェックの場合は既存の糖度測定値・統計値を変更せず、その他の項目だけ更新します。")
                edit_m_values: list[float | None] = []
                em_cols = st.columns(3)
                for i in range(1, 7):
                    field = f"m{i}"
                    existing = None if row_missing(field) else float(target[field])
                    with em_cols[(i - 1) % 3]:
                        st.checkbox(f"測定 {i} 有効", value=(existing is not None), key=f"edit_{suffix}_chk_m{i}")
                        st.number_input(
                            f"測定 {i} (°Bx)", min_value=0.0, max_value=50.0, step=0.01,
                            value=(existing if existing is not None else 0.0), format="%.2f",
                            key=f"edit_{suffix}_val_m{i}",
                        )
                        edit_m_values.append(
                            float(st.session_state.get(f"edit_{suffix}_val_m{i}", 0.0))
                            if st.session_state.get(f"edit_{suffix}_chk_m{i}", existing is not None) else None
                        )

                edit_submitted = st.form_submit_button("💾 編集内容を保存")
                if edit_submitted:
                    if not _clean_text(e_brewery) or not _clean_text(e_name):
                        st.error("蔵元名と銘柄名は必須です。")
                    else:
                        try:
                            update_fields = {
                                "brewery": _clean_text(e_brewery), "name": _clean_text(e_name),
                                "by_year": _clean_text(e_by) or None, "lot_date": _clean_text(e_lot) or None,
                                "spec_category": e_spec, "processing_state": _clean_text(e_state) or None,
                                "alcohol": e_numeric["alcohol"], "smv": e_numeric["smv"],
                                "acidity": e_numeric["acidity"], "amino_acid": e_numeric["amino_acid"],
                                "rice_polished": e_numeric["rice_polished"], "rice_used": _clean_text(e_rice) or None,
                                "yeast": _clean_text(e_yeast) or None, "memo": _clean_text(e_memo) or None,
                            }
                            valid_edit_m = [x for x in edit_m_values if x is not None]
                            if valid_edit_m:
                                update_fields.update(calculate_statistics(edit_m_values))
                                update_fields.update({f"m{i}": edit_m_values[i - 1] for i in range(1, 7)})
                            conn = connect_db(DB_NAME)
                            try:
                                conn.execute("BEGIN IMMEDIATE")
                                assignments = ", ".join(f"{column} = ?" for column in update_fields)
                                values = list(update_fields.values()) + [selected_id]
                                cursor = conn.execute(f"UPDATE measurements SET {assignments} WHERE id = ?", values)
                                if cursor.rowcount != 1:
                                    raise RuntimeError("対象レコードが見つかりません。")
                                conn.commit()
                            finally:
                                conn.close()
                            st.session_state["db_notice"] = ("success", "レコードを更新しました。")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"更新エラー: {exc}")

# -----------------------------------------------------------------------------
# Screen 3: compare
# -----------------------------------------------------------------------------
elif menu == "⚖️ データ比較":
    st.header("⚖️ 銘柄データ比較")
    conn = connect_db(DB_NAME)
    try:
        df = pd.read_sql_query(
            "SELECT id, brewery, name, brix, mean, max_val, min_val, range_val, median, std_dev, "
            "spec_category, processing_state, alcohol, smv, acidity, amino_acid, rice_polished, "
            "rice_used, yeast, recorded_at FROM measurements ORDER BY recorded_at DESC", conn
        )
    finally:
        conn.close()
    if len(df) < 2:
        st.info("比較するには2件以上のデータを保存してください。")
    else:
        labels = {
            str(row["id"]): f"{row['brewery']} — {row['name']} ({row['mean']}°Bx / {row['recorded_at']})"
            for _, row in df.iterrows()
        }
        selected_ids = st.multiselect(
            "比較するレコードを選択してください（複数可）",
            options=list(labels.keys()),
            format_func=lambda record_id: labels[record_id],
            key="comparison_ids",
        )
        if selected_ids:
            comp = df[df["id"].astype(str).isin(selected_ids)].copy()
            st.dataframe(
                comp[[
                    "recorded_at", "brewery", "name", "brix", "mean", "max_val", "min_val",
                    "range_val", "median", "std_dev", "spec_category", "processing_state", "alcohol",
                    "smv", "acidity", "amino_acid", "rice_polished", "rice_used", "yeast",
                ]],
                use_container_width=True,
                hide_index=True,
            )

# -----------------------------------------------------------------------------
# Screen 4: validated JSON restore
# -----------------------------------------------------------------------------
elif menu == "🔄 バックアップ・復元":
    st.header("🔄 データの復元 (JSONインポート)")
    st.warning("ファイルを選択しただけでは復元されません。内容を確認してから実行してください。")
    restore_notice = st.session_state.pop("restore_notice", None)
    if restore_notice:
        (st.success if restore_notice[0] == "success" else st.error)(restore_notice[1])

    uploaded_json = st.file_uploader("バックアップJSONファイル", type=["json"], key="restore_json_file")
    if uploaded_json is not None:
        source_bytes = uploaded_json.getvalue()
        try:
            parsed_json = json.loads(source_bytes.decode("utf-8-sig"))
            if not isinstance(parsed_json, list):
                st.error("JSONの最上位はレコード配列である必要があります。")
            elif not parsed_json:
                st.warning("このバックアップにはレコードがありません。復元できるデータは0件です。")
            else:
                preview = pd.DataFrame(parsed_json)
                st.info(f"バックアップ内のレコード数: {len(parsed_json)}件")
                st.dataframe(preview.head(5), use_container_width=True, hide_index=True)
                st.caption("最大5件をプレビューしています。旧形式の列名も復元時に対応付けます。")
                if st.button("🚀 内容を検証して復元を実行", type="primary", key="run_restore"):
                    try:
                        normalized_records = normalize_import_records(parsed_json, source_bytes=source_bytes)
                        counts = restore_records(DB_NAME, normalized_records)
                        st.success(
                            f"復元完了: 追加 {counts['added']}件 / 重複スキップ {counts['skipped']}件。"
                            "このファイルを再度取り込んでも、同じIDは重複追加されません。"
                        )
                    except Exception as exc:
                        st.error(f"復元できませんでした。変更はロールバックされています。詳細: {exc}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            st.error(f"JSONを読み込めませんでした: {exc}")

st.caption(
    f"DBファイル: {DB_NAME} ｜ データ件数はローカルDBに保存されています。"
    "クラウド環境のローカルファイルは消失する場合があるため、定期的にJSONバックアップを保存してください。"
)
