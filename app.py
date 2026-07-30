import os
from re import match
import altair as alt
import pandas as pd
import streamlit as st

DEMO_MODE = True 

if DEMO_MODE:
    gps_path = "dummy_gps_master.csv"
    sc_path = "dummy_sc_testing_master.csv"
else:
    gps_path = "gps_master.csv"
    sc_path = "sc_testing_master.csv"

# Your page setup
st.set_page_config(page_title="Performance Dashboard", layout="wide")

# Custom CSS to hide Streamlit UI and cutoff everything after the IDP section
print_css = """
<style>
@media print {
    /* Hide standard Streamlit headers, footers, and sidebars */
    [data-testid="stSidebar"], 
    [data-testid="collapsedControl"],
    header, 
    footer { 
        display: none !important; 
    }

    /* Force the browser to print the dark theme background colors */
    body {
        -webkit-print-color-adjust: exact !important;
        print-color-adjust: exact !important;
    }

    /* THE CUTOFF TRICK: Hide the cutoff marker and ALL elements that come after it */
    div.element-container:has(#print-cutoff),
    div.element-container:has(#print-cutoff) ~ div.element-container {
        display: none !important;
    }
}
</style>
"""

# Inject the CSS into the app
st.markdown(print_css, unsafe_allow_html=True)

# --- 1. DEFINE THE ROBUST FUNCTION ---
def add_year_week(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=["Year-Week"])
    
    df.columns = df.columns.str.strip()
    
    # --- NUCLEAR BYPASS ---
    # If load_sc_data() already created this column perfectly, leave it alone!
    if "Year-Week" in df.columns:
        return df
    
    # Priority 1: S&C Data (Has 'Date' to get the year, and 'Week' is already correct)
    if 'Date' in df.columns and 'Week' in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df["Year"] = df["Date"].dt.year
        df["Year-Week"] = (df["Year"].fillna(0).astype(int).astype(str) + "-W" + 
                           df["Week"].fillna(0).astype(int).astype(str).str.zfill(2))
                           
    # Priority 2: GPS Data (Has 'Year' and 'Week' explicitly provided)
    elif 'Week' in df.columns and 'Year' in df.columns:
        df["Year-Week"] = (df['Year'].fillna(0).astype(int).astype(str) + "-W" + 
                           df['Week'].fillna(0).astype(int).astype(str).str.zfill(2))
                           
    # Priority 3: Absolute Fallback (Only has a Date column, must calculate from scratch)
    elif 'Date' in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"])
        # Use strftime('%U') for Sunday-start week numbers, avoiding the ISO +1 shift
        df["Year-Week"] = df["Date"].dt.strftime('%Y-W%U')
        
    else:
        df["Year-Week"] = "Unknown"
        
    return df

# --- GLOBAL CALLBACK ---
def sync_selection():
    if 'phase_filter' in st.session_state:
        selected_phases = st.session_state.phase_filter
        
        # Determine if we are in GPS or S&C mode to pick the right state
        # We check the session state or a global variable if you have one, 
        # or just update both if needed.
        new_weeks = [week for week, phase in phase_mapping.items() if phase in selected_phases]
        
        if new_weeks:
            # Update the specific GPS bucket
            st.session_state.gps_active_weeks = new_weeks

def render_styled(df, title):
    if df.empty:
        st.info(f"No data to display for {title}")
        return
    st.markdown(f"#### {title}")
    st.dataframe(df, use_container_width=True, hide_index=True)

# Define this function at the top level of your script
def update_week(state_key, week):
    # Get current list
    current_weeks = st.session_state.get(state_key, [])
    
    if week in current_weeks:
        # Remove it if already selected (toggle off)
        current_weeks.remove(week)
    else:
        # Add it if not selected (toggle on)
        current_weeks.append(week)
    
    # Sort them so they stay in order
    st.session_state[state_key] = sorted(current_weeks, reverse=True)

   # --- 1. DEFINE ALL FUNCTIONS FIRST ---
def get_squad_rank_df(full_data, metric, test_col, t_col):
# NEW GUARDRAIL: If the data is empty or missing required columns, stop here.
    if full_data.empty or test_col not in full_data.columns or t_col not in full_data.columns:
        return pd.DataFrame(columns=['Player', test_col, t_col, 'Rank_Str'])
    
    d = full_data.copy()
    
    # Force the metric to be a numeric float (This prevents string-sorting bugs)
    d[metric] = pd.to_numeric(d[metric], errors='coerce')
    d = d[d[metric].notna() & (d[metric] > 0)]

    # Initialize columns
    d['Rank'] = 0
    d['Total'] = 0

    # Iterate manually through unique tests to completely isolate the ranking logic
    for test in d[test_col].unique():
        is_bronco = "bronco" in str(test).lower().strip()
        
        for tag in d[t_col].unique():
            # Create a true/false mask for this exact Test + Tag combination
            mask = (d[test_col] == test) & (d[t_col] == tag)
            
            if mask.any():
                if is_bronco:
                    # Bronco: Ascending=True (Smaller time = Rank 1)
                    d.loc[mask, 'Rank'] = d.loc[mask, metric].rank(method='min', ascending=True)
                else:
                    # Strength: Ascending=False (Larger weight = Rank 1)
                    d.loc[mask, 'Rank'] = d.loc[mask, metric].rank(method='min', ascending=False)
                
                # Calculate total athletes in this group
                d.loc[mask, 'Total'] = mask.sum()

    # Format the Rank_Str output
    d['Rank'] = d['Rank'].astype(int)
    d['Rank_Str'] = d['Rank'].astype(str) + "/" + d['Total'].astype(int).astype(str)
    
    return d[['Player', test_col, t_col, 'Rank_Str']]

def render_styled(df_table, title, key):
    if df_table.empty: return
    st.markdown(f"**{title}**")
    styler = df_table.style

    # 1. Helper to color the Test Name column
    def style_test_name(val):
        colors = {
            "Back Squat": "background-color: red; color: white", 
            "Bench Press": "background-color: navy; color: white", 
            "Pull Up": "background-color: white; color: black; border: 1px solid black", 
            "Power Clean": "background-color: turquoise; color: white",
            "Bronco": "background-color: #800080; color: #FFFFFF"
        }
        return colors.get(val, 'background-color: transparent; color: white')

    # 2. Helper to color values based on the Test Name in that row
    def get_diff_style(row):
        styles = [''] * len(row)  # Default: no style
        test_name = row['Test Name']
        
        for i, col in enumerate(df_table.columns):
            if "Rank" in col or col == "Test Name":
                continue
            
            val_str = str(row[col])
            if "(" in val_str and ")" in val_str:
                try:
                    diff_val = float(val_str.split('(')[1].replace(')', ''))
                    
                    # Logic: If Bronco, lower is better (Green if diff < 0)
                    if test_name == "Bronco":
                        if diff_val < 0: styles[i] = 'color: #00FF00; font-weight: bold'
                        elif diff_val > 0: styles[i] = 'color: #FF4444; font-weight: bold'
                    else:
                        # Strength: higher is better (Green if diff > 0)
                        if diff_val > 0: styles[i] = 'color: #00FF00; font-weight: bold'
                        elif diff_val < 0: styles[i] = 'color: #FF4444; font-weight: bold'
                        
                    if styles[i] == '': styles[i] = 'color: #FFD700; font-weight: bold' # Yellow for 0.0
                except: pass
        return styles

    # Apply styles using .apply (row-wise)
    styler = styler.apply(get_diff_style, axis=1)
    styler = styler.map(style_test_name, subset=["Test Name"])
    
    st.dataframe(styler, use_container_width=True, hide_index=True, key=key)
            
def build_stage_table(p_name):
    p_df = df[(df['Player'] == p_name)].copy()
    bw_col = "Body Weight (kg)"
    
    # Identify latest week per test
    p_df['Latest_Test_Week'] = p_df.groupby('Test Name')['Week'].transform('max')
    latest_data_df = p_df[p_df['Week'] == p_df['Latest_Test_Week']].copy()
    latest_data_df = latest_data_df.drop_duplicates(subset=['Test Name'])
    
    # Convert ALL necessary metrics to dictionaries, INCLUDING Body Weight
    data_1rm = latest_data_df.set_index('Test Name')['1RM Predicted'].to_dict()
    data_reps = latest_data_df.set_index('Test Name')['Reps_Num'].to_dict()
    data_load = latest_data_df.set_index('Test Name')['Weight_Num'].to_dict()
    
    # NEW: Get the exact body weight recorded for that specific test row
    data_bw = latest_data_df.set_index('Test Name')[bw_col].to_dict() if bw_col in latest_data_df.columns else {}
    
    stage_data = {}
    for test in ["Back Squat", "Bench Press", "Pull Up", "Bronco"]:
        if test in data_1rm:
            # Safely fetch the specific body weight for THIS test, fallback to 0
            test_specific_bw = float(data_bw.get(test, 0))
            
            stage_data[test] = get_stage(
                test, 
                data_1rm[test], 
                test_specific_bw, 
                data_reps.get(test, 0),
                data_load.get(test, 0)
            )
        else:
            stage_data[test] = "N/A"
            
    return pd.DataFrame([stage_data])

def build_table(p_name, metric_name, col_label, precision, rank_df, tag_col):
    p_df = df[df['Player'] == p_name].copy()
    
    # Ensure numeric for safety
    p_df[metric_name] = pd.to_numeric(p_df[metric_name], errors='coerce')
    p_df[metric_name] = p_df[metric_name].replace(0, pd.NA)

    pivot = p_df.pivot_table(index='Test Name', columns=tag_col, values=metric_name, aggfunc='last', dropna=False)
    if pivot.isna().all().all(): return pd.DataFrame()

    data = {"Test Name": pivot.index.tolist()}
    all_tags = sorted(pivot.columns.tolist())

    for i, tag in enumerate(all_tags):
        col = f"{tag} {col_label}"
        
        col_values = []
        ranks = []
        
        for t_name in pivot.index:
            val = pivot.loc[t_name, tag]
            
            # 1. Handle Values
            if pd.isna(val) or val == 0:
                col_values.append("N/A")
            else:
                val_float = float(val)
                if i > 0:
                    prev_tag = all_tags[i-1]
                    prev_val = pivot.loc[t_name, prev_tag]
                    if pd.notna(prev_val) and prev_val != 0:
                        diff = val_float - float(prev_val)
                        col_values.append(f"{val_float:.{precision}f} ({diff:+.{precision}f})")
                    else:
                        col_values.append(f"{val_float:.{precision}f}")
                else:
                    col_values.append(f"{val_float:.{precision}f}")
            
            # 2. Handle Ranks
            match = rank_df[(rank_df['Player'] == p_name) & 
                            (rank_df['Test Name'] == str(t_name)) & 
                            (rank_df[tag_col] == str(tag))]
            val_exists = pd.notna(val) and val != 0
            ranks.append(match['Rank_Str'].iloc[0] if (not match.empty and val_exists) else "N/A")
        
        data[col] = col_values
        data[f"Rank_{tag}"] = ranks
        
    final_df = pd.DataFrame(data)
    
    # 3. The Invisible Space Trick: Fixes the Styler KeyError 
    new_cols = []
    rank_spaces = "" 
    for c in final_df.columns:
        if c.startswith("Rank_"):
            new_cols.append("Rank" + rank_spaces)
            rank_spaces += " "  # Adds a hidden space for the next duplicate
        else:
            new_cols.append(c)
            
    final_df.columns = new_cols
    return final_df

def get_rs_cell_style(test_name, rs_val):
    val = float(rs_val or 0)
    
    # 1. Define Box Colors (Background)
    box_colors = {
        "Back Squat": "#FF0000",   # Red
        "Bench Press": "#000080",  # Navy
        "Pull Up": "#FFFFFF",      # White
        "Power Clean": "#40E0D0"   # Turquoise
    }
    bg = box_colors.get(test_name, "#262730")
    
    # 2. Text Colors
    # For Pull Up (White box), force black text; otherwise white
    text_col = "#000000" if test_name == "Pull Up" else "#FFFFFF"
        
    return f"background-color: {bg}; color: {text_col}; font-weight: bold; text-align: center;"

def safe_float(val, default=0.0):
    """Safely converts any value to a float, returning a default if it fails."""
    if pd.isna(val) or val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def get_rs_text_color(test_name, row):
    # Safely extract all values using our new helper
    load_val = safe_float(row.get('Load', 0))
    reps = safe_float(row.get('Reps', 0))
    bw = safe_float(row.get('BW', 70))
    if bw == 0: bw = 70.0 # Prevent division by zero
    
    # 1. Handle Bronco
    if test_name == "Bronco":
        val = safe_float(row.get('1RM', 0))
        if val <= 0: return ""
        if val < 320: return "color: #00FF00; font-weight: bold"
        if val < 340: return "color: #FFFF00; font-weight: bold"
        if 340 <= val <= 355: return "color: #FFA500; font-weight: bold"
        if 356 <= val <= 369: return "color: #FF0000; font-weight: bold"
        return "color: #0000FF; font-weight: bold"

    # 2. Handle Max Speed
    elif test_name == "Max Speed":
        val = safe_float(row.get('1RM', 0)) # Uses the same key as Bronco for the primary score
        if val <= 0: return "color: #0000FF; font-weight: bold" # N/A
        if val >= 31.0: return "color: #00FF00; font-weight: bold"   # Stage 3
        if val >= 29.0: return "color: #FFFF00; font-weight: bold"   # Stage 2
        if val >= 27.0: return "color: #FFA500; font-weight: bold"   # Stage 1B
        return "color: #FF0000; font-weight: bold"                  # Stage 1A

    # 3. Guard: If it's a strength test but RS is missing, exit
    rs_val_str = str(row.get('RS', ''))
    if rs_val_str == "": return ""
    
    rs_val = safe_float(row.get('RS', 0))
    if rs_val <= 0: return "color: #0000FF; font-weight: bold" # Blue for N/A

    # --- Back Squat ---
    if test_name == "Back Squat":
        if load_val < 90: return "color: #0000FF; font-weight: bold" # Blue
        if rs_val >= 1.9: return "color: #00FF00; font-weight: bold" # Green
        if rs_val >= 1.7: return "color: #FFFF00; font-weight: bold" # Yellow
        if load_val >= 100: return "color: #FFA500; font-weight: bold" # Orange
        return "color: #FF0000; font-weight: bold" # Red
    
    # --- Bench Press ---
    elif test_name == "Bench Press":
        if load_val < 45: return "color: #0000FF; font-weight: bold" # Blue
        if rs_val >= 1.2: return "color: #00FF00; font-weight: bold"
        if rs_val >= 1.1: return "color: #FFFF00; font-weight: bold"
        if load_val >= 55: return "color: #FFA500; font-weight: bold"
        return "color: #FF0000; font-weight: bold"

    # --- Power Clean ---
    elif test_name == "Power Clean":
        if load_val < 40: return "color: #0000FF; font-weight: bold" # Blue (N/A)
        if rs_val >= 1.3: return "color: #00FF00; font-weight: bold" # Green (Stage 3)
        if rs_val >= 1.0: return "color: #FFFF00; font-weight: bold" # Yellow (Stage 2)
        if load_val >= 50: return "color: #FFA500; font-weight: bold" # Orange (Stage 1B)
        return "color: #FF0000; font-weight: bold" # Red (Stage 1A: 40-49kg)
        
    # --- Pull Up Logic ---
    elif test_name == "Pull Up":
        if load_val > (bw + 0.5):
            ratio = load_val / bw
            if ratio >= 1.4: return "color: #00FF00; font-weight: bold"
            if ratio >= 1.3: return "color: #FFFF00; font-weight: bold"
            return "color: #FFA500; font-weight: bold"
        else:
            if reps >= 5: return "color: #FFA500; font-weight: bold"
            if 2 <= reps <= 4: return "color: #FF0000; font-weight: bold"
            return "color: #0000FF; font-weight: bold"

    return "color: white; font-weight: bold"

# 1. Global Page Configuration (Must be first)
st.set_page_config(
    page_title="Rugby S&C Performance Dashboard", layout="wide", page_icon="🏉"
)


def get_stage(test_name, val, bw, reps, load_kg):
    val = float(val or 0)
    bw = float(bw or 0)
    reps = float(reps or 0)
    load_kg = float(load_kg or 0)
    
    if val <= 0: return "N/A"
    
    # BACK SQUAT
    if test_name == "Back Squat":
        if val >= 1.9 * bw: return "Stage 3"
        elif val >= 1.7 * bw: return "Stage 2"
        elif val >= 100: return "Stage 1B"
        elif val >= 90: return "Stage 1A"
        return "N/A"
    
    # BENCH PRESS
    elif test_name == "Bench Press":
        if val >= 1.2 * bw: return "Stage 3"
        elif val >= 1.1 * bw: return "Stage 2"
        elif val >= 55: return "Stage 1B"
        elif val >= 45: return "Stage 1A"
        return "N/A"

    # POWER CLEAN
    elif test_name == "Power Clean":
        if val >= 1.3 * bw: return "Stage 3"
        elif val >= 1.0 * bw: return "Stage 2"
        elif val >= 50: return "Stage 1B"
        elif val >= 40: return "Stage 1A"
        return "N/A"
        
    # PULL UP
    elif test_name == "Pull Up":
        effective_load = bw if load_kg <= 0 else load_kg
        r_load = round(effective_load, 1)
        r_bw = round(bw, 1)
        
        if r_load > r_bw:
            if val >= 1.4 * bw: return "Stage 3"
            elif val >= 1.3 * bw: return "Stage 2"
            else: return "Stage 1B"
        elif r_load <= r_bw:
            if reps >= 5: return "Stage 1B"
            elif 2 <= reps <= 4: return "Stage 1A"
            else: return "N/A"
    
    # BRONCO
    elif test_name == "Bronco":
        if val >= 370: return "N/A"
        if val < 320: return "Stage 3"
        elif val < 340: return "Stage 2"
        elif val <= 355: return "Stage 1B"
        return "Stage 1A"

    # MAX SPEED
    elif test_name == "Max Speed":
        if val >= 31: return "Stage 3"
        elif val >= 29: return "Stage 2"
        elif val >= 27: return "Stage 1B"
        return "Stage 1A"
        
    return "N/A"

# 2. Data Ingestion Paths
DATA_DIR = "data"
GPS_FILE = os.path.join(DATA_DIR, "gps_master.csv")
SC_FILE = os.path.join(DATA_DIR, "sc_testing_master.csv")

@st.cache_data
def load_gps_data(file_path):
    if not os.path.exists(file_path):
        return pd.DataFrame()
    
    # header=0 tells pandas to use the first row as the header
    df = pd.read_csv(file_path, header=0)
    
    # Strip whitespace from column names to ensure "Year" is recognized
    df.columns = df.columns.str.strip()
    return df

@st.cache_data
def load_sc_data(file_path):
    if not os.path.exists(file_path):
        # Return an empty DataFrame but with the exact columns your app expects
        return pd.DataFrame(columns=[
            "Test Name", "Player", "Date", "Week", "Body Weight (kg)", 
            "Load (kg)", "Reps", "Year-Week", "1RM Predicted", "Relative Strength", "Entry Type"
        ])
        
    df = pd.read_csv(file_path)
    df.columns = df.columns.str.strip()

    # 1. Rename 'Exercise' to 'Test Name' FIRST
    if "Exercise" in df.columns:
        df = df.rename(columns={"Exercise": "Test Name"})

    # 2. Safely perform replacements ONLY if 'Test Name' exists
    if "Test Name" in df.columns:
        df["Test Name"] = df["Test Name"].replace("Bench Press (Swiss Bar)", "Bench Press")

    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%y", errors="coerce")

    numeric_cols = ["Body Weight (kg)", "Load (kg)", "Reps"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # EXACT YEAR-WEEK CALCULATION
    if "Date" in df.columns and "Week" in df.columns:
        year_str = df['Date'].dt.year.astype(str).str.replace('.0', '', regex=False)
        week_str = df['Week'].astype(str).str.replace('.0', '', regex=False).str.zfill(2)
        df['Year-Week'] = year_str + "-W" + week_str

    # BRZYCKI FORMULA CALCULATION
    def calculate_brzycki(row):
        load = row.get("Load (kg)", 0)
        reps = row.get("Reps", 0)
        if load <= 0 or reps <= 0: return 0
        if reps == 1: return load
        if reps > 37: return load
        return round(load / (1.0278 - (0.0278 * reps)), 1)

    df["1RM Predicted"] = df.apply(calculate_brzycki, axis=1)
    
    # Calculate Relative Strength safely
    if "Body Weight (kg)" in df.columns:
        df["Relative Strength"] = (df["1RM Predicted"] / df["Body Weight (kg)"].replace(0, 1)).round(2)

    return df

# --- UNIVERSAL GLOBAL DATA INGESTION ---
# Pass gps_path and sc_path here!
raw_gps_df = load_gps_data(gps_path)
raw_sc_df = load_sc_data(sc_path)

# Apply Year-Week helper
raw_gps_df = add_year_week(raw_gps_df)
raw_sc_df = add_year_week(raw_sc_df)

# Guarantee clean formatting
if "Entry Type" in raw_sc_df.columns:
    raw_sc_df["Entry Type"] = raw_sc_df["Entry Type"].astype(str).str.strip()
if "Test Name" in raw_sc_df.columns:
    raw_sc_df["Test Name"] = raw_sc_df["Test Name"].astype(str).str.strip()
if "1RM Predicted" in raw_sc_df.columns:
    raw_sc_df["1RM Predicted"] = pd.to_numeric(raw_sc_df["1RM Predicted"], errors="coerce").fillna(0)
if "max speed" in raw_gps_df.columns:
    raw_gps_df["max speed"] = pd.to_numeric(raw_gps_df["max speed"], errors="coerce").fillna(0)

# Load raw data globally (Pass gps_path and sc_path here too!)
gps_df = load_gps_data(gps_path)
sc_df = load_sc_data(sc_path)

# Apply Year-Week helper ONCE
gps_df = add_year_week(gps_df)
sc_df = add_year_week(sc_df)


header_styles = {
    "Back Squat": {"bg": "#FF0000", "text": "#FFFFFF"},
    "Bench Press": {"bg": "#000080", "text": "#FFFFFF"},
    "Pull Up": {"bg": "#FFFFFF", "text": "#000000"},
    "Bronco": {"bg": "#800080", "text": "#FFFFFF"},
    "Power Clean": {"bg": "#40E0D0", "text": "#FFFFFF"},
    "Max Speed": {"bg": "#87CEEB", "text": "#FFFFFF"}, # Dark Orange
}

# ==========================================
# 🏋️‍♂️ MASTER TEST NAME STANDARDIZATION (TOP OF SCRIPT)
# ==========================================
if not sc_df.empty and "Test Name" in sc_df.columns:
    # 1. Clean off any hidden trailing/leading whitespaces first
    sc_df["Test Name"] = sc_df["Test Name"].astype(str).str.strip()
    
    # 2. Use partial matching (Ignores typos, case-sensitivity, extra spaces, or bracket styles)
    sc_df.loc[sc_df["Test Name"].str.contains("Swiss Bar", case=False, na=False), "Test Name"] = "Bench Press"
    sc_df.loc[sc_df["Test Name"].str.contains("Safety Bar", case=False, na=False), "Test Name"] = "Back Squat"
    sc_df.loc[sc_df["Test Name"].str.contains("Decline", case=False, na=False), "Test Name"] = "Bench Press"

# ==========================================
    # ⚖️ SAFELY HANDLE MISSING BODY WEIGHT
    # ==========================================
    if "Body Weight (kg)" in sc_df.columns and "Relative Strength" in sc_df.columns:
        # Force BW to be a number (turns blanks into NaN)
        sc_df["Body Weight (kg)"] = pd.to_numeric(sc_df["Body Weight (kg)"], errors="coerce")
        
        # Identify rows where Body Weight is missing or exactly 0
        missing_bw_mask = sc_df["Body Weight (kg)"].isna() | (sc_df["Body Weight (kg)"] == 0)
        
        # Erase the skewed Relative Strength calculation for those specific rows
        sc_df.loc[missing_bw_mask, "Relative Strength"] = pd.NA

# --- 3. SYNC SESSION STATE ---
# Initialize session state only if not already there
if 'gps_active_weeks' not in st.session_state:
    all_gps = sorted(gps_df["Year-Week"].unique().tolist(), reverse=True)
    st.session_state.gps_active_weeks = [all_gps[0]] if all_gps else []

# --- 1. INITIALIZE SESSION STATE ---
if 'sc_active_weeks' not in st.session_state:
    st.session_state.sc_active_weeks = []

if 'sc_available_weeks' not in st.session_state:
    st.session_state.sc_available_weeks = []
    
# --- 4. INITIALIZE SESSION STATE ---

if 'selected_metrics' not in st.session_state:
    # Define the default list based on what is actually in your DF
    active_metrics = [m for m in ['total distance', 'hml distance', 'sprint distance', 'max speed', 'sprints', 'average heart rate'] if m in gps_df.columns]
    st.session_state.selected_metrics = active_metrics[:3]

# --- 3. SYNC SESSION STATE ---
if 'gps_active_weeks' not in st.session_state:
    if not gps_df.empty and "Year-Week" in gps_df.columns:
        all_gps = sorted(gps_df["Year-Week"].unique().tolist(), reverse=True)
        st.session_state.gps_active_weeks = [all_gps[0]] if all_gps else []
    else:
        st.session_state.gps_active_weeks = []

# --- SAFE PHASE MAPPING ---
if not gps_df.empty and "Year-Week" in gps_df.columns and "Training Phase" in gps_df.columns:
    phase_mapping = gps_df.groupby("Year-Week")["Training Phase"].first().to_dict()
else:
    phase_mapping = {}

# ==========================================
# --- 5. SIDEBAR NAVIGATION ---
# ==========================================
dashboard_mode = st.sidebar.radio(
    "Navigation", ["🏃‍♂️ GPS Tracking", "🏋️‍♂️ S&C Testing", "🎯 IDP Generator"]
)

# --- 👥 GLOBAL ROSTER VIEW ---
squad_view = st.sidebar.radio(
    "👥 Roster View", ["Current Squad", "All-Time"], index=0
)

# --- GLOBAL CHRONOLOGICAL ORDERING ---
timeline_col = 'Training Phase' 
chronological_axis_order = []

if not gps_df.empty and timeline_col in gps_df.columns:
    if 'Week' in gps_df.columns:
        gps_df['Week'] = pd.to_numeric(gps_df['Week'], errors='coerce')
        phase_order_df = gps_df.groupby(timeline_col)['Week'].min().sort_values().reset_index()
        chronological_axis_order = phase_order_df[timeline_col].tolist()
    else:
        chronological_axis_order = sorted(gps_df[timeline_col].dropna().unique().tolist())

if 'gps_active_weeks' not in st.session_state:
    st.session_state['gps_active_weeks'] = []
if 'sc_active_weeks' not in st.session_state:
    st.session_state['sc_active_weeks'] = []


# ==========================================
# ⚡ LIVE DATA ROUTING ENGINE
# ==========================================
if dashboard_mode == "🏃‍♂️ GPS Tracking":
    state_key = 'gps_active_weeks'
    
    # Filter GPS display data using the sidebar Roster View
    working_gps_df = gps_df.copy()
    if squad_view == "Current Squad" and 'Squad Status' in working_gps_df.columns:
        working_gps_df = working_gps_df[working_gps_df['Squad Status'] == 'Active']
        
    data_source = working_gps_df
    current_weeks = sorted(working_gps_df["Year-Week"].unique().tolist(), reverse=True)
    filtered_df_sc = None

# 🛑 CHANGE 1: Explicitly lock the S&C Controls to the S&C Testing page only
elif dashboard_mode == "🏋️‍♂️ S&C Testing":
    state_key = 'sc_active_weeks'
    
    # S&C Master Data Cleanup
    sc_df.columns = sc_df.columns.str.strip()
        # Apply the same Roster View filter used in GPS mode
    if squad_view == "Current Squad" and 'Squad Status' in sc_df.columns:
        sc_df = sc_df[sc_df['Squad Status'] == 'Active']
    tag_col = 'Testing_Tag' if 'Testing_Tag' in sc_df.columns else ('Testing Tag' if 'Testing Tag' in sc_df.columns else None)
    
    COL_WEIGHT = next((c for c in ['Load Lifted', 'Load', 'Load (kg)'] if c in sc_df.columns), 'Load Lifted')
    COL_REPS = next((c for c in ['Reps', 'Repetitions'] if c in sc_df.columns), 'Reps')
    
    sc_df['Weight_Num'] = pd.to_numeric(sc_df[COL_WEIGHT], errors='coerce')
    sc_df['Reps_Num'] = pd.to_numeric(sc_df[COL_REPS], errors='coerce')
    sc_df['BW_Num'] = pd.to_numeric(sc_df['Body Weight (kg)'], errors='coerce')
    sc_df['1RM Predicted'] = sc_df['Weight_Num'] / (1.0278 - (0.0278 * sc_df['Reps_Num']))
    sc_df['RelStr'] = sc_df['1RM Predicted'] / sc_df['BW_Num']

    stage_colors = {
        "Stage 1A": "#FF0000", "Stage 1B": "#FFA500", "Stage 2": "#FFD700", "Stage 3": "#008000"   
    }

    # ==========================================
    # --- DEFINE WORKING DATAFRAME FIRST ---
    # ==========================================
    working_sc_df = sc_df.copy()
    
    # Check if squad_view exists and filter if needed
    if 'squad_view' in locals() or 'squad_view' in globals():
        if squad_view == "Current Squad" and 'Squad Status' in working_sc_df.columns:
            working_sc_df = working_sc_df[working_sc_df['Squad Status'] == 'Active']

    # 🎛️ S&C Global Controls 
    st.sidebar.header("🎛️ Global S&C Controls")
    
    all_players = sorted(working_sc_df['Player'].dropna().unique().tolist()) if 'Player' in sc_df.columns else []
    selected_players = st.sidebar.multiselect("1️⃣ Select Athletes:", all_players, key="sc_filter_players")
    df_step1 = working_sc_df[working_sc_df['Player'].isin(selected_players)] if selected_players else working_sc_df.copy()

    available_entries = sorted(df_step1['Entry Type'].dropna().unique().tolist()) if 'Entry Type' in df_step1.columns else []
    selected_entry_types = st.sidebar.multiselect("2️⃣ Select Entry Type:", available_entries, key="sc_filter_entries")
    df_step2 = df_step1[df_step1['Entry Type'].isin(selected_entry_types)] if selected_entry_types else df_step1

    available_tests = sorted(df_step2['Test Name'].dropna().unique().tolist()) if 'Test Name' in df_step2.columns else []
    selected_tests = st.sidebar.multiselect("3️⃣ Select Test Name:", available_tests, key="sc_filter_tests")
    df_step3 = df_step2[df_step2['Test Name'].isin(selected_tests)] if selected_tests else df_step2

    selected_tags = []
    if tag_col:
        available_tags = sorted(df_step3[tag_col].dropna().unique().tolist())
        selected_tags = st.sidebar.multiselect("🏷️ Testing Tag:", available_tags, key="sc_filter_tags")
    
    filtered_df_sc = df_step3[df_step3[tag_col].isin(selected_tags)] if selected_tags and tag_col else df_step3
    data_source = filtered_df_sc
    
    current_weeks = sorted(filtered_df_sc["Year-Week"].dropna().unique().tolist(), reverse=True)
    st.session_state.sc_available_weeks = current_weeks
    st.session_state.sc_active_weeks = [w for w in st.session_state.sc_active_weeks if w in current_weeks]

# ==========================================
# 🎨 CUSTOM CSS STYLING
# ==========================================
st.markdown("""
<style>
div[data-testid="stHorizontalBlock"]:has(> div[data-testid="stColumn"]:nth-child(5)):has(div[data-testid="stButton"]) {
    display: flex !important; flex-direction: row !important; flex-wrap: nowrap !important;   
    overflow-x: auto !important; overflow-y: hidden !important; padding-bottom: 15px !important; gap: 5px !important;
}
div[data-testid="stHorizontalBlock"]:has(> div[data-testid="stColumn"]:nth-child(5)):has(div[data-testid="stButton"]) > div[data-testid="stColumn"] {
    width: 85px !important; min-width: 85px !important; max-width: 85px !important; flex: 0 0 85px !important; 
}
div[data-testid="stHorizontalBlock"]:has(> div[data-testid="stColumn"]:nth-child(5)):has(div[data-testid="stButton"])::-webkit-scrollbar { height: 8px; }
div[data-testid="stHorizontalBlock"]:has(> div[data-testid="stColumn"]:nth-child(5)):has(div[data-testid="stButton"])::-webkit-scrollbar-thumb { background-color: #4B4B4B; border-radius: 10px; }
</style>
""", unsafe_allow_html=True)


# 🛑 CHANGE 2: Wrap the Timeline display so it completely hides on the IDP page
if dashboard_mode != "🎯 IDP Generator":
    # ==========================================
    # 📅 RENDER DASHBOARD TIMELINE
    # ==========================================
    st.title("🏉 Rugby S&C Performance Dashboard")
    st.markdown("### 📅 Select Timeline")

    c1, c2, c3, _ = st.columns([1, 1, 1, 7]) 

    with c1:
        if st.button("✅ All", key="btn_sel"):
            st.session_state[state_key] = current_weeks
            st.rerun()

    with c2:
        if st.button("❌ All", key="btn_clr"):
            st.session_state[state_key] = []
            st.rerun()

    with c3:
        if st.button("🕒 Recent", key="btn_recent"):
            target_weeks = []
            if dashboard_mode == "🏋️‍♂️ S&C Testing":
                valid_data = data_source[data_source['1RM Predicted'] > 0]
                if not valid_data.empty:
                    target_weeks = valid_data.groupby('Test Name')['Year-Week'].max().unique().tolist()
            else:
                valid_data = data_source.dropna(how='all')
                valid_weeks = valid_data["Year-Week"].unique().tolist()
                if valid_weeks:
                    target_weeks = [max(valid_weeks)]
                    
            if target_weeks:
                st.session_state[state_key] = sorted(target_weeks, reverse=True)
                st.rerun()

    with st.container():
        if len(current_weeks) > 0:
            cols = st.columns(len(current_weeks)) 
            for i, week in enumerate(current_weeks):
                is_active = week in st.session_state.get(state_key, [])
                with cols[i]:
                    st.button(
                        week.replace("-", "\n"), 
                        key=f"btn_{dashboard_mode}_{week}", 
                        type="primary" if is_active else "secondary", 
                        on_click=update_week, 
                        args=(state_key, week)
                    )
        else:
            st.info("No timeline data available for these filter settings.")

# ==========================================
# 🏃‍♂️ GPS TRACKING MODULE
# ==========================================
if dashboard_mode == "🏃‍♂️ GPS Tracking":
    available_weeks = sorted(gps_df["Year-Week"].unique().tolist())
    st.write("GPS Logic active...")
    if gps_df.empty:
        st.error("⚠️ GPS data not found.")
        st.stop()

    # --- 1. SETUP & SORTING ---
    player_col = 'Player'
    timeline_col = 'Training Phase' 
    
    # Dynamic Sorting for Phases
    if timeline_col in gps_df.columns and 'Week' in gps_df.columns:
        gps_df['Week'] = pd.to_numeric(gps_df['Week'], errors='coerce')
        phase_order_df = gps_df.groupby(timeline_col)['Week'].min().sort_values().reset_index()
        chronological_axis_order = phase_order_df[timeline_col].tolist()
    else:
        chronological_axis_order = sorted(gps_df[timeline_col].dropna().unique().tolist()) if timeline_col in gps_df.columns else []
    
# --- 2. SIDEBAR CONTROLS ---
    st.sidebar.header("🎛️ Global GPS Controls")
    
    # 1. Player Filter (Correctly implemented)
    all_players = sorted(gps_df[player_col].unique())
    selected_players = st.sidebar.multiselect(
        "👥 Select Athletes:", 
        options=all_players, 
        default=st.session_state.get('selected_players', all_players[:5]),
        key="player_filter"
    )
    st.session_state.selected_players = selected_players    
    
    # 2. Metric Filter (Apply the same pattern)
# 2. Metric Filter (Apply the same pattern)
    metric_choices = {
        'total distance': 'Total Distance (m)', 
        'hml distance': 'High Metabolic Load Distance (m)', 
        'sprint distance': 'Sprint Distance (m)', 
        'max speed': 'Max Speed (km/h)', 
        'sprints': 'Sprint Count', 
        'average heart rate': 'Average Heart Rate (bpm)',
        'high speed running (absolute)': 'High Speed Running (m)' # Added this line
    }
    
    # This automatically includes the key if it exists in your CSV columns
    active_metrics = [m for m in metric_choices.keys() if m in gps_df.columns]
    
    selected_metrics = st.sidebar.multiselect(
        "📊 Select Tracked Metrics:", 
        options=active_metrics, 
        default=st.session_state.get('selected_metrics', active_metrics[:3]), 
        format_func=lambda x: metric_choices[x],
        key="metric_filter"
    )
    st.session_state.selected_metrics = selected_metrics
    
# 3. Timeline/Phase Filter
    
    # 1. Update the state to match the timeline buttons BEFORE the widget renders.
    # We do this so the sidebar 'knows' what the timeline buttons selected.
    current_phases = list(set([phase_mapping.get(week) for week in st.session_state.gps_active_weeks if phase_mapping.get(week)]))
    st.session_state.phase_filter = current_phases
    
    # --- 3. Timeline/Phase Filter ---
    # Only set the default if the sidebar filter is empty
    if 'phase_filter' not in st.session_state or not st.session_state.phase_filter:
        st.session_state.phase_filter = list(set([phase_mapping.get(week) for week in st.session_state.gps_active_weeks if phase_mapping.get(week)]))

    phases = st.sidebar.multiselect(
        f"📅 Filter {timeline_col}:", 
        options=chronological_axis_order, 
        key="phase_filter",
        on_change=sync_selection 
    )

    # --- 3. APPLY FILTERS SIMULTANEOUSLY ---
    # Access the specific GPS state key
    gps_active = st.session_state.gps_active_weeks

    # Apply filters, INCLUDING the week filter
    mask = (gps_df[player_col].isin(selected_players)) & \
        (gps_df[timeline_col].isin(phases)) & \
        (gps_df["Year-Week"].isin(gps_active)) # <--- Add this

    filtered_gps = gps_df[mask].copy()
    
# If the user has empty selections, handle it here
    if not selected_players or not phases:
        st.warning("⚠️ No athletes or phases selected. Please make a selection.")
    elif filtered_gps.empty:
        st.warning("⚠️ No data matches the current filter selection.")
    else:
        # Proceed with displaying data
        st.write(f"Displaying {len(filtered_gps)} records.")

    # DEFINE TABS
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Squad Leaderboard", "📈 Squad Visualizations", "👤 Individual Profiles", "📋 Data Ledger"])

    with tab4:
        st.subheader("Raw Data Ledger")
        if not filtered_gps.empty:
            st.dataframe(filtered_gps, use_container_width=True, hide_index=True)
        else:
            st.info("No data available for the selected filters.")

    with tab1:
        st.subheader("Squad Performance Output Summary")
        if not filtered_gps.empty:
            agg_gps = filtered_gps.groupby(player_col, as_index=False).agg({m: 'max' if m == 'max speed' else 'sum' for m in selected_metrics})
            st.dataframe(agg_gps.rename(columns=metric_choices), use_container_width=True, hide_index=True)

    with tab2:
        st.subheader("Squad Visual Charts")
        if not filtered_gps.empty and timeline_col:
            for metric in selected_metrics:
                agg_func = 'max' if metric == 'max speed' else 'sum'
                squad_trend_df = filtered_gps.groupby([timeline_col, player_col], as_index=False).agg({metric: agg_func})
                st.markdown(f"#### 📈 Squad Track: {metric_choices[metric]}")
                squad_line_chart = alt.Chart(squad_trend_df).mark_line(point=True, strokeWidth=3).encode(
                    x=alt.X(f'{timeline_col}:N', title=timeline_col, sort=chronological_axis_order),
                    y=alt.Y(f'{metric}:Q', title=metric_choices[metric], scale=alt.Scale(zero=False)),
                    color=alt.Color(f'{player_col}:N', title='Athletes', scale=alt.Scale(scheme='tableau10')),
                    tooltip=[player_col, timeline_col, alt.Tooltip(f'{metric}:Q', title=metric_choices[metric])]
                ).properties(height=420).interactive() # <-- Added .interactive() here
                st.altair_chart(squad_line_chart, use_container_width=True)
                st.markdown("---")

    with tab3:
        st.subheader("👤 Longitudinal Individual Athlete Profiles")
        if not filtered_gps.empty and timeline_col:
            agg_rules = {m: 'max' if m == 'max speed' else 'sum' for m in selected_metrics}
            player_history = filtered_gps.groupby([player_col, timeline_col], as_index=False).agg(agg_rules)
            for metric in selected_metrics:
                st.markdown(f"#### 📊 Week-by-Week Trend: {metric_choices[metric]}")
                indiv_bar_chart = alt.Chart(player_history).mark_bar().encode(
                    x=alt.X(f'{player_col}:N', title="Athlete Profiles"),
                    y=alt.Y(f'{metric}:Q', title=metric_choices[metric]),
                    color=alt.Color(f'{timeline_col}:N', sort=chronological_axis_order),
                    xOffset=alt.XOffset(f'{timeline_col}:N', sort=chronological_axis_order),
                    tooltip=[player_col, timeline_col, metric]
                ).properties(height=360).interactive() # <-- Added .interactive() here
                st.altair_chart(indiv_bar_chart, use_container_width=True)
                st.markdown("---")

# ==========================================
# 🏋️‍♂️ S&C TESTING MODULE ENGINE
# ==========================================
elif dashboard_mode == "🏋️‍♂️ S&C Testing":    
    
    import numpy as np # <-- Essential for catching and fixing 'inf' values
    
    # --- SANITIZE HISTORICAL/MISSING BW DATA FOR RANKINGS ---
    working_sc_df = working_sc_df.copy()
    
    problem_mask_working = (
        (working_sc_df['Body Weight (kg)'].isna()) | 
        (working_sc_df['Body Weight (kg)'] <= 0) | 
        (working_sc_df.get('Entry Type', '') == 'Historical')
    )
    
    working_sc_df.loc[problem_mask_working, 'RelStr'] = np.nan
    # Force preexisting 'inf' calculations into NaN
    working_sc_df['RelStr'] = pd.to_numeric(working_sc_df['RelStr'], errors='coerce').replace([np.inf, -np.inf], np.nan)
    
    if 'Stage' in working_sc_df.columns:
        working_sc_df.loc[problem_mask_working, 'Stage'] = None
    
    # Step 4: Final filter calculation using timeline status
    # Note: Added .copy() to ensure we can modify 'df' safely
    df = filtered_df_sc[filtered_df_sc["Year-Week"].isin(st.session_state.sc_active_weeks)].copy()
    
    # --- SANITIZE 'df' SO SUMMARY TABLES DON'T SHOW INF ---
    if not df.empty:
        problem_mask_df = (
            (df['Body Weight (kg)'].isna()) | 
            (df['Body Weight (kg)'] <= 0) | 
            (df.get('Entry Type', '') == 'Historical')
        )
        df.loc[problem_mask_df, 'RelStr'] = np.nan
        df['RelStr'] = pd.to_numeric(df['RelStr'], errors='coerce').replace([np.inf, -np.inf], np.nan)

    if df.empty:
        st.warning("⚠️ No data found for the selected Filters AND Timeline Week. Please adjust your selections.")
    else:
        tab1, tab2, tab3, tab4 = st.tabs(["📊 Summary", "🏆 Leaderboard", "📈 Visuals", "📋 Data Ledger"])
        
        with tab1:
            st.subheader("📊 Athlete Strength Comparisons")
            rank_df_1rm = get_squad_rank_df(working_sc_df, '1RM Predicted', 'Test Name', tag_col)
            rank_df_rel = get_squad_rank_df(working_sc_df, 'RelStr', 'Test Name', tag_col)
            
            if not selected_players:
                st.info("💡 Select one or more athletes from '1️⃣ Select Athletes' in the sidebar to display the Summary tables.")
            
            for player in selected_players:
                st.markdown(f"### 👤 {player}")
                st.markdown("##### 🚀 Current Strength Stages (Latest Week)")
                
                # Generate initial stage table
                stage_df = build_stage_table(player)

                # --- OVERRIDE STAGES FOR HISTORICAL/MISSING BW ---
                # This catches dynamic division-by-zero errors that result in "Stage 3"
                p_df = df[df['Player'] == player]
                for col_name in stage_df.columns:
                    test_rows = p_df[p_df['Test Name'] == col_name]
                    if not test_rows.empty:
                        # Find the actual row driving this result
                        if 'Week' in test_rows.columns:
                            latest_row = test_rows.loc[test_rows['Week'].idxmax()]
                        else:
                            latest_row = test_rows.iloc[-1]
                            
                        is_historical = latest_row.get('Entry Type', '') == 'Historical'
                        bw = latest_row.get('Body Weight (kg)', 0)
                        
                        # If Historical or missing BW, force the stage to N/A
                        if is_historical or pd.isna(bw) or float(bw) <= 0:
                            stage_df.at[0, col_name] = "N/A"
                # ------------------------------------------------

                cols = st.columns(len(stage_df.columns))

                # Display Headers
                for i, col_name in enumerate(stage_df.columns):
                    conf = header_styles.get(col_name, {"bg": "#eee", "text": "#000"})
                    cols[i].markdown(
                        f"""<div style="background-color: {conf['bg']}; color: {conf['text']}; 
                        padding: 10px; text-align: center; font-weight: bold; border: 1px solid #000;">
                            {col_name}</div>""", 
                        unsafe_allow_html=True
                    )
                
                # Display values
                for i, col_name in enumerate(stage_df.columns):
                    val = stage_df.iloc[0][col_name]
                    color = stage_colors.get(val, "#FFFFFF")
                    cols[i].markdown(
                        f"""<div style="text-align: center; padding: 10px; border: 1px solid #ddd; 
                        color: {color}; font-weight: bold; background-color: #1a1a1a;">
                            {val}</div>""", 
                        unsafe_allow_html=True
                    )

                st.markdown("<br>", unsafe_allow_html=True)
                render_styled(build_table(player, '1RM Predicted', '1RM', 1, rank_df_1rm, tag_col), "Result in Absolute Number", key=f"abs_{player}")
                
                # --- INTERCEPT & REMOVE BRONCO FROM RELATIVE STRENGTH ---
                rel_table = build_table(player, 'RelStr', 'RS', 2, rank_df_rel, tag_col)
                
                # Case A: If Bronco is an index label row
                if 'Bronco' in rel_table.index:
                    rel_table = rel_table.drop(index='Bronco', errors='ignore')
                
                # Case B: If Bronco is sitting in a text column
                for col_identifier in ['Test Name', 'Test', 'Exercise']:
                    if col_identifier in rel_table.columns:
                        rel_table = rel_table[rel_table[col_identifier] != 'Bronco']
                
                # Send the clean data to the renderer
                render_styled(rel_table, "Relative Strength (1RM / BW)", key=f"rel_{player}")
                st.markdown("---")

        with tab2:
            st.subheader("🏆 Squad Leaderboard (Filtered)")
            
            TARGET_TESTS = ["Back Squat", "Bench Press", "Pull Up", "Power Clean", "Bronco"]
            
            # This locks the columns strictly to what tests are active inside your timeline-filtered dataset!
            active_test_list = [t for t in TARGET_TESTS if t in df['Test Name'].unique()]
            
            if not df.empty and active_test_list:
                cols = st.columns(len(active_test_list))
                
                def get_leaderboard_style(row, test_name, display_cols, logic_df):
                    match = logic_df[logic_df['Player'] == row['Player']]
                    if match.empty: return [""] * len(display_cols)
                    logic_row = match.iloc[0]
                    target = "1RM" if test_name == "Bronco" else "RS"
                    return [get_rs_text_color(test_name, logic_row) if col == target else "" for col in display_cols]

                for i, test_name in enumerate(active_test_list):
                    with cols[i]:
                        style = header_styles.get(test_name, {"bg": "#262730", "text": "#FFFFFF"})
                        st.markdown(f"""<div style="background-color: {style['bg']}; color: {style['text']}; padding: 10px; text-align: center; font-weight: bold; border: 1px solid #000;">{test_name}</div>""", unsafe_allow_html=True)
                        
                        test_data = df[(df['Test Name'] == test_name) & (df['1RM Predicted'] > 0)].copy()
                        if test_data.empty: 
                            st.info("No data")
                            continue
                            
                        test_data = test_data.sort_values(by='1RM Predicted', ascending=(test_name == "Bronco"))
                        test_data['Rank'] = range(1, len(test_data) + 1)
                        
                        df_logic = test_data.rename(columns={
                            '1RM Predicted': '1RM', 'Relative Strength': 'RS',
                            'Load (kg)': 'Load', 'Reps': 'Reps', 'Body Weight (kg)': 'BW'
                        })
                        df_logic = df_logic.loc[:, ~df_logic.columns.duplicated()]
                        
                        display_cols = ['Rank', 'Player', '1RM']
                        if 'RS' in df_logic.columns and test_name != "Bronco":
                            display_cols.append('RS')

                        df_display = df_logic[display_cols]
                        
# The na_rep="-" tells Streamlit to print a dash if the data is missing
                        styler = df_display.style.format({"1RM": "{:.1f}", "RS": "{:.2f}"}, na_rep="-")
                        styler = styler.apply(get_leaderboard_style, test_name=test_name, display_cols=df_display.columns, logic_df=df_logic, axis=1)
                        st.dataframe(styler, use_container_width=True, hide_index=True)
            else:
                st.info("No leaderboard data available for the selected tests.")
                    
        with tab3:
            st.subheader("Performance Trends")
            
            if not df.empty and 'Date' in df.columns:
                # --- DATA PREPARATION ---
                working_df = df.copy()
                working_df['Date'] = pd.to_datetime(working_df['Date'])
                working_df = working_df.sort_values(by='Date')
                working_df['Year'] = working_df['Date'].dt.year
                
                # FIX 1: Use .str.zfill(2) so weeks become "W01", "W02", preventing alphabetical sorting issues
                working_df['Week_Formatted'] = working_df['Year'].astype(str) + "-W" + working_df['Week'].astype(str).str.zfill(2)
                
                # FIX 2: Extract the exact chronological order of weeks to force Altair to respect it
                chronological_axis_order = working_df['Week_Formatted'].drop_duplicates().tolist()
                
                for exercise in working_df['Test Name'].unique():
                    exercise_df = working_df[(working_df['Test Name'] == exercise) & (working_df['1RM Predicted'] > 0)].copy()
                    
                    if not exercise_df.empty:
                        st.markdown("---") # Clean separator line between each graph
                        
                        # --- INDEPENDENT CONTROLS (Next to Test Name) ---
                        header_col, toggle_col1, toggle_col2 = st.columns([2, 1.2, 1.2])
                        
                        with header_col:
                            st.markdown(f"#### 🎯 {exercise}")
                            
                        with toggle_col1:
                            show_group_average = st.toggle(
                                "📊 Group Avg", 
                                value=False, 
                                key=f"sc_avg_{exercise}"
                            )
                            
                        with toggle_col2:
                            show_player_data = st.toggle(
                                "👥 Athletes", 
                                value=True, 
                                key=f"sc_players_{exercise}"
                            )
                            
                        if not show_group_average and not show_player_data:
                            st.info(f"💡 Please activate at least one toggle above to visualize {exercise} trend lines.")
                        else:
                            chart_layers = []
                            
                            # Layer A: Individual Athlete Lines
                            if show_player_data:
                                player_chart = alt.Chart(exercise_df).mark_line(point=True).encode(
                                    # FIX 3: Apply the chronological order here
                                    x=alt.X('Week_Formatted:O', title='Year-Week', sort=chronological_axis_order),
                                    y=alt.Y('1RM Predicted:Q', title='1RM', scale=alt.Scale(zero=False)),
                                    color='Player:N', 
                                    tooltip=['Player', '1RM Predicted', 'Week_Formatted']
                                )
                                chart_layers.append(player_chart)
                            
                            # Layer B: Filtered Group Average Line & Anchor Points
                            if show_group_average:
                                avg_data = exercise_df.groupby('Week_Formatted')['1RM Predicted'].mean().reset_index()
                                
                                avg_line = alt.Chart(avg_data).mark_line(
                                    color='#FF4B4B',
                                    strokeWidth=4,
                                    strokeDash=[6, 4]
                                ).encode(
                                    # FIX 3: Apply the chronological order here
                                    x=alt.X('Week_Formatted:O', sort=chronological_axis_order),
                                    y=alt.Y('1RM Predicted:Q', title='1RM', scale=alt.Scale(zero=False)),
                                    tooltip=[
                                        alt.Tooltip('Week_Formatted', title='Week'),
                                        alt.Tooltip('1RM Predicted', title='Filtered Group Avg', format='.1f')
                                    ]
                                )
                                
                                avg_points = alt.Chart(avg_data).mark_point(
                                    color='#FF4B4B',
                                    size=80,
                                    filled=True
                                ).encode(
                                    # FIX 3: Apply the chronological order here
                                    x=alt.X('Week_Formatted:O', sort=chronological_axis_order),
                                    y=alt.Y('1RM Predicted:Q')
                                )
                                
                                chart_layers.append(avg_line)
                                chart_layers.append(avg_points)
                            
                            # --- COMBINE AND RENDER LAYERS ---
                            if chart_layers:
                                final_chart = alt.layer(*chart_layers).interactive()
                                st.altair_chart(final_chart, use_container_width=True)

        with tab4:
            st.subheader("📋 Data Ledger")
            if not df.empty: 
                st.dataframe(df, use_container_width=True)
            else: 
                st.info("Select filters.")

# ==========================================
# --- IDP GENERATOR PAGE ---
# ==========================================
elif dashboard_mode == "🎯 IDP Generator":

    # --- 1. ROBUST PRINT STYLING ---
    st.markdown("""
        <style>
        @media print {
            /* Hide Sidebar, Header, Footer, and standard buttons */
            [data-testid="stSidebar"], 
            header, 
            footer, 
            .stButton, 
            .print-btn-container,
            .no-print { 
                display: none !important; 
            }

            /* Expand main containers for proper printing width and page breaks */
            [data-testid="stAppViewContainer"], 
            [data-testid="stMainBlockContainer"], 
            .main { 
                position: static !important; 
                overflow: visible !important; 
                height: auto !important; 
                width: 100% !important; 
            }

            /* EXPLICITLY HIDE SPECIFIC SECTIONS USING INJECTED MARKERS */
            div.element-container:has(#hide-print-title),
            div.element-container:has(#hide-print-leaderboard) {
                display: none !important;
            }
        }
        </style>
    """, unsafe_allow_html=True)

    # --- 2. SIDEBAR & PLAYER SELECTION LOGIC ---
    st.sidebar.markdown("---")
    st.sidebar.subheader("⚙️ IDP Settings")
    idp_roster_view = st.sidebar.radio("Roster View", ["Current Squad", "All-Time"], index=0, key="idp_roster_toggle")

    idp_gps_df = gps_df.copy() if 'gps_df' in locals() and not gps_df.empty else pd.DataFrame()
    idp_sc_df = sc_df.copy() if 'sc_df' in locals() and not sc_df.empty else pd.DataFrame()

    if idp_roster_view == "Current Squad":
        if 'Squad Status' in idp_gps_df.columns: idp_gps_df = idp_gps_df[idp_gps_df['Squad Status'] == 'Active']
        if 'Squad Status' in idp_sc_df.columns: idp_sc_df = idp_sc_df[idp_sc_df['Squad Status'] == 'Active']

    sc_players = set(idp_sc_df['Player'].dropna().unique()) if not idp_sc_df.empty and 'Player' in idp_sc_df.columns else set()
    gps_players = set(idp_gps_df['Player'].dropna().unique()) if not idp_gps_df.empty and 'Player' in idp_gps_df.columns else set()
    available_players = sorted(list(sc_players | gps_players))
    selected_idp_players = st.sidebar.multiselect("👤 Select Athletes for IDP", available_players, default=available_players[:1] if available_players else [])

    # --- 3. TITLE & PRINT BUTTON (Wrapped for Print Exclusion) ---
    import streamlit.components.v1 as components
    title_print_container = st.container()
    
    with title_print_container:
        st.markdown('<div id="hide-print-title"></div>', unsafe_allow_html=True)
        col_title, col_print = st.columns([4, 1])
        with col_title:
            st.title("🎯 Individual Development Plan (IDP) Generator")
        with col_print:
            st.markdown('<div class="print-btn-container">', unsafe_allow_html=True)
            if st.button("🖨️ Print IDP"):
                components.html(
                    "<script>setTimeout(function(){ window.parent.print(); }, 300);</script>",
                    height=0,
                    width=0,
                )
            st.markdown('</div>', unsafe_allow_html=True)

    # --- 4. RENDER IDP CONTENT ---
    if not selected_idp_players:
        st.warning("⚠️ Please select at least one athlete from the sidebar.")
    else:
        print_container = st.container()
        with print_container:
            
            # --- TOP SECTION: STANDARDS & BENCHMARKS ---
            st.header("🏆 Performance Standards")

            standards_html = "<table style='width: 100%; border-collapse: collapse; margin-bottom: 20px; font-family: sans-serif; font-size: 13px;'>"
            header_style = "padding: 6px; border: 1px solid #555; background-color: #222; color: white; text-align: center; vertical-align: middle;"
            standards_html += f"<tr><th style='{header_style}'>Stage</th>"

            tests = ["Back Squat", "Bench Press", "Pull Up", "Power Clean", "Bronco", "Max Speed"]
            for test in tests:
                style = header_styles.get(test, {"bg": "#444", "text": "#fff"})
                standards_html += f"<th style='padding: 6px; border: 1px solid #555; background-color: {style['bg']}; color: {style['text']}; text-align: center; vertical-align: middle;'>{test}</th>"
            standards_html += "</tr>"

            rows = [
                ("Stage 1A", "#FF4B4B", ["90-99kg", "45-54kg", "2-4 Reps", "40-49 kg", "345-360s", "<278.0km/h"]),
                ("Stage 1B", "#FFA500", ["100kg+", "55kg+", "5+ Reps", "50+ kg", "320-344s", "28.0-29.9km/h"]),
                ("Stage 2", "#FFD700", ["1.70-1.89 x BW", "1.10-1.19 x BW", "1.30-1.39 x BW", "1.00-1.29 x BW", "300-319s", "30.0-31.9km/h"]),
                ("Stage 3", "#32CD32", ["1.90+ x BW", "1.20+ x BW", "1.40+ x BW", "1.30+ x BW", "<300s", "≥ 32.0km/h"])
            ]

            for stage_name, color_hex, values in rows:
                standards_html += f"<tr><td style='padding: 6px; border: 1px solid #555; background-color: {color_hex}; color: white; font-weight: bold; text-align: center; vertical-align: middle;'>{stage_name}</td>"
                for val in values:
                    standards_html += f"<td style='padding: 6px; border: 1px solid #555; color: {color_hex}; font-weight: bold; text-align: center; background-color: #1E1E1E;'>{val}</td>"
                standards_html += "</tr>"
            standards_html += "</table>"

            st.markdown(standards_html, unsafe_allow_html=True)
            st.markdown("---")

            # --- LOOP THROUGH SELECTED PLAYERS ---
            for player in selected_idp_players:
                st.markdown(f"## 🏉 IDP Profile: {player}")

                player_sc_df = idp_sc_df[idp_sc_df['Player'] == player].copy() if not idp_sc_df.empty else pd.DataFrame()
                player_gps_df = idp_gps_df[idp_gps_df['Player'] == player].copy() if not idp_gps_df.empty else pd.DataFrame()

                if not player_sc_df.empty and 'Date' in player_sc_df.columns:
                    player_sc_df['Date'] = pd.to_datetime(player_sc_df['Date'])
                    if 'Week' in player_sc_df.columns:
                        player_sc_df['Year-Week'] = player_sc_df['Date'].dt.year.astype(str) + '-W' + pd.to_numeric(player_sc_df['Week'], errors='coerce').fillna(0).astype(int).astype(str).str.zfill(2)
                    else:
                        player_sc_df['Year-Week'] = player_sc_df['Date'].dt.strftime('%Y-W%V')

                st.markdown("""
                <style>
                .sticky-table-container { overflow-x: auto; width: 100%; }
                .sticky-table { border-collapse: collapse; width: 100%; font-family: inherit; font-size: 13px; }
                .sticky-table th, .sticky-table td { border: 1px solid #262730; padding: 4px 8px; text-align: center; white-space: nowrap; }
                .sticky-table th { background-color: #131722; color: #9ca3af; font-weight: 600; }
                .sticky-table td { background-color: #0e1117; color: #ffffff; }
                .sticky-table td:first-child, .sticky-table th:first-child {
                    position: sticky; left: 0; z-index: 2; text-align: left; font-weight: bold;
                    box-shadow: 2px 0 5px rgba(0,0,0,0.3); padding: 0;
                }
                .sticky-table th:first-child { z-index: 3; background-color: #131722; color: #9ca3af; padding: 4px 8px; }
                .color-box { display: flex; align-items: center; height: 100%; width: 100%; padding: 4px 8px; box-sizing: border-box; }
                .table-v-center { height: 180px; display: flex; flex-direction: column; justify-content: center; }
                </style>
                """, unsafe_allow_html=True)

                # --- GYM KPIs ---
                st.markdown("<h3 style='margin-top: -20px; margin-bottom: 10px; font-size: 1.5rem; font-weight: 600;'>🏋️ Gym KPIs</h3>", unsafe_allow_html=True)

# 1. Define all valid tests (Original + New)
                gym_tests = ["Back Squat", "Power Clean", "Bench Press", "Pull Up", "Bulgarian Split Squat", "Split Squat", "Belt Squat"]
                extra_tests = ["Bulgarian Split Squat", "Split Squat", "Belt Squat"]

                # GUARDRAIL: Only filter if the DataFrame actually has a 'Test Name' column
                if not player_sc_df.empty and 'Test Name' in player_sc_df.columns:
                    # 2. Strict Filter: MUST be in the list AND must be an 'Entry Type' of 'Test'
                    gym_data = player_sc_df[
                        (player_sc_df['Test Name'].isin(gym_tests)) &
                        (player_sc_df['Entry Type'] == 'Test')
                    ].copy()

                else:
                    # If there is no data, create an empty dataframe so the IDP generator doesn't break
                    # CHANGED: Renamed from 'filtered_gym' to 'gym_data'
                    gym_data = pd.DataFrame()

                if gym_data.empty:
                    st.info(f"No Test-specific Gym KPI data available for {player}.")
                else:
                    col_gym_tbl, col_gym_graph = st.columns([1.5, 1])
                    
                    # ... [rest of your code continues normally here] ...

                    with col_gym_tbl:
                        pivot_gym = gym_data.pivot_table(index='Test Name', columns='Year-Week', values='1RM Predicted', aggfunc='last')
                        pivot_gym = pivot_gym.replace(0, None)
                        # REVERSE THE WEEKS
                        weeks_order = sorted(pivot_gym.columns, reverse=True)
                        latest_gym_rows = gym_data.sort_values('Date').groupby('Test Name').tail(1).set_index('Test Name')

                        # MOVE 'Latest RS' NEXT TO TEST NAME
                        html_gym = '<div class="sticky-table-container"><table class="sticky-table"><thead><tr><th>Test Name</th><th>Latest RS</th>'
                        for w in weeks_order:
                            html_gym += f'<th>{w}</th>'
                        html_gym += '</tr></thead><tbody>'

                        for test in gym_tests:
                            if test in pivot_gym.index:
                                if test in extra_tests:
                                    box_style = {"bg": "maroon", "text": "#ffffff"}
                                else:
                                    box_style = header_styles.get(test, {"bg": "#222", "text": "#FFF"})
                                    
                                html_gym += f'<tr><td><div class="color-box" style="background-color: {box_style["bg"]}; color: {box_style["text"]};">{test}</div></td>'
                                
                                # CALCULATE RS FIRST
                                rs_val_str = "-"
                                rs_style = ""
                                if test in latest_gym_rows.index:
                                    actual_row = latest_gym_rows.loc[test]
                                    rm_val = float(actual_row.get('1RM Predicted', 0)) if pd.notna(actual_row.get('1RM Predicted')) else 0.0
                                    bw_val = float(actual_row.get('Body Weight (kg)', 70)) if pd.notna(actual_row.get('Body Weight (kg)')) else 70.0
                                    if bw_val == 0: bw_val = 70.0
                                    calculated_rs = rm_val / bw_val
                                    rs_val_str = f"{calculated_rs:.2f}"
                                    
                                    if test not in extra_tests:
                                        mapped_row = {'Load': rm_val, 'Reps': actual_row.get('Reps_Num', 0), 'BW': bw_val, '1RM': rm_val, 'RS': calculated_rs}
                                        color_css = get_rs_text_color(test, mapped_row)
                                        if color_css: rs_style = f' style="{color_css}"'
                                
                                # APPEND RS COLUMN BEFORE THE WEEKS
                                html_gym += f'<td{rs_style}>{rs_val_str}</td>'
                                
                                # THEN APPEND THE WEEKS
                                for w in weeks_order:
                                    val = pivot_gym.loc[test, w]
                                    html_gym += f'<td>{val:.1f}</td>' if pd.notna(val) else '<td>-</td>'

                                html_gym += '</tr>'
                        html_gym += '</tbody></table></div>'
                        st.markdown(html_gym, unsafe_allow_html=True)

                    with col_gym_graph:
                        gym_chart_data = gym_data[gym_data['1RM Predicted'] > 0].copy()
                        
                        # Generate dynamic domain/range
                        present_tests = gym_chart_data['Test Name'].unique().tolist()
                        color_range = [
                            "maroon" if t in extra_tests else header_styles.get(t, {"bg": "#444"})["bg"] 
                            for t in present_tests
                        ]

                        gym_chart = alt.Chart(gym_chart_data).mark_line(point=True).encode(
                            x=alt.X('Year-Week:O', title='Timeline'),
                            y=alt.Y('1RM Predicted:Q', title='1RM (kg)', scale=alt.Scale(zero=False)),
                            color=alt.Color('Test Name:N', scale=alt.Scale(domain=present_tests, range=color_range)),
                            tooltip=['Year-Week', 'Test Name', '1RM Predicted', 'Relative Strength']
                        ).properties(height=260).interactive()
                        
                        st.altair_chart(gym_chart, use_container_width=True)

                # --- FIELD KPIs ---
                st.markdown("<h3 style='margin-top: -15px; margin-bottom: 10px; font-size: 1.5rem; font-weight: 600;'>🏃 Field KPIs</h3>", unsafe_allow_html=True)

                bronco_df = player_sc_df[player_sc_df['Test Name'] == 'Bronco'].copy() if not player_sc_df.empty else pd.DataFrame()
                if not bronco_df.empty:
                    bronco_df['Score'] = bronco_df['1RM Predicted']

                speed_df = pd.DataFrame()
                if not player_gps_df.empty and 'Year' in player_gps_df.columns and 'Week' in player_gps_df.columns:
                    speed_col = None
                    for col in player_gps_df.columns:
                        if str(col).lower().strip() in ['max speed', 'max_speed']:
                            speed_col = col
                            break
                    if not speed_col:
                        for col in player_gps_df.columns:
                            if 'max' in str(col).lower() and 'speed' in str(col).lower():
                                speed_col = col
                                break
                    if speed_col:
                        gps_filtered = player_gps_df[player_gps_df[speed_col] > 0].copy()
                        if not gps_filtered.empty:
                            gps_filtered = gps_filtered.sort_values(by=['Year', 'Week'])
                            gps_filtered['Year-Week'] = gps_filtered['Year'].astype(str).str.strip() + '-W' + gps_filtered['Week'].astype(int).astype(str).str.zfill(2)
                            gps_filtered['Score'] = gps_filtered[speed_col]
                            gps_filtered['Test Name'] = 'Max Speed'
                            speed_df = gps_filtered

                col_f_tbl, col_f_graph = st.columns([1.5, 1])

                with col_f_tbl:
                    if not bronco_df.empty:
                        pivot_b = bronco_df.pivot_table(index='Test Name', columns='Year-Week', values='Score', aggfunc='last')
                        # REVERSE WEEKS
                        b_weeks = sorted(pivot_b.columns, reverse=True)
                        latest_b_score = bronco_df.sort_values('Date').iloc[-1]['Score'] if 'Date' in bronco_df.columns else bronco_df['Score'].iloc[-1]
                        style_b = header_styles.get("Bronco", {"bg": "#800080", "text": "#FFF"})

                        # MOVE 'Latest Score' NEXT TO TEST NAME
                        html_b = '<div class="table-v-center"><div class="sticky-table-container"><table class="sticky-table"><thead><tr><th>Test Name</th><th>Latest Score</th>'
                        for w in b_weeks:
                            html_b += f'<th>{w}</th>'
                        html_b += '</tr></thead><tbody>'
                        html_b += f'<tr><td><div class="color-box" style="background-color: {style_b["bg"]}; color: {style_b["text"]};">Bronco</div></td>'
                        
                        b_color_css = get_rs_text_color("Bronco", {'1RM': latest_b_score})
                        b_style_str = f' style="{b_color_css}"' if b_color_css else ""
                        
                        # ADD SCORE CELL
                        html_b += f'<td{b_style_str}>{latest_b_score:.1f}</td>'
                        
                        # ADD WEEK CELLS
                        for w in b_weeks:
                            val = pivot_b.loc['Bronco', w]
                            html_b += f'<td>{val:.1f}</td>' if pd.notna(val) else '<td>-</td>'
                            
                        html_b += '</tr></tbody></table></div></div>'
                        st.markdown(html_b, unsafe_allow_html=True)

                    if not speed_df.empty:
                        pivot_s = speed_df.pivot_table(index='Test Name', columns='Year-Week', values='Score', aggfunc='last')
                        # REVERSE WEEKS
                        s_weeks = sorted(pivot_s.columns, reverse=True)
                        max_s_score = speed_df['Score'].max()
                        style_s = header_styles.get("Max Speed", {"bg": "#87CEEB", "text": "#000"})

                        # MOVE 'Max Score' NEXT TO TEST NAME
                        html_s = '<div class="table-v-center"><div class="sticky-table-container"><table class="sticky-table"><thead><tr><th>Test Name</th><th>Max Score</th>'
                        for w in s_weeks:
                            html_s += f'<th>{w}</th>'
                        html_s += '</tr></thead><tbody>'
                        html_s += f'<tr><td><div class="color-box" style="background-color: {style_s["bg"]}; color: {style_s["text"]};">Max Speed</div></td>'
                        
                        if max_s_score >= 31.0: s_color = "color: #32CD32; font-weight: bold;"
                        elif max_s_score >= 29.0: s_color = "color: #FFD700; font-weight: bold;"
                        elif max_s_score >= 27.0: s_color = "color: #FFA500; font-weight: bold;"
                        else: s_color = "color: #FF4B4B; font-weight: bold;"
                        
                        # ADD MAX SCORE CELL
                        html_s += f'<td style="{s_color}">{max_s_score:.1f}</td>'
                        
                        # ADD WEEK CELLS
                        for w in s_weeks:
                            val = pivot_s.loc['Max Speed', w]
                            html_s += f'<td>{val:.1f}</td>' if pd.notna(val) else '<td>-</td>'
                            
                        html_s += '</tr></tbody></table></div></div>'
                        st.markdown(html_s, unsafe_allow_html=True)

                with col_f_graph:
                    if not bronco_df.empty:
                        bronco_chart_data = bronco_df[bronco_df['Score'] > 0].copy()
                        b_color = header_styles.get("Bronco", {"bg": "#800080"})["bg"]
                        b_chart = alt.Chart(bronco_chart_data).mark_line(
                            color=b_color, point=alt.OverlayMarkDef(color=b_color, fill=b_color)
                        ).encode(
                            x=alt.X('Year-Week:O', title='Timeline'),
                            y=alt.Y('Score:Q', title='Bronco (s)', scale=alt.Scale(zero=False), axis=alt.Axis(tickCount=6)),
                            tooltip=['Year-Week', 'Score']
                        ).properties(height=180)
                        st.altair_chart(b_chart, use_container_width=True)

                    if not speed_df.empty:
                        s_color = header_styles.get("Max Speed", {"bg": "#87CEEB"})["bg"]
                        s_chart = alt.Chart(speed_df).mark_line(
                            color=s_color, point=alt.OverlayMarkDef(color=s_color, fill=s_color)
                        ).encode(
                            x=alt.X('Year-Week:O', title='Timeline'),
                            y=alt.Y('Score:Q', title='Speed (km/h)', scale=alt.Scale(zero=False), axis=alt.Axis(tickCount=6)),
                            tooltip=['Year-Week', 'Score']
                        ).properties(height=180)
                        st.altair_chart(s_chart, use_container_width=True)

                st.markdown("---")

# ==========================================
# --- ALL-TIME LEADERBOARD SECTION ---
# ==========================================

# 1. Add the toggle
print_mode = st.toggle("🖨️ Enable Print View (Hides Leaderboard for 1-Page PDF)")

# 2. ALWAYS create the container (Prevents the NameError!)
leaderboard_container = st.container()

# 3. Only fill the container if Print Mode is OFF
if not print_mode:
    with leaderboard_container:
    # 1. Create side-by-side layout for the title and the independent inline toggle
        title_col, toggle_col = st.columns([2, 1])

        with title_col:
            st.subheader("🌎 All-Time Squad Leaderboard")

        with toggle_col:
            # This selector acts independently and defaults explicitly to "All-Time" (index=1)
            leaderboard_squad_view = st.radio(
                "Leaderboard Roster View",
                ["Current Squad", "All-Time"],
                index=1,
                horizontal=True,
                key="global_all_time_leaderboard_toggle",
                label_visibility="collapsed" # Hides the text label so it fits cleanly in line
            )

        # 2. Safely create local copies to protect raw data from global mutations
        lb_gps_df = raw_gps_df.copy() if not raw_gps_df.empty else pd.DataFrame()
        lb_sc_df = raw_sc_df.copy() if raw_sc_df is not None and not raw_sc_df.empty else pd.DataFrame()

        # 3. Intercept and filter data local to this block if "Current Squad" is selected
        if leaderboard_squad_view == "Current Squad":
            if 'Squad Status' in lb_gps_df.columns:
                lb_gps_df = lb_gps_df[lb_gps_df['Squad Status'] == 'Active']
            if 'Squad Status' in lb_sc_df.columns:
                lb_sc_df = lb_sc_df[lb_sc_df['Squad Status'] == 'Active']

        # ==========================================
        # ⚖️ SAFELY HANDLE MISSING BODY WEIGHT (LOCAL FIX)
        # ==========================================
        if not lb_sc_df.empty and "Body Weight (kg)" in lb_sc_df.columns and "Relative Strength" in lb_sc_df.columns:
            lb_sc_df["Body Weight (kg)"] = pd.to_numeric(lb_sc_df["Body Weight (kg)"], errors="coerce")
            missing_bw_mask = lb_sc_df["Body Weight (kg)"].isna() | (lb_sc_df["Body Weight (kg)"] == 0)
            lb_sc_df.loc[missing_bw_mask, "Relative Strength"] = pd.NA

        # 4. Render Leaderboard Columns
        leaderboard_tests = ["Back Squat", "Bench Press", "Pull Up", "Power Clean", "Bronco", "Max Speed"]
        cols = st.columns(len(leaderboard_tests))

        for i, test_name in enumerate(leaderboard_tests):
            with cols[i]:
                # Visual Header
                style = header_styles.get(test_name, {"bg": "#262730", "text": "#FFFFFF"})
                st.markdown(
                    f"""<div style="background-color: {style['bg']}; color: {style['text']}; 
                    padding: 10px; text-align: center; font-weight: bold; border: 1px solid #000;">
                    {test_name}</div>""", unsafe_allow_html=True
                )
                
                # ==========================================
                # BRANCH A: GPS DATA (Max Speed)
                # ==========================================
                if test_name == "Max Speed":
                    speed_col = "max speed" 
                    
                    if lb_gps_df.empty or speed_col not in lb_gps_df.columns:
                        st.info("No GPS data available")
                        continue
                    
                    # Filter valid speeds
                    valid_gps = lb_gps_df[lb_gps_df[speed_col] > 0].copy()
                    if valid_gps.empty:
                        st.info("No valid speeds")
                        continue

                    # Find absolute max speed per player
                    idx = valid_gps.groupby('Player')[speed_col].idxmax()
                    at_best_gps = valid_gps.loc[idx.dropna()].copy()
                    at_best_gps = at_best_gps.sort_values(by=speed_col, ascending=False)
                    at_best_gps['Rank'] = range(1, len(at_best_gps) + 1)
                    
                    # Prepare display dataframe
                    df_display = at_best_gps[['Rank', 'Player', speed_col, 'Year-Week']].copy()
                    df_display = df_display.rename(columns={speed_col: 'Speed', 'Year-Week': 'Week'})
                    
                    # 1. Apply Formatting
                    styler = df_display.style.format({"Speed": "{:.2f}"})
                    
                    # 2. Apply Coloring Logic
                    def apply_speed_coloring(row):
                        # Create a mock row for the helper function (it expects '1RM' key)
                        mock_row = {'1RM': row['Speed']}
                        color_css = get_rs_text_color("Max Speed", mock_row)
                        return [color_css if col == "Speed" else "" for col in df_display.columns]

                    styler = styler.apply(apply_speed_coloring, axis=1)
                    
                    st.dataframe(styler, use_container_width=True, hide_index=True)

# ==========================================
                # BRANCH B: S&C DATA (Everything Else)
                # ==========================================
                else:
                    # 1. Guardrail: If missing data/columns, stop here and display info
                    if lb_sc_df.empty or 'Test Name' not in lb_sc_df.columns:
                        st.info("No data loaded")
                    else:
                        # 2. Catch "Test", "test", "Testing", etc.
                        if 'Entry Type' in lb_sc_df.columns:
                            is_test_mode = lb_sc_df['Entry Type'].astype(str).str.contains('test', case=False, na=False)
                        else:
                            is_test_mode = True

                        # 3. Force 1RM Predicted to be a numeric value
                        if '1RM Predicted' in lb_sc_df.columns:
                            numeric_1rm = pd.to_numeric(lb_sc_df['1RM Predicted'], errors='coerce').fillna(0)
                        else:
                            numeric_1rm = pd.Series(0, index=lb_sc_df.index)

                        # 4. Filter data
                        at_data = lb_sc_df[
                            (lb_sc_df['Test Name'] == test_name) & 
                            is_test_mode & 
                            (numeric_1rm > 0)
                        ].copy()

                        # 5. Display Leaderboard or Error
                        if at_data.empty:
                            st.error(f"No >0 scores found for {test_name}.")
                        else:
                            # Rank calculation
                            if test_name == "Bronco":
                                at_best = at_data.loc[at_data.groupby('Player')['1RM Predicted'].idxmin()].copy()
                                at_best = at_best.sort_values(by='1RM Predicted', ascending=True)
                            else:
                                at_best = at_data.loc[at_data.groupby('Player')['1RM Predicted'].idxmax()].copy()
                                at_best = at_best.sort_values(by='1RM Predicted', ascending=False)
                                
                            at_best['Rank'] = range(1, len(at_best) + 1)
                            
                            # Display rendering (Using the RAW column names)
                            df_logic = at_best[['Rank', 'Player', '1RM Predicted', 'Relative Strength', 'Load (kg)', 'Reps', 'Body Weight (kg)']].copy()
                            
                            df_logic = df_logic.rename(columns={
                                '1RM Predicted': '1RM', 
                                'Relative Strength': 'RS', 
                                'Load (kg)': 'Load', 
                                'Reps': 'Reps', 
                                'Body Weight (kg)': 'BW'
                            })
                            
                            df_display = df_logic[['Rank', 'Player', '1RM', 'RS']].copy() if test_name != "Bronco" else df_logic[['Rank', 'Player', '1RM']].copy()
                            
                            # --- FIXED FORMATTING: Force RS to numeric and use na_rep ---
                            if "RS" in df_display.columns:
                                df_display["RS"] = pd.to_numeric(df_display["RS"], errors="coerce")
                                
                            styler = df_display.style.format({"1RM": "{:.1f}", "RS": "{:.2f}"}, na_rep="-")
                            
                            # --- COLORING LOGIC ---
                            def apply_all_time_coloring(row, current_test=test_name, df_l=df_logic, df_d=df_display):
                                logic_row = df_l[df_l['Player'] == row['Player']].iloc[0]
                                target_col = "1RM" if current_test == "Bronco" else "RS"
                                if target_col in df_d.columns:
                                    return [get_rs_text_color(current_test, logic_row) if col == target_col else "" for col in df_d.columns]
                                return [""] * len(df_d.columns)

                            styler = styler.apply(apply_all_time_coloring, axis=1)
                            st.dataframe(styler, use_container_width=True, hide_index=True)
        

# --- 🔄 FORCE REFRESH AT THE BOTTOM OF SIDEBAR ---
st.sidebar.markdown("---")
if st.sidebar.button("🔄 Force Refresh Cache", key="sidebar_refresh"):
    st.cache_data.clear()
    st.rerun()
