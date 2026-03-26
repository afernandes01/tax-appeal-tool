"""
helpers.py — Core logic for the Cook County Property Tax Appeal Tool
All API calls, scoring, and analysis functions live here.
"""

import requests
import pandas as pd
import numpy as np
# Socrata dataset IDs
ASSESSED_VALUES = "uzyt-m557"
PARCEL_UNIVERSE = "nj4t-kc8j"
CHARACTERISTICS = "x54s-btds"
PARCEL_SALES = "wvhk-k5uv"
PARCEL_ADDRESSES = "3723-97qp"

BASE_URL = "https://datacatalog.cookcountyil.gov/resource"
TIMEOUT = 180
CURRENT_YEAR = 2025

DEFAULT_WEIGHTS = {
    "sqft": 40,
    "year_built": 30,
    "bedrooms": 15,
    "lot_size": 15
}

# Hard cutoffs: beyond these, property is not comparable at all
DEFAULT_CUTOFFS = {
    "sqft_pct": 0.50,     # 50% bigger or smaller
    "beds_diff": 2,        # 2+ bedrooms difference
    "lot_pct": 0.60,       # 60% bigger or smaller lot
    "age_min": 10,         # Minimum age cutoff in years
    "age_pct": 0.40,       # Age cutoff as percentage of subject age
}

# Supported single-family classes
SINGLE_FAMILY_CLASSES = [
    "202", "203", "204", "205", "206", "207", "208", "209", "210", "234", "278", "295"
]


def query_socrata(dataset_id, params, retries=3):
    """Make a request to the Socrata API with retry logic."""
    url = f"{BASE_URL}/{dataset_id}.json"
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.ReadTimeout:
            if attempt < retries - 1:
                print(f"Timeout on {dataset_id}, retrying ({attempt + 2}/{retries})...")
                continue
            else:
                print(f"Failed after {retries} attempts on {dataset_id}")
                return []
        except requests.exceptions.RequestException as e:
            print(f"API error on {dataset_id}: {e}")
            return []
    return []


def get_age_cutoff(my_age):
    """Age cutoff scales with house age, minimum 10 years."""
    return max(DEFAULT_CUTOFFS["age_min"], my_age * DEFAULT_CUTOFFS["age_pct"])

def fetch_property(pin):
    """
    Fetch everything we need about a property: characteristics, assessment, and address.
    Returns a dictionary with all property info, or None if not found.
    """
    # Get characteristics (most recent year)
    chars_data = query_socrata(CHARACTERISTICS, {
        "$where": f"pin='{pin}'",
        "$order": "year DESC",
        "$limit": 1
    })

    if not chars_data:
        return None

    chars = chars_data[0]

    # Check if this is a supported single-family class
    prop_class = chars.get("class", "")
    if prop_class not in SINGLE_FAMILY_CLASSES:
        return {"error": f"Class {prop_class} is not a single-family property. This tool currently supports single-family homes only (classes {', '.join(SINGLE_FAMILY_CLASSES)})."}

    # Get assessed values (most recent year with data)
    assess_data = query_socrata(ASSESSED_VALUES, {
        "$where": f"pin='{pin}'",
        "$order": "year DESC",
        "$limit": 5
    })

    # Find the most recent year that has a mailed_tot value
    assessment = None
    for row in assess_data:
        if row.get("mailed_tot"):
            assessment = row
            break

    # Get neighborhood code from Parcel Universe
    parcel_data = query_socrata(PARCEL_UNIVERSE, {
        "$where": f"pin='{pin}'",
        "$order": "year DESC",
        "$limit": 1
    })


    # Build the property dictionary
    prop = {
        "pin": pin,
        "class": prop_class,
        "sqft": to_float(chars.get("char_bldg_sf")),
        "land_sf": to_float(chars.get("char_land_sf")),
        "year_built": to_float(chars.get("char_yrblt")),
        "age": CURRENT_YEAR - to_float(chars.get("char_yrblt", CURRENT_YEAR)),
        "beds": to_float(chars.get("char_beds")),
        "rooms": to_float(chars.get("char_rooms")),
        "full_baths": to_float(chars.get("char_fbath")),
        "half_baths": to_float(chars.get("char_hbath")),
        "basement": chars.get("char_bsmt", "Unknown"),
        "exterior": chars.get("char_ext_wall", "Unknown"),
        "air": chars.get("char_air", "Unknown"),
        "type": chars.get("char_type_resd", "Unknown"),
        "construction_quality": chars.get("char_cnst_qlty", "Unknown"),
    }

    # Add assessment info
    if assessment:
        prop["assess_year"] = assessment.get("year", "Unknown")
        prop["mailed_tot"] = to_float(assessment.get("mailed_tot"))
        prop["mailed_bldg"] = to_float(assessment.get("mailed_bldg"))
        prop["mailed_land"] = to_float(assessment.get("mailed_land"))
        prop["certified_tot"] = to_float(assessment.get("certified_tot"))
        prop["board_tot"] = to_float(assessment.get("board_tot"))
        prop["assess_per_sqft"] = prop["mailed_tot"] / prop["sqft"] if prop["sqft"] else 0
        prop["implied_fmv"] = prop["mailed_tot"] / 0.10
    else:
        prop["assess_year"] = "N/A"
        prop["mailed_tot"] = 0
        prop["assess_per_sqft"] = 0
        prop["implied_fmv"] = 0

    # Add neighborhood info
    if parcel_data:
        prop["nbhd_code"] = parcel_data[0].get("nbhd_code", "")
        prop["township"] = parcel_data[0].get("township_name", "")
    else:
        prop["nbhd_code"] = ""
        prop["township"] = ""

    prop["address"] = ""

    return prop


def find_comps(prop, weights=None, ranges=None, limit=30):
    """
    Find comparable properties for uniformity analysis.
    Two-stage approach:
      1. Score all properties by similarity, keep top quartile
      2. Within that pool, rank by value advantage (appeal strength)
    Returns a DataFrame of scored and ranked comps.
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS

    nbhd = prop["nbhd_code"]
    prop_class = prop["class"]

    # Step 1: Get PINs in same neighborhood and class (current year)
    pin_data = query_socrata(PARCEL_UNIVERSE, {
        "$where": f"nbhd_code='{nbhd}' AND class='{prop_class}' AND year='{CURRENT_YEAR}'",
        "$select": "pin",
        "$limit": 5000
    })
    pin_list = [p["pin"] for p in pin_data]

    if len(pin_list) == 0:
        return pd.DataFrame()

    # Remove the subject property
    pin_list = [p for p in pin_list if p != prop["pin"]]

    # Step 2: Get characteristics for those PINs
    pin_filter = ",".join([f"'{p}'" for p in pin_list])
    chars_data = query_socrata(CHARACTERISTICS, {
        "$where": f"pin IN ({pin_filter})",
        "$order": "year DESC",
        "$limit": 50000
    })
    chars_df = pd.DataFrame(chars_data)

    if chars_df.empty:
        return pd.DataFrame()

    # Keep only the most recent record per PIN
    chars_df = chars_df.sort_values("year", ascending=False).drop_duplicates(subset="pin", keep="first")

    # Convert to numeric
    for col in ["char_bldg_sf", "char_land_sf", "char_yrblt", "char_beds"]:
        chars_df[col] = pd.to_numeric(chars_df[col], errors="coerce")

    # Drop rows with missing key fields
    chars_df = chars_df.dropna(subset=["char_bldg_sf", "char_yrblt"])

    # Step 3: Get assessed values
    comp_pins = chars_df["pin"].tolist()
    pin_filter2 = ",".join([f"'{p}'" for p in comp_pins])

    assess_year = prop["assess_year"]
    assess_data = query_socrata(ASSESSED_VALUES, {
        "$where": f"pin IN ({pin_filter2}) AND year='{assess_year}'",
        "$select": "pin, mailed_tot, mailed_bldg, mailed_land",
        "$limit": 50000
    })
    assess_df = pd.DataFrame(assess_data)

    if assess_df.empty:
        return pd.DataFrame()

    assess_df["mailed_tot"] = pd.to_numeric(assess_df["mailed_tot"], errors="coerce")

    # Merge characteristics with assessments
    merged = chars_df.merge(assess_df, on="pin", how="inner")

    # Step 4: Calculate age and apply hard cutoffs
    merged["age"] = CURRENT_YEAR - merged["char_yrblt"]
    my_age = prop["age"]
    age_cutoff = get_age_cutoff(my_age)

    # Hard cutoffs: remove properties that are not comparable at all
    merged = merged[
        (abs(merged["char_bldg_sf"] - prop["sqft"]) / prop["sqft"] <= DEFAULT_CUTOFFS["sqft_pct"]) &
        (abs(merged["age"] - my_age) <= age_cutoff) &
        (abs(merged["char_beds"] - prop["beds"]) <= DEFAULT_CUTOFFS["beds_diff"]) &
        (abs(merged["char_land_sf"] - prop["land_sf"]) / max(prop["land_sf"], 1) <= DEFAULT_CUTOFFS["lot_pct"])
    ].copy()

    if merged.empty:
        return pd.DataFrame()

    # Step 5: Score similarity (multiplicative with weighted exponents)
    # Normalize weights to sum to 1
    total_w = sum([weights["sqft"], weights["year_built"], weights["bedrooms"], weights["lot_size"]])
    w = {
        "sqft": weights["sqft"] / total_w,
        "age": weights["year_built"] / total_w,
        "beds": weights["bedrooms"] / total_w,
        "lot": weights["lot_size"] / total_w,
    }

    # Each factor: 1.0 = identical, 0.0 = at cutoff boundary
    merged["sqft_sim"] = 1 - abs(merged["char_bldg_sf"] - prop["sqft"]) / (prop["sqft"] * DEFAULT_CUTOFFS["sqft_pct"])
    merged["age_sim"] = 1 - abs(merged["age"] - my_age) / age_cutoff
    merged["beds_sim"] = 1 - abs(merged["char_beds"] - prop["beds"]) / DEFAULT_CUTOFFS["beds_diff"]
    merged["lot_sim"] = 1 - abs(merged["char_land_sf"] - prop["land_sf"]) / max(prop["land_sf"] * DEFAULT_CUTOFFS["lot_pct"], 1)

    # Multiplicative: all factors must be decent
    merged["similarity_pct"] = (
        merged["sqft_sim"] ** w["sqft"] *
        merged["age_sim"] ** w["age"] *
        merged["beds_sim"] ** w["beds"] *
        merged["lot_sim"] ** w["lot"]
    * 100).round(1)

    # Step 6: Calculate assessment per sqft and value advantage
    merged["assess_per_sqft"] = merged["mailed_tot"] / merged["char_bldg_sf"]
    merged["savings_per_sqft"] = prop["assess_per_sqft"] - merged["assess_per_sqft"]

    # Step 7: Deduplicate by PIN
    merged = merged.drop_duplicates(subset="pin", keep="first")

    # Step 8: Calculate appeal score
    # Savings as percentage, clamped 0-100
    merged["savings_pct"] = merged["savings_per_sqft"].apply(
        lambda x: min(max(x / prop["assess_per_sqft"] * 100, 0), 100)
    )

    # Multiply: similarity × savings potential
    merged["appeal_score"] = (
        merged["similarity_pct"] / 100 * merged["savings_pct"]
    ).round(1)

    # Sort by appeal score
    merged = merged.sort_values("appeal_score", ascending=False)
    all_comps = merged.copy()

    prop["address"] = ""

    return all_comps

def find_sales_comps(prop, weights=None, ranges=None):
    """
    Find comparable sales for overvaluation analysis.
    Returns a dictionary with sales analysis results.
    """
    median_price = 0
    mean_price = 0
    implied_fmv = prop.get("implied_fmv", 0)

    if weights is None:
        weights = DEFAULT_WEIGHTS

    nbhd = prop["nbhd_code"]
    prop_class = prop["class"]

    # Get recent sales in same neighborhood and class
    sales_data = query_socrata(PARCEL_SALES, {
        "$where": (
            f"nbhd='{nbhd}' AND class='{prop_class}'"
            f" AND sale_price > '10000'"
            f" AND sale_date > '2022-01-01'"
        ),
        "$select": (
            "pin, sale_price, sale_date,"
            " sale_filter_deed_type,"
            " sale_filter_less_than_10k,"
            " sale_filter_same_sale_within_365"
        ),
        "$order": "sale_date DESC",
        "$limit": 500
    })

    sales_df = pd.DataFrame(sales_data)

    if sales_df.empty:
        return {"has_case": False, "reason": "No recent sales found in your neighborhood for this property class.", "sales": pd.DataFrame(), "strong_comps": pd.DataFrame()}

    sales_df["sale_price"] = pd.to_numeric(sales_df["sale_price"], errors="coerce")

    # Filter out flagged transactions
    clean_sales = sales_df[
        (sales_df["sale_filter_deed_type"] == False) &
        (sales_df["sale_filter_less_than_10k"] == False) &
        (sales_df["sale_filter_same_sale_within_365"] == False)
    ].copy()

    if clean_sales.empty:
        return {"has_case": False, "reason": "No clean arm's-length sales found.",
                "sales": pd.DataFrame(), "strong_comps": pd.DataFrame(),
                "all_below": pd.DataFrame(), "all_sales_df": pd.DataFrame(),
                "median_price": 0, "mean_price": 0, "total_sales": 0,
                "sales_below_fmv": 0, "implied_fmv": implied_fmv,
                "case_strength": "none"}

    # Calculate stats HERE — after we know clean_sales has data
    median_price = clean_sales["sale_price"].median()
    mean_price = clean_sales["sale_price"].mean()
    
    # Sales below implied FMV
    implied_fmv = prop["implied_fmv"]
    below = clean_sales[clean_sales["sale_price"] < implied_fmv].copy()

    # Get characteristics for ALL clean sales
    all_sale_pins = clean_sales["pin"].tolist()
    if all_sale_pins:
        pin_filter_chars = ",".join([f"'{p}'" for p in all_sale_pins])
        chars_data = query_socrata(CHARACTERISTICS, {
            "$where": f"pin IN ({pin_filter_chars})",
            "$order": "year DESC",
            "$limit": 5000
        })
        if chars_data:
            chars_df = pd.DataFrame(chars_data)
            chars_df = chars_df.sort_values("year", ascending=False).drop_duplicates(subset="pin", keep="first")
            for col in ["char_bldg_sf", "char_land_sf", "char_yrblt", "char_beds"]:
                chars_df[col] = pd.to_numeric(chars_df[col], errors="coerce")

            clean_sales = clean_sales.merge(
                chars_df[["pin", "char_bldg_sf", "char_land_sf", "char_yrblt", "char_beds"]],
                on="pin", how="left"
            )

            # Score similarity using multiplicative approach with cutoffs
            clean_sales["char_yrblt"] = pd.to_numeric(clean_sales["char_yrblt"], errors="coerce")
            clean_sales["char_bldg_sf"] = pd.to_numeric(clean_sales["char_bldg_sf"], errors="coerce")
            clean_sales["char_land_sf"] = pd.to_numeric(clean_sales["char_land_sf"], errors="coerce")
            clean_sales["char_beds"] = pd.to_numeric(clean_sales["char_beds"], errors="coerce")

            has_data = clean_sales["char_bldg_sf"].notna() & clean_sales["char_yrblt"].notna()
            clean_sales.loc[has_data, "age"] = CURRENT_YEAR - clean_sales.loc[has_data, "char_yrblt"]
            my_age = prop["age"]
            age_cutoff = get_age_cutoff(my_age)

            # Normalize weights
            total_w = sum([weights["sqft"], weights["year_built"], weights["bedrooms"], weights["lot_size"]])
            w = {
                "sqft": weights["sqft"] / total_w,
                "age": weights["year_built"] / total_w,
                "beds": weights["bedrooms"] / total_w,
                "lot": weights["lot_size"] / total_w,
            }

            # Each factor with cutoff bounds
            s = clean_sales.loc[has_data]
            # Same formula as uniformity, tiny floor to prevent multiplication collapse
            FLOOR = 0.001
            sqft_sim = (1 - abs(s["char_bldg_sf"] - prop["sqft"]) / (prop["sqft"] * DEFAULT_CUTOFFS["sqft_pct"])).clip(FLOOR, 1)
            age_sim = (1 - abs(s["age"] - my_age) / age_cutoff).clip(FLOOR, 1)
            beds_sim = (1 - abs(s["char_beds"] - prop["beds"]) / DEFAULT_CUTOFFS["beds_diff"]).clip(FLOOR, 1)
            lot_sim = (1 - abs(s["char_land_sf"] - prop["land_sf"]) / max(prop["land_sf"] * DEFAULT_CUTOFFS["lot_pct"], 1)).clip(FLOOR, 1)

            clean_sales.loc[has_data, "similarity_pct"] = (
                sqft_sim ** w["sqft"] *
                age_sim ** w["age"] *
                beds_sim ** w["beds"] *
                lot_sim ** w["lot"]
            * 100).round(1)

    # Fill missing similarity scores
    if "similarity_pct" not in clean_sales.columns:
        clean_sales["similarity_pct"] = 0
    clean_sales["similarity_pct"] = clean_sales["similarity_pct"].fillna(0)

    # Sales below implied FMV
    below = clean_sales[clean_sales["sale_price"] < implied_fmv].copy()
    if not below.empty and "similarity_pct" in below.columns:
        below = below.sort_values("similarity_pct", ascending=False)

    # Assess case strength
    strong = pd.DataFrame()
    if not below.empty and "similarity_pct" in below.columns:
        strong = below[below["similarity_pct"] >= 30]
    elif not below.empty:
        strong = below.head(5)

    if len(strong) >= 5:
        case_strength = "strong"
        has_case = True
    elif len(strong) > 0:
        case_strength = "weak"
        has_case = True
    else:
        case_strength = "none"
        has_case = False

    
    return {
        "has_case": has_case,
        "case_strength": case_strength,
        "implied_fmv": implied_fmv,
        "median_price": median_price,
        "mean_price": mean_price,
        "total_sales": len(clean_sales),
        "sales_below_fmv": len(below),
        "strong_comps": strong.head(5) if not strong.empty else pd.DataFrame(),
        "all_below": below.head(10) if not below.empty else pd.DataFrame(),
        "all_sales_df": clean_sales,
    }


def batch_fetch_addresses(pin_list):
    """Fetch addresses for a list of PINs in a single API call."""
    if not pin_list:
        return {}

    # Deduplicate
    unique_pins = list(set(pin_list))

    pin_filter = ",".join([f"'{p}'" for p in unique_pins])
    addr_data = query_socrata(PARCEL_ADDRESSES, {
        "$where": f"pin IN ({pin_filter})",
        "$limit": 5000
    })

    addr_map = {}
    if addr_data:
        for row in addr_data:
            pin = row.get("pin", "")
            if pin and pin not in addr_map:
                addr_map[pin] = (
                    f"{row.get('prop_address_full', '')},"
                    f" {row.get('prop_address_city_name', '')}"
                    f" {row.get('prop_address_zipcode_1', '')}"
                )

    return addr_map

def calculate_savings(current_assess, proposed_assess, eq_factor=2.9160, tax_rate=None):
    """
    Estimate potential savings from a successful appeal.
    eq_factor: Cook County equalization factor (2024 value: ~2.9160)
    tax_rate: local tax rate as a decimal (varies by tax code, typical ~8-12%)
    """
    if tax_rate is None:
        tax_rate = 0.08

    current_eav = current_assess * eq_factor
    proposed_eav = proposed_assess * eq_factor
    savings = (current_eav - proposed_eav) * tax_rate

    return {
        "current_assess": current_assess,
        "proposed_assess": proposed_assess,
        "reduction": current_assess - proposed_assess,
        "current_eav": current_eav,
        "proposed_eav": proposed_eav,
        "eq_factor": eq_factor,
        "tax_rate": tax_rate,
        "estimated_annual_savings": max(savings, 0),
    }

def to_float(value, default=0):
    """Safely convert a value to float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default