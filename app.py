import streamlit as st
import pymongo
import certifi
import pandas as pd
import Constants

st.set_page_config(page_title="Live Dashboard", page_icon="📊", layout="wide")
st.title("Live Dashboard")

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
                    first_row_overall = int(derived_df["overall_sentiment"].iloc[0]) if not derived_df.empty else 0

                    strike_cols_display = [str(s) for s in strike_cols]

                    def apply_styles(row):
                        styles = []
                        for col in derived_df.columns:
                            value = row[col]
                            if col == "time_label":
                                styles.append("background-color: #f3f4f6; color: #111; font-weight: 600")
                                continue
                            if col == "overall_sentiment":
                                prev_value = None
                                if row.name > 0:
                                    prev_value = derived_df.iloc[row.name - 1]["overall_sentiment"]
                                if prev_value is None or value >= prev_value:
                                    styles.append("background-color: #e8f5e9; color: green; font-weight: bold")
                                else:
                                    styles.append("background-color: #ffebee; color: red; font-weight: bold")
                                continue
                            if col == "sentiment":
                                styles.append("background-color: #eef2ff; color: #1f2937; font-weight: 600")
                                continue

                            if value in [None, "", "0", "0.0", "0.00", "NaN", "nan"]:
                                styles.append("")
                                continue

                            nature = cell_nature.get((row["time_label"], float(col)), "")
                            if nature == "RESISTANCE":
                                styles.append("background-color: #ffebee; color: red; font-weight: bold")
                            elif nature == "SUPPORT":
                                styles.append("background-color: #e8f5e9; color: green; font-weight: bold")
                            else:
                                styles.append("")
                        return styles

                    styled_derived = derived_df.style.apply(apply_styles, axis=1)
                    st.subheader("Derived strength matrix")
                    st.dataframe(styled_derived, use_container_width=True, height=700, hide_index=True)

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
                    st.dataframe(styled_tracker, use_container_width=True, height=420, hide_index=True)
                    st.markdown(f"<span style='font-size: 18px; font-weight: bold; color: #111;'>Overall Sentiment : {overall_sentiment_total}</span>", unsafe_allow_html=True)

    st.subheader("Raw MongoDB data")
    st.dataframe(raw_df, use_container_width=True, height=420, hide_index=True)

except Exception as e:
    st.error(f"Unable to connect to MongoDB: {e}")
