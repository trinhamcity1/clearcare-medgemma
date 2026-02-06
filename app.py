import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
import streamlit as st

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

    # per-role subfolders
    (case_dir / "patient_uploads").mkdir(exist_ok=True)
    (case_dir / "clinic_a_uploads").mkdir(exist_ok=True)
    (case_dir / "clinic_b_uploads").mkdir(exist_ok=True)
    (case_dir / "outputs").mkdir(exist_ok=True)

    return case_dir

def case_json_path(case_dir: Path) -> Path:
    return case_dir / "case.json"

def load_case_state(case_dir: Path) -> dict:
    p = case_json_path(case_dir)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    # default state
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
        "audit": [],  # simple event log
    }

def save_case_state(case_dir: Path, state: dict) -> None:
    p = case_json_path(case_dir)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")

def log_event(state: dict, event: str, actor_role: str) -> None:
    state["audit"].append({"ts": now_iso(), "actor": actor_role, "event": event})

def list_files(folder: Path) -> list:
    if not folder.exists():
        return []
    files = []
    for f in sorted(folder.iterdir()):
        if f.is_file():
            files.append(f.name)
    return files

def save_uploaded_files(uploaded_files, dst_folder: Path) -> list:
    saved = []
    dst_folder.mkdir(parents=True, exist_ok=True)

    for uf in uploaded_files:
        # Streamlit UploadedFile: uf.name, uf.getbuffer()
        safe_name = re.sub(r"[^a-zA-Z0-9._\- ]", "", uf.name).strip()
        if not safe_name:
            safe_name = f"upload_{int(datetime.now().timestamp())}.bin"
        out_path = dst_folder / safe_name
        with open(out_path, "wb") as f:
            f.write(uf.getbuffer())
        saved.append(out_path.name)
    return saved


# =========================
# Streamlit App
# =========================
st.set_page_config(page_title="ClearCare", page_icon="🩺", layout="centered")
st.title("🩺 ClearCare")
st.caption("Prototype: role-based workflow (no real authentication)")

# Session state init
if "role" not in st.session_state:
    st.session_state.role = "Patient"
if "case_name" not in st.session_state:
    st.session_state.case_name = ""
if "case_dir" not in st.session_state:
    st.session_state.case_dir = None

# ----- Step 1 UI (Role + Case) -----
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
        help="Used as a folder/session identifier. Keep it simple.",
    )

    col1, col2 = st.columns([1, 1])
    with col1:
        start = st.button("Create / Load case", type="primary", use_container_width=True)
    with col2:
        clear = st.button("Clear selection", use_container_width=True)

if clear:
    st.session_state.case_name = ""
    st.session_state.case_dir = None
    st.rerun()

if start:
    try:
        case_dir = ensure_case_folder(st.session_state.case_name)
        st.session_state.case_dir = str(case_dir)
        # ensure case.json exists
        state = load_case_state(case_dir)
        save_case_state(case_dir, state)
        st.success(f"Case ready: `{case_dir}`")
    except Exception as e:
        st.error(str(e))

# Stop if no case
if not st.session_state.case_dir:
    st.info("Create / load a case to continue.")
    st.stop()

case_dir = Path(st.session_state.case_dir)
state = load_case_state(case_dir)

# Sidebar navigation (Step 2 added: Patient page)
st.sidebar.header("Navigation")
page = st.sidebar.radio(
    "Go to",
    options=["Patient: Request & Upload", "Status"],
    index=0,
)

# =========================
# Page: Patient (Step 2)
# =========================
if page == "Patient: Request & Upload":
    if st.session_state.role != "Patient":
        st.warning("Switch Role to **Patient** to use this page.")
        st.stop()

    st.subheader("Step 2: Patient requests records or uploads documents")

    st.markdown("### A) Request records from clinics")
    a_col, b_col = st.columns(2)

    with a_col:
        req_a = st.button("Request from Clinic A", use_container_width=True)
        if req_a:
            if not state["requests"]["clinic_a"]["requested"]:
                state["requests"]["clinic_a"]["requested"] = True
                state["requests"]["clinic_a"]["requested_at"] = now_iso()
                log_event(state, "Patient requested records from Clinic A", "Patient")
                save_case_state(case_dir, state)
                st.success("Requested Clinic A records.")
            else:
                st.info("Clinic A already requested.")

    with b_col:
        req_b = st.button("Request from Clinic B", use_container_width=True)
        if req_b:
            if not state["requests"]["clinic_b"]["requested"]:
                state["requests"]["clinic_b"]["requested"] = True
                state["requests"]["clinic_b"]["requested_at"] = now_iso()
                log_event(state, "Patient requested records from Clinic B", "Patient")
                save_case_state(case_dir, state)
                st.success("Requested Clinic B records.")
            else:
                st.info("Clinic B already requested.")

    st.markdown("### B) Upload documents you already have")
    st.caption("Use **de-identified** sample documents for the demo (no real PHI).")

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
            # update state
            state["uploads"]["patient_files"] = list_files(case_dir / "patient_uploads")
            log_event(state, f"Patient uploaded files: {', '.join(saved)}", "Patient")
            save_case_state(case_dir, state)
            st.success(f"Saved {len(saved)} file(s): {', '.join(saved)}")

    st.divider()
    st.markdown("### Current case status (quick view)")
    st.write({
        "clinic_a_requested": state["requests"]["clinic_a"]["requested"],
        "clinic_a_received": state["requests"]["clinic_a"]["received"],
        "clinic_b_requested": state["requests"]["clinic_b"]["requested"],
        "clinic_b_received": state["requests"]["clinic_b"]["received"],
        "patient_files": list_files(case_dir / "patient_uploads"),
    })

# =========================
# Page: Status (so you can see requests/files easily)
# =========================
elif page == "Status":
    st.subheader("Case status")
    st.code(f"Case folder: {case_dir}", language="text")

    # refresh live filesystem view
    state["uploads"]["patient_files"] = list_files(case_dir / "patient_uploads")
    state["uploads"]["clinic_a_files"] = list_files(case_dir / "clinic_a_uploads")
    state["uploads"]["clinic_b_files"] = list_files(case_dir / "clinic_b_uploads")
    save_case_state(case_dir, state)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### Requests")
        st.json(state["requests"])
    with col2:
        st.markdown("### Uploaded files")
        st.json(state["uploads"])

    st.markdown("### Audit log")
    if state["audit"]:
        for item in reversed(state["audit"][-30:]):
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
