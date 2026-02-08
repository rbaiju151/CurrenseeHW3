import time
from datetime import date

import requests
import streamlit as st
from dateutil.relativedelta import relativedelta


try:
    CURRENCYAPI_KEY = st.secrets["CURRENCYAPI_KEY"]
except Exception:
    try:
        from api_keys import CURRENCYAPI_KEY
    except ImportError:
        CURRENCYAPI_KEY = None


RESTCOUNTRIES_ALL = "https://restcountries.com/v3.1/all"
CURRENCYAPI_BASE = "https://api.currencyapi.com/v3"


# ----------------------------
# Helpers
# ----------------------------

def _currencyapi_headers() -> dict:
    # currencyapi supports api key via header 'apikey'
    if not CURRENCYAPI_KEY:
        raise RuntimeError(
            "Missing CURRENCYAPI_KEY. Create api_keys.py with CURRENCYAPI_KEY='your_key'."
        )
    return {"apikey": CURRENCYAPI_KEY}


def _get_json(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = 15):
    r = requests.get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=24 * 3600)
def load_countries():
    params = {"fields": "name,cca2,currencies,flags,capital,region"}
    data = _get_json(RESTCOUNTRIES_ALL, params=params)

    countries = []
    for c in data:
        name = (c.get("name") or {}).get("common")
        cca2 = c.get("cca2")
        flags = (c.get("flags") or {}).get("png") or (c.get("flags") or {}).get("svg")
        capital = (c.get("capital") or [None])[0]
        region = c.get("region")

        currencies_obj = c.get("currencies") or {}
        currency_codes = list(currencies_obj.keys())
        if not name or not cca2 or not currency_codes:
            continue

        primary_code = currency_codes[0]
        primary_name = (currencies_obj.get(primary_code) or {}).get("name") or primary_code
        primary_symbol = (currencies_obj.get(primary_code) or {}).get("symbol") or ""

        countries.append({
            "name": name,
            "cca2": cca2,
            "flag_url": flags,
            "capital": capital,
            "region": region,
            "currency_code": primary_code,
            "currency_name": primary_name,
            "currency_symbol": primary_symbol,
        })

    countries.sort(key=lambda x: x["name"])
    return countries


def _parse_currencyapi_rate(resp_json: dict, currency: str) -> float:
    data = resp_json.get("data") or {}
    node = data.get(currency)
    if not node or "value" not in node:
        raise RuntimeError(
            f"currencyapi response missing data for {currency}. "
            f"Got keys: {list(data.keys())[:10]}"
        )
    return float(node["value"])


@st.cache_data(ttl=24 * 3600)
def get_pair_rate_on_day(day: date, home: str, dest: str) -> float:
    """
    Returns: 1 unit of 'home' equals X units of 'dest'

    currencyapi endpoints:
      /latest?base_currency=HOME&currencies=DEST
      /historical?date=YYYY-MM-DD&base_currency=HOME&currencies=DEST
    """
    if home == dest:
        return 1.0

    headers = _currencyapi_headers()

    if day == date.today():
        url = f"{CURRENCYAPI_BASE}/latest"
        params = {"base_currency": home, "currencies": dest}
        j = _get_json(url, params=params, headers=headers)
        return _parse_currencyapi_rate(j, dest)

    url = f"{CURRENCYAPI_BASE}/historical"
    params = {"date": day.isoformat(), "base_currency": home, "currencies": dest}
    j = _get_json(url, params=params, headers=headers)
    return _parse_currencyapi_rate(j, dest)


def pct_change(current: float, past: float) -> float:
    return (current - past) / past * 100.0


def favorability_label(diff_vs_1y: float) -> str:
    # Simple MVP thresholds
    if diff_vs_1y >= 7.5:
        return "🟢 More favorable than ~1y ago"
    if diff_vs_1y <= -7.5:
        return "🔴 Less favorable than ~1y ago"
    return "🟡 Similar to ~1y ago"


# ----------------------------
# UI
# ----------------------------

st.set_page_config(page_title="Currensee", page_icon="💱", layout="centered")

st.title("💱 Currensee")
st.caption("A quick way to check whether a destination’s exchange rate is historically favorable.")

countries = load_countries()

home_currency = st.selectbox(
    "Home currency",
    ["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "INR"],
    index=0,
    help="The currency you earn/spend (e.g., USD).",
)

country_names = [c["name"] for c in countries]
chosen_name = st.selectbox(
    "Primary destination country",
    country_names,
    index=country_names.index("Japan") if "Japan" in country_names else 0,
)

chosen = next(c for c in countries if c["name"] == chosen_name)
dest_currency = chosen["currency_code"]

colA, colB = st.columns([1, 3], vertical_alignment="center")
with colA:
    if chosen["flag_url"]:
        st.image(chosen["flag_url"], width=96)
with colB:
    st.subheader(f"{chosen['name']} — {chosen['region'] or 'Region unknown'}")
    st.write(f"**Capital:** {chosen['capital'] or '—'}")
    sym = f" ({chosen['currency_symbol']})" if chosen["currency_symbol"] else ""
    st.write(f"**Currency:** {dest_currency} — {chosen['currency_name']}{sym}")

st.divider()

st.markdown(f"### Snapshot: **{home_currency} → {dest_currency}**")

today = date.today()
d1y = today - relativedelta(years=1)
d3y = today - relativedelta(years=3)
d5y = today - relativedelta(years=5)

fetch_single = st.button("Fetch snapshot for primary destination")

if not fetch_single:
    st.info(
        "Use **Fetch snapshot** to pull current and historical rates (today, ~1y, ~3y, ~5y). "
        "Tip: try a few destinations to see which ones are more favorable vs last year."
    )
else:
    with st.spinner("Fetching exchange rates…"):
        t0 = time.perf_counter()
        try:
            r_today = get_pair_rate_on_day(today, home_currency, dest_currency)
            r_1y = get_pair_rate_on_day(d1y, home_currency, dest_currency)
            r_3y = get_pair_rate_on_day(d3y, home_currency, dest_currency)
            r_5y = get_pair_rate_on_day(d5y, home_currency, dest_currency)
        except requests.HTTPError as e:
            if getattr(e.response, "status_code", None) == 429:
                st.error("Too many requests (429). You may be temporarily throttled — try again in a bit.")
            else:
                st.error("HTTP error from currencyapi.")
            st.exception(e)
            st.stop()
        except Exception as e:
            st.error("Couldn’t fetch rates (check API key, currency codes, or plan limits).")
            st.exception(e)
            st.stop()
        finally:
            st.session_state["single_fetch_s"] = round(time.perf_counter() - t0, 3)

    k1, k2, k3 = st.columns(3)
    k1.metric("Today", f"{r_today:,.4f}")
    k2.metric("~1 year ago", f"{r_1y:,.4f}", f"{pct_change(r_today, r_1y):+.1f}%")
    k3.metric("~3 years ago", f"{r_3y:,.4f}", f"{pct_change(r_today, r_3y):+.1f}%")
    st.metric("~5 years ago", f"{r_5y:,.4f}", f"{pct_change(r_today, r_5y):+.1f}%")

    st.subheader("Verdict")
    diff = pct_change(r_today, r_1y)
    st.write(favorability_label(diff))
    st.caption(
        "Rule of thumb: if **home→destination** is higher than before, your home currency buys more local currency. "
        "This MVP ignores inflation and local price changes."
    )

st.divider()

# ----------------------------
# Multi-country compare
# ----------------------------

st.subheader("Multi-country compare")
st.caption(
    "Compare **today vs ~1 year ago** across multiple destinations. "
    "Sorted by most favorable (largest increase in home→local rate)."
)

# Build labels like "Japan (JPY)"
name_to_country = {c["name"]: c for c in countries}
labels = [f"{c['name']} ({c['currency_code']})" for c in countries]

default_labels = []
for l in labels:
    if l.startswith("Japan ("):
        default_labels.append(l)
        break

selected_labels = st.multiselect(
    "Select destination countries to compare",
    options=labels,
    default=default_labels,
    max_selections=8,
)

fetch_compare = st.button("Compare selected countries")

if fetch_compare:
    if not selected_labels:
        st.warning("Pick at least one destination country.")
    else:
        with st.spinner("Fetching comparison snapshot…"):
            t0 = time.perf_counter()
            rows = []
            try:
                for lab in selected_labels:
                    # parse "Country (CCY)" -> "Country"
                    name = lab.rsplit(" (", 1)[0]
                    c = name_to_country.get(name)
                    if not c:
                        continue

                    dest = c["currency_code"]

                    if home_currency == dest:
                        r_today = r_1y = 1.0
                    else:
                        r_today = get_pair_rate_on_day(today, home_currency, dest)
                        r_1y = get_pair_rate_on_day(d1y, home_currency, dest)

                    change = pct_change(r_today, r_1y)

                    rows.append({
                        "Country": c["name"],
                        "Currency": dest,
                        "Today Rate": r_today,
                        "~1y Ago Rate": r_1y,
                        "% vs ~1y": change,
                        "Verdict": favorability_label(change),
                    })

            except requests.HTTPError as e:
                if getattr(e.response, "status_code", None) == 429:
                    st.error("Too many requests (429). You may be temporarily throttled — try again in a bit.")
                else:
                    st.error("HTTP error from currencyapi.")
                st.exception(e)
                st.stop()
            except Exception as e:
                st.error("Couldn’t fetch comparison rates.")
                st.exception(e)
                st.stop()
            finally:
                st.session_state["compare_fetch_s"] = round(time.perf_counter() - t0, 3)

        # Sort and display
        rows.sort(key=lambda r: r["% vs ~1y"], reverse=True)

        # Render a clean table (no per-destination columns)
        st.dataframe(
            [
                {
                    "Country": r["Country"],
                    "Currency": r["Currency"],
                    f"Today ({home_currency}→local)": f"{r['Today Rate']:,.4f}",
                    f"~1y Ago ({home_currency}→local)": f"{r['~1y Ago Rate']:,.4f}",
                    "% vs ~1y": f"{r['% vs ~1y']:+.1f}%",
                    "Verdict": r["Verdict"],
                }
                for r in rows
            ],
            use_container_width=True,
        )

        top = rows[0]
        st.success(
            f"Most favorable now (vs ~1y): **{top['Country']} ({top['Currency']})** "
            f"at **{top['% vs ~1y']:+.1f}%**"
        )
        st.caption(
            "Note: more favorable here means your home currency buys more of the local currency than it did ~1 year ago."
        )
