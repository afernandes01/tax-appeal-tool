"""
app.py — Flask web application for the Cook County Property Tax Appeal Tool
"""

import pandas as pd

from flask import Flask, render_template, request, session
from helpers import (
    fetch_property, find_comps, find_sales_comps,
    calculate_savings, batch_fetch_addresses,
    DEFAULT_WEIGHTS
)

app = Flask(__name__)
app.secret_key = "cs50-tax-appeal-tool"

# Store results in memory for "see more" pages
results_cache = {}


@app.route("/")
def index():
    """Home page with PIN input."""
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    """Run the full appeal analysis for a given PIN."""

    # Get PIN from the form
    pin = request.form.get("pin", "").strip()

    # Clean the PIN: remove dashes and spaces, zero-pad to 14 digits
    pin = pin.replace("-", "").replace(" ", "")
    if len(pin) < 14:
        pin = pin.zfill(14)

    # Get user-adjusted weights (or use defaults)
    weights = {
        "sqft": float(request.form.get("w_sqft", DEFAULT_WEIGHTS["sqft"])),
        "year_built": float(request.form.get("w_year", DEFAULT_WEIGHTS["year_built"])),
        "bedrooms": float(request.form.get("w_beds", DEFAULT_WEIGHTS["bedrooms"])),
        "lot_size": float(request.form.get("w_lot", DEFAULT_WEIGHTS["lot_size"])),
    }


    try:
        # Step 1: Fetch the property
        prop = fetch_property(pin)

        if prop is None:
            return render_template("error.html",
                                   message=f"No property found for PIN {pin}. "
                                           "Please check the number and try again.")

        if "error" in prop:
            return render_template("error.html", message=prop["error"])

        # Step 2: Find uniformity comps
        comps_df = find_comps(prop, weights=weights, limit=30)

        # Step 3: Find sales comps for overvaluation
        sales_result = find_sales_comps(prop, weights=weights)

        # Step 4: Calculate potential savings based on top uniformity comps
        savings = None
        uniformity_strength = "none"

        if not comps_df.empty:
            strong_comps = comps_df[comps_df["appeal_score"] >= 20]
            moderate_comps = comps_df[comps_df["appeal_score"] >= 10]

            if len(strong_comps) >= 5:
                uniformity_strength = "strong"
            elif len(moderate_comps) >= 3:
                uniformity_strength = "moderate"
            else:
                uniformity_strength = "weak"

            positive_comps = comps_df[comps_df["savings_per_sqft"] > 0]

        # Batch fetch all addresses in ONE API call
        all_pins = [pin]
        if not comps_df.empty:
            all_pins.extend(comps_df["pin"].tolist())
        if not sales_result.get("all_below", pd.DataFrame()).empty:
            all_pins.extend(sales_result["all_below"]["pin"].tolist())
        if not sales_result.get("all_sales_df", pd.DataFrame()).empty:
            all_pins.extend(sales_result["all_sales_df"]["pin"].tolist())

        addr_map = batch_fetch_addresses(all_pins)

        # Apply addresses
        prop["address"] = addr_map.get(pin, "Address not found")

        if not comps_df.empty:
            comps_df["address"] = comps_df["pin"].map(addr_map).fillna("N/A")

        if not sales_result.get("all_below", pd.DataFrame()).empty:
            sales_result["all_below"]["address"] = (
                sales_result["all_below"]["pin"].map(addr_map).fillna("N/A")
            )

        if not sales_result.get("strong_comps", pd.DataFrame()).empty:
            sales_result["strong_comps"]["address"] = (
                sales_result["strong_comps"]["pin"].map(addr_map).fillna("N/A")
            )

        if not sales_result.get("all_sales_df", pd.DataFrame()).empty:
            sales_result["all_sales_df"]["address"] = (
                sales_result["all_sales_df"]["pin"].map(addr_map).fillna("N/A")
            )

        # Prepare data for templates
        comps_top10 = comps_df.head(10).to_dict("records") if not comps_df.empty else []
        comps_all = comps_df.to_dict("records") if not comps_df.empty else []

        sales_comps_list = []
        if not sales_result["strong_comps"].empty:
            sales_comps_list = sales_result["strong_comps"].to_dict("records")
        elif not sales_result.get("all_below", pd.DataFrame()).empty:
            sales_comps_list = sales_result["all_below"].head(5).to_dict("records")

        all_sales = []
        if "all_sales_df" in sales_result and not sales_result["all_sales_df"].empty:
            all_sales = sales_result["all_sales_df"].to_dict("records")

        # Cache for "see more" pages
        results_cache[pin] = {
            "prop": prop,
            "comps_all": comps_all,
            "all_sales": all_sales,
            "weights": weights,
        }

        return render_template("results.html",
                               prop=prop,
                               comps=comps_top10,
                               sales=sales_result,
                               sales_comps=sales_comps_list,
                               savings=savings,
                               weights=weights,
                               uniformity_strength=uniformity_strength,
                               pin=pin)

    except Exception as e:
        print(f"Error during analysis: {e}")
        return render_template("error.html",
                               message="The Cook County data portal is responding slowly. "
                                       "This sometimes happens during peak hours. "
                                       "Please wait a moment and try again.")


@app.route("/comps/<pin>")
def all_comps(pin):
    """Show full uniformity comp table."""
    cached = results_cache.get(pin)
    if not cached:
        return render_template("error.html",
                               message="Session expired. Please run the analysis again.")
    return render_template("all_comps.html",
                           prop=cached["prop"],
                           comps=cached["comps_all"])


@app.route("/sales/<pin>")
def all_sales(pin):
    """Show full sales table."""
    cached = results_cache.get(pin)
    if not cached:
        return render_template("error.html",
                               message="Session expired. Please run the analysis again.")
    return render_template("all_sales.html",
                           prop=cached["prop"],
                           sales=cached["all_sales"])


@app.route("/learn")
def learn():
    """Educational page about the assessment and appeal process."""
    return render_template("learn.html")


if __name__ == "__main__":
    app.run(debug=True)