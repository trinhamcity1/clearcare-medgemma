# app.py — ClearCare (Steps 1–4 + Step 6 with REAL local MedGemma)
# Run: streamlit run app.py

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Tuple

import streamlit as st

# ---- Optional heavy deps for MedGemma (only used on Step 6) ----
import torch
from transformers import AutoProcessor, AutoModelForImageTextToText


# =========================
# Core Helpers
# =========================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_case_name(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^a-z0-9_\-]", "", name)
    return name[:60]


def ensure_case_folder(case_name: str) -> Path:
    base = Path("cases")
    base.mkdir(exist_ok=True)

    safe = sanitize_case_name(case_name)
    if not safe:
        raise ValueError("Case name became empty after sanitizing. Use letters/numbers.")

    case_dir = base / safe
    case_dir.mkdir(parents=True, exist_ok=True)

    # Raw uploads
    (case_dir / "patient_uploads").mkdir(exist_ok=True)
    (case_dir / "clinic_a_uploads").mkdir(exist_ok=True)
    (case_dir / "clinic_b_uploads").mkdir(exist_ok=True)

    # Privacy-preserved outputs
    (case_dir / "pp_clinic_a").mkdir(exist_ok=True)
    (case_dir / "pp_clinic_b").mkdir(exist_ok=True)

    # Generated outputs
    (case_dir / "outputs").mkdir(exist_ok=True)

    return case_dir


def case_json_path(case_dir: Path) -> Path:
    return case_dir / "case.json"


def load_case_state(case_dir: Path) -> dict:
    p = case_json_path(case_dir)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    # New default state
    return {
        "case_name": case_dir.name,
        "created_at": now_iso(),
        "requests": {
            "clinic_a": {"requested": False, "requested_at": None, "received": False, "received_at": None},
            "clinic_b": {"requested": False, "requested_at": None, "received": False, "received_at": None},
        },
        "uploads": {"patient_files": [], "clinic_a_files": [], "clinic_b_files": []},
        "pp": {"clinic_a_pp_files": [], "clinic_b_pp_files": [], "last_run_at": None},
        "audit": [],
    }


def ensure_state_schema(state: dict) -> dict:
    """Backward-compatible schema patcher so old case.json files don't break new code."""
    state.setdefault("case_name", "unknown_case")
    state.setdefault("created_at", now_iso())

    state.setdefault(
        "requests",
        {
            "clinic_a": {"requested": False, "requested_at": None, "received": False, "received_at": None},
            "clinic_b": {"requested": False, "requested_at": None, "received": False, "received_at": None},
        },
    )
    # Ensure request subkeys
    state["requests"].setdefault("clinic_a", {})
    state["requests"].setdefault("clinic_b", {})
    for k in ["clinic_a", "clinic_b"]:
        state["requests"][k].setdefault("requested", False)
        state["requests"][k].setdefault("requested_at", None)
        state["requests"][k].setdefault("received", False)
        state["requests"][k].setdefault("received_at", None)

    state.setdefault("uploads", {})
    state["uploads"].setdefault("patient_files", [])
    state["uploads"].setdefault("clinic_a_files", [])
    state["uploads"].setdefault("clinic_b_files", [])

    state.setdefault("pp", {})
    state["pp"].setdefault("clinic_a_pp_files", [])
    state["pp"].setdefault("clinic_b_pp_files", [])
    state["pp"].setdefault("last_run_at", None)

    state.setdefault("audit", [])
    return state


def save_case_state(case_dir: Path, state: dict) -> None:
    case_json_path(case_dir).write_text(json.dumps(state, indent=2), encoding="utf-8")


def log_event(state: dict, event: str, actor_role: str) -> None:
    state["audit"].append({"ts": now_iso(), "actor": actor_role, "event": event})


def list_files(folder: Path) -> list:
    if not folder.exists():
        return []
    return [f.name for f in sorted(folder.iterdir()) if f.is_file()]


def refresh_lists(case_dir: Path, state: dict) -> dict:
    state["uploads"]["patient_files"] = list_files(case_dir / "patient_uploads")
    state["uploads"]["clinic_a_files"] = list_files(case_dir / "clinic_a_uploads")
    state["uploads"]["clinic_b_files"] = list_files(case_dir / "clinic_b_uploads")
    state["pp"]["clinic_a_pp_files"] = list_files(case_dir / "pp_clinic_a")
    state["pp"]["clinic_b_pp_files"] = list_files(case_dir / "pp_clinic_b")
    return state


def save_uploaded_files(uploaded_files, dst_folder: Path) -> list:
    saved = []
    dst_folder.mkdir(parents=True, exist_ok=True)

    for uf in uploaded_files:
        safe_name = re.sub(r"[^a-zA-Z0-9._\- ]", "", uf.name).strip()
        if not safe_name:
            safe_name = f"upload_{int(datetime.now().timestamp())}.bin"
        out_path = dst_folder / safe_name
        out_path.write_bytes(uf.getbuffer())
        saved.append(out_path.name)
    return saved


# =========================
# Step 4: Privacy-preserving redaction (TEXT ONLY)
# =========================
# NOTE: We removed "address regex" because it can corrupt clinical text (you observed it).
PHI_PATTERNS = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\(\d{3}\)\s*|\d{3}[-.\s]?)\d{3}[-.\s]?\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b(?:DOB|Date of Birth)\s*[:\-]?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", re.IGNORECASE), "[REDACTED_DOB]"),
    (re.compile(r"\b(?:MRN|Patient\s*ID|Member\s*ID)\s*[:\-]?\s*[A-Za-z0-9\-]{4,}\b", re.IGNORECASE), "[REDACTED_ID]"),
]
NAME_FIELD = re.compile(r"\b(?:Patient Name|Patient|Name)\s*[:\-]\s*[A-Za-z ,.'-]{2,}\b", re.IGNORECASE)


def redact_text(text: str) -> str:
    out = text
    out = NAME_FIELD.sub(lambda m: m.group(0).split(":")[0] + ": [REDACTED_NAME]", out)
    for pat, repl in PHI_PATTERNS:
        out = pat.sub(repl, out)
    return out


def pp_transform_folder(src_folder: Path, dst_folder: Path, state: dict, actor: str, clinic_label: str) -> dict:
    """
    Transform raw clinic files into privacy-preserved outputs.
    - .txt: redact -> save *_PP.txt
    - other: copy bytes but mark as NEEDS_TEXT_EXTRACT (no OCR in this prototype)
    """
    dst_folder.mkdir(parents=True, exist_ok=True)

    processed = []
    stored = []

    for f in sorted(src_folder.iterdir()):
        if not f.is_file():
            continue

        ext = f.suffix.lower()
        if ext == ".txt":
            raw = f.read_text(encoding="utf-8", errors="ignore")
            red = redact_text(raw)
            out_name = f.stem + "_PP.txt"
            (dst_folder / out_name).write_text(red, encoding="utf-8")
            processed.append(out_name)
        else:
            out_name = f.name + ".NEEDS_TEXT_EXTRACT"
            (dst_folder / out_name).write_bytes(f.read_bytes())
            stored.append(out_name)

    if processed:
        log_event(state, f"PP pipeline processed {clinic_label} text files: {', '.join(processed)}", actor)
    if stored:
        log_event(state, f"PP pipeline stored {clinic_label} non-text files (no OCR): {', '.join(stored)}", actor)

    return state


# =========================
# Step 6: Local MedGemma
# =========================
MODEL_OPTIONS = {
    "MedGemma 1.5 4B (Fast, Recommended)": "google/medgemma-1.5-4b-it",
    "MedGemma 4B (Original)": "google/medgemma-4b-it",
}


def pick_device_and_dtype():
    if torch.cuda.is_available():
        return "cuda", torch.bfloat16
    if torch.backends.mps.is_available():
        return "mps", torch.float32   # <-- stability over speed
    return "cpu", torch.float32



@st.cache_resource
def load_medgemma_model(model_id: str):
    device, dtype = pick_device_and_dtype()

    model = AutoModelForImageTextToText.from_pretrained(
        model_id,
        dtype=dtype,                          # <--- use dtype (torch_dtype is deprecated)
        device_map="auto" if device == "cuda" else None,
    )
    processor = AutoProcessor.from_pretrained(model_id)

    if device != "cuda":
        model.to(device)

    model.eval()
    return model, processor, device



def read_pp_texts(case_dir: Path) -> Dict[str, List[str]]:
    """Read PP text files from pp_clinic_a and pp_clinic_b."""
    def read_dir(d: Path) -> List[str]:
        texts = []
        for f in sorted(d.glob("*_PP.txt")):
            try:
                texts.append(f.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
        return texts

    return {
        "clinic_a": read_dir(case_dir / "pp_clinic_a"),
        "clinic_b": read_dir(case_dir / "pp_clinic_b"),
    }


def truncate_blocks(texts: List[str], max_chars: int) -> str:
    """Join texts and truncate to max_chars for faster/safer inference."""
    joined = "\n\n---\n\n".join(texts) if texts else ""
    if len(joined) <= max_chars:
        return joined
    return joined[:max_chars] + "\n\n[TRUNCATED FOR SAFETY & SPEED]"


def build_patient_safe_prompt(clinic_a_texts: List[str], clinic_b_texts: List[str], max_chars_each: int = 12000) -> str:
    a_block = truncate_blocks(clinic_a_texts, max_chars_each) if clinic_a_texts else "(No PP Clinic A text found)"
    b_block = truncate_blocks(clinic_b_texts, max_chars_each) if clinic_b_texts else "(No PP Clinic B text found)"

    return f"""
You are ClearCare, a patient-facing medical record explainer.
You must be cautious:
- Do NOT diagnose.
- Do NOT prescribe or tell the patient what treatment to choose.
- Do NOT claim certainty.
You CAN:
- Explain what the documents say in plain language.
- Define medical terms.
- Highlight uncertainty and missing information.
- Suggest prioritized questions to ask a clinician.

Return Markdown with EXACT headings:

## Plain-English Summary
## Key Findings (from the documents)
## What might connect Clinic A and Clinic B (high-level, cautious)
## Questions to Ask Your Clinician (prioritized)
## What’s Missing / Unclear
## Safety Note

CLINIC A (PRIVACY-PRESERVED TEXT):
{a_block}

CLINIC B (PRIVACY-PRESERVED TEXT):
{b_block}
""".strip()


def run_medgemma(prompt: str, model_id: str, max_new_tokens: int = 600) -> str:
    model, processor, device = load_medgemma_model(model_id)

    messages = [
        {"role": "system", "content": [{"type": "text", "text":
            "You are ClearCare, a cautious medical record explainer. "
            "Do not diagnose. Do not prescribe. "
            "Explain findings in plain language and suggest questions for a clinician."
        }]},
        {"role": "user", "content": [{"type": "text", "text": prompt}]},
    ]

    inputs = processor.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
    )
    inputs = {k: v.to(device) for k, v in inputs.items()}
    input_len = inputs["input_ids"].shape[-1]

    # safer pad/eos ids
    eos_id = getattr(model.config, "eos_token_id", None)
    pad_id = getattr(model.config, "pad_token_id", None) or eos_id

    with torch.inference_mode():
        output = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            min_new_tokens=150,      # force it to say something
            do_sample=False,         # <-- greedy decoding (no multinomial)
            num_beams=1,
            repetition_penalty=1.05,
            pad_token_id=pad_id,
            eos_token_id=eos_id,
        )

    # decode tail; fallback to full decode if tail is empty
    tail_ids = output[0][input_len:]
    tail_text = processor.decode(tail_ids, skip_special_tokens=True).strip()
    if tail_text:
        return tail_text

    full_text = processor.decode(output[0], skip_special_tokens=True).strip()
    return full_text


def save_outputs(case_dir: Path, md_text: str, meta: Dict[str, Any]) -> Path:
    out_dir = case_dir / "outputs"
    out_dir.mkdir(exist_ok=True)
    md_path = out_dir / "clearcare_report.md"
    json_path = out_dir / "clearcare_report_meta.json"
    md_path.write_text(md_text, encoding="utf-8")
    json_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return md_path


# =========================
# Streamlit App UI
# =========================
st.set_page_config(page_title="ClearCare", page_icon="🩺", layout="centered")
st.title("🩺 ClearCare")
st.caption("Hackathon prototype: multi-clinic record collection → PP pipeline → local MedGemma report")

# Session init
if "role" not in st.session_state:
    st.session_state.role = "Patient"
if "case_name" not in st.session_state:
    st.session_state.case_name = ""
if "case_dir" not in st.session_state:
    st.session_state.case_dir = None

# Step 1: Role + Case
with st.container(border=True):
    st.subheader("Step 1: Select role + case")

    st.session_state.role = st.selectbox(
        "Role",
        options=["Patient", "Clinic A", "Clinic B"],
        index=["Patient", "Clinic A", "Clinic B"].index(st.session_state.role),
        help="Hackathon role switch (not real authentication).",
    )

    st.session_state.case_name = st.text_input(
        "Case name",
        value=st.session_state.case_name,
        placeholder="e.g., tri_feb06_followup",
        help="Used as a folder/session identifier.",
    )

    c1, c2 = st.columns(2)
    with c1:
        start = st.button("Create / Load case", type="primary", use_container_width=True)
    with c2:
        clear = st.button("Clear selection", use_container_width=True)

if clear:
    st.session_state.case_name = ""
    st.session_state.case_dir = None
    st.rerun()

if start:
    try:
        case_dir = ensure_case_folder(st.session_state.case_name)
        st.session_state.case_dir = str(case_dir)

        state = load_case_state(case_dir)
        state = ensure_state_schema(state)
        state = refresh_lists(case_dir, state)
        save_case_state(case_dir, state)

        st.success(f"Case ready: `{case_dir}`")
    except Exception as e:
        st.error(str(e))

if not st.session_state.case_dir:
    st.info("Create / load a case to continue.")
    st.stop()

case_dir = Path(st.session_state.case_dir)
state = load_case_state(case_dir)
state = ensure_state_schema(state)
state = refresh_lists(case_dir, state)
save_case_state(case_dir, state)

# Navigation
st.sidebar.header("Navigation")
page = st.sidebar.radio(
    "Go to",
    options=[
        "Patient: Request & Upload",
        "Clinic A: Notifications & Upload",
        "Clinic B: Notifications & Upload",
        "Privacy Pipeline: De-identify (PP)",
        "MedGemma: Generate ClearCare Report (Local)",
        "Status",
    ],
    index=0,
)

# =========================
# Step 2: Patient page
# =========================
if page == "Patient: Request & Upload":
    if st.session_state.role != "Patient":
        st.warning("Switch Role to **Patient** to use this page.")
        st.stop()

    st.subheader("Step 2: Patient requests records or uploads documents")

    st.markdown("### A) Request records from clinics")
    a_col, b_col = st.columns(2)

    with a_col:
        if st.button("Request from Clinic A", use_container_width=True):
            if not state["requests"]["clinic_a"]["requested"]:
                state["requests"]["clinic_a"]["requested"] = True
                state["requests"]["clinic_a"]["requested_at"] = now_iso()
                log_event(state, "Patient requested records from Clinic A", "Patient")
                save_case_state(case_dir, state)
                st.success("Requested Clinic A records.")
            else:
                st.info("Clinic A already requested.")

    with b_col:
        if st.button("Request from Clinic B", use_container_width=True):
            if not state["requests"]["clinic_b"]["requested"]:
                state["requests"]["clinic_b"]["requested"] = True
                state["requests"]["clinic_b"]["requested_at"] = now_iso()
                log_event(state, "Patient requested records from Clinic B", "Patient")
                save_case_state(case_dir, state)
                st.success("Requested Clinic B records.")
            else:
                st.info("Clinic B already requested.")

    st.markdown("### B) Upload documents you already have")
    st.caption("Use de-identified sample documents for the demo (no real PHI).")

    uploaded = st.file_uploader(
        "Upload reports/scans (PDF, images, text)",
        type=["pdf", "png", "jpg", "jpeg", "txt"],
        accept_multiple_files=True,
    )

    if st.button("Save uploaded files", type="primary", use_container_width=True):
        if not uploaded:
            st.warning("No files selected.")
        else:
            saved = save_uploaded_files(uploaded, case_dir / "patient_uploads")
            state = refresh_lists(case_dir, state)
            log_event(state, f"Patient uploaded files: {', '.join(saved)}", "Patient")
            save_case_state(case_dir, state)
            st.success(f"Saved {len(saved)} file(s): {', '.join(saved)}")

    st.divider()
    st.markdown("### Quick status")
    st.write({
        "clinic_a_requested": state["requests"]["clinic_a"]["requested"],
        "clinic_a_received": state["requests"]["clinic_a"]["received"],
        "clinic_b_requested": state["requests"]["clinic_b"]["requested"],
        "clinic_b_received": state["requests"]["clinic_b"]["received"],
        "patient_files": state["uploads"]["patient_files"],
    })

# =========================
# Step 3: Clinic A page
# =========================
elif page == "Clinic A: Notifications & Upload":
    if st.session_state.role != "Clinic A":
        st.warning("Switch Role to **Clinic A** to use this page.")
        st.stop()

    st.subheader("Step 3: Clinic A receives request and uploads records")

    req = state["requests"]["clinic_a"]
    if not req["requested"]:
        st.warning("No request from patient yet. (For demo: switch to Patient and request Clinic A.)")
        st.stop()

    if not req["received"]:
        st.success("🔔 Notification: Patient requested records from Clinic A.")
    else:
        st.info("✅ Records already sent for this case.")
    st.caption(f"Requested at: {req['requested_at']}")

    uploaded = st.file_uploader(
        "Upload Clinic A documents (TXT recommended for PP demo)",
        type=["pdf", "png", "jpg", "jpeg", "txt"],
        accept_multiple_files=True,
        key="clinic_a_uploader",
    )

    if st.button("Send to ClearCare (Clinic A)", type="primary", use_container_width=True):
        if not uploaded:
            st.warning("No files selected.")
        else:
            saved = save_uploaded_files(uploaded, case_dir / "clinic_a_uploads")
            state = refresh_lists(case_dir, state)
            state["requests"]["clinic_a"]["received"] = True
            state["requests"]["clinic_a"]["received_at"] = now_iso()
            log_event(state, f"Clinic A uploaded files: {', '.join(saved)}", "Clinic A")
            save_case_state(case_dir, state)
            st.success(f"Sent {len(saved)} file(s): {', '.join(saved)}")

    st.markdown("### Clinic A raw files")
    st.write(state["uploads"]["clinic_a_files"])

# =========================
# Step 3: Clinic B page
# =========================
elif page == "Clinic B: Notifications & Upload":
    if st.session_state.role != "Clinic B":
        st.warning("Switch Role to **Clinic B** to use this page.")
        st.stop()

    st.subheader("Step 3: Clinic B receives request and uploads records")

    req = state["requests"]["clinic_b"]
    if not req["requested"]:
        st.warning("No request from patient yet. (For demo: switch to Patient and request Clinic B.)")
        st.stop()

    if not req["received"]:
        st.success("🔔 Notification: Patient requested records from Clinic B.")
    else:
        st.info("✅ Records already sent for this case.")
    st.caption(f"Requested at: {req['requested_at']}")

    uploaded = st.file_uploader(
        "Upload Clinic B documents (TXT recommended for PP demo)",
        type=["pdf", "png", "jpg", "jpeg", "txt"],
        accept_multiple_files=True,
        key="clinic_b_uploader",
    )

    if st.button("Send to ClearCare (Clinic B)", type="primary", use_container_width=True):
        if not uploaded:
            st.warning("No files selected.")
        else:
            saved = save_uploaded_files(uploaded, case_dir / "clinic_b_uploads")
            state = refresh_lists(case_dir, state)
            state["requests"]["clinic_b"]["received"] = True
            state["requests"]["clinic_b"]["received_at"] = now_iso()
            log_event(state, f"Clinic B uploaded files: {', '.join(saved)}", "Clinic B")
            save_case_state(case_dir, state)
            st.success(f"Sent {len(saved)} file(s): {', '.join(saved)}")

    st.markdown("### Clinic B raw files")
    st.write(state["uploads"]["clinic_b_files"])

# =========================
# Step 4: Privacy Pipeline page
# =========================
elif page == "Privacy Pipeline: De-identify (PP)":
    st.subheader("Step 4: Privacy-preserving pipeline (PP copies)")

    st.info(
        "This pipeline **does not modify originals**. It creates de-identified copies for model analysis.\n\n"
        "- `.txt` files: PHI redaction is applied\n"
        "- PDFs/images: stored as-is (no OCR in this hackathon prototype)"
    )

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### Clinic A → PP Clinic A")
        st.write("Raw files:", state["uploads"]["clinic_a_files"])
        st.write("PP files:", state["pp"]["clinic_a_pp_files"])
    with col2:
        st.markdown("### Clinic B → PP Clinic B")
        st.write("Raw files:", state["uploads"]["clinic_b_files"])
        st.write("PP files:", state["pp"]["clinic_b_pp_files"])

    if st.button("Run PP Pipeline Now", type="primary", use_container_width=True):
        state = pp_transform_folder(
            src_folder=case_dir / "clinic_a_uploads",
            dst_folder=case_dir / "pp_clinic_a",
            state=state,
            actor=st.session_state.role,
            clinic_label="Clinic A",
        )
        state = pp_transform_folder(
            src_folder=case_dir / "clinic_b_uploads",
            dst_folder=case_dir / "pp_clinic_b",
            state=state,
            actor=st.session_state.role,
            clinic_label="Clinic B",
        )

        state["pp"]["last_run_at"] = now_iso()
        state = refresh_lists(case_dir, state)
        log_event(state, "PP pipeline ran successfully", st.session_state.role)
        save_case_state(case_dir, state)

        st.success("PP pipeline complete. PP files updated.")
        st.write("PP Clinic A:", state["pp"]["clinic_a_pp_files"])
        st.write("PP Clinic B:", state["pp"]["clinic_b_pp_files"])

# =========================
# Step 6: MedGemma local generation page
# =========================
elif page == "MedGemma: Generate ClearCare Report (Local)":
    st.subheader("Step 6: Generate ClearCare report with LOCAL MedGemma")

    st.warning(
        "This prototype is educational and supports question preparation. "
        "It does NOT diagnose or prescribe treatment."
    )

    pp = read_pp_texts(case_dir)
    a_texts, b_texts = pp["clinic_a"], pp["clinic_b"]

    if not a_texts and not b_texts:
        st.error("No PP text files found. Run the PP Pipeline first (Step 4) and ensure clinics uploaded .txt files.")
        st.stop()

    st.markdown("### PP inputs detected")
    st.write({
        "pp_clinic_a_text_count": len(a_texts),
        "pp_clinic_b_text_count": len(b_texts),
        "pp_clinic_a_files": list_files(case_dir / "pp_clinic_a"),
        "pp_clinic_b_files": list_files(case_dir / "pp_clinic_b"),
    })

    with st.expander("Preview PP text (first 800 chars each)"):
        for i, t in enumerate(a_texts):
            st.markdown(f"**Clinic A PP text #{i+1}**")
            st.code(t[:800])
        for i, t in enumerate(b_texts):
            st.markdown(f"**Clinic B PP text #{i+1}**")
            st.code(t[:800])

    model_label = st.selectbox("Model", list(MODEL_OPTIONS.keys()))
    model_id = MODEL_OPTIONS[model_label]

    max_chars_each = st.slider(
        "Max characters from each clinic (speed/safety)",
        min_value=2000,
        max_value=20000,
        value=6000,
        step=1000,
        help="Truncates PP text blocks before sending to MedGemma (faster and avoids context overflow).",
    )

    max_new_tokens = st.slider(
        "Max new tokens (generation length)",
        min_value=200,
        max_value=1200,
        value=500,
        step=50,
    )

    prompt = build_patient_safe_prompt(a_texts, b_texts, max_chars_each=max_chars_each)

    with st.expander("Show prompt sent to MedGemma (safe template)"):
        st.code(prompt[:4000] + ("\n\n...[TRUNCATED DISPLAY]..." if len(prompt) > 4000 else ""))

    device, dtype = pick_device_and_dtype()
    st.caption(f"Runtime device: **{device}** | dtype: **{dtype}** | model: **{model_id}**")

    if st.button("Generate ClearCare Report", type="primary", use_container_width=True):
        with st.spinner("Loading model (first run may take a while) and generating..."):
            report_md = run_medgemma(prompt=prompt, model_id=model_id, max_new_tokens=max_new_tokens)

        meta = {
            "generated_at": now_iso(),
            "engine": "local_medgemma",
            "model_id": model_id,
            "device": device,
            "dtype": str(dtype),
            "max_chars_each": max_chars_each,
            "max_new_tokens": max_new_tokens,
            "pp_a_files": list_files(case_dir / "pp_clinic_a"),
            "pp_b_files": list_files(case_dir / "pp_clinic_b"),
        }

        md_path = save_outputs(case_dir, report_md, meta)
        log_event(state, f"Generated ClearCare report using {model_id}", st.session_state.role)
        save_case_state(case_dir, state)

        st.success(f"Report generated and saved: `{md_path}`")
        st.markdown("---")
        st.markdown(report_md)

        st.download_button(
            "Download report (Markdown)",
            data=report_md.encode("utf-8"),
            file_name="clearcare_report.md",
            mime="text/markdown",
            use_container_width=True,
        )

# =========================
# Status page
# =========================
elif page == "Status":
    st.subheader("Case status")
    st.code(f"Case folder: {case_dir}", language="text")

    state = refresh_lists(case_dir, state)
    save_case_state(case_dir, state)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### Requests")
        st.json(state["requests"])
    with col2:
        st.markdown("### Files")
        st.json({"uploads": state["uploads"], "pp": state["pp"]})

    st.markdown("### Audit log")
    if state["audit"]:
        for item in reversed(state["audit"][-80:]):
            st.write(f"- **{item['ts']}** — *{item['actor']}*: {item['event']}")
    else:
        st.write("No events yet.")

st.divider()
st.subheader("Current session")
st.write(
    {
        "role": st.session_state.role,
        "case_name": st.session_state.case_name,
        "case_dir": st.session_state.case_dir,
    }
)
