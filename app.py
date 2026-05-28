# -*- coding: utf-8 -*-
"""
Clean, Session State Safe OFQUAL QAN Processing App
"""

# ============================================================
# IMPORT LIBRARIES 
# ============================================================
import streamlit as st
from st_copy import copy_button

import pandas as pd
import requests
import numpy as np
import re

# ============================================================
# DEFINE MAPPINGS AND CONSTANTS
# ============================================================
# ------------------------------------------------------------
# Grade Scales
# ------------------------------------------------------------

grade_scales = {
    'Pass/Fail': 'PASS_FAIL',
    'A to D/Fail': 'ALEVEL_STYLE',
    'A to C/Fail': 'ALEVEL_STYLE',
    'Pass/Merit/Distinction': 'BTEC_UNIT',
    'P/M/D/D*': 'BTEC_UNIT',
    'D*D*/D*D/DD/DM/MM/MP/PP': 'BTEC_COMPOSITE_2',
    'D*D*D*/D*D*D/D*DD/DDD/DDM/DMM/MMM/MMP/MPP/PPP': 'BTEC_COMPOSITE_3',
    'A*/A/B/C/D/E': 'ALEVEL_FULL',
    'A/B/C/D/E': 'ALEVEL_FULL',
    'D1, D2, D3, ...': 'CAMBRIDGE_TECH'
}

BTEC_BASE = {
    "D*": 14,
    "D": 12,
    "M": 8,
    "P": 4
}

ALEVEL_BASE = {
    "A*": 14,
    "A": 12,
    "B": 10,
    "C": 8,
    "D": 6,
    "E": 4
}


# ============================================================
# DEFINE FUNCTIONS 
# ============================================================
BASE = "https://register-api.ofqual.gov.uk/api/qualifications/"

def fetch_qan(q):
    """
    Fetch a single QAN from the Ofqual API.
    """
    url = BASE + q.replace("/", "")
    
    try:
        r = requests.get(url, timeout=10)

        if r.status_code != 200:
            return [{"qualificationNumber": q, "error": r.text}]
        
        js = r.json()
        return js if isinstance(js, list) else [js]

    except Exception as e:
        return [{"qualificationNumber": q, "error": str(e)}]

def fetch_many(qans):
    """
    Fetch multiple QANs and return a combined dataframe.
    """
    rows = []

    for q in qans:
        rows.extend(fetch_qan(q))

    if rows:
        return pd.json_normalize(rows)
    
    return pd.DataFrame()


def parse_qans(raw):
    """
    Parses free-text QAN input into a clean, validated DataFrame.
    Accepts messy input (spaces, commas, line breaks, etc.)
    """

    if not raw or not raw.strip():
        return pd.DataFrame(columns=["QANs"]), []

    # Split input on common delimiters
    tokens = re.split(r"[,\;\|\t\r\n]+|\s{2,}", raw.strip())
    tokens = [t.strip() for t in tokens if t.strip()]

    cleaned = []
    for t in tokens:
        t = re.sub(r"/{2,}", "/", t)

        parts = t.split("/")
        if len(parts) == 3 and parts[-1].lower() == "x":
            parts[-1] = "X"
            t = "/".join(parts)

        cleaned.append(t)

    # Validate QAN pattern
    pat = re.compile(r"^\d{2,4}/\d{3,5}/(\d|X)$")

    valid = [c for c in cleaned if pat.match(c)]
    invalid = [c for c in cleaned if c not in valid]

    # Deduplicate while preserving order
    seen = set()
    dedup = []
    for c in valid:
        if c not in seen:
            dedup.append(c)
            seen.add(c)

    return pd.DataFrame({"QANs": dedup}), invalid


def classify_scale(scale):

    if scale in grade_scales:
        return grade_scales[scale]

    scale = str(scale)

    if "D*D*D" in scale or "DDD" in scale:
        return "BTEC_COMPOSITE_3"

    if "D*D" in scale or "DD" in scale:
        return "BTEC_COMPOSITE_2"

    if "D*/D/M/P" in scale or "P/M/D" in scale:
        return "BTEC_UNIT"

    if "A*/A/B/C" in scale:
        return "ALEVEL_FULL"

    return "UNKNOWN"

def get_size_band(glh):
    bands = [
        (0, 119, 1),
        (120, 219, 2),
        (220, 319, 3),
        (320, 499, 4),
        (500, 719, 6),
        (720, 999, 8),
        (1000, float("inf"), 12)
    ]
    
    if pd.isna(glh):
        return None

    for low, high, band in bands:
        if low <= glh <= high:
            return band
    
    return None

def split_grades(grade):
    """
    Splits a composite grade string into its components.
    
    Examples:
        "D*DD" → ["D*", "D", "D"]
        "MMM" → ["M", "M", "M"]
        "A*AB" → ["A*", "A", "B"]
    """
    if grade is None:
        return []

    grade = str(grade).strip()

    parts = []
    i = 0

    while i < len(grade):
        # Check for 2-character grades first
        if grade[i:i+2] in ["D*", "A*"]:
            parts.append(grade[i:i+2])
            i += 2
        else:
            parts.append(grade[i])
            i += 1

    return parts

def btec_tariff(grade, size_band):
    """
    Calculates tariff for BTEC-style grades.
    Works for both single grades (D, M, P) and composite (D*DD, MMM, etc.)
    """
    if grade is None or size_band is None:
        return None

    parts = split_grades(grade)
    
    try:
        base_score = sum(BTEC_BASE[g] for g in parts)
        return base_score * size_band
    except KeyError:
        return None

def calculate_tariff(row):

    grade = row["Grade"]
    size_band = row["Size Band"]

    if grade is None or size_band is None:
        return None

    parts = split_grades(grade)

    # base score using your unified scale
    base_score = sum(BTEC_BASE.get(g, ALEVEL_BASE.get(g, 0)) for g in parts)

    return base_score * size_band
    
def alevel_tariff(grade):
    if grade is None:
        return None
    return ALEVEL_BASE.get(grade)


def cambridge_tariff(grade, size_band):
    CAMBRIDGE_MAP = {
        "D1": 14, "D2": 14, "D3": 13,
        "M1": 11, "M2": 10, "M3": 9,
        "P1": 7, "P2": 6, "P3": 5
    }

    if grade is None or size_band is None:
        return None

    return CAMBRIDGE_MAP.get(grade) * size_band if grade in CAMBRIDGE_MAP else None

def generate_grades(scale_type):
    
    if scale_type == "BTEC_UNIT":
        return ["D*", "D", "M", "P"]
    
    elif scale_type == "BTEC_COMPOSITE_3":
        return ["D*D*D*", "D*D*D", "D*DD", "DDD", "DDM", "DMM", "MMM", "MMP", "MPP", "PPP"]
    
    elif scale_type == "ALEVEL_FULL":
        return ["A*", "A", "B", "C", "D", "E"]
    
    else:
        return []

def collapse(g):
    """
    Collapse grouped tariff rows into aggregated grade/tariff strings.
    """

    grades = g["Grade"].tolist()
    tariffs = g["TariffNum"].tolist()

    # sort by tariff descending
    pairs = sorted(zip(grades, tariffs), key=lambda x: x[1], reverse=True)

    return pd.Series({
        "QAN": g["QAN"].iloc[0],
        "Title": g["Title"].iloc[0],
        "Grade": "/".join([str(p[0]) for p in pairs]),
        "Tariff": "/".join([str(int(p[1])) if pd.notna(p[1]) else "NA" for p in pairs])
    })


# ============================================================
# SESSION STATE INITIALISATION (MUST BE FIRST)
# ============================================================
qans_df = st.session_state.get("qans_df")
st.session_state.setdefault("qans_df", None)
st.session_state.setdefault("results_df", None)
st.session_state.setdefault("last_params", {})
st.session_state.setdefault("DF", None)

# ------------------------------------------------------------
# Page Configuration
# ------------------------------------------------------------
st.set_page_config(page_title="Tariff Data Direct from Ofqual",
                   page_icon="🇹🇹",
                   layout="wide")




# --- Build lookup with tuple expansion ---


# ------------------------------------------------------------
# USER INPUT
# ------------------------------------------------------------
st.title("Tariff Data Direct from Ofqual")

text_input = st.text_area(
    "Paste QANs",
    height=150,
    placeholder="e.g.\n601/5731/8\n601/3330/2\n610/3958/5\n\nYou can paste from Excel, CSV, or emails."
)
if text_input:
    st.caption("✔ Supports copy/paste from Excel, emails, or comma-separated lists")

if qans_df is not None:
    st.success(f"{len(qans_df)} valid QAN(s) detected")

if st.session_state.get("invalid"):
    st.warning(f"{len(st.session_state['invalid'])} invalid entries removed")

with st.expander("✅ Parsed QANs", expanded=True):
    st.dataframe(qans_df, hide_index=True)

col1, col2 = st.columns([4,1])

with col2:
    if st.button("Clear"):
        st.session_state["qans_df"] = None
        st.session_state["invalid"] = []
        st.rerun()

uploaded_file = st.file_uploader("Upload QANs (CSV or Excel)", type=["csv", "xlsx"])   

if uploaded_file:
    if uploaded_file.name.endswith(".csv"):
        df_upload = pd.read_csv(uploaded_file)
    else:
        df_upload = pd.read_excel(uploaded_file)

    # assume QANs in first column
    raw_qans = "\n".join(df_upload.iloc[:,0].astype(str))
    qdf, invalid = parse_qans(raw_qans)

    st.session_state["qans_df"] = qdf
    st.session_state["invalid"] = invalid

# ------------------------------------------------------------
# HANDLE PARSED INPUT
# ------------------------------------------------------------
if text_input:
    qdf, invalid = parse_qans(text_input)
    st.session_state["qans_df"] = qdf    # <<<<<<<<<< IMPORTANT
    st.session_state["invalid"] = invalid

# SHOW PARSED QANs
with st.expander("Parsed QANs", expanded=True):
    qans_df = st.session_state.get("qans_df")
    if qans_df is not None and not qans_df.empty:
        st.dataframe(qans_df, hide_index=True)
    else:
        st.write("No valid QANs yet.")

if st.session_state.get("invalid"):
    with st.expander("Invalid entries"):
        st.write(st.session_state["invalid"])


# ------------------------------------------------------------
# GUARD: STOP IF NO QANs
# ------------------------------------------------------------
qans_df = st.session_state.get("qans_df")
if qans_df is None or qans_df.empty:
    st.stop()


# ------------------------------------------------------------
# FETCH FROM OFQUAL
# ------------------------------------------------------------
BASE = "https://register-api.ofqual.gov.uk/api/qualifications/"



if st.button("Fetch Ofqual Data & Process Tariffs", type="primary"):
    DF = fetch_many(qans_df["QANs"].tolist())
    st.session_state["DF"] = DF

# st.write("Actual DF columns:", DF.columns)
# ------------------------------------------------------------
# GUARD: STOP IF DF NOT READY
# ------------------------------------------------------------
DF = st.session_state.get("DF")

# ✅ guard first
if DF is None or DF.empty:
    st.info("Click **Fetch Ofqual Data** to continue.")
    st.stop()

# ✅ now safe to use DF
# st.write("Actual DF columns:", DF.columns)


# ------------------------------------------------------------
# NORMALISE GLH COLUMN ✅ (single authoritative block)
# ------------------------------------------------------------
glh_candidates = ["glh", "guidedLearningHours", "guided_learning_hours"]

for col in glh_candidates:
    if col in DF.columns:
        DF["glh"] = pd.to_numeric(DF[col], errors="coerce")
        break
else:
    DF["glh"] = None
    st.warning("No GLH field found in Ofqual response")



# ------------------------------------------------------------
# SIZE BANDS
# ------------------------------------------------------------
DF = DF.copy()
DF["glh"] = pd.to_numeric(DF.get("glh"), errors="coerce")

DF["Size Band"] = DF["glh"].apply(get_size_band)


# ------------------------------------------------------------
# FILTER + MERGE FOR TARIFF
# ------------------------------------------------------------
for col in ["gradingType", "gradingScale", "level", "type"]:
    DF[col] = DF.get(col, pd.NA)

DF.loc[DF["gradingType"] == "Pass/Fail", "gradingScale"] = "Pass/Fail"

mask = (
    (DF["level"] == "Level 3") &
    (DF["type"].fillna("") != "End-Point Assessment")
)


# ------------------------------------------------------------
# FILTER FOR RELEVANT QUALS
# ------------------------------------------------------------

DF_f = DF.loc[mask, :].copy()
# st.write("DF_f columns:", DF_f.columns)
# st.write(DF_f[["glh"]].head())


# ------------------------------------------------------------
# GENERATE GRADES PER QUALIFICATION ✅
# ------------------------------------------------------------
rows = []

for _, r in DF_f.iterrows():

    # ✅ generate grades for THIS qualification
    grades = generate_grades_from_scale(r["gradingScale"])

    # ✅ safeguard (your Step 4)
    if not grades:
        st.warning(
            f"No grades generated for QAN {r.get('qualificationNumber')}"
        )
        continue

    for g in grades:
        new_row = r.copy()
        new_row["glh"] = r.get("glh")
        new_row["Grade"] = g
        rows.append(new_row)

DF2 = pd.DataFrame(rows)
DF2["GLH Missing"] = DF2["glh"].isna()

if DF2["GLH Missing"].any():
    st.write("DEBUG: detailed GLH missing block triggered")
    affected = (
        DF2.loc[DF2["GLH Missing"], ["qualificationNumber", "title"]]
        .drop_duplicates()
    )

    st.warning(
        f"{len(affected)} qualification(s) are missing GLH — tariff cannot be calculated."
    )

    st.dataframe(
        affected.rename(columns={
            "qualificationNumber": "QAN",
            "title": "Qualification Title"
        }),
        hide_index=True
    )

if "glh" not in DF2.columns:
    st.error("GLH missing in DF2")
    st.stop()

# ------------------------------------------------------------
# SIZE BAND ✅
# ------------------------------------------------------------

# st.write("DF columns:", DF.columns)
# st.write(DF2[["title", "glh"]].drop_duplicates())

DF2["Size Band"] = DF2["glh"].apply(get_size_band)
# st.write(DF2[["glh", "Size Band"]].drop_duplicates())
# st.write("DF2 columns:", DF2.columns)

# ------------------------------------------------------------
# TARIFF CALCULATION ✅
# ------------------------------------------------------------
DF2["TariffNum"] = DF2.apply(calculate_tariff, axis=1)


# st.write('This is DF2, use for generating TT and TC stacks')
# st.dataframe(DF2)
# st.write(DF2.dtypes)

# Tariff Table Processing
st.subheader("Tariff Table 📖")
tariff_table = DF2[[
    'qualificationNumber', 
    'title', 
    'glh', 
    'Size Band', 
    'Grade', 
    'TariffNum', 
    'organisationName'
    ]]

tariff_table.columns = [
    'QAN',
    'Qualification Title',
    'GLH',
    'Size Band',
    'Grade',
    'TARIFF POINTS',
    'Awarding Body'
]

tariff_table["Tariff Status"] = tariff_table["GLH"].apply(
    lambda x: "OK" if pd.notna(x) else "Missing GLH"
)



# Tariff Calculator Processing

tariff_calculator = DF2[[ 'title', 'Grade','TariffNum',]] # 'qualificationNumber']] # 'glh', 'Size Band', 'Grade Score',  'organisationName']] #
tariff_calculator .columns = [ 'Qualification Title', 'Grade', 'Tariff Point'] # , 'Aliases'] # 'GLH',	'Size band',	 'Grade Band',	,	'Awarding Body'] #'QAN',
tariff_calculator [['Qualification Ordering', 'Qualification Grouping', 'Qualification Grouping Code']] = [0, 'Other', 99]   
tariff_calculator ['Aliases'] = DF2['qualificationNumber']


# ------------------------------------------------------------
# SORT TO ENSURE D > M > P ETC.
# ------------------------------------------------------------
grade_rank = {
    "D*": 0, "D": 1, "M": 2, "P": 3,
    "A*": 0, "A": 1, "B": 2, "C": 3, "D": 4, "E": 5,
}
DF2["GradeRank"] = DF2["Grade"].map(grade_rank).fillna(99)

DF2 = DF2.sort_values(["qualificationNumber", "TariffNum", "GradeRank"],
                      ascending=[True, False, True])

DF3 = DF2[["qualificationNumber", "title", "Grade", "TariffNum"]].copy()
DF3.columns = ["QAN", "Title", "Grade", "TariffNum"]


# ------------------------------------------------------------
# COLLAPSE PER QAN
# ------------------------------------------------------------


DF4 = DF3.groupby(["QAN", "Title"], as_index=False).apply(collapse)
DF4 = DF4.reset_index(drop=True)


# ------------------------------------------------------------
# DISPLAY TARIFF DATA OBJECTS
# ------------------------------------------------------------
# ------------------------------------------------------------
# MAIN SUMMARY
# ------------------------------------------------------------
st.subheader("Tariff Summary 🎯")
st.dataframe(DF4, hide_index=True)


# ------------------------------------------------------------
# DETAILED TABLE
# ------------------------------------------------------------
with st.expander("Detailed Tariff Table 📖", expanded=False):
    st.table(tariff_table.reset_index(drop=True))


# ------------------------------------------------------------
# CALCULATOR
# ------------------------------------------------------------
with st.expander("Tariff Calculator 🧮", expanded=False):
    st.table(tariff_calculator.reset_index(drop=True))
