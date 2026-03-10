import os

import streamlit as st
import requests
import pandas as pd
from datetime import date, timedelta

# -------------------------------------------------------
# Config
# -------------------------------------------------------
# API_BASE_URL: set qua env var
# - Docker local:  http://backend:8000  (default)
# - Render:        https://<backend-service>.onrender.com  (set trong render.yaml)
# - Local dev:     http://localhost:8000
_api_base_raw = os.environ.get("API_BASE_URL", "http://backend:8000")
# Render fromService host tra ve hostname k co scheme, prepend https://
if _api_base_raw and not _api_base_raw.startswith("http"):
    _api_base_raw = "https://" + _api_base_raw
API_BASE = _api_base_raw.rstrip("/") + "/api"

st.set_page_config(
    page_title="RFQ Automation System",
    page_icon=None,
    layout="wide",
)


def api_get(endpoint: str):
    """Helper: GET request toi backend."""
    try:
        resp = requests.get(f"{API_BASE}{endpoint}", timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to backend server. Make sure it's running on port 8000.")
        return None
    except Exception as exc:
        st.error(f"API error: {exc}")
        return None


def api_post(endpoint: str, data: dict = None):
    """Helper: POST request toi backend."""
    try:
        resp = requests.post(f"{API_BASE}{endpoint}", json=data, timeout=180)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error("Cannot connect to backend server. Make sure it's running on port 8000.")
        return None
    except requests.exceptions.HTTPError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            pass
        st.error(f"API error: {detail or exc}")
        return None
    except Exception as exc:
        st.error(f"Error: {exc}")
        return None


# -------------------------------------------------------
# Sidebar navigation
# -------------------------------------------------------

st.sidebar.title("RFQ Automation")
page = st.sidebar.radio(
    "Navigate",
    ["Create RFQ", "RFQ List", "Dashboard"],
)


# -------------------------------------------------------
# Page: Create RFQ
# -------------------------------------------------------

if page == "Create RFQ":
    st.header("Create New RFQ")
    st.write("Fill in the shipment details and add vendor contacts.")

    with st.form("rfq_form"):
        col1, col2 = st.columns(2)

        with col1:
            product = st.text_input("Product", placeholder="e.g., 40ft Container - Electronics")
            quantity = st.number_input("Quantity", min_value=1, value=3)
            origin = st.text_input("Origin", placeholder="e.g., Shenzhen")

        with col2:
            destination = st.text_input("Destination", placeholder="e.g., Los Angeles")
            delivery_date = st.date_input(
                "Required Delivery Date",
                value=date.today() + timedelta(days=30),
            )
            special_notes = st.text_area("Special Notes", placeholder="e.g., Temperature control required")

        st.subheader("Vendors")
        st.write("Add at least 1 vendor. You can add up to 10.")

        num_vendors = st.number_input("Number of vendors", min_value=1, max_value=10, value=5)

        vendors_data = []
        for i in range(num_vendors):
            st.markdown(f"**Vendor {i + 1}**")
            vcol1, vcol2, vcol3 = st.columns(3)
            with vcol1:
                v_name = st.text_input(f"Name", key=f"vname_{i}", placeholder="Vendor name")
            with vcol2:
                v_email = st.text_input(f"Email", key=f"vemail_{i}", placeholder="vendor@example.com")
            with vcol3:
                v_company = st.text_input(f"Company", key=f"vcomp_{i}", placeholder="Company name")
            vendors_data.append({"name": v_name, "email": v_email, "company": v_company})

        submitted = st.form_submit_button("Create RFQ")

        if submitted:
            # Validation
            errors = []
            if not product.strip():
                errors.append("Product is required.")
            if not origin.strip():
                errors.append("Origin is required.")
            if not destination.strip():
                errors.append("Destination is required.")

            valid_vendors = [v for v in vendors_data if v["name"].strip() and v["email"].strip()]
            if not valid_vendors:
                errors.append("At least 1 vendor with name and email is required.")

            if errors:
                for e in errors:
                    st.error(e)
            else:
                payload = {
                    "product": product.strip(),
                    "quantity": quantity,
                    "origin": origin.strip(),
                    "destination": destination.strip(),
                    "required_delivery_date": str(delivery_date),
                    "special_notes": special_notes.strip() or None,
                    "vendors": valid_vendors,
                }
                result = api_post("/rfq", payload)
                if result:
                    st.success(f"RFQ #{result['id']} created successfully!")
                    st.json(result)


# -------------------------------------------------------
# Page: RFQ List
# -------------------------------------------------------

elif page == "RFQ List":
    st.header("All RFQs")

    rfqs = api_get("/rfq")
    if rfqs:
        if not rfqs:
            st.info("No RFQs yet. Create one first!")
        else:
            for rfq in rfqs:
                with st.expander(f"RFQ #{rfq['id']} - {rfq['product']} ({rfq['status']})"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**Product:** {rfq['product']}")
                        st.write(f"**Quantity:** {rfq['quantity']}")
                        st.write(f"**Route:** {rfq['origin']} -> {rfq['destination']}")
                    with col2:
                        st.write(f"**Delivery Date:** {rfq.get('required_delivery_date', 'N/A')}")
                        st.write(f"**Status:** {rfq['status']}")
                        st.write(f"**Created:** {rfq['created_at']}")

                    # Action buttons
                    bcol1, bcol2, bcol3 = st.columns(3)

                    with bcol1:
                        if st.button("Send Emails", key=f"send_{rfq['id']}"):
                            with st.spinner("Queueing email task..."):
                                result = api_post(f"/rfq/{rfq['id']}/send")
                                if result:
                                    st.success(f"📨 {result.get('message', 'Task queued')} (task: {result.get('task_id', 'N/A')[:8]}...)")
                                    st.info("Emails are being sent in background. Refresh to see status.")

                    with bcol2:
                        if st.button("Poll Responses", key=f"poll_{rfq['id']}"):
                            with st.spinner("Queueing poll task..."):
                                result = api_post(f"/rfq/{rfq['id']}/poll")
                                if result:
                                    st.success(f"📬 {result.get('message', 'Task queued')} (task: {result.get('task_id', 'N/A')[:8]}...)")
                                    st.info("Polling in background. Refresh to see new responses.")

                    with bcol3:
                        if st.button("View Details", key=f"detail_{rfq['id']}"):
                            detail = api_get(f"/rfq/{rfq['id']}")
                            if detail:
                                st.subheader("Vendors")
                                for v in detail.get("vendors", []):
                                    st.write(f"- {v['name']} ({v['email']})")

                                st.subheader("Responses")
                                responses = detail.get("vendor_responses", [])
                                # Deduplicate: giu lai response tot nhat moi vendor
                                seen_vendors = {}
                                for r in responses:
                                    key = r["vendor_email"]
                                    prev = seen_vendors.get(key)
                                    if prev is None or (
                                        r["status"] == "extracted" and prev["status"] != "extracted"
                                    ):
                                        seen_vendors[key] = r
                                responses = list(seen_vendors.values())
                                if responses:
                                    for r in responses:
                                        st.write(f"**{r.get('vendor_name', r['vendor_email'])}** "
                                                 f"- Status: {r['status']}")
                                        if r.get("unit_price_usd"):
                                            st.write(f"  Price: ${r['unit_price_usd']} USD | "
                                                     f"Lead time: {r.get('lead_time_days', 'N/A')} days | "
                                                     f"Terms: {r.get('payment_terms', 'N/A')}")
                                else:
                                    st.info("No responses yet.")


# -------------------------------------------------------
# Page: Dashboard (Comparison Table)
# -------------------------------------------------------

elif page == "Dashboard":
    st.header("Vendor Comparison Dashboard")

    # Chon RFQ
    rfqs = api_get("/rfq")
    if not rfqs:
        st.info("No RFQs available.")
    else:
        rfq_options = {f"RFQ #{r['id']} - {r['product']}": r["id"] for r in rfqs}
        selected = st.selectbox("Select RFQ", list(rfq_options.keys()))
        rfq_id = rfq_options[selected]

        comparison = api_get(f"/rfq/{rfq_id}/comparison")
        if comparison and comparison.get("rows"):
            st.subheader(f"{comparison['product']} | {comparison['route']}")

            # Chuyen sang DataFrame de hien thi bang
            rows = comparison["rows"]
            # Deduplicate: giu response tot nhat moi vendor (theo email)
            seen = {}
            for r in rows:
                key = r["vendor_email"]
                if key not in seen:
                    seen[key] = r
            rows = list(seen.values())
            df = pd.DataFrame(rows)

            # Rename columns cho dep
            column_names = {
                "vendor_name": "Vendor",
                "vendor_email": "Email",
                "unit_price_usd": "Unit Price (USD)",
                "lead_time_days": "Lead Time (Days)",
                "payment_terms": "Payment Terms",
                "confidence_score": "Confidence",
                "incoterms": "Incoterms",
                "penalty_clause": "Penalty Clause",
                "validity": "Validity",
            }
            df = df.rename(columns=column_names)

            # Format
            if "Unit Price (USD)" in df.columns:
                df["Unit Price (USD)"] = df["Unit Price (USD)"].apply(
                    lambda x: f"${x:,.2f}" if pd.notna(x) else "N/A"
                )
            if "Confidence" in df.columns:
                df["Confidence"] = df["Confidence"].apply(
                    lambda x: f"{x:.2f}" if pd.notna(x) else "N/A"
                )

            st.dataframe(df, use_container_width=True, hide_index=True)

            # Highlight: vendor re nhat va nhanh nhat
            st.subheader("Recommendations")

            numeric_rows = [r for r in rows if r.get("unit_price_usd") is not None]
            if numeric_rows:
                cheapest = min(numeric_rows, key=lambda x: x["unit_price_usd"])
                st.write(
                    f"**Lowest Price:** {cheapest.get('vendor_name', cheapest['vendor_email'])} "
                    f"at ${cheapest['unit_price_usd']:,.2f}"
                )

            time_rows = [r for r in rows if r.get("lead_time_days") is not None]
            if time_rows:
                fastest = min(time_rows, key=lambda x: x["lead_time_days"])
                st.write(
                    f"**Fastest Delivery:** {fastest.get('vendor_name', fastest['vendor_email'])} "
                    f"at {fastest['lead_time_days']} days"
                )

            confidence_rows = [r for r in rows if r.get("confidence_score") is not None]
            if confidence_rows:
                most_reliable = max(confidence_rows, key=lambda x: x["confidence_score"])
                st.write(
                    f"**Highest Confidence:** {most_reliable.get('vendor_name', most_reliable['vendor_email'])} "
                    f"at {most_reliable['confidence_score']:.2f}"
                )

        else:
            st.info("No vendor responses available for this RFQ yet. "
                    "Send emails and poll for responses first.")
