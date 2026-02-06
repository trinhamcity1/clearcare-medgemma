import json
import re
from datetime import datetime, timezone
from pathlib import Path
import streamlit as st
import os
from typing import Dict, Any, List

# =========================
# Helpers
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

    # raw uploads
    (case_dir / "patient_uploads").mkdir(exist_ok=True)
    (case_dir / "clinic_a_uploads").mkdir(exist_ok=True)
    (case_dir / "clinic_b_uploads").mkdir(exist_ok=True)

    # privacy-preserved (PP) outputs
    (case_dir / "pp_clinic_a").mkdir(exist_ok=True)
    (case_dir / "pp_clinic_b").mkdir(exist_ok=True)

    (case_dir / "outputs").mkdir(exist_ok=True)
    return case_dir

def case_json_path(case_dir: Path) -> Path:
    return case_dir / "case.json"

def load_case_state(case_dir: Path) -> dict:
    p = case_json_path(case_dir)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {
        "case_name": case_dir.name,
        "created_at": now_iso(),
        "requests": {
            "clinic_a": {"requested": False, "requested_at": None, "received": False, "received_at": None},
            "clinic_b": {"requested": False, "requested_at": None, "received": False, "received_at": None},
        },
        "uploads": {
            "patient_files": [],
            "clinic_a_files": [],
            "clinic_b_files": [],
        },
        "pp": {
            "clinic_a_pp_files": [],
            "clinic_b_pp_files": [],
            "last_run_at": None,
        },
        "audit": [],
    }

def save_case_state(case_dir: Path, state: dict) -> None:
    case_json_path(case_dir).write_text(json.dumps(state, indent=2), encoding="utf-8")

def log_event(state: dict, event: str, actor_role: str) -> None:
    state["audit"].append({"ts": now_iso(), "actor": actor_role, "event": event})

def list_files(folder: Path) -> list:
    if not folder.exists():
        return []
    return [f.name for f in sorted(folder.iterdir()) if f.is_file()]

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

def refresh_lists(case_dir: Path, state: dict) -> dict:
    state["uploads"]["patient_files"]  = list_files(case_dir / "patient_uploads")
    state["uploads"]["clinic_a_files"] = list_files(case_dir / "clinic_a_uploads")
    state["uploads"]["clinic_b_files"] = list_files(case_dir / "clinic_b_uploads")
    state["pp"]["clinic_a_pp_files"]   = list_files(case_dir / "pp_clinic_a")
    state["pp"]["clinic_b_pp_files"]   = list_files(case_dir / "pp_clinic_b")
    return state

# MEDGEMMA
def read_pp_texts(case_dir: Path) -> Dict[str, List[str]]:
    """Read PP text files from pp_clinic_a and pp_clinic_b. Returns dict with file contents."""
    pp_a_dir = case_dir / "pp_clinic_a"
    pp_b_dir = case_dir / "pp_clinic_b"

    def read_dir(d: Path) -> List[str]:
        texts = []
        for f in sorted(d.glob("*_PP.txt")):
            try:
                texts.append(f.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
        return texts

    return {
        "clinic_a": read_dir(pp_a_dir),
        "clinic_b": read_dir(pp_b_dir),
    }

def build_patient_safe_prompt(clinic_a_texts: List[str], clinic_b_texts: List[str]) -> str:
    """Build a safe, human-centered prompt. No diagnosing, no treatment orders."""
    clinic_a_block = "\n\n---\n\n".join(clinic_a_texts) if clinic_a_texts else "(No PP Clinic A text found)"
    clinic_b_block = "\n\n---\n\n".join(clinic_b_texts) if clinic_b_texts else "(No PP Clinic B text found)"

    return f"""
You are ClearCare, a patient-facing medical record explainer.
You must be cautious: do NOT diagnose, do NOT give treatment instructions, do NOT claim certainty.
You can explain what the documents say, define terms, point out what is missing, and suggest questions to ask a clinician.
If something is uncertain, say so.

OUTPUT FORMAT (Markdown):
## Plain-English Summary
## Key Findings (from the documents)
## What might connect Clinic A and Clinic B (high-level, cautious)
## Questions to Ask Your Clinician (prioritized)
## What’s Missing / Unclear
## Safety Note

CLINIC A (PP TEXT):
{clinic_a_block}

CLINIC B (PP TEXT):
{clinic_b_block}
""".strip()

def run_medgemma_stub(prompt: str) -> str:
    """
    Stub generator so the demo works without MedGemma.
    Produces a structured answer using lightweight heuristics.
    """
    # ultra-simple heuristic hints (demo-only)
    hints = []
    p = prompt.lower()
    if "a1c" in p or "hba1c" in p:
        hints.append("HbA1c relates to average blood sugar over ~3 months.")
    if "egfr" in p or "creatinine" in p:
        hints.append("Creatinine/eGFR relate to kidney function and should be interpreted by a clinician.")
    if "ldl" in p or "cholesterol" in p:
        hints.append("LDL/HDL/triglycerides relate to cardiovascular risk factors.")
    if "ejection fraction" in p or "lvef" in p:
        hints.append("Ejection fraction is a measure of heart pumping function; 'low-normal' can be clinically meaningful.")

    hint_block = "\n".join([f"- {h}" for h in hints]) if hints else "- (No heuristic hints triggered.)"

    return f"""
## Plain-English Summary
These documents include lab testing (Clinic A) and cardiology testing (Clinic B). ClearCare can help translate terminology and highlight what to discuss with your clinician. This is educational and not a diagnosis.

## Key Findings (from the documents)
- Clinic A: Possible blood sugar control concerns (if HbA1c/glucose elevated), cholesterol markers, and kidney-related markers (creatinine/eGFR/urine albumin) depending on values reported.
- Clinic B: Heart rhythm/ECG notes and echocardiogram findings (e.g., ejection fraction, structural findings) depending on what was documented.

## What might connect Clinic A and Clinic B (high-level, cautious)
Some metabolic risk factors (blood sugar, cholesterol, kidney markers) can relate to cardiovascular health. Only your clinician can interpret your situation in context.

## Questions to Ask Your Clinician (prioritized)
1. Which findings are most important right now, and which are “watch and repeat”?
2. Are there trends compared to my prior results (improving or worsening)?
3. What follow-up tests or monitoring do you recommend, and when?
4. What symptoms would require urgent attention?
5. What lifestyle or medication considerations should I discuss (pros/cons, side effects)?

## What’s Missing / Unclear
- Prior baseline labs and prior cardiology studies for comparison
- Current medications and relevant history
- Symptoms context (chest pain, shortness of breath, fatigue, etc.)

## Safety Note
This report is for education and question preparation. It does not diagnose or replace clinician judgment.

### Demo “hints” detected
{hint_block}
""".strip()

def save_outputs(case_dir: Path, md_text: str, meta: Dict[str, Any]) -> Path:
    out_dir = case_dir / "outputs"
    out_dir.mkdir(exist_ok=True)
    md_path = out_dir / "clearcare_report.md"
    json_path = out_dir / "clearcare_report_meta.json"

    md_path.write_text(md_text, encoding="utf-8")
    json_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return md_path


# =========================
# Privacy-preserving redaction (text only)
# =========================
PHI_PATTERNS = [
    # Emails
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    # Phones: 123-456-7890, (123) 456-7890, 1234567890
    (re.compile(r"\b(?:\(\d{3}\)\s*|\d{3}[-.\s]?)\d{3}[-.\s]?\d{4}\b"), "[REDACTED_PHONE]"),
    # DOB-like: DOB: 01/02/1990 or 01-02-1990
    (re.compile(r"\b(?:DOB|Date of Birth)\s*[:\-]?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", re.IGNORECASE), "[REDACTED_DOB]"),
    # Dates (optional): keep OFF by default, but pattern included if you want to toggle later
    # (re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"), "[REDACTED_DATE]"),
    # MRN / ID patterns
    (re.compile(r"\b(?:MRN|Patient\s*ID|Member\s*ID)\s*[:\-]?\s*[A-Za-z0-9\-]{4,}\b", re.IGNORECASE), "[REDACTED_ID]"),
    # Address field only (label-anchored)
    (re.compile(r"\b(?:Address)\s*[:\-]\s*.+", re.IGNORECASE), "Address: [REDACTED_ADDRESS]")
]

# Optional: naive "Patient Name:" fields
NAME_FIELD = re.compile(r"\b(?:Patient|Name)\s*[:\-]\s*[A-Za-z ,.'-]{2,}\b", re.IGNORECASE)

def redact_text(text: str) -> str:
    out = text

    # redact explicit Name fields
    out = NAME_FIELD.sub(lambda m: m.group(0).split(":")[0] + ": [REDACTED_NAME]", out)

    # apply patterns
    for pat, repl in PHI_PATTERNS:
        out = pat.sub(repl, out)

    return out

def pp_transform_folder(src_folder: Path, dst_folder: Path, state: dict, actor: str, clinic_label: str) -> dict:
    """
    Transform raw clinic files into privacy-preserved outputs.
    - For .txt files: redact + save to dst
    - For other files: copy as-is but mark as 'unprocessed' with suffix
      (hackathon-friendly: avoids OCR)
    """
    dst_folder.mkdir(parents=True, exist_ok=True)

    processed = []
    skipped = []

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
            # Keep file, but label as "needs extract" (we're not doing OCR in this hackathon pipeline)
            out_name = f.name + ".NEEDS_TEXT_EXTRACT"
            (dst_folder / out_name).write_bytes(f.read_bytes())
            skipped.append(out_name)

    if processed:
        log_event(state, f"PP pipeline processed {clinic_label} text files: {', '.join(processed)}", actor)
    if skipped:
        log_event(state, f"PP pipeline stored {clinic_label} non-text files (no OCR): {', '.join(skipped)}", actor)

    return state


# =========================
# Streamlit App
# =========================
st.set_page_config(page_title="ClearCare", page_icon="🩺", layout="centered")
st.title("🩺 ClearCare")
st.caption("Prototype: role-based workflow (no real authentication)")

if "role" not in st.session_state:
    st.session_state.role = "Patient"
if "case_name" not in st.session_state:
    st.session_state.case_name = ""
if "case_dir" not in st.session_state:
    st.session_state.case_dir = None

# Step 1
with st.container(border=True):
    st.subheader("Step 1: Select role + case")

    st.session_state.role = st.selectbox(
        "Role",
        options=["Patient", "Clinic A", "Clinic B"],
        index=["Patient", "Clinic A", "Clinic B"].index(st.session_state.role),
        help="Hackathon role switch (not real auth).",
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
state = refresh_lists(case_dir, state)
save_case_state(case_dir, state)

# Nav
st.sidebar.header("Navigation")
page = st.sidebar.radio(
    "Go to",
    options=[
        "Patient: Request & Upload",
        "Clinic A: Notifications & Upload",
        "Clinic B: Notifications & Upload",
        "Privacy Pipeline: De-identify (PP)",
        "Status",
        "MedGemma: Generate ClearCare Report",

    ],
    index=0,
)

# =========================
# Patient Page (Step 2)
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
    st.write({
        "clinic_a_requested": state["requests"]["clinic_a"]["requested"],
        "clinic_a_received": state["requests"]["clinic_a"]["received"],
        "clinic_b_requested": state["requests"]["clinic_b"]["requested"],
        "clinic_b_received": state["requests"]["clinic_b"]["received"],
        "patient_files": state["uploads"]["patient_files"],
    })

# =========================
# Clinic A Page (Step 3)
# =========================
elif page == "Clinic A: Notifications & Upload":
    if st.session_state.role != "Clinic A":
        st.warning("Switch Role to **Clinic A** to use this page.")
        st.stop()

    st.subheader("Step 3: Clinic A receives request and uploads records")

    req = state["requests"]["clinic_a"]
    if not req["requested"]:
        st.warning("No request from patient yet.")
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
# Clinic B Page (Step 3)
# =========================
elif page == "Clinic B: Notifications & Upload":
    if st.session_state.role != "Clinic B":
        st.warning("Switch Role to **Clinic B** to use this page.")
        st.stop()

    st.subheader("Step 3: Clinic B receives request and uploads records")

    req = state["requests"]["clinic_b"]
    if not req["requested"]:
        st.warning("No request from patient yet.")
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

    run_pp = st.button("Run PP Pipeline Now", type="primary", use_container_width=True)

    if run_pp:
        # run transform for both clinics
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
# Status
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
        for item in reversed(state["audit"][-60:]):
            st.write(f"- **{item['ts']}** — *{item['actor']}*: {item['event']}")
    else:
        st.write("No events yet.")

# MEDGEMMA PAGE handler
        
elif page == "MedGemma: Generate ClearCare Report":
    st.subheader("Step 6: Generate ClearCare report (MedGemma-ready)")

    st.warning(
        "This is a prototype. It provides education and question prompts — "
        "not diagnosis or treatment instructions."
    )

    # Load PP texts
    pp = read_pp_texts(case_dir)
    a_texts, b_texts = pp["clinic_a"], pp["clinic_b"]

    if not a_texts and not b_texts:
        st.error("No PP text files found. Run PP Pipeline first, and ensure clinics uploaded .txt files.")
        st.stop()

    st.markdown("### PP inputs detected")
    st.write({
        "pp_clinic_a_text_count": len(a_texts),
        "pp_clinic_b_text_count": len(b_texts),
    })

    with st.expander("Preview PP text (first 800 chars each)"):
        for i, t in enumerate(a_texts):
            st.markdown(f"**Clinic A PP text #{i+1}**")
            st.code(t[:800])
        for i, t in enumerate(b_texts):
            st.markdown(f"**Clinic B PP text #{i+1}**")
            st.code(t[:800])

    prompt = build_patient_safe_prompt(a_texts, b_texts)

    with st.expander("Show prompt sent to model (safe template)"):
        st.code(prompt[:3000])

    # Choose engine
    engine = st.selectbox(
        "Engine",
        ["Stub (demo-safe)", "Local Transformers (if you installed a MedGemma model)"],
        help="Stub works anywhere. Local requires you to set up a model locally.",
    )

    if st.button("Generate report", type="primary", use_container_width=True):
        if engine.startswith("Stub"):
            report_md = run_medgemma_stub(prompt)
            used = "stub"
        else:
            # Minimal local hook (you’ll fill model_id)
            try:
                from transformers import AutoTokenizer, AutoModelForCausalLM
                import torch

                model_id = st.text_input(
                    "Model ID (Hugging Face / local path)",
                    value=os.environ.get("CLEARCARE_MODEL_ID", ""),
                    help="Example: set env CLEARCARE_MODEL_ID to your MedGemma model id/path."
                )

                if not model_id:
                    st.error("No model id provided. Set CLEARCARE_MODEL_ID env var or paste it here.")
                    st.stop()

                tok = AutoTokenizer.from_pretrained(model_id)
                model = AutoModelForCausalLM.from_pretrained(model_id)
                model.eval()

                inputs = tok(prompt, return_tensors="pt", truncation=True, max_length=4096)
                with torch.no_grad():
                    out = model.generate(
                        **inputs,
                        max_new_tokens=700,
                        do_sample=False,
                    )
                report_md = tok.decode(out[0], skip_special_tokens=True)
                used = f"local:{model_id}"
            except Exception as e:
                st.error(f"Local model failed: {e}")
                st.stop()

        meta = {
            "generated_at": now_iso(),
            "engine": used,
            "pp_a_files": list_files(case_dir / "pp_clinic_a"),
            "pp_b_files": list_files(case_dir / "pp_clinic_b"),
        }

        md_path = save_outputs(case_dir, report_md, meta)
        log_event(state, f"Generated ClearCare report using {used}", st.session_state.role)
        save_case_state(case_dir, state)

        st.success(f"Report generated: {md_path}")
        st.markdown("---")
        st.markdown(report_md)

        st.download_button(
            "Download report (Markdown)",
            data=report_md.encode("utf-8"),
            file_name="clearcare_report.md",
            mime="text/markdown",
            use_container_width=True,
        )


st.divider()
st.subheader("Current session")
st.write(
    {
        "role": st.session_state.role,
        "case_name": st.session_state.case_name,
        "case_dir": st.session_state.case_dir,
    }
)
