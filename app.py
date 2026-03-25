import sys
import json
import pathlib
from datetime import datetime, timezone

# Make core/ importable
sys.path.insert(0, str(pathlib.Path(__file__).parent / "core"))

import streamlit as st

ROOT = pathlib.Path(__file__).parent

# ─────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="VI Calculator",
    page_icon="⚓",
    layout="wide",
)

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

@st.cache_resource
def load_profiles() -> dict:
    return json.loads((ROOT / "calculation_profiles.json").read_text(encoding="utf-8"))


def load_tariff(port_key: str) -> dict:
    filename = port_key.lower() + ".json"
    path = ROOT / "tariffs" / filename
    if not path.exists():
        raise FileNotFoundError(f"Tariff file not found: tariffs/{filename}")
    return json.loads(path.read_text(encoding="utf-8"))


VERDICT_COLORS = {
    "AUTO_APPROVED": ("#d4edda", "#155724"),   # green bg, green text
    "MISMATCH":      ("#f8d7da", "#721c24"),   # red
    "REVIEW_REQUIRED": ("#fff3cd", "#856404"), # amber
}

LINE_VERDICT_COLORS = {
    "MATCH":       ("#28a745", "white"),
    "MISMATCH":    ("#dc3545", "white"),
    "UNSUPPORTED": ("#6c757d", "white"),
    "REVIEW":      ("#fd7e14", "white"),
}

CONFIDENCE_COLORS = {
    "HIGH":   "#28a745",
    "MEDIUM": "#fd7e14",
    "LOW":    "#dc3545",
}

COMMENTS_FILE = ROOT / "officer_comments.json"


def load_comments() -> list:
    if COMMENTS_FILE.exists():
        try:
            return json.loads(COMMENTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_comments(records: list) -> None:
    COMMENTS_FILE.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def badge(label: str, bg: str, fg: str) -> str:
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 10px;'
        f'border-radius:12px;font-size:0.82em;font-weight:600;">{label}</span>'
    )


def verdict_badge(verdict: str) -> str:
    bg, fg = LINE_VERDICT_COLORS.get(verdict, ("#6c757d", "white"))
    return badge(verdict, bg, fg)


def confidence_chip(label: str) -> str:
    color = CONFIDENCE_COLORS.get(label, "#6c757d")
    return badge(label, color, "white")


def fmt_eur(val: float, currency: str = "EUR") -> str:
    return f"{currency} {val:,.2f}"


def normalise_invoice_fields(inv: dict) -> tuple[str, str, str, str]:
    """Return (invoice_reference, vendor, vessel_name, service_date) handling both field conventions."""
    invoice_reference = inv.get("invoice_reference") or inv.get("invoice_number", "")
    vendor            = inv.get("vendor") or inv.get("vendor_name", "")
    vessel_name       = inv.get("vessel_name", "")
    service_date      = inv.get("service_date") or inv.get("date_of_issue", "")
    return invoice_reference, vendor, vessel_name, service_date


def prepare_lines(inv: dict) -> tuple[list, list]:
    """Inject GT and split service / adjustment lines."""
    invoice_gt = inv.get("gross_tonnage") or inv.get("gt")
    service_lines: list = []
    adjustment_lines: list = []
    for line in inv.get("line_items", []):
        lc = dict(line)
        if invoice_gt and "gt" not in lc:
            lc["gt"] = invoice_gt
        if lc.get("is_adjustment"):
            adjustment_lines.append(lc)
        else:
            service_lines.append(lc)
    return service_lines, adjustment_lines


# ─────────────────────────────────────────────
# Load static data
# ─────────────────────────────────────────────
profiles_data = load_profiles()
all_port_keys = sorted(profiles_data["calculation_profiles"].keys())

# ─────────────────────────────────────────────
# Top-level tabs
# ─────────────────────────────────────────────
tab_verify, tab_log = st.tabs(["⚓ Verification", "📋 Officer Comments Log"])

# ══════════════════════════════════════════════
# TAB 1 — VERIFICATION
# ══════════════════════════════════════════════
with tab_verify:
    st.title("VI Calculator")
    st.caption("Upload invoice and SOF files, select a port, then run the verification engine.")

    # ── Upload boxes ──────────────────────────
    col_inv, col_sof, col_oth = st.columns(3)

    with col_inv:
        st.markdown("#### 📄 Invoice")
        invoice_file = st.file_uploader(
            "Upload Invoice JSON",
            type=["json"],
            key="invoice_upload",
            label_visibility="collapsed",
        )

    with col_sof:
        st.markdown("#### 📋 SOF")
        sof_file = st.file_uploader(
            "Upload SOF JSON",
            type=["json"],
            key="sof_upload",
            label_visibility="collapsed",
        )

    with col_oth:
        st.markdown("#### 📎 Others")
        other_files = st.file_uploader(
            "Upload other files",
            accept_multiple_files=True,
            key="other_upload",
            label_visibility="collapsed",
        )
        if other_files:
            st.markdown("**Uploaded files:**")
            for f in other_files:
                st.markdown(f"- `{f.name}`")
            st.info("Will be auto-processed in a future release.")

    st.divider()

    # ── Port selector ─────────────────────────
    selected_port = st.selectbox("Select Port", options=all_port_keys, index=None,
                                 placeholder="Choose a port…")

    # ── Run button ────────────────────────────
    can_run = invoice_file is not None and sof_file is not None and selected_port is not None
    run_clicked = st.button(
        "▶ Run Verification",
        disabled=not can_run,
        type="primary",
        use_container_width=False,
    )

    if not can_run and not run_clicked:
        missing = []
        if invoice_file is None:
            missing.append("invoice JSON")
        if sof_file is None:
            missing.append("SOF JSON")
        if selected_port is None:
            missing.append("port selection")
        if missing:
            st.caption(f"Waiting for: {', '.join(missing)}")

    # ── Engine call ───────────────────────────
    if run_clicked and can_run:
        try:
            from port_router import route  # imported late to avoid top-level failure

            invoice_dict = json.load(invoice_file)
            sof_dict     = json.load(sof_file)
            tariff_dict  = load_tariff(selected_port)

            invoice_reference, vendor, vessel_name, service_date = normalise_invoice_fields(invoice_dict)
            service_lines, adjustment_lines = prepare_lines(invoice_dict)

            with st.spinner("Running engine…"):
                result = route(
                    port=selected_port,
                    sof_data=sof_dict,
                    invoice_lines=service_lines,
                    tariff_data=tariff_dict,
                    calculation_profiles=profiles_data,
                    invoice_reference=invoice_reference,
                    vendor=vendor,
                    vessel_name=vessel_name,
                    service_date=service_date,
                    match_tolerance_pct=1.0,
                )

            st.session_state["result"]           = result
            st.session_state["adjustment_lines"] = adjustment_lines
            st.session_state["selected_port"]    = selected_port

        except FileNotFoundError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Engine error: {e}")
            raise

    # ── Results ───────────────────────────────
    if "result" in st.session_state:
        result: dict          = st.session_state["result"]
        adjustment_lines: list = st.session_state["adjustment_lines"]
        port_key: str          = st.session_state["selected_port"]

        verdict = result.get("overall_verdict", "")

        # ── 8a. Verdict banner ─────────────────
        bg, fg = VERDICT_COLORS.get(verdict, ("#e9ecef", "#212529"))
        st.markdown(
            f'<div style="background:{bg};color:{fg};padding:16px 24px;border-radius:8px;'
            f'font-size:1.4em;font-weight:700;margin:16px 0;text-align:center;">'
            f'{verdict}</div>',
            unsafe_allow_html=True,
        )

        # ── 8b. Invoice header ──────────────────
        st.subheader("Invoice Summary")
        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Vessel",    result.get("vessel_name", "—"))
        h2.metric("Port",      result.get("port", "—"))
        h3.metric("Vendor",    result.get("vendor", "—"))
        h4.metric("Invoice Ref", result.get("invoice_reference", "—"))

        h5, h6, h7, h8 = st.columns(4)
        h5.metric("Service Date",  result.get("service_date", "—"))
        h6.metric("Currency",      result.get("currency", "—"))
        h7.metric("Total Expected", fmt_eur(result.get("total_expected", 0), result.get("currency", "EUR")))
        h8.metric("Total Invoiced", fmt_eur(result.get("total_invoiced", 0), result.get("currency", "EUR")))

        variance_val = result.get("total_variance", 0)
        conf_label   = result.get("overall_confidence_label", "")
        conf_score   = result.get("overall_confidence", 0.0)

        hc1, hc2, hc3 = st.columns(3)
        hc1.metric("Total Variance", fmt_eur(variance_val, result.get("currency", "EUR")))
        hc2.metric("Confidence",     f"{conf_label} ({conf_score:.0%})")
        hc3.metric("Validated At",   result.get("validated_at", "—")[:19].replace("T", " "))

        st.divider()

        # ── 8c–8h. Line items ──────────────────
        st.subheader("Line Items")

        line_items = result.get("line_items", [])
        currency   = result.get("currency", "EUR")

        for li in line_items:
            ln        = li.get("line_number", "?")
            desc      = li.get("service_description", "")
            expected  = li.get("expected_amount", 0.0)
            invoiced  = li.get("invoiced_amount", 0.0)
            var_pct   = li.get("variance_pct", 0.0)
            var_amt   = li.get("variance", 0.0)
            lv        = li.get("verdict", "")
            cl        = li.get("confidence_label", "")
            cs        = li.get("confidence_score", 0.0)

            # Row header
            rc1, rc2, rc3, rc4, rc5 = st.columns([2, 2, 2, 1.5, 1.5])
            rc1.markdown(f"**Line {ln}** — {desc}")
            rc2.markdown(f"Expected: **{fmt_eur(expected, currency)}**")
            rc3.markdown(f"Invoiced: **{fmt_eur(invoiced, currency)}**")
            rc4.markdown(
                f"Variance: **{var_pct:+.2f}%**<br><small>({fmt_eur(var_amt, currency)})</small>",
                unsafe_allow_html=True,
            )
            rc5.markdown(verdict_badge(lv), unsafe_allow_html=True)

            # ── 8d. Audit trail expander ──────
            with st.expander(f"Line {ln} — audit trail & details"):
                ac1, ac2 = st.columns(2)
                with ac1:
                    st.markdown(f"**SOF event:** {li.get('sof_event_cited') or '—'}")
                    st.markdown(f"**Tariff rule:** {li.get('tariff_rule_cited') or '—'}")
                    st.markdown(f"**Handler used:** `{li.get('handler_used') or '—'}`")
                with ac2:
                    st.markdown(
                        f"**Confidence:** {confidence_chip(cl)} "
                        f"<small>({cs:.0%})</small>",
                        unsafe_allow_html=True,
                    )
                    if li.get("overtime_applied"):
                        st.markdown(f"**Overtime:** {li['overtime_applied']}")
                    if li.get("human_review_flag"):
                        st.warning(f"Review flag: {li.get('human_review_reason', '')}")
                    if li.get("notes"):
                        st.markdown(f"**Notes:** {li['notes']}")

                surcharges = li.get("surcharges_applied") or []
                if surcharges:
                    st.markdown("**Surcharges applied:**")
                    for s in surcharges:
                        st.markdown(
                            f"- {s.get('name')} — ×{s.get('multiplier')} "
                            f"= {fmt_eur(s.get('amount', 0), currency)} "
                            f"({s.get('citation', '')})"
                        )

                # ── 8e. Candidate explanations (per line) ──
                expls = li.get("candidate_explanations") or []
                if expls:
                    st.markdown("**Candidate explanations for variance:**")
                    for ex in expls:
                        st.markdown(
                            f"- *{ex.get('type', '')}* — {ex.get('description', '')} "
                            f"→ expected {fmt_eur(ex.get('expected_amount', 0), currency)} "
                            f"({ex.get('variance_pct', 0):+.2f}%)"
                        )

            # ── 8h. Officer comment inputs ────
            with st.container():
                oc1, oc2 = st.columns([3, 1])
                with oc1:
                    st.text_area(
                        label=f"Officer notes — Line {ln} ({desc})",
                        placeholder=(
                            "e.g. Expected rate was X — vendor applied Y. "
                            "Tariff table row used appears incorrect."
                        ),
                        key=f"comment_line_{ln}",
                        height=80,
                    )
                with oc2:
                    st.number_input(
                        "Officer expected amount (0 = no override)",
                        min_value=0.0,
                        step=0.01,
                        format="%.2f",
                        key=f"override_line_{ln}",
                    )

            st.markdown("---")

        # ── 8g. Summary notes ──────────────────
        notes = result.get("summary_notes") or []
        if notes:
            st.info("\n\n".join(notes))

        # ── 8f. Adjustment lines ───────────────
        if adjustment_lines:
            st.subheader("Informational — not tariff validated")
            st.caption("Adjustment lines (bunker surcharges, fuel adjustments, discounts) are shown here for completeness only.")
            adj_rows = []
            for a in adjustment_lines:
                adj_rows.append({
                    "Description": a.get("description", ""),
                    "Amount":      fmt_eur(a.get("amount", 0), currency),
                    "Currency":    a.get("currency", currency),
                })
            st.table(adj_rows)

        st.divider()

        # ── 8i. Save comments ──────────────────
        if st.button("💾 Save Officer Comments", key="save_comments_btn"):
            now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
            existing = load_comments()
            # Build lookup by key for upsert
            lookup: dict[str, int] = {
                r["_key"]: i for i, r in enumerate(existing) if "_key" in r
            }

            saved_count = 0
            for li in line_items:
                ln   = li.get("line_number")
                desc = li.get("service_description", "")
                comment_text = st.session_state.get(f"comment_line_{ln}", "").strip()
                override_val = st.session_state.get(f"override_line_{ln}", 0.0)

                if not comment_text and (not override_val or override_val == 0.0):
                    continue  # nothing to save for this line

                rec_key = f"{port_key}::{result.get('invoice_reference','')}::{result.get('service_date','')}::{ln}"
                record = {
                    "_key":                    rec_key,
                    "port":                    port_key,
                    "invoice_reference":       result.get("invoice_reference", ""),
                    "vessel_name":             result.get("vessel_name", ""),
                    "service_date":            result.get("service_date", ""),
                    "line_number":             ln,
                    "service_description":     desc,
                    "engine_expected":         li.get("expected_amount"),
                    "invoiced_amount":         li.get("invoiced_amount"),
                    "officer_expected_override": override_val if override_val else None,
                    "officer_comment":         comment_text,
                    "engine_verdict":          li.get("verdict", ""),
                    "variance_pct":            li.get("variance_pct"),
                    "handler_used":            li.get("handler_used", ""),
                    "saved_at":                now_utc,
                }

                if rec_key in lookup:
                    existing[lookup[rec_key]] = record
                else:
                    existing.append(record)
                saved_count += 1

            if saved_count:
                try:
                    save_comments(existing)
                    st.success(f"Saved {saved_count} comment(s) to officer_comments.json")
                except Exception as e:
                    st.error(f"Failed to save comments: {e}")
            else:
                st.info("No comments or overrides entered — nothing to save.")

        st.divider()

        # ── 8k. Approve / Escalate ─────────────
        st.subheader("Officer Action")
        ba1, ba2 = st.columns(2)
        if ba1.button("✓ Approve Invoice", use_container_width=True, key="approve_btn"):
            st.success("Invoice approved by officer.")
        if ba2.button("⚑ Escalate for Review", use_container_width=True, key="escalate_btn"):
            st.warning("Invoice escalated for human review.")


# ══════════════════════════════════════════════
# TAB 2 — OFFICER COMMENTS LOG
# ══════════════════════════════════════════════
with tab_log:
    st.title("Officer Comments Log")
    st.caption("All saved officer annotations, filterable by port. Export as JSON for tariff calibration.")

    comments = load_comments()

    if not comments:
        st.info("No comments recorded yet. Run a verification and save officer notes to populate this log.")
    else:
        # Port filter
        ports_in_log = sorted({r.get("port", "") for r in comments if r.get("port")})
        filter_options = ["All"] + ports_in_log
        port_filter = st.selectbox("Filter by port", options=filter_options, key="log_port_filter")

        filtered = comments if port_filter == "All" else [r for r in comments if r.get("port") == port_filter]

        st.caption(f"Showing {len(filtered)} of {len(comments)} record(s).")

        # Build display rows
        display_rows = []
        for r in filtered:
            display_rows.append({
                "Saved At":          r.get("saved_at", "")[:19].replace("T", " "),
                "Port":              r.get("port", ""),
                "Invoice Ref":       r.get("invoice_reference", ""),
                "Vessel":            r.get("vessel_name", ""),
                "Date":              r.get("service_date", ""),
                "Line":              r.get("line_number", ""),
                "Description":       r.get("service_description", ""),
                "Engine Expected":   r.get("engine_expected"),
                "Invoiced":          r.get("invoiced_amount"),
                "Officer Override":  r.get("officer_expected_override"),
                "Verdict":           r.get("engine_verdict", ""),
                "Variance %":        r.get("variance_pct"),
                "Handler":           r.get("handler_used", ""),
                "Comment":           r.get("officer_comment", ""),
            })

        st.dataframe(
            display_rows,
            use_container_width=True,
            hide_index=True,
        )

        # Download
        export_json = json.dumps(
            [{k: v for k, v in r.items() if k != "_key"} for r in filtered],
            indent=2,
            ensure_ascii=False,
        )
        st.download_button(
            label="⬇ Download filtered records as JSON",
            data=export_json,
            file_name=f"officer_comments_{port_filter.lower()}.json",
            mime="application/json",
            key="download_comments_btn",
        )
