import streamlit as st
import pymongo
import certifi
import pandas as pd
import Constants

st.set_page_config(page_title="Uppie", page_icon="📊", layout="wide")
st.title("Uppie")

if st.button("Refresh Data", type="primary"):
    st.rerun()

try:
    client = pymongo.MongoClient(Constants.mongo_conn, tlsCAFile=certifi.where())
    db = client["uppie"]
    collection = db["raw_data"]

    raw_docs = list(collection.find({}).sort([
        ("date_label", -1),
        ("time_label", -1),
    ]))

    raw_df = pd.DataFrame(raw_docs)
    if not raw_df.empty:
        raw_df = raw_df.drop(columns=["_id"], errors="ignore")
        raw_df = raw_df.fillna("")

    nifty_collection = db["nifty_data"]
    nifty_docs = list(nifty_collection.find({}).sort([
        ("date_label", -1),
        ("time_label", -1),
    ]))
    nifty_df = pd.DataFrame(nifty_docs)
    if not nifty_df.empty:
        nifty_df = nifty_df.drop(columns=["_id"], errors="ignore")
        nifty_df = nifty_df.fillna("")

    processed_docs = list(collection.find({"processed": 1}).sort([
        ("date_label", -1),
        ("time_label", -1),
    ]))
    processed_df = pd.DataFrame(processed_docs)

    if processed_df.empty:
        st.info("No processed rows found for the derived dataframe.")
    else:
        processed_df = processed_df.drop(columns=["_id"], errors="ignore")
        processed_df["time_sort"] = pd.to_datetime(processed_df["time_label"].astype(str), format="%H:%M", errors="coerce")
        latest_time = processed_df["time_sort"].dropna().max()

        if pd.isna(latest_time):
            st.warning("No valid time_label values found in processed rows.")
        else:
            strike_cols = sorted(
                pd.to_numeric(processed_df["strike_price"], errors="coerce").dropna().unique().tolist()
            )

            if not strike_cols:
                st.info("No strike prices available for the latest processed time label.")
            else:
                all_times = sorted(
                    processed_df["time_label"].dropna().unique().tolist(),
                    key=lambda x: pd.to_datetime(x, format="%H:%M"),
                )
                row_map = []
                cell_nature = {}
                previous_overall = 0

                for time_label in all_times:
                    time_rows = processed_df[processed_df["time_label"] == time_label]
                    sentiment_total = pd.to_numeric(time_rows["sentiment_count"], errors="coerce").fillna(0).sum()
                    sentiment_value = int(sentiment_total) if pd.notna(sentiment_total) else 0
                    overall_value = previous_overall + sentiment_value
                    row = {
                        "time_label": time_label,
                        "overall_sentiment": overall_value,
                        "sentiment": sentiment_value,
                    }
                    for strike in strike_cols:
                        record = time_rows[
                            pd.to_numeric(time_rows["strike_price"], errors="coerce") == float(strike)
                        ]
                        if record.empty:
                            row[str(strike)] = ""
                            continue

                        rec = record.iloc[0]
                        nature = str(rec.get("nature_strength", "")).strip().upper()

                        if nature == "RESISTANCE":
                            value = rec.get("ce_current_strength")
                        elif nature == "SUPPORT":
                            value = rec.get("pe_current_strength")
                        else:
                            value = ""

                        if value is None or value == 0 or value == 0.0:
                            row[str(strike)] = ""
                        else:
                            value_text = f"{float(value):.2f}" if isinstance(value, (int, float)) else str(value)
                            row[str(strike)] = value_text
                            cell_nature[(time_label, float(strike))] = nature

                    row_values = [v for k, v in row.items() if k not in ["time_label", "sentiment", "overall_sentiment"]]
                    if not any(
                        (v is not None)
                        and (str(v).strip() not in ["", "0", "0.0", "0.00", "NaN", "nan"])
                        for v in row_values
                    ):
                        continue

                    previous_overall = row["overall_sentiment"]
                    row_map.append(row)

                if not row_map:
                    st.info("No valid rows after removing blank entries.")
                else:
                    derived_df = pd.DataFrame(row_map)
                    derived_df = derived_df[["time_label", "overall_sentiment", "sentiment"] + [str(s) for s in strike_cols]]
                    derived_df = derived_df.fillna("")
                    derived_df = derived_df.sort_values("time_label", key=lambda x: pd.to_datetime(x, format="%H:%M"), ascending=False).reset_index(drop=True)

                    def safe_float_label(value):
                        if value is None:
                            return ""
                        if isinstance(value, str):
                            value = value.strip()
                            if value in ["", "nan", "NaN", "None", "none"]:
                                return ""
                        try:
                            return f"{float(value):.2f}"
                        except (TypeError, ValueError):
                            return ""

                    if not nifty_df.empty:
                        nifty_lookup = nifty_df[["time_label"]].copy()
                        nifty_lookup["time_label"] = nifty_lookup["time_label"].astype(str)
                        nifty_lookup["nifty"] = pd.to_numeric(
                            nifty_df["nifty_value"] if "nifty_value" in nifty_df.columns else nifty_df["nifty_ltp"],
                            errors="coerce"
                        )
                        nifty_lookup["vix"] = pd.to_numeric(
                            nifty_df["vix_value"] if "vix_value" in nifty_df.columns else nifty_df["vix_ltp"],
                            errors="coerce"
                        )
                        for col in ["dsm_direction", "bullish_score", "bearish_score", "dsm_gap_status"]:
                            nifty_lookup[col] = nifty_df[col] if col in nifty_df.columns else ""
                        nifty_lookup = nifty_lookup.drop_duplicates(subset=["time_label"], keep="last")
                        derived_df = derived_df.merge(
                            nifty_lookup,
                            on="time_label",
                            how="left"
                        )
                        derived_df["nifty"] = derived_df["nifty"].map(safe_float_label)
                        derived_df["vix"] = derived_df["vix"].map(safe_float_label)
                        derived_df["bullish_score"] = derived_df["bullish_score"].map(safe_float_label)
                        derived_df["bearish_score"] = derived_df["bearish_score"].map(safe_float_label)
                        derived_df = derived_df[["time_label", "nifty", "vix", "dsm_direction", "bullish_score", "bearish_score", "dsm_gap_status", "overall_sentiment", "sentiment"] + [str(s) for s in strike_cols]]
                    else:
                        derived_df.insert(1, "nifty", "")
                        derived_df.insert(2, "vix", "")
                        derived_df.insert(3, "dsm_direction", "")
                        derived_df.insert(4, "bullish_score", "")
                        derived_df.insert(5, "bearish_score", "")
                        derived_df.insert(6, "dsm_gap_status", "")
                        derived_df = derived_df[["time_label", "nifty", "vix", "dsm_direction", "bullish_score", "bearish_score", "dsm_gap_status", "overall_sentiment", "sentiment"] + [str(s) for s in strike_cols]]

                    first_row_overall = int(derived_df["overall_sentiment"].iloc[0]) if not derived_df.empty else 0
                    strike_cols_display = [str(s) for s in strike_cols]

                    for col in strike_cols_display:
                        for row_idx in range(len(derived_df)):
                            current_value = pd.to_numeric(derived_df.iloc[row_idx][col], errors="coerce")
                            if pd.isna(current_value):
                                continue
                            if row_idx < len(derived_df) - 1:
                                compare_value = pd.to_numeric(derived_df.iloc[row_idx + 1][col], errors="coerce")
                            else:
                                compare_value = None
                            if pd.notna(compare_value):
                                if current_value > compare_value:
                                    derived_df.at[row_idx, col] = f"↑ {float(current_value):.2f}"
                                elif current_value < compare_value:
                                    derived_df.at[row_idx, col] = f"↓ {float(current_value):.2f}"
                                else:
                                    derived_df.at[row_idx, col] = f"{float(current_value):.2f}"

                    def apply_styles(row):
                        styles = []
                        for col in derived_df.columns:
                            value = row[col]
                            if col == "time_label":
                                styles.append("background-color: #f3f4f6; color: #111; font-weight: 600")
                                continue
                            if col in ["nifty", "vix"]:
                                styles.append("background-color: #f3f4f6; color: #1f2937; font-weight: 600")
                                continue
                            if col == "dsm_direction":
                                direction = str(value).upper() if value not in [None, "", "nan", "NaN"] else ""
                                if direction == "BULLISH":
                                    styles.append("background-color: #e8f5e9; color: green; font-weight: bold")
                                elif direction == "BEARISH":
                                    styles.append("background-color: #ffebee; color: red; font-weight: bold")
                                else:
                                    styles.append("background-color: #f3f4f6; color: #1f2937; font-weight: 600")
                                continue
                            if col in ["bullish_score", "bearish_score"]:
                                styles.append("background-color: #f8fafc; color: #1f2937; font-weight: 600")
                                continue
                            if col == "dsm_gap_status":
                                gap_status = str(value).upper() if value not in [None, "", "nan", "NaN"] else ""
                                if gap_status in ["UPGAP", "GAP UP", "BULLISH GAP"]:
                                    styles.append("background-color: #e8f5e9; color: green; font-weight: bold")
                                elif gap_status in ["DOWNGAP", "GAP DOWN", "BEARISH GAP"]:
                                    styles.append("background-color: #ffebee; color: red; font-weight: bold")
                                else:
                                    styles.append("background-color: #f3f4f6; color: #1f2937; font-weight: 600")
                                continue
                            if col == "overall_sentiment":
                                compare_value = None
                                if row.name < len(derived_df) - 1:
                                    compare_value = derived_df.iloc[row.name + 1]["overall_sentiment"]
                                elif row.name > 0:
                                    compare_value = derived_df.iloc[row.name - 1]["overall_sentiment"]
                                if compare_value is None or pd.isna(compare_value):
                                    styles.append("background-color: #f3f4f6; color: #111; font-weight: bold")
                                elif value > compare_value:
                                    styles.append("background-color: #e8f5e9; color: green; font-weight: bold")
                                elif value < compare_value:
                                    styles.append("background-color: #ffebee; color: red; font-weight: bold")
                                else:
                                    styles.append("background-color: #f3f4f6; color: #111; font-weight: bold")
                                continue
                            if col == "sentiment":
                                styles.append("background-color: #eef2ff; color: #1f2937; font-weight: 600")
                                continue

                            if value in [None, "", "0", "0.0", "0.00", "NaN", "nan"]:
                                styles.append("")
                                continue

                            arrow_color = ""
                            if isinstance(value, str) and value.startswith(("↑", "↓")):
                                if value.startswith("↑"):
                                    arrow_color = "color: blue; font-weight: bold"
                                else:
                                    arrow_color = "color: red; font-weight: bold"

                            nature = cell_nature.get((row["time_label"], float(col)), "")
                            if nature == "RESISTANCE":
                                styles.append("background-color: #ffebee; color: red; font-weight: bold")
                            elif nature == "SUPPORT":
                                styles.append("background-color: #e8f5e9; color: green; font-weight: bold")
                            elif arrow_color:
                                styles.append(arrow_color)
                            else:
                                styles.append("")
                        return styles

                    styled_derived = derived_df.style.apply(apply_styles, axis=1)
                    st.subheader("Derived strength matrix")
                    st.dataframe(styled_derived, use_container_width=True, height=350, hide_index=True)

                    event_labels = sorted(
                        {
                            label.strip()
                            for label in (
                                processed_df["call_event_recorded"].dropna().astype(str).tolist()
                                + processed_df["put_event_recorded"].dropna().astype(str).tolist()
                            )
                            if str(label).strip() and str(label).strip() != "nan"
                        }
                    )

                    latest_strike_df = processed_df[processed_df["time_sort"] == latest_time].copy()
                    strike_tracker_df = pd.DataFrame({"strike_price": sorted(
                        pd.to_numeric(latest_strike_df["strike_price"], errors="coerce").dropna().unique().tolist()
                    )})

                    strike_tracker_df["strike_price"] = strike_tracker_df["strike_price"].astype(float)
                    strike_tracker_df["strike_price"] = strike_tracker_df["strike_price"].map(lambda x: f"{float(x):.1f}" if pd.notna(x) else x)

                    for event_label in event_labels:
                        values = []
                        for strike in strike_tracker_df["strike_price"]:
                            match_call = processed_df[
                                (pd.to_numeric(processed_df["strike_price"], errors="coerce") == float(strike))
                                & (processed_df["call_event_recorded"].astype(str) == event_label)
                            ]
                            match_put = processed_df[
                                (pd.to_numeric(processed_df["strike_price"], errors="coerce") == float(strike))
                                & (processed_df["put_event_recorded"].astype(str) == event_label)
                            ]
                            total = pd.to_numeric(match_call["sentiment_count"], errors="coerce").fillna(0).sum()
                            total += pd.to_numeric(match_put["sentiment_count"], errors="coerce").fillna(0).sum()
                            values.append(int(total) if pd.notna(total) else 0)
                        strike_tracker_df[event_label] = values

                    strike_tracker_df = strike_tracker_df.sort_values("strike_price").reset_index(drop=True)

                    strike_tracker_df = strike_tracker_df.fillna("")

                    row_totals = []
                    for _, row in strike_tracker_df.iterrows():
                        total = 0
                        for col in event_labels:
                            val = row[col]
                            if pd.notna(val) and str(val).strip() not in ["", "nan", "NaN"]:
                                total += int(pd.to_numeric(val, errors="coerce"))
                        row_totals.append(total)

                    strike_tracker_df["row_total"] = row_totals

                    totals_row = {"strike_price": "TOTAL", "row_total": first_row_overall}
                    for col in event_labels:
                        totals_row[col] = int(pd.to_numeric(strike_tracker_df[col], errors="coerce").fillna(0).sum())

                    strike_tracker_df = strike_tracker_df[["strike_price", "row_total"] + event_labels]
                    strike_tracker_df = pd.concat([strike_tracker_df, pd.DataFrame([totals_row])], ignore_index=True)

                    overall_sentiment_total = int(first_row_overall)

                    def style_strike_table(df):
                        styles = pd.DataFrame('', index=df.index, columns=df.columns)
                        for i, row in df.iterrows():
                            row_total = row.get('row_total', '')
                            if pd.notna(row_total) and str(row_total).strip() not in ['', 'nan', 'NaN']:
                                val = float(row_total)
                                if val > 0:
                                    styles.at[i, 'row_total'] = 'background-color: #e8f5e9; color: green; font-weight: bold'
                                elif val < 0:
                                    styles.at[i, 'row_total'] = 'background-color: #ffebee; color: red; font-weight: bold'
                        return styles

                    styled_tracker = strike_tracker_df.style.set_properties(**{'font-size': '11px'})
                    styled_tracker = styled_tracker.apply(style_strike_table, axis=None)

                    st.subheader("Strike Event Tracker")
                    st.dataframe(styled_tracker, use_container_width=True, height=210, hide_index=True)
                    st.markdown(f"<span style='font-size: 18px; font-weight: bold; color: #111;'>Overall Sentiment : {overall_sentiment_total}</span>", unsafe_allow_html=True)

                    activity_meanings = [
                        {
                            "Activity": "CE Fresh Buying",
                            "Simple Meaning": "Traders are buying Calls expecting the market to go up.",
                            "Count": 0,
                        },
                        {
                            "Activity": "PE Fresh Buying",
                            "Simple Meaning": "Traders are buying Puts expecting the market to go down.",
                            "Count": 0,
                        },
                        {
                            "Activity": "CE Fresh Writing",
                            "Simple Meaning": "Traders are selling Calls expecting the market to stay below or fall.",
                            "Count": 0,
                        },
                        {
                            "Activity": "PE Fresh Writing",
                            "Simple Meaning": "Traders are selling Puts expecting the market to stay above or rise.",
                            "Count": 0,
                        },
                        {
                            "Activity": "CE Short Covering",
                            "Simple Meaning": "Call sellers are exiting because the market is rising.",
                            "Count": 0,
                        },
                        {
                            "Activity": "PE Short Covering",
                            "Simple Meaning": "Put sellers are exiting because the market is falling or uncertainty is increasing.",
                            "Count": 0,
                        },
                        {
                            "Activity": "CE Long Unwinding",
                            "Simple Meaning": "Call buyers are booking losses/profits and exiting; bullishness is weakening.",
                            "Count": 0,
                        },
                        {
                            "Activity": "PE Long Unwinding",
                            "Simple Meaning": "Put buyers are booking losses/profits and exiting; bearishness is weakening.",
                            "Count": 0,
                        },
                    ]

                    activity_df = pd.DataFrame(activity_meanings)
                    if isinstance(strike_tracker_df, pd.DataFrame) and not strike_tracker_df.empty:
                        totals_row_map = pd.DataFrame([totals_row]).iloc[0].to_dict()
                        for idx, row in activity_df.iterrows():
                            activity_name = row["Activity"]
                            activity_total = 0
                            if activity_name in totals_row_map:
                                activity_total = int(pd.to_numeric(totals_row_map[activity_name], errors="coerce"))
                            activity_df.at[idx, "Count"] = activity_total

                    st.subheader("Activity Meanings")
                    green_activities = {
                        "CE Fresh Buying",
                        "PE Fresh Writing",
                        "CE Short Covering",
                        "PE Long Unwinding",
                    }
                    red_activities = {
                        "PE Fresh Buying",
                        "CE Fresh Writing",
                        "PE Short Covering",
                        "CE Long Unwinding",
                    }

                    display_activity_df = activity_df[["Activity", "Count", "Simple Meaning"]].copy()

                    def color_activity_rows(row):
                        activity = row["Activity"]
                        if activity in green_activities:
                            return ["background-color: #e8f5e9; color: green; font-weight: bold"] * 3
                        if activity in red_activities:
                            return ["background-color: #ffebee; color: red; font-weight: bold"] * 3
                        return ["background-color: #f3f4f6; color: #111; font-weight: normal"] * 3

                    st.dataframe(
                        display_activity_df.style.apply(color_activity_rows, axis=1),
                        use_container_width=False,
                        width=1000,
                        hide_index=True,
                        column_config={
                            "Activity": st.column_config.TextColumn(width="small"),
                            "Count": st.column_config.NumberColumn(width="small"),
                            "Simple Meaning": st.column_config.TextColumn(width="large"),
                        },
                    )

    st.subheader("Raw MongoDB data")
    st.dataframe(raw_df, use_container_width=True, height=420, hide_index=True)

    st.markdown("<div style='height: 24px;'></div>", unsafe_allow_html=True)

    if st.button("Cleanup Data", type="secondary"):
        st.session_state.cleanup_requested = True

    if st.session_state.get("cleanup_requested"):
        st.warning("This will delete all raw_data and nifty_data entries. This action cannot be undone. Click below to confirm.")
        col_confirm, col_cancel = st.columns([1, 1])
        with col_confirm:
            if st.button("Confirm Cleanup", type="primary"):
                db["raw_data"].delete_many({})
                db["nifty_data"].delete_many({})
                st.session_state.cleanup_requested = False
                st.success("Cleanup complete.")
                st.rerun()
        with col_cancel:
            if st.button("Cancel", type="secondary"):
                st.session_state.cleanup_requested = False
                st.rerun()

except Exception as e:
    st.error(f"Unable to connect to MongoDB: {e}")
