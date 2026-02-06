import os
import re
from pathlib import Path
import streamlit as st

# ---------- Helpers ----------
def sanitize_case_name(name: str) -> str:
    """
    Turn user input into a safe folder name:
    - lowercase
    - spaces -> underscores
    - remove weird characters
    """
    name = name.strip().lower()
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^a-z0-9_\-]", "", name)
    return name[:60]  # keep it reasonable

def ensure_case_folder(case_name: str) -> Path:
    base = Path("cases")
    base.mkdir(exist_ok=True)

    safe = sanitize_case_name(case_name)
    if not safe:
        raise ValueError("Case name became empty after sanitizing. Use letters/numbers.")
    case_dir = base / safe
    case_dir.mkdir(parents=True, exist_ok=True)

    # Optional: per-role subfolders (you'll use later)
    (case_dir / "patient_uploads").mkdir(exist_ok=True)
    (case_dir / "clinic_a_uploads").mkdir(exist_ok=True)
    (case_dir / "clinic_b_uploads").mkdir(exist_ok=True)
    (case_dir / "outputs").mkdir(exist_ok=True)

    return case_dir


# ---------- Streamlit UI ----------
st.set_page_config(page_title="ClearCare", page_icon="🩺", layout="centered")
st.title("🩺 ClearCare")
st.caption("Prototype: role-based workflow (no real authentication)")

# Initialize session state
if "role" not in st.session_state:
    st.session_state.role = "Patient"
if "case_name" not in st.session_state:
    st.session_state.case_name = ""
if "case_dir" not in st.session_state:
    st.session_state.case_dir = None

with st.container(border=True):
    st.subheader("Step 1: Select role + case")

    role = st.selectbox(
        "Role",
        options=["Patient", "Clinic A", "Clinic B"],
        index=["Patient", "Clinic A", "Clinic B"].index(st.session_state.role),
        help="This is a hackathon role switch (not real auth).",
    )
    st.session_state.role = role

    case_name = st.text_input(
        "Case name",
        value=st.session_state.case_name,
        placeholder="e.g., tri_feb06_followup",
        help="Used as a folder/session identifier. Keep it simple.",
    )
    st.session_state.case_name = case_name

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
        st.success(f"Case ready: `{case_dir}`")
    except Exception as e:
        st.error(str(e))

# Show current state
st.divider()
st.subheader("Current session")
st.write(
    {
        "role": st.session_state.role,
        "case_name": st.session_state.case_name,
        "case_dir": st.session_state.case_dir,
    }
)

if st.session_state.case_dir:
    st.info("Next steps: we’ll add upload pages per role (Clinic A/B + Patient).")
