"""Nhúng trình xem 3D vào giao diện web Streamlit (component tự viết, không cần build)."""

from __future__ import annotations

import json
import os
import time

import streamlit as st
import streamlit.components.v1 as components

from .viewer_data import build_scene, scene_key

VIEWER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer")
CACHE_DIR = os.path.join(VIEWER_DIR, "_cache")
_component = components.declare_component("thamtra_viewer", path=VIEWER_DIR)


def _cleanup(max_age_h: float = 12):
    try:
        now = time.time()
        for f in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, f)
            if now - os.path.getmtime(p) > max_age_h * 3600:
                os.remove(p)
    except Exception:
        pass


def prepare_scene(models, result=None, clash_run=None, progress=None) -> dict:
    """Dựng cảnh 3D, ghi ra thư mục đệm của component. Trả về {key, meta, ...}."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    _cleanup()
    data, meta = build_scene(models, result, clash_run, progress=progress)
    key = scene_key(data, meta)
    with open(os.path.join(CACHE_DIR, key + ".bin"), "wb") as f:
        f.write(data)
    with open(os.path.join(CACHE_DIR, key + ".json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    return {"key": key, "meta": meta, "size_mb": len(data) / 1e6}


def viewer(scene: dict, focus_ids=None, focus_clash: str = "", height: int = 620, key: str = "viewer"):
    """Hiển thị trình xem; trả về đối tượng người dùng nháy chọn (dict) hoặc None."""
    k = scene["key"]
    return _component(
        key=key, default=None, scene_key=k,
        data_url=f"_cache/{k}.bin", meta_url=f"_cache/{k}.json", height=height,
        focus_ids=[int(i) for i in (focus_ids or [])], focus_clash=focus_clash or "",
    )


def ensure_scene(state_key: str, models, result=None, clash_run=None):
    """Dựng cảnh 1 lần và lưu trong session_state theo dấu vết dữ liệu đầu vào."""
    sig = (tuple(sorted(m.path for m in models)), id(result), id(clash_run),
           len(getattr(clash_run, "clashes", []) or []))
    cur = st.session_state.get(state_key)
    if cur and cur.get("sig") == sig:
        return cur
    with st.spinner("Đang dựng mô hình 3D (lần đầu có thể mất 1-2 phút với mô hình lớn)..."):
        sc = prepare_scene(models, result, clash_run)
    sc["sig"] = sig
    st.session_state[state_key] = sc
    return sc
