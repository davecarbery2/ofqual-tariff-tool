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
# DEFINE CONSTANTS
# ============================================================

BASE = "https://register-api.ofqual.gov.uk/api/qualifications/"

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

BASE_MAP = {**BTEC_BASE, **ALEVEL_BASE}

# ============================================================
# FUNCTIONS
# ============================================================

def fetch_qan(q):
    url = BASE + q.replace("/", "")
    
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return [{"qualificationNumber": q, "error": r.text}]
        
        js = r.json()
        return js if isinstance(js, list) else [js]

    except Exception as e:
        return [{"qualificationNumber": q, "error": str(e)}]


@st.cache_data
def fetch_many(qans):
    rows = []
    for q in qans:
        rows.extend(fetch_qan(q))

    return pd.json_normalize(rows) if rows else pd.DataFrame()


def parse_qans(raw):
    if not raw or not raw.strip():
        return pd.DataFrame(columns=["QANs"]), []

    tokens = re.split(r"[,\;\|\t\r\n]+|\s{2,}", raw.strip())
    tokens = [t.strip() for t in tokens if t.strip()]

    cleaned = []
    for t in tokens:
        t = re.sub(r"/{2,}", "/", t)
        parts = t.split("/")
        if len(parts) == 3 and parts[-1].lower() == "x":
            parts[-1] = "X"
        cleaned.append("/".join(parts))

    pat = re.compile(r"^\d{2,4}/\d{3,5}/(\d|X)$")

    valid = [c for c in cleaned if pat.match(c)]
    invalid = [c for c in cleaned if c not in valid]

    seen = set()
    dedup = []
    for c in valid:
        if c not in seen:
            dedup.append(c)
            seen.add(c)

    return pd.DataFrame({"QANs": dedup}), invalid


def generate_grades_from_scale(scale):
    if pd.isna(scale):
        return []

    grades = re.split(r"[\/,\s]+", str(scale))
    return [g.strip() for g in grades if g.strip()]


def split_grades(grade):
    if grade is None:
        return []

    grade = str(grade).strip()
    parts = []
    i = 0

    while i < len(grade):
        if grade[i:i+2] in ["D*", "A*"]:
            parts.append(grade[i:i+2])
            i += 2
        else:
            parts.append(grade[i])
            i += 1

    return parts


def calculate_tariff(row):
    grade = row["Grade"]
    size_band = row["Size Band"]

    if grade is None or size_band is None:
        return None

    parts = split_grades(grade)
    base_score = sum(BASE_MAP.get(g, 0) for g in parts)

    return base_score * size_band


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


def collapse(g):
    grades = g["Grade"].tolist()
    tariffs = g["TariffNum"].tolist()

    pairs = sorted(zip(grades, tariffs), key=lambda x: x[1], reverse=True)

    QAN, Title = g.name

    return pd.Series({
        "QAN": QAN,
        "Title": Title,
        "Grade": " / ".join(str(p[0]) for p in pairs),
        "Tariff": " / ".join(
            str(int(p[1])) if pd.notna(p[1]) else "NA"
            for p in pairs
        )
    })


# ============================================================
# SESSION STATE
# ============================================================
st.session_state.setdefault("qans_df", None)
st.session_state.setdefault("DF", None)

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(
    page_title="Tariff Data Direct from Ofqual",
    page_icon="🇹🇹",
    layout="wide"
)

st.title("Tariff Data Direct from Ofqual")

# ============================================================
# INPUT
# ============================================================

text_input = st.text_area(
    "Paste QANs",
    height=150,
    placeholder="e.g.\n601/5731/8\n601/3330/2\n610/3958/5"
)

if text_input:
    qdf, invalid = parse_qans(text_input)
    st.session_state["qans_df"] = qdf
    st.session_state["invalid"] = invalid


qans_df = st.session_state.get("qans_df")

if qans_df is not None:
    st.success(f"{len(qans_df)} valid QAN(s) detected")

if st.session_state.get("invalid"):
    st.warning(f"{len(st.session_state['invalid'])} invalid entries removed")

with st.expander("✅ Parsed QANs"):
    st.dataframe(qans_df, hide_index=True)


# ============================================================
# GUARD
# ============================================================
if qans_df is None or qans_df.empty:
    st.info("Paste QANs above to begin.")
    st.stop()

# ============================================================
# FETCH
# ============================================================
if st.button("Fetch Ofqual Data & Process Tariffs", type="primary"):
    DF = fetch_many(qans_df["QANs"].tolist())
    st.session_state["DF"] = DF


DF = st.session_state.get("DF")

if DF is None or DF.empty:
    st.info("Click **Fetch Ofqual Data** to continue.")
    st.stop()


# ============================================================
# NORMALISE GLH
# ============================================================
for col in ["glh", "guidedLearningHours", "guided_learning_hours"]:
    if col in DF.columns:
        DF["glh"] = pd.to_numeric(DF[col], errors="coerce")
        break
else:
    DF["glh"] = None


DF["Size Band"] = DF["glh"].apply(get_size_band)

# ============================================================
# FILTER
# ============================================================
for col in ["gradingType", "gradingScale", "level", "type"]:
    DF[col] = DF.get(col, pd.NA)

mask = (
    (DF["level"] == "Level 3") &
    (DF["type"].fillna("") != "End-Point Assessment")
)

DF_f = DF.loc[mask].copy()

# ============================================================
# GENERATE GRADES (✅ KEY FIX)
# ============================================================
rows = []

for _, r in DF_f.iterrows():

    grades = generate_grades_from_scale(r["gradingScale"])

    if not grades:
        continue

    for g in grades:
        new_row = r.copy()
        new_row["Grade"] = g
        rows.append(new_row)

DF2 = pd.DataFrame(rows)

DF2["Size Band"] = DF2["glh"].apply(get_size_band)
DF2["TariffNum"] = DF2.apply(calculate_tariff, axis=1)

# ============================================================
# COLLAPSE
# ============================================================
DF3 = DF2[["qualificationNumber", "title", "Grade", "TariffNum"]].copy()
DF3.columns = ["QAN", "Title", "Grade", "TariffNum"]

DF4 = DF3.groupby(["QAN", "Title"], as_index=False).apply(collapse)
DF4 = DF4.reset_index(drop=True)

# ============================================================
# OUTPUT
# ============================================================
st.subheader("Tariff Summary 🎯")
st.dataframe(DF4, hide_index=True)

with st.expander("Detailed Tariff Table 📖"):
    st.dataframe(DF2, hide_index=True)

with st.expander("Tariff Calculator 🧮"):
    st.dataframe(DF2[["title", "Grade", "TariffNum"]], hide_index=True)
