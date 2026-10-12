# redeploy-refresh-check

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd
import streamlit as st
from pydantic import BaseModel, ConfigDict
from streamlit.components.v2 import component

from data_model import MEASUREMENT_COLUMNS, NUMERIC_SPEC_FIELDS, json_safe, normalize_kana, parse_aliases, validate_measurement_input
from db import (
    delete_measurement,
    ensure_schema,
    export_json_records,
    get_measurement,
    get_measurements,
    find_name_candidates,
    get_search_cache,
    insert_measurement,
    put_search_cache,
    restore_json_records,
    search_cache_key,
    update_measurement,
)
from search_sources import DEFAULT_SEARCH_MODEL, SOURCE_REGISTRY, canonical_url, direct_source_search_url, grounded_product_search

st.set_page_config(
    page_title="日本酒 糖度測定・スペック管理 V2",
    page_icon="🍶",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    html, body, [class*="css"] { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    @media (max-width: 768px) {
      h1 { font-size: 1.55rem !important; }
      h2 { font-size: 1.25rem !important; }
      .stTextInput input, .stTextArea textarea, .stSelectbox, .stNumberInput input { font-size: 16px !important; }
      .stButton button { min-height: 2.75rem; width: 100%; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

SPEC_OPTIONS = ["未選択", "純米酒", "特別純米酒", "純米吟醸", "吟醸酒", "純米大吟醸", "大吟醸", "本醸造", "普通酒", "その他"]
FIELD_LABELS = {
    "alcohol": "アルコール度数 (%)",
    "smv": "日本酒度",
    "acidity": "酸度",
    "amino_acid": "アミノ酸度",
    "rice_polished": "精米歩合 (%)",
}

class LabelCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brewery: str | None = None
    name: str | None = None
    name_kana: str | None = None
    brewery_kana: str | None = None
    spec_category: str | None = None
    processing_state: str | None = None
    by_year: str | None = None
    lot_date: str | None = None
    alcohol: float | None = None
    smv: float | None = None
    acidity: float | None = None
    amino_acid: float | None = None
    rice_polished: float | None = None
    rice_used: str | None = None
    yeast: str | None = None

class LabelScanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    labels: list[LabelCandidate]

WHEEL_HTML = """
<div class="brix-input-panel" role="group" aria-label="糖度実測値の直接入力とドラムロール">
  <div class="brix-measurement-rows"></div>
  <div class="brix-wheel-panel">
    <div class="brix-wheel-title"><strong>ドラムロール</strong><span>編集する測定枠を選択</span></div>
    <select class="brix-target-select" aria-label="ホイールで編集する測定枠">
      <option value="1">測定1</option><option value="2">測定2</option><option value="3">測定3</option>
      <option value="4">測定4</option><option value="5">測定5</option><option value="6">測定6</option>
    </select>
    <div class="brix-wheel-columns"></div>
    <div class="brix-wheel-readout" aria-live="polite">未選択</div>
  </div>
</div>
"""
WHEEL_CSS = """
.brix-input-panel { width:100%; box-sizing:border-box; color:var(--st-text-color); font-family:inherit; }
.brix-measurement-rows { display:flex; flex-direction:column; gap:4px; margin-bottom:10px; }
.brix-measurement-row { display:grid; grid-template-columns:minmax(94px, .8fr) minmax(0, 2fr); align-items:center; gap:8px; min-width:0; }
.brix-row-check { display:flex; align-items:center; gap:6px; min-width:0; font-size:.88rem; cursor:pointer; }
.brix-row-check input { width:17px; height:17px; flex:0 0 auto; margin:0; accent-color:var(--st-primary-color); }
.brix-row-input { box-sizing:border-box; width:100%; min-width:0; height:34px; padding:5px 9px; border:1px solid var(--st-border-color); border-radius:7px; background:var(--st-background-color); color:var(--st-text-color); font:inherit; font-size:16px; font-variant-numeric:tabular-nums; }
.brix-row-input::placeholder { color:var(--st-secondary-text-color); opacity:.8; }
.brix-row-input:focus { outline:2px solid var(--st-primary-color); outline-offset:1px; }
.brix-wheel-panel { border-top:1px solid var(--st-border-color); padding-top:7px; }
.brix-wheel-title { display:flex; flex-wrap:wrap; align-items:baseline; gap:6px; margin:0 0 4px; font-size:.75rem; color:var(--st-secondary-text-color); }
.brix-wheel-title strong { color:var(--st-text-color); font-size:.86rem; }
.brix-target-select { box-sizing:border-box; width:100%; max-width:260px; height:32px; margin-bottom:5px; padding:4px 8px; border:1px solid var(--st-border-color); border-radius:7px; background:var(--st-background-color); color:var(--st-text-color); font:inherit; font-size:.9rem; }
.brix-wheel-columns { display:grid; grid-template-columns:1.2fr 1fr 1fr; gap:5px; width:100%; }
.brix-wheel-part { min-width:0; }
.brix-wheel-part-label { font-size:.69rem; text-align:center; color:var(--st-secondary-text-color); margin-bottom:2px; line-height:1.15; }
.brix-wheel-column { height:78px; overflow-y:auto; overflow-x:hidden; overscroll-behavior-y:contain; touch-action:pan-y; -webkit-overflow-scrolling:touch; scroll-snap-type:y mandatory; scrollbar-width:none; border:1px solid var(--st-border-color); border-radius:7px; background:var(--st-secondary-background-color); }
.brix-wheel-column::-webkit-scrollbar { display:none; }
.brix-wheel-list { padding:25px 2px; }
.brix-wheel-option { display:flex; justify-content:center; align-items:center; box-sizing:border-box; width:100%; height:28px; border:0; border-radius:5px; margin:0; padding:0 2px; background:transparent; color:var(--st-text-color); font-size:.98rem; font-weight:500; font-variant-numeric:tabular-nums; scroll-snap-align:center; cursor:pointer; }
.brix-wheel-option.is-active { color:var(--st-primary-color); background:var(--st-background-color); box-shadow:inset 0 0 0 1.5px var(--st-primary-color); font-weight:750; }
.brix-wheel-readout { text-align:center; font-size:.95rem; font-weight:750; font-variant-numeric:tabular-nums; margin-top:3px; min-height:1.2em; }
@media (max-width:480px) {
  .brix-measurement-rows { gap:3px; margin-bottom:8px; }
  .brix-measurement-row { grid-template-columns:minmax(92px, .85fr) minmax(0, 1.7fr); gap:6px; }
  .brix-row-check { font-size:.82rem; }
  .brix-row-input { height:33px; padding:4px 7px; }
  .brix-wheel-column { height:76px; }
  .brix-wheel-list { padding:24px 1px; }
  .brix-wheel-option { height:28px; font-size:.94rem; }
}
"""
WHEEL_JS = r"""
export default function(component) {
  const { parentElement, data, setStateValue } = component;
  const root = parentElement.querySelector('.brix-input-panel');
  if (!root) return;
  const resetToken = String(data?.resetToken ?? 0);
  const rowsRoot = root.querySelector('.brix-measurement-rows');
  const targetSelect = root.querySelector('.brix-target-select');
  const columnsRoot = root.querySelector('.brix-wheel-columns');
  const readout = root.querySelector('.brix-wheel-readout');
  const rowHeight = 28;
  const columns = [
    {key:'integer', label:'整数部', values:Array.from({length:51},(_,i)=>i)},
    {key:'tenths', label:'小数第1位', values:Array.from({length:10},(_,i)=>i)},
    {key:'hundredths', label:'小数第2位', values:Array.from({length:10},(_,i)=>i)}
  ];
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
  const normalizeState = (raw) => ({
    values: Array.from({length:6},(_,i)=>String(raw?.values?.[i] ?? '')),
    enabled: Array.from({length:6},(_,i)=>Boolean(raw?.enabled?.[i])),
    target: clamp(Number(raw?.target ?? 1) || 1,1,6)
  });
  const stateSignature = raw => JSON.stringify(normalizeState(raw));

  function build(initialRaw) {
    rowsRoot.replaceChildren();
    columnsRoot.replaceChildren();
    let state = normalizeState(initialRaw);
    const inputEls = [];
    const checkEls = [];
    const wheels = {};
    const suppressUntil = {};
    let sendTimer = null;

    function payload() {
      return {
        values: inputEls.map(input=>input.value),
        enabled: checkEls.map(check=>check.checked),
        target: Number(targetSelect.value) || 1
      };
    }
    function sendState(immediate=false) {
      if (sendTimer) { clearTimeout(sendTimer); sendTimer=null; }
      const next = payload();
      const sig = stateSignature(next);
      if (root.dataset.lastSentState === sig) return;
      const publish = () => {
        root.dataset.lastSentState = sig;
        setStateValue('value', next);
      };
      if (immediate) publish();
      else sendTimer = setTimeout(publish, 100);
    }
    function activeIndex(wheel, idx) {
      wheel.querySelectorAll('.brix-wheel-option').forEach((el,i)=>{
        const active = i === idx;
        el.classList.toggle('is-active', active);
        el.setAttribute('aria-selected', active ? 'true' : 'false');
      });
    }
    function move(key, idx) {
      const wheel = wheels[key];
      if (!wheel) return;
      idx = clamp(idx,0,wheel.querySelectorAll('.brix-wheel-option').length - 1);
      suppressUntil[key] = Date.now() + 200;
      wheel.scrollTop = idx * rowHeight;
      activeIndex(wheel,idx);
    }
    function selectedIndex(wheel) {
      return clamp(Math.round(wheel.scrollTop / rowHeight),0,wheel.querySelectorAll('.brix-wheel-option').length - 1);
    }
    function parseValue(raw) {
      const text = String(raw ?? '').trim().replace('，','.');
      if (!text) return null;
      let value;
      if (/^\d{1,2}(?:\.\d{0,2})?$/.test(text)) value = Number(text);
      else if (/^\d{1,2},\d{1,2}$/.test(text)) value = Number(text.replace(',','.'));
      else return null;
      if (!Number.isFinite(value) || value < 0 || value > 50) return null;
      if (value === 50 && /[.,]\d*[1-9]/.test(text)) return null;
      return Number(value.toFixed(2));
    }
    function currentWheelValue(digits) {
      if (Object.values(digits).some(v=>v===null)) return null;
      const cents = digits.integer*100 + digits.tenths*10 + digits.hundredths;
      return cents <= 5000 ? Number((cents/100).toFixed(2)) : null;
    }
    const digits = {integer:null, tenths:null, hundredths:null};
    function renderValue() {
      const value = currentWheelValue(digits);
      readout.textContent = value === null ? '未選択' : value.toFixed(2) + ' °Bx';
      return value;
    }
    function setDigitsFromValue(value) {
      if (value === null || value === undefined || value === '') {
        digits.integer=null; digits.tenths=null; digits.hundredths=null;
      } else {
        const n = Number(value);
        if (!Number.isFinite(n) || n<0 || n>50) return;
        const cents = Math.round(n*100);
        digits.integer=Math.floor(cents/100);
        digits.tenths=Math.floor((cents%100)/10);
        digits.hundredths=cents%10;
      }
      columns.forEach(({key})=>move(key,digits[key]===null?0:digits[key]+1));
      renderValue();
    }
    function refreshWheelFromTarget() {
      const target = Number(targetSelect.value) || 1;
      setDigitsFromValue(parseValue(inputEls[target-1].value));
    }
    function choose(key, idx, fromScroll=false) {
      const wheel=wheels[key]; if(!wheel) return;
      idx=clamp(idx,0,wheel.querySelectorAll('.brix-wheel-option').length-1);
      const val=idx===0?null:idx-1;
      digits[key]=val;
      if(key==='integer' && val===50) {
        digits.tenths=0; digits.hundredths=0;
        move('tenths',1); move('hundredths',1);
      } else if(key==='integer' && val!==null) {
        if(digits.tenths===null) {digits.tenths=0;move('tenths',1);}
        if(digits.hundredths===null) {digits.hundredths=0;move('hundredths',1);}
      }
      if(digits.integer===50 && (key==='tenths'||key==='hundredths') && val!==null && val!==0) {
        digits[key]=0; move(key,1);
      }
      if(!fromScroll) move(key,digits[key]===null?0:digits[key]+1);
      const value=renderValue();
      const target=Number(targetSelect.value)-1;
      const input=inputEls[target];
      if(value===null) {
        input.value='';
        checkEls[target].checked=false;
      } else {
        input.value=value.toFixed(2);
        checkEls[target].checked=true;
      }
      sendState(true);
    }

    for(let i=0;i<6;i++) {
      const row=document.createElement('div'); row.className='brix-measurement-row';
      const label=document.createElement('label'); label.className='brix-row-check';
      const check=document.createElement('input'); check.type='checkbox'; check.checked=state.enabled[i];
      check.setAttribute('aria-label',`測定${i+1}を保存対象にする`);
      const labelText=document.createElement('span'); labelText.textContent=`測定${i+1}を保存`;
      label.append(check,labelText);
      const input=document.createElement('input'); input.type='text'; input.inputMode='decimal'; input.autocomplete='off'; input.autocapitalize='off'; input.spellcheck=false; input.maxLength=6;
      input.className='brix-row-input'; input.value=state.values[i]; input.placeholder='例：8.45'; input.setAttribute('aria-label',`測定${i+1}の糖度（°Bx）`);
      check.addEventListener('change',()=>sendState(true));
      input.addEventListener('input',()=>{
        if(Number(targetSelect.value)===i+1) refreshWheelFromTarget();
        sendState(false);
      });
      input.addEventListener('change',()=>{
        if(input.value.trim() && parseValue(input.value)!==null) check.checked=true;
        sendState(true);
      });
      input.addEventListener('blur',()=>{
        if(input.value.trim() && parseValue(input.value)!==null) check.checked=true;
        sendState(true);
      });
      input.addEventListener('keydown',(event)=>{
        if(event.key==='Enter') {event.preventDefault(); input.blur();}
      });
      inputEls.push(input); checkEls.push(check); row.append(label,input); rowsRoot.appendChild(row);
    }
    targetSelect.value=String(state.target);
    targetSelect.addEventListener('change',()=>{refreshWheelFromTarget();sendState(true);});

    columns.forEach(({key,label,values})=>{
      const part=document.createElement('div'); part.className='brix-wheel-part';
      const title=document.createElement('div'); title.className='brix-wheel-part-label'; title.textContent=label;
      const wheel=document.createElement('div'); wheel.className='brix-wheel-column'; wheel.setAttribute('role','listbox'); wheel.setAttribute('aria-label',label); wheel.tabIndex=0;
      const list=document.createElement('div'); list.className='brix-wheel-list';
      [null,...values].forEach((value,index)=>{
        const option=document.createElement('button'); option.type='button'; option.className='brix-wheel-option';
        option.textContent=value===null?'—':String(value); option.setAttribute('role','option'); option.setAttribute('aria-selected','false');
        option.addEventListener('click',()=>{move(key,index);choose(key,index,true);});
        list.appendChild(option);
      });
      wheel.appendChild(list); wheels[key]=wheel;
      let timer=null;
      wheel.addEventListener('scroll',()=>{
        if(Date.now()<(suppressUntil[key]||0)) return;
        if(timer) clearTimeout(timer);
        timer=setTimeout(()=>{const idx=selectedIndex(wheel);move(key,idx);choose(key,idx,true);},80);
      },{passive:true});
      wheel.addEventListener('keydown',(event)=>{
        if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key)) return;
        event.preventDefault();
        if(event.key==='Home') choose(key,0);
        else if(event.key==='End') choose(key,wheel.querySelectorAll('.brix-wheel-option').length-1);
        else choose(key,selectedIndex(wheel)+(event.key==='ArrowDown'?1:-1));
      });
      part.append(title,wheel); columnsRoot.appendChild(part); activeIndex(wheel,0);
    });
    refreshWheelFromTarget();
    root.dataset.lastSentState=stateSignature(payload());
  }

  const incoming = normalizeIncoming(data?.initialState);
  function normalizeIncoming(raw) {
    return {
      values: Array.from({length:6},(_,i)=>String(raw?.values?.[i] ?? '')),
      enabled: Array.from({length:6},(_,i)=>Boolean(raw?.enabled?.[i])),
      target: Math.max(1,Math.min(6,Number(raw?.target ?? 1)||1))
    };
  }
  if(root.dataset.built!=='true' || root.dataset.resetToken!==resetToken) {
    root.dataset.built='true'; root.dataset.resetToken=resetToken;
    build(incoming);
  } else {
    // Only replace browser input state when Python changed it independently.
    const signature=JSON.stringify(incoming);
    if(root.dataset.lastServerState!==signature && root.dataset.lastSentState!==signature) {
      root.dataset.lastServerState=signature;
      build(incoming);
    }
  }
  root.dataset.lastServerState=JSON.stringify(incoming);
}
"""


brix_measurement_input = component(
    name="sake_brix_compact_measurements_v1",
    html=WHEEL_HTML,
    css=WHEEL_CSS,
    js=WHEEL_JS,
)


def get_secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
        if value is not None and str(value).strip():
            return str(value).strip()
    except Exception:
        pass
    return os.getenv(name, default).strip()


def get_database_url() -> str:
    return get_secret("DATABASE_URL")


def get_gemini_key() -> str:
    value = st.session_state.get("gemini_key", "")
    return str(value).strip() if value else get_secret("GEMINI_API_KEY")


def get_gemini_model() -> str:
    value = st.session_state.get("gemini_model", "")
    return str(value).strip() if value else get_secret("GEMINI_MODEL", "gemini-3.8-flash")


def get_gemini_search_model() -> str:
    """Separate search-grounding model from image/spec extraction model."""
    value = st.session_state.get("gemini_search_model", "")
    return str(value).strip() if value else get_secret("GEMINI_SEARCH_MODEL", DEFAULT_SEARCH_MODEL)


def component_state_value(component_key: str) -> Any:
    result = st.session_state.get(component_key)
    if result is None:
        return None
    value = getattr(result, "value", None)
    if value is None and isinstance(result, dict):
        value = result.get("value")
    return value


def normalize_measurement_ui_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    values = raw.get("values") if isinstance(raw.get("values"), list) else []
    enabled = raw.get("enabled") if isinstance(raw.get("enabled"), list) else []
    try:
        target = max(1, min(6, int(raw.get("target", 1))))
    except (TypeError, ValueError):
        target = 1
    return {
        "values": [str(values[i] if i < len(values) and values[i] is not None else "") for i in range(6)],
        "enabled": [bool(enabled[i]) if i < len(enabled) else False for i in range(6)],
        "target": target,
    }


def sync_compact_measurements() -> None:
    reset_token = int(st.session_state.get("wheel_reset_token", 0))
    component_key = f"brix_measurements_{reset_token}"
    payload = component_state_value(component_key)
    if isinstance(payload, dict):
        st.session_state["compact_measurements"] = normalize_measurement_ui_state(payload)


def initialise_form_state() -> None:
    defaults = {
        "f_brewery": "", "f_name": "", "f_name_kana": "", "f_brewery_kana": "", "f_name_aliases": "",
        "f_reading_confirmed": False, "f_by_year": "", "f_lot_date": "",
        "f_spec_category": "未選択", "f_processing_state": "", "f_rice_used": "", "f_yeast": "", "f_memo": "",
        "f_alcohol": "", "f_smv": "", "f_acidity": "", "f_amino_acid": "", "f_rice_polished": "",
        "has_alcohol": False, "has_smv": False, "has_acidity": False, "has_amino_acid": False, "has_rice_polished": False,
        "draft_sources": [], "auto_filled_values": {}, "wheel_reset_token": 0, "flash_message": "", "flash_type": "success",
        "compact_measurements": {"values": [""] * 6, "enabled": [False] * 6, "target": 1},
        "local_name_candidates": [], "search_cache_note": "", "auto_filled_fields": [],
        "label_candidates": [], "selected_label_idx": 0, "search_candidates": [], "search_notes": [],
        "grounding_sources": [], "search_query": "", "selected_search_idx": 0, "last_search_key": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

def set_flash(message: str, kind: str = "success") -> None:
    st.session_state["flash_message"] = message
    st.session_state["flash_type"] = kind


def _apply_candidate_values(candidate: dict[str, Any], source_ref: dict[str, Any] | None = None, *, origin: str = "external") -> None:
    """Apply one selected candidate without overwriting sugar measurements.

    Values auto-filled from a previously selected candidate are cleared only if the user
    has not changed them since. Manual edits therefore survive a candidate switch.
    """
    field_to_state = {
        "brewery": "f_brewery", "name": "f_name", "name_kana": "f_name_kana",
        "brewery_kana": "f_brewery_kana", "by_year": "f_by_year", "lot_date": "f_lot_date",
        "spec_category": "f_spec_category", "processing_state": "f_processing_state",
        "rice_used": "f_rice_used", "yeast": "f_yeast", "name_aliases": "f_name_aliases", "alcohol": "f_alcohol",
        "smv": "f_smv", "acidity": "f_acidity", "amino_acid": "f_amino_acid",
        "rice_polished": "f_rice_polished",
    }
    numeric_fields = set(NUMERIC_SPEC_FIELDS)
    previous = st.session_state.get("auto_filled_values", {})
    if not isinstance(previous, dict):
        previous = {}
    # Clear stale values inserted by the previous candidate. If the user changed one,
    # treat it as a manual edit and keep it.
    for field, old_value in previous.items():
        state_key = field_to_state.get(field)
        if not state_key:
            continue
        current_value = st.session_state.get(state_key)
        if str(current_value) == str(old_value):
            if field in numeric_fields:
                st.session_state[state_key] = ""
                st.session_state[f"has_{field}"] = False
            elif field == "spec_category":
                st.session_state[state_key] = "未選択"
            else:
                st.session_state[state_key] = ""
    st.session_state["auto_filled_values"] = {}

    candidate_name = candidate.get("name") or candidate.get("product_name")
    value_sources: dict[str, Any] = {
        "brewery": candidate.get("brewery"),
        "name": candidate_name,
        "name_kana": normalize_kana(candidate.get("name_kana")),
        "brewery_kana": normalize_kana(candidate.get("brewery_kana")),
        "by_year": candidate.get("by_year"),
        "lot_date": candidate.get("lot_date"),
        "spec_category": candidate.get("spec_category"),
        "processing_state": candidate.get("processing_state"),
        "rice_used": candidate.get("rice_used"),
        "yeast": candidate.get("yeast"),
    }
    newly_filled: dict[str, Any] = {}
    for field, value in value_sources.items():
        if value is None or not str(value).strip():
            continue
        state_key = field_to_state[field]
        normalized_value = str(value).strip()
        st.session_state[state_key] = normalized_value
        newly_filled[field] = normalized_value

    numeric_ranges = {
        "alcohol": (0.0, 30.0), "smv": (-30.0, 30.0), "acidity": (0.0, 5.0),
        "amino_acid": (0.0, 5.0), "rice_polished": (0.0, 100.0),
    }
    for field in NUMERIC_SPEC_FIELDS:
        value = candidate.get(field)
        if value is None:
            continue
        try:
            numeric_value = float(value)
        except (ValueError, TypeError):
            continue
        low, high = numeric_ranges[field]
        if not (low <= numeric_value <= high):
            # Ignore impossible/out-of-range values from an AI or external source.
            continue
        st.session_state[f"has_{field}"] = True
        st.session_state[f"f_{field}"] = format(numeric_value, "g")
        newly_filled[field] = format(numeric_value, "g")

    aliases = candidate.get("name_aliases")
    if isinstance(aliases, list) and aliases:
        alias_text = "、".join(str(x) for x in aliases)
        st.session_state["f_name_aliases"] = alias_text
        newly_filled["name_aliases"] = alias_text

    # A found reading is still unconfirmed unless it came from a user-confirmed local record.
    # Never carry a previous candidate's confirmation checkbox into a candidate
    # that has no reading. Only a previously confirmed local record may pre-check it.
    st.session_state["f_reading_confirmed"] = bool(
        value_sources.get("name_kana")
        and origin == "local"
        and candidate.get("reading_status") == "confirmed"
    )
    st.session_state["auto_filled_values"] = newly_filled

    if source_ref:
        sources = list(st.session_state.get("draft_sources", []))
        if not any(s.get("url") == source_ref.get("url") and s.get("source_name") == source_ref.get("source_name") for s in sources):
            sources.append(source_ref)
        st.session_state["draft_sources"] = sources
    elif origin == "local":
        for src in candidate.get("info_sources") or []:
            if isinstance(src, dict) and src not in st.session_state.get("draft_sources", []):
                st.session_state["draft_sources"].append(src)

    if origin == "local":
        set_flash("登録済みの銘柄候補を反映しました。糖度の実測値は変更していません。", "info")
    elif origin == "label":
        set_flash("ラベル解析候補を反映しました。読み方・スペックをラベルと照合してください。", "info")
    else:
        set_flash("検索候補の掲載項目を反映しました。読み仮名は未確認状態です。値と出典を確認して保存してください。", "info")


def apply_search_candidate(index: int) -> None:
    candidates = st.session_state.get("search_candidates", [])
    if not isinstance(candidates, list) or index < 0 or index >= len(candidates):
        set_flash("検索候補が見つかりません。再検索してください。", "error")
        return
    candidate = candidates[index]
    if candidate.get("source_id") == "local":
        _apply_candidate_values(candidate, None, origin="local")
        return
    source_ref = {
        "source_name": candidate.get("source_name", "外部検索"),
        "url": candidate.get("source_url"),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "product_name": candidate.get("product_name") or candidate.get("name"),
        "fields_reported_by_ai": candidate.get("fields_reported_by_ai", []),
        "url_verified_by_grounding": bool(candidate.get("url_verified_by_grounding")),
    }
    _apply_candidate_values(candidate, source_ref, origin="external")


def apply_label_candidate() -> None:
    candidates = st.session_state.get("label_candidates", [])
    index = int(st.session_state.get("selected_label_idx", 0))
    if isinstance(candidates, list) and 0 <= index < len(candidates):
        _apply_candidate_values(candidates[index], None, origin="label")


def reset_form_state() -> None:
    for key in ["f_brewery", "f_name", "f_name_kana", "f_brewery_kana", "f_name_aliases", "f_by_year", "f_lot_date", "f_processing_state", "f_rice_used", "f_yeast", "f_memo"]:
        st.session_state[key] = ""
    st.session_state["f_spec_category"] = "未選択"
    st.session_state["f_reading_confirmed"] = False
    for field in NUMERIC_SPEC_FIELDS:
        st.session_state[f"f_{field}"] = ""
        st.session_state[f"has_{field}"] = False
    st.session_state["compact_measurements"] = {"values": [""] * 6, "enabled": [False] * 6, "target": 1}
    st.session_state["wheel_reset_token"] = int(st.session_state.get("wheel_reset_token", 0)) + 1
    st.session_state["draft_sources"] = []
    st.session_state["auto_filled_values"] = {}
    st.session_state["label_candidates"] = []
    st.session_state["search_candidates"] = []
    st.session_state["local_name_candidates"] = []
    st.session_state["search_notes"] = []
    st.session_state["selected_label_idx"] = 0
    set_flash("保存しました。次の銘柄を入力できます。")


def save_current_measurement(database_url: str) -> None:
    try:
        raw: dict[str, Any] = {
            "brewery": st.session_state.get("f_brewery", ""),
            "name": st.session_state.get("f_name", ""),
            "name_kana": st.session_state.get("f_name_kana", ""),
            "brewery_kana": st.session_state.get("f_brewery_kana", ""),
            "reading_confirmed": bool(st.session_state.get("f_reading_confirmed", False)),
            "name_aliases": st.session_state.get("f_name_aliases", ""),
            "by_year": st.session_state.get("f_by_year", ""),
            "lot_date": st.session_state.get("f_lot_date", ""),
            "spec_category": st.session_state.get("f_spec_category", "未選択"),
            "processing_state": st.session_state.get("f_processing_state", ""),
            "rice_used": st.session_state.get("f_rice_used", ""),
            "yeast": st.session_state.get("f_yeast", ""),
            "memo": st.session_state.get("f_memo", ""),
            "info_sources": list(st.session_state.get("draft_sources", [])),
        }
        measurement_state = normalize_measurement_ui_state(st.session_state.get("compact_measurements", {}))
        for i in range(1, 7):
            enabled = bool(measurement_state["enabled"][i - 1])
            text_value = str(measurement_state["values"][i - 1]).strip()
            if enabled and not text_value:
                raise ValueError(f"測定{i}が保存対象ですが、糖度が空欄です。直接入力するか、対象を外してください。")
            if enabled:
                normalized = text_value.replace("，", ".")
                if "," in normalized and "." not in normalized and normalized.count(",") == 1:
                    normalized = normalized.replace(",", ".")
                try:
                    decimal_value = Decimal(normalized)
                except InvalidOperation as exc:
                    raise ValueError(f"測定{i}は0.00〜50.00の数値で入力してください。") from exc
                if not decimal_value.is_finite() or decimal_value < 0 or decimal_value > 50:
                    raise ValueError(f"測定{i}は0.00〜50.00の範囲で入力してください。")
                if decimal_value.as_tuple().exponent < -2:
                    raise ValueError(f"測定{i}は小数第2位まで入力してください。")
                raw[f"m{i}"] = normalized
            else:
                raw[f"m{i}"] = None
        for field in NUMERIC_SPEC_FIELDS:
            raw[f"has_{field}"] = bool(st.session_state.get(f"has_{field}", False))
            raw[field] = st.session_state.get(f"f_{field}")
        insert_measurement(database_url, raw)
        reset_form_state()
    except Exception as exc:
        set_flash(f"保存できませんでした。入力値は保持しています。詳細: {exc}", "error")


def run_label_scan(api_key: str, model: str, image_bytes: bytes, mime_type: str) -> list[dict[str, Any]]:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)
    prompt = """画像内の日本酒ラベルを読んでください。複数本なら個別に抽出。
銘柄・蔵元名、銘柄名の読み（ひらがな。根拠がなければnull）、特定名称、製法状態、醸造年度、製造年月/ロット、アルコール度数、日本酒度、酸度、アミノ酸度、精米歩合、使用米、酵母を抽出します。
明記されていない項目はnull。推測しない。画像上のラベルに見える数値を、別商品や別年度から補完しないでください。"""
    response = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type), prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=LabelScanResponse),
    )
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, LabelScanResponse):
        result = parsed
    elif parsed is not None and hasattr(parsed, "model_dump"):
        result = LabelScanResponse.model_validate(parsed.model_dump())
    else:
        result = LabelScanResponse.model_validate_json(response.text)
    return [item.model_dump(mode="json") for item in result.labels if item.brewery or item.name]


def render_source_search(database_url: str) -> None:
    with st.expander("🔎 銘柄情報を複数の情報源から検索（ひらがな対応）", expanded=False):
        st.write(
            "登録済みデータは即時検索できます。外部検索は2つの使い方があります。"
            "「サイト別リンク」は無料で即時表示、「AI外部検索」はGemini 2.5 Flashで候補を整理します。"
            "AI検索は速度と無料枠保護のため一度に最大3情報源です。"
        )
        labels = {source["name"]: source["id"] for source in SOURCE_REGISTRY}
        default_sources = ["はせがわ酒店", "いまでや酒店", "佐野屋 JIZAKE.COM"]
        selected_labels = st.multiselect(
            "AI検索の対象（最大3つ）",
            options=list(labels.keys()),
            default=[label for label in default_sources if label in labels],
            max_selections=3,
            key="search_source_labels",
            help="一度に選ぶ情報源を3つまでに絞ると、検索が速くなり、無料枠の消費も抑えられます。全店舗の直接検索リンクは下に表示します。",
        )
        query = st.text_input(
            "銘柄名・読み仮名・蔵元名・商品特徴",
            key="search_query",
            placeholder="例：ほうおうびでん / 鳳凰美田",
        )
        if st.session_state.get("last_query_for_error") != query:
            st.session_state["search_error"] = ""
            st.session_state["last_query_for_error"] = query
        force_search = st.checkbox(
            "保存済み結果を使わずにAI外部検索を再実行する",
            value=False,
            key="force_search",
        )

        # Always offer direct search links before any slower API call.
        if query.strip():
            with st.expander("⚡ 情報源別の直接検索（すぐ開く・Gemini API利用なし）", expanded=False):
                st.caption(
                    "この欄は検索リンクを作るだけなので待ち時間やGeminiの検索クォータを使いません。"
                    "商品ページは別タブで確認し、年度・容量・生/火入れ・ロットを照合してください。"
                )
                link_sources = [source for source in SOURCE_REGISTRY if source["id"] in [labels.get(x) for x in selected_labels]]
                # Other supported information sources remain immediately accessible too.
                link_sources += [
                    source for source in SOURCE_REGISTRY
                    if source["id"] in {"katidoki", "hasegawa", "imadeya", "yamanaka", "sumiyoshi", "nobori", "sanoya", "kagiya", "yajima", "matsuzaki", "brewery", "sakenowa", "sakenowa_data", "sakeopendata", "sake_dataset"}
                    and source not in link_sources
                ]
                cols = st.columns(2)
                for idx, source in enumerate(link_sources):
                    try:
                        search_url = direct_source_search_url(query, source["id"])
                        cols[idx % 2].link_button(f"{source['name']}で検索", search_url, use_container_width=True)
                    except (ValueError, KeyError):
                        continue
                generic_url = direct_source_search_url(query, "general")
                st.link_button("🌐 一般ウェブ検索", generic_url, use_container_width=True)

        col_local, col_web = st.columns(2)
        with col_local:
            local_clicked = st.button(
                "⚡ 登録済みデータを即時検索",
                type="secondary",
                key="run_local_sake_search",
                use_container_width=True,
            )
        with col_web:
            web_clicked = st.button(
                "🌐 AIで外部検索",
                type="primary",
                key="run_external_sake_search",
                disabled=not bool(get_gemini_key() and selected_labels and query.strip()),
                use_container_width=True,
                help="検索モデルはgemini-2.5-flash。1回のリクエストを15秒でタイムアウトさせ、再試行による長時間待ちを避けます。",
            )

        if local_clicked or web_clicked:
            if not query.strip():
                st.session_state["search_error"] = "銘柄名または読み仮名を入力してください。"
            else:
                try:
                    local_candidates = find_name_candidates(database_url, query, limit=12)
                    st.session_state["local_name_candidates"] = local_candidates
                    st.session_state["search_error"] = ""
                    if local_clicked:
                        st.session_state["search_candidates"] = local_candidates
                        st.session_state["search_notes"] = []
                        st.session_state["grounding_sources"] = []
                        st.session_state["search_cache_note"] = "登録済みデータだけを検索しました（外部API利用なし）。"
                        set_flash(f"登録済みデータ：{len(local_candidates)}件", "info")
                    elif web_clicked:
                        source_ids = [labels[label] for label in selected_labels]
                        search_model = get_gemini_search_model()
                        key = search_cache_key(query, source_ids, search_model)
                        cached = None if force_search else get_search_cache(database_url, key)
                        if cached:
                            payload = cached
                            st.session_state["search_cache_note"] = "保存済みの外部検索結果（7日間キャッシュ）を表示しています。"
                        else:
                            with st.spinner(f"AI外部検索中（{search_model}・最大15秒）…直接検索リンクは上に表示されています"):
                                payload = grounded_product_search(get_gemini_key(), search_model, query, source_ids)
                            payload["retrieved_at"] = datetime.now(timezone.utc).isoformat()
                            put_search_cache(database_url, key, query, source_ids, payload, days=7)
                            st.session_state["search_cache_note"] = "外部検索結果を取得しました。7日間キャッシュします。"
                        web_candidates = payload.get("candidates", [])
                        st.session_state["search_notes"] = payload.get("search_notes", [])
                        st.session_state["grounding_sources"] = payload.get("grounding_sources", [])
                        st.session_state["search_candidates"] = local_candidates + web_candidates
                        st.session_state["last_search_key"] = key
                        set_flash(f"検索完了：登録済み {len(local_candidates)}件、外部候補 {len(web_candidates)}件", "info")
                except Exception as exc:
                    message = str(exc)
                    st.session_state["search_candidates"] = st.session_state.get("local_name_candidates", [])
                    st.session_state["search_notes"] = []
                    st.session_state["grounding_sources"] = []
                    if "429" in message or "quota" in message.lower() or "too_many_requests" in message.lower():
                        st.session_state["search_error"] = (
                            "GeminiのGoogle検索グラウンディングがクォータ制限に達したか、"
                            "このプロジェクトでは利用できない可能性があります。Neonの接続とは別の問題です。"
                            "下の情報源別の直接検索リンクは引き続き利用できます。"
                        )
                    elif "timeout" in message.lower() or "timed out" in message.lower():
                        st.session_state["search_error"] = (
                            "AI検索が15秒以内に応答しませんでした。情報源を1つに絞るか、"
                            "上の直接検索リンクを利用してください。"
                        )
                    else:
                        st.session_state["search_error"] = f"AI外部検索に失敗しました: {message}"

        if st.session_state.get("search_error"):
            st.warning(st.session_state["search_error"])
        if st.session_state.get("search_cache_note"):
            st.caption(st.session_state["search_cache_note"])
        candidates = st.session_state.get("search_candidates", [])
        if candidates:
            st.markdown("#### 検索候補")
            for idx, candidate in enumerate(candidates):
                with st.container(border=True):
                    product_name = candidate.get("product_name") or candidate.get("name") or "商品名不明"
                    brewery = candidate.get("brewery") or "蔵元不明"
                    st.write(f"**{product_name}** — {brewery}")
                    st.caption(f"情報源：{candidate.get('source_name') or '不明'}")
                    if candidate.get("name_kana"):
                        read_status = "登録済み・確認済み" if candidate.get("reading_status") == "confirmed" else "読み候補（未確認）"
                        st.write(f"読み候補：{candidate['name_kana']}（{read_status}）")
                    if candidate.get("reading_basis"):
                        st.caption(f"読みの根拠：{str(candidate['reading_basis'])[:180]}")
                    safe_candidate_url = canonical_url(candidate.get("source_url"))
                    if safe_candidate_url:
                        st.link_button("商品ページを開く", safe_candidate_url)
                    elif candidate.get("source_id") != "local":
                        st.warning("商品ページURLを確認できていません。名称・スペックを確定情報とみなさず、直接検索リンクやラベルで確認してください。")
                    fact_pairs = [
                        ("特定名称", "spec_category"), ("製法・状態", "processing_state"), ("醸造年度", "by_year"),
                        ("製造年月/ロット", "lot_date"), ("アルコール度数", "alcohol"), ("日本酒度", "smv"),
                        ("酸度", "acidity"), ("アミノ酸度", "amino_acid"), ("精米歩合", "rice_polished"),
                        ("使用米", "rice_used"), ("酵母", "yeast"), ("価格表示", "price_text"), ("在庫表示", "availability"),
                    ]
                    shown = [(label, candidate.get(field)) for label, field in fact_pairs if candidate.get(field) is not None]
                    if shown:
                        st.write(" / ".join(f"{label}: {value}" for label, value in shown))
                    if candidate.get("brief_note"):
                        st.caption(str(candidate["brief_note"])[:180])
                    if candidate.get("fields_reported_by_ai"):
                        st.caption("ページに明記とAIが判断した項目（保存前に要確認）:" + ", ".join(candidate["fields_reported_by_ai"]))
                    st.button("この候補を入力欄へ反映", key=f"apply_search_{idx}", on_click=apply_search_candidate, args=(idx,))
        elif st.session_state.get("search_cache_note"):
            st.info("該当する登録済み候補またはAI外部候補はありません。上の直接検索リンクもご利用ください。")
        for note in st.session_state.get("search_notes", []):
            st.caption(f"検索メモ：{note}")
        grounding_sources = st.session_state.get("grounding_sources", [])
        if grounding_sources:
            with st.expander("Google検索で参照された出典一覧"):
                for source in grounding_sources:
                    title = source.get("title") or source.get("url") or "参照先"
                    url = source.get("url")
                    safe_url = canonical_url(url)
                    if safe_url:
                        st.link_button(str(title)[:120], safe_url)
                    else:
                        st.write(f"- {str(title)[:120]}")


def render_entry_page(database_url: str) -> None:
    st.header("📝 新規測定・入力")
    render_source_search(database_url)

    with st.expander("📷 ラベル画像からスペック候補を読み取る（任意）", expanded=False):
        image_file = st.file_uploader("日本酒ラベル画像 (JPEG/PNG)", type=["jpg", "jpeg", "png"], key="label_image")
        if image_file:
            image_bytes = image_file.getvalue()
            current_hash = hashlib.sha256(image_bytes).hexdigest()
            if st.session_state.get("label_image_hash") != current_hash:
                st.session_state["label_image_hash"] = current_hash
                st.session_state["label_candidates"] = []
            st.image(image_bytes, caption="読み取るラベル", use_container_width=True)
            if st.button("ラベルをAI解析", key="scan_label"):
                if not get_gemini_key():
                    set_flash("AI解析にはGemini APIキーが必要です。", "error")
                else:
                    try:
                        with st.spinner("ラベルを読み取っています..."):
                            candidates = run_label_scan(get_gemini_key(), get_gemini_model(), image_bytes, image_file.type)
                        st.session_state["label_candidates"] = candidates
                        set_flash(f"ラベル解析が完了しました（候補 {len(candidates)}件）。内容を確認してください。", "info")
                    except Exception as exc:
                        set_flash(f"ラベル解析に失敗しました: {exc}", "error")
        label_candidates = st.session_state.get("label_candidates", [])
        if label_candidates:
            opts = [f"{i+1}. {x.get('brewery') or '蔵元不明'} — {x.get('name') or '銘柄不明'}" for i, x in enumerate(label_candidates)]
            st.selectbox("反映するラベル候補", options=range(len(opts)), format_func=lambda i: opts[i], key="selected_label_idx")
            st.button("選択したラベルを入力欄へ反映", on_click=apply_label_candidate, key="apply_label_candidate")

    st.subheader("銘柄情報")
    c1, c2 = st.columns(2)
    with c1:
        st.text_input("蔵元名 *", key="f_brewery")
    with c2:
        st.text_input("銘柄名・商品名 *", key="f_name")
    reading_col1, reading_col2 = st.columns([2, 1])
    with reading_col1:
        st.text_input("銘柄名の読み（ひらがな）", key="f_name_kana", placeholder="例：ほうおうびでん", help="難読・当て字の銘柄名を、次回以降ひらがなで探すために保存します。")
    with reading_col2:
        st.checkbox("読みを確認済み", key="f_reading_confirmed", help="実際の読みを確認できた場合のみチェックしてください。外部検索・AI候補の読みは未確認が初期状態です。")
    st.text_input("蔵元名の読み（任意）", key="f_brewery_kana", placeholder="例：こばやししゅぞう")
    st.text_area("別名・別読み（任意、カンマ／読点／改行区切り）", key="f_name_aliases", height=68, help="別表記や別の読みがある場合に入力します。")
    c3, c4, c5 = st.columns([1, 1, 1])
    with c3:
        if st.session_state["f_spec_category"] not in SPEC_OPTIONS:
            SPEC_OPTIONS_CURRENT = SPEC_OPTIONS + [st.session_state["f_spec_category"]]
        else:
            SPEC_OPTIONS_CURRENT = SPEC_OPTIONS
        st.selectbox("特定名称", SPEC_OPTIONS_CURRENT, key="f_spec_category")
    with c4:
        st.text_input("製法・状態", key="f_processing_state", placeholder="例：無濾過生原酒、火入れ")
    with c5:
        st.text_input("醸造年度（BY）", key="f_by_year", placeholder="例：2025BY")
    st.text_input("製造年月・ロット", key="f_lot_date", placeholder="例：2026.04")

    st.subheader("糖度の実測値")
    st.caption("6回分をコンパクトな行で表示します。直接入力または共通ドラムロールで設定できます。実測値は平均化せず個別に保存します。")
    reset_token = int(st.session_state.get("wheel_reset_token", 0))
    component_key = f"brix_measurements_{reset_token}"
    initial_measurements = normalize_measurement_ui_state(st.session_state.get("compact_measurements", {}))
    brix_measurement_input(
        data={"resetToken": reset_token, "initialState": initial_measurements},
        default={"value": initial_measurements},
        on_value_change=sync_compact_measurements,
        key=component_key,
        width="stretch",
        height=420,
    )

    st.subheader("任意のスペック情報")
    st.caption("不明な値はチェックを入れずにおきます。ゼロと未入力を区別します。検索結果を反映した場合も、保存前に値を確認してください。")
    spec_cols = st.columns(2)
    numeric_limits = {
        "alcohol": (0.0, 30.0, 0.1),
        "smv": (-30.0, 30.0, 0.1),
        "acidity": (0.0, 5.0, 0.01),
        "amino_acid": (0.0, 5.0, 0.01),
        "rice_polished": (0.0, 100.0, 0.1),
    }
    for idx, field in enumerate(NUMERIC_SPEC_FIELDS):
        low, high, step = numeric_limits[field]
        with spec_cols[idx % 2]:
            st.checkbox(f"{FIELD_LABELS[field]}を記録する", key=f"has_{field}")
            st.text_input(
                FIELD_LABELS[field],
                key=f"f_{field}",
                placeholder=f"{low:g}〜{high:g}（任意）",
                disabled=not st.session_state[f"has_{field}"],
                help="キーボードから直接入力できます。値は保存時に範囲・桁数を検証します。",
            )
    c_rice, c_yeast = st.columns(2)
    with c_rice:
        st.text_input("使用米", key="f_rice_used")
    with c_yeast:
        st.text_input("使用酵母", key="f_yeast")
    st.text_area("テイスティングメモ・感想", key="f_memo", height=100)

    st.subheader("保存元情報")
    source_refs = st.session_state.get("draft_sources", [])
    if source_refs:
        for src in source_refs:
            link = src.get("url")
            if link:
                st.markdown(f"- {src.get('source_name', '外部情報')}：[{src.get('product_name') or link}]({link})")
            else:
                st.write(f"- {src.get('source_name', '外部情報')}：URL未確認")
    else:
        st.caption("外部情報を反映していない場合、出典は登録されません。")

    st.button("💾 実測値・スペックを保存", type="primary", on_click=save_current_measurement, args=(database_url,), key="save_measurement")


def render_records_page(database_url: str) -> None:
    st.header("📚 保存データ・検索・編集")
    query = st.text_input("蔵元名・銘柄名・メモの部分一致検索", key="record_search")
    try:
        rows, total = get_measurements(database_url, query, limit=100)
    except Exception as exc:
        st.error(f"データ取得エラー: {exc}")
        return
    st.caption(f"全 {total} 件中、最新・該当順に {len(rows)} 件を表示しています（1画面最大100件）。")
    if not rows:
        st.info("該当するデータはありません。")
        return
    display_fields = ["recorded_at", "brewery", "name", "name_kana", "reading_status", *MEASUREMENT_COLUMNS, "spec_category", "processing_state", *NUMERIC_SPEC_FIELDS, "rice_used", "yeast"]
    legacy_summary_keys = ("brix", "mean", "average", "avg", "max_val", "maximum", "min_val", "minimum", "range_val", "range", "median", "std_dev", "stdev", "standard_deviation")
    table_rows = []
    for row in rows:
        legacy = row.get("legacy_data") if isinstance(row.get("legacy_data"), dict) else {}
        legacy_summary = {key: legacy[key] for key in legacy_summary_keys if key in legacy and legacy[key] is not None}
        table_rows.append(
            {field: row.get(field) for field in display_fields}
            | {"過去形式の糖度情報": json.dumps(legacy_summary, ensure_ascii=False) if legacy_summary else "", "id": row.get("id")}
        )
    df = pd.DataFrame(table_rows)
    st.dataframe(df.drop(columns=["id"]), use_container_width=True, hide_index=True)

    ids = [row["id"] for row in rows]
    labels = {row["id"]: f"{row.get('recorded_at')} | {row.get('brewery')} — {row.get('name')}" for row in rows}
    selected_id = st.selectbox("編集・削除するレコード", options=ids, format_func=lambda value: labels.get(value, str(value)), key="selected_record_id")
    selected = next((row for row in rows if row["id"] == selected_id), None)
    if selected is None:
        return
    if selected.get("legacy_data"):
        with st.expander("過去バージョンから引き継いだ情報", expanded=False):
            st.caption("現在のアプリでは集計値を新規計算しません。ここに表示する値は旧記録から引き継いだ参考情報です。個別実測値とは区別してください。")
            st.json(selected.get("legacy_data"))
    if selected.get("info_sources"):
        with st.expander("この記録の情報源", expanded=False):
            for source in selected.get("info_sources") or []:
                if not isinstance(source, dict):
                    st.write(source)
                    continue
                url = source.get("url")
                label = source.get("source_name") or source.get("product_name") or "情報源"
                safe_url = canonical_url(url)
                if safe_url:
                    st.link_button(str(label)[:120], safe_url)
                else:
                    st.write(f"- {label}（URL未確認）")
    with st.expander("選択したレコードを編集", expanded=False):
        edit_prefix = f"edit_{str(selected_id).replace('-', '_')}"
        with st.form(f"edit_form_{edit_prefix}"):
            edit_values: dict[str, Any] = {}
            for field, label in [("brewery", "蔵元名"), ("name", "銘柄名"), ("name_kana", "銘柄名の読み（ひらがな）"), ("brewery_kana", "蔵元名の読み"), ("by_year", "醸造年度"), ("lot_date", "製造年月・ロット"), ("spec_category", "特定名称"), ("processing_state", "製法・状態"), ("rice_used", "使用米"), ("yeast", "使用酵母")]:
                edit_values[field] = st.text_input(label, value=str(selected.get(field) or ""), key=f"{edit_prefix}_{field}")
            st.markdown("**糖度実測値（空欄は未記録）**")
            for field in MEASUREMENT_COLUMNS:
                current = selected.get(field)
                edit_values[field] = st.text_input(
                    f"{field.upper()} (°Bx)",
                    value=f"{float(current):.2f}" if current is not None else "",
                    placeholder="例：8.45（空欄は未記録）",
                    key=f"{edit_prefix}_{field}",
                )
            for field in NUMERIC_SPEC_FIELDS:
                val = selected.get(field)
                low, high = {"alcohol": (0.0,30.0), "smv": (-30.0,30.0), "acidity": (0.0,5.0), "amino_acid": (0.0,5.0), "rice_polished": (0.0,100.0)}[field]
                edit_values[field] = st.text_input(
                    FIELD_LABELS[field],
                    value="" if val is None else str(val),
                    placeholder=f"{low:g}〜{high:g}（任意）",
                    key=f"{edit_prefix}_{field}",
                )
            edit_values["reading_confirmed"] = st.checkbox("銘柄名の読みを確認済み", value=selected.get("reading_status") == "confirmed", key=f"{edit_prefix}_reading_confirmed")
            edit_values["name_aliases"] = st.text_area("別名・別読み（区切りは読点・カンマ・改行）", value="、".join(selected.get("name_aliases") or []), key=f"{edit_prefix}_name_aliases")
            edit_values["memo"] = st.text_area("メモ", value=str(selected.get("memo") or ""), key=f"{edit_prefix}_memo")
            save_edit = st.form_submit_button("変更を保存")
        if save_edit:
            try:
                edit_values["has_alcohol"] = bool(str(edit_values["alcohol"]).strip())
                edit_values["has_smv"] = bool(str(edit_values["smv"]).strip())
                edit_values["has_acidity"] = bool(str(edit_values["acidity"]).strip())
                edit_values["has_amino_acid"] = bool(str(edit_values["amino_acid"]).strip())
                edit_values["has_rice_polished"] = bool(str(edit_values["rice_polished"]).strip())
                edit_values["info_sources"] = selected.get("info_sources") or []
                update_measurement(database_url, str(selected_id), edit_values)
                set_flash("変更を保存しました。")
                st.rerun()
            except Exception as exc:
                st.error(f"編集エラー: {exc}")
    with st.expander("選択したレコードを削除", expanded=False):
        st.warning(f"削除対象：{selected.get('brewery')} — {selected.get('name')}。削除後は元に戻せません。")
        confirm = st.checkbox("このレコードを削除することを確認しました", key=f"confirm_delete_{edit_prefix}")
        if st.button("このレコードを削除", disabled=not confirm, key=f"delete_{edit_prefix}"):
            try:
                delete_measurement(database_url, str(selected_id))
                set_flash("レコードを削除しました。")
                st.rerun()
            except Exception as exc:
                st.error(f"削除エラー: {exc}")


def render_compare_page(database_url: str) -> None:
    st.header("⚖️ 実測値の比較")
    rows, total = get_measurements(database_url, limit=500)
    if len(rows) < 2:
        st.info("比較するには2件以上のデータを保存してください。")
        return
    ids = [row["id"] for row in rows]
    labels = {row["id"]: f"{row.get('brewery')} — {row.get('name')} ({row.get('recorded_at')})" for row in rows}
    selected_ids = st.multiselect("比較する測定記録（複数可）", options=ids, format_func=lambda value: labels.get(value, str(value)), key="compare_ids")
    if selected_ids:
        chosen = [row for row in rows if row["id"] in selected_ids]
        columns = ["recorded_at", "brewery", "name", "name_kana", "reading_status", *MEASUREMENT_COLUMNS, "spec_category", "processing_state", *NUMERIC_SPEC_FIELDS, "rice_used", "yeast"]
        st.dataframe(pd.DataFrame([{field: row.get(field) for field in columns} for row in chosen]), use_container_width=True, hide_index=True)


def render_backup_page(database_url: str) -> None:
    st.header("🔄 バックアップ・復元")
    st.write("JSONバックアップは定期的にダウンロードし、GitHubとは別の場所にも保管してください。")
    try:
        backup_data = export_json_records(database_url)
        st.download_button("JSONバックアップをダウンロード", data=backup_data, file_name=f"sake_brix_backup_{datetime.now().date().isoformat()}.json", mime="application/json")
    except Exception as exc:
        st.error(f"バックアップ作成に失敗しました: {exc}")

    st.markdown("---")
    st.subheader("JSONバックアップから復元")
    uploaded = st.file_uploader("復元するJSONファイルを選択", type=["json"], key="restore_json_file")
    if uploaded is None:
        return
    try:
        payload = json.loads(uploaded.getvalue().decode("utf-8-sig"))
        if not isinstance(payload, list):
            raise ValueError("JSONの最上位は配列である必要があります。")
        if not payload:
            raise ValueError("JSONに復元できるレコードがありません。")
        st.info(f"{len(payload)}件のレコードを検出しました。先頭3件を確認してください。")
        preview = []
        errors = []
        from data_model import normalize_import_record
        for index, raw in enumerate(payload):
            try:
                preview.append(normalize_import_record(raw, row_index=index))
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            st.error(f"復元前検証で {len(errors)} 件のエラーが見つかりました。データは変更しません。")
            for err in errors[:10]:
                st.write(f"- {err}")
            return
        safe_preview = [{k: json_safe(v) for k, v in row.items()} for row in preview[:3]]
        st.dataframe(pd.DataFrame(safe_preview), use_container_width=True, hide_index=True)
        st.warning("同じIDのレコードはスキップされます。1件でも取り込みエラーが発生した場合は、トランザクション全体をロールバックします。")
        if st.button("確認したJSONを復元する", type="primary", key="run_restore"):
            try:
                added, skipped = restore_json_records(database_url, payload)
                set_flash(f"復元完了：追加 {added} 件、重複スキップ {skipped} 件")
                st.rerun()
            except Exception as exc:
                st.error(f"復元に失敗しました。追加内容はロールバックされました: {exc}")
    except Exception as exc:
        st.error(f"JSONを読み込めませんでした: {exc}")


def main() -> None:
    initialise_form_state()
    st.title("🍶 日本酒 糖度測定・スペック管理 V2")
    st.caption("糖度は個別の実測値を保存し、集計値は自動計算しません。銘柄名はひらがな読みでも検索できます。")

    st.sidebar.header("⚙️ 接続設定")
    st.sidebar.write("Neon PostgreSQL")
    has_db = bool(get_database_url())
    st.sidebar.write("DB接続情報：" + ("設定済み" if has_db else "未設定"))
    if "gemini_key" not in st.session_state:
        st.session_state["gemini_key"] = get_secret("GEMINI_API_KEY")
    if "gemini_model" not in st.session_state:
        st.session_state["gemini_model"] = get_secret("GEMINI_MODEL", "gemini-3.8-flash")
    if "gemini_search_model" not in st.session_state:
        st.session_state["gemini_search_model"] = get_secret("GEMINI_SEARCH_MODEL", DEFAULT_SEARCH_MODEL)
    st.sidebar.text_input("Gemini APIキー（この入力は保存されません）", type="password", key="gemini_key")
    st.sidebar.text_input("ラベル解析用Geminiモデル", key="gemini_model")
    st.sidebar.text_input("外部検索用Geminiモデル", key="gemini_search_model")
    st.sidebar.caption("外部検索は既定で gemini-2.5-flash（Google検索の無料枠対応モデル）を使い、15秒でタイムアウト・自動再試行なしにします。サイト別直接検索リンクはGemini APIを使いません。本番ではAPIキーをStreamlit Secretsに設定してください。")

    if not has_db:
        st.error("Neon PostgreSQLへの接続情報がまだ設定されていません。アプリを使う前にStreamlitのSettings → Secretsへ DATABASE_URL を登録してください。")
        st.code('DATABASE_URL = "postgresql://ユーザー:パスワード@ホスト/データベース?sslmode=require"\nGEMINI_API_KEY = "必要な場合のみ設定"\nGEMINI_MODEL = "gemini-3.8-flash"\nGEMINI_SEARCH_MODEL = "gemini-2.5-flash"', language="toml")
        st.markdown("- Neonでプロジェクトを作成し、接続文字列をコピーします。\n- Streamlitのアプリ設定 → Secretsへ設定します。\n- DB URLに実際のパスワードを含むため、GitHubやチャットには貼らないでください。")
        st.stop()

    database_url = get_database_url()
    if not st.session_state.get("db_initialized"):
        try:
            with st.spinner("Neon PostgreSQLに接続し、テーブルを確認しています..."):
                ensure_schema(database_url)
            st.session_state["db_initialized"] = True
        except Exception as exc:
            st.error(f"Neonへの接続・初期化に失敗しました: {exc}")
            st.info("Neonの接続文字列、パスワード、SSL、プロジェクト状態を確認してください。接続文字列はこの会話に貼らないでください。")
            st.stop()

    if st.session_state.get("flash_message"):
        kind = st.session_state.get("flash_type", "success")
        message = st.session_state["flash_message"]
        if kind == "error":
            st.error(message)
        elif kind == "info":
            st.info(message)
        else:
            st.success(message)
        st.session_state["flash_message"] = ""

    page = st.radio(
        "メニュー",
        ["📝 新規測定・入力", "📚 保存データ・検索・編集", "⚖️ 実測値の比較", "🔄 バックアップ・復元", "ℹ️ このアプリについて"],
        horizontal=True,
        key="navigation",
    )
    if page == "📝 新規測定・入力":
        render_entry_page(database_url)
    elif page == "📚 保存データ・検索・編集":
        render_records_page(database_url)
    elif page == "⚖️ 実測値の比較":
        render_compare_page(database_url)
    elif page == "🔄 バックアップ・復元":
        render_backup_page(database_url)
    else:
        st.header("このアプリについて")
        st.write("新しい測定記録は個々の実測値を保存し、統計値を自動計算しません。外部銘柄情報は検索候補と出典を確認してからフォームへ反映します。")
        st.write("DB保存先：Neon PostgreSQL")
        st.write("検索結果キャッシュ：7日間。キャッシュを使わず再検索するとGemini API利用量が増える場合があります。")
        st.write("現在の対象：実測値、酒質スペック、テイスティングメモ、外部出典URLです。画像ファイルはDBに保存しません。")

if __name__ == "__main__":
    main()
