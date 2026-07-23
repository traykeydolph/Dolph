#!/usr/bin/env python3
"""Parse-ability analysis for candidate analysts (reconstruction of the tool
that produced ANALYST_RANKING.md, July 13 2026).

Method: run each raw corpus through DETERMINISTIC tiers only (NO LLM):
  1. Noise short-circuit  — role-mention-only, image-only, empty.
  2. Structured extraction — regex that pulls the full trade instrument a real
     per-analyst parser would need (action + ticker [+ strike/expiry/price]).
  3. Everything else that still contains trade tokens = AMBIGUOUS (would fall to
     Gemini today) — this is the "eyeball-clean != machine-parseable" bucket.

Buckets per message:
  ENTRY_STRUCT / EXIT_STRUCT / TRIM_STRUCT  -> deterministically resolved signal
  AMBIG_ACTIONABLE                          -> trade-related but not machine-clean
  NOISE                                     -> no trade content (safely skippable)

Reported metrics (per analyst):
  deterministic %      = (NOISE + *_STRUCT) / total          -> resolved w/o LLM
  actionable signals/wk= all real signals (struct + ambig) / weeks spanned
  noise rate           = NOISE / total
  ambiguity rate       = AMBIG_ACTIONABLE / total
  entry fidelity       = ENTRY_STRUCT / all entries          -> can we open cleanly?

For analysts WITH a real parser (waxui) we ALSO run the actual bot parser
(is_noise + extract_details) on the fresh pull and report its coverage.

Run: ./venv/bin/python analyze_parseability.py waxui ace luigi
"""

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HIST = Path(__file__).parent / "data" / f"history_{datetime.today():%Y%m%d}"

ROLE_MENTION = re.compile(r"<@&?\d+>")
CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")
URL = re.compile(r"https?://\S+")
CDN = re.compile(r"https://cdn\.discordapp\.com/")

# --- Structured instrument regexes (analyst-agnostic) --------------------
# Full option spec: [verb] $TICKER [M/DD[/YY]] $STRIKE c/p ... @ $PRICE
OPT_ENTRY = re.compile(
    r"\b(?:BTO|BOT)\b\s+\$?[A-Za-z]{1,5}\b"          # buy verb + ticker
    r"(?:\s+\d{1,2}/\d{1,2}(?:/\d{2,4})?)?"           # optional expiry
    r"\s+\$?\d+(?:\.\d+)?\s*[cCpP]\b"                 # strike + c/p
    r".*?@\s*\$?\d+(?:\.\d+)?",                       # entry price
    re.IGNORECASE | re.DOTALL,
)
# Waxui entry form: "<TICKER> here <M/DD> <STRIKE>C/P ... Avg[.,] <PRICE>"
WAXUI_ENTRY = re.compile(
    r"\b[A-Z]{2,5}\s+here\b.*?\d{1,2}/\d{1,2}.*?\d+(?:\.\d+)?[CP]\b.*?Avg",
    re.IGNORECASE | re.DOTALL,
)
# "Added to TICKER @ price" avg-down entry
ADD_ENTRY = re.compile(r"\bAdded\s+to\s+\$?[A-Z]{1,5}\s+@\s*\$?\d", re.IGNORECASE)
# Share entry: BTO $TICKER @ price (no strike/expiry)
SHARE_ENTRY = re.compile(r"\b(?:BTO|BOT)\b\s+\$?[A-Za-z]{1,5}\s+@\s*\$?\d", re.IGNORECASE)

# Full option spec on a sell: STC $TICKER [date] $STRIKE c/p @ $price
OPT_STC = re.compile(
    r"\bSTC\b\s+\$?[A-Za-z]{1,5}\b"
    r"(?:\s+\d{1,2}/\d{1,2}(?:/\d{2,4})?)?"
    r"\s+\$?\d+(?:\.\d+)?\s*[cCpP]\b"
    r".*?@\s*\$?\d+(?:\.\d+)?",
    re.IGNORECASE | re.DOTALL,
)
# Waxui structured trim/exit line: "1.70 - 2.10 ✅ 24%"
WAXUI_TRIMLINE = re.compile(r"\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?\s*✅\s*\d+%")

# --- Action keyword sets (for the ambiguous/prose actionable bucket) -----
ENTRY_KW = re.compile(
    r"\b(?:BTO|BOT|buying|bought|entered|entry|adding|added|starter|loading)\b",
    re.IGNORECASE,
)
TRIM_KW = re.compile(
    r"\b(?:trim|trimmed|trimming|selling\s+(?:1/2|half|some|1/4|1/3)|"
    r"took?\s+profit|taking\s+profit|scal\w+\s+out|holding\s+(?:most|majority|half|runners?|1/2|last))\b",
    re.IGNORECASE,
)
EXIT_KW = re.compile(
    r"\b(?:STC|closed|closing|stopped\s+out|all\s+out|exit(?:ing|ed)?|out\s+on|"
    r"sold|cut|dumped|flat)\b",
    re.IGNORECASE,
)
# Any option-contract mention (ticker + strike + c/p), regardless of verb
OPT_MENTION = re.compile(r"\$?[A-Za-z]{1,5}\s+(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?\s+)?\$?\d+(?:\.\d+)?\s*[cCp]\b")
PCT_CHECK = re.compile(r"\d+%|✅|@\s*\$?\d")   # profit/price markers -> likely actionable


def clean(text: str) -> str:
    t = ROLE_MENTION.sub(" ", text)
    t = CUSTOM_EMOJI.sub(" ", t)
    return t.strip()


def is_noise(raw: str, cleaned: str) -> bool:
    if not cleaned:
        return True
    # image-only / link-only message
    stripped = URL.sub("", cleaned).strip()
    if not stripped and URL.search(cleaned):
        return True
    if CDN.match(cleaned) and "\n" not in cleaned:
        return True
    return False


def classify(text: str) -> str:
    cleaned = clean(text)
    if is_noise(text, cleaned):
        return "NOISE"

    # --- Tier: structured, fully machine-extractable signals ---
    if OPT_ENTRY.search(cleaned) or WAXUI_ENTRY.search(cleaned) or ADD_ENTRY.search(cleaned):
        return "ENTRY_STRUCT"
    if SHARE_ENTRY.search(cleaned):
        return "ENTRY_STRUCT"
    if OPT_STC.search(cleaned):
        # partial => trim, else exit
        if TRIM_KW.search(cleaned) or re.search(r"\b(1/2|half|holding)\b", cleaned, re.I):
            return "TRIM_STRUCT"
        return "EXIT_STRUCT"
    if WAXUI_TRIMLINE.search(cleaned):
        return "TRIM_STRUCT" if re.search(r"holding", cleaned, re.I) else "EXIT_STRUCT"

    # --- Tier: prose actionable (trade-related but not machine-clean) ---
    has_action = bool(ENTRY_KW.search(cleaned) or TRIM_KW.search(cleaned) or EXIT_KW.search(cleaned))
    has_instrument = bool(OPT_MENTION.search(cleaned) or re.search(r"\$[A-Z]{1,5}\b", cleaned))
    has_marker = bool(PCT_CHECK.search(cleaned))
    if has_action and (has_instrument or has_marker):
        return "AMBIG_ACTIONABLE"

    return "NOISE"


def analyze(name: str) -> dict:
    msgs = json.load(open(HIST / f"{name}.json"))
    buckets = Counter()
    span_days = None
    if msgs:
        t0 = datetime.fromisoformat(msgs[0]["timestamp"].replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(msgs[-1]["timestamp"].replace("Z", "+00:00"))
        span_days = max((t1 - t0).days, 1)

    samples = {b: [] for b in ("ENTRY_STRUCT", "EXIT_STRUCT", "TRIM_STRUCT", "AMBIG_ACTIONABLE")}
    for m in msgs:
        text = m.get("content") or ""
        for e in (m.get("embeds") or []):
            if e.get("title"):
                text += "\n" + e["title"]
            if e.get("description"):
                text += "\n" + e["description"]
            for f in (e.get("fields") or []):
                text += f"\n{f.get('name','')}: {f.get('value','')}"
        b = classify(text)
        buckets[b] += 1
        if b in samples and len(samples[b]) < 6:
            samples[b].append(clean(text)[:140].replace("\n", " / "))

    total = len(msgs)
    struct = buckets["ENTRY_STRUCT"] + buckets["EXIT_STRUCT"] + buckets["TRIM_STRUCT"]
    ambig = buckets["AMBIG_ACTIONABLE"]
    noise = buckets["NOISE"]
    resolved = struct + noise
    signals = struct + ambig
    weeks = span_days / 7 if span_days else 1
    entries = buckets["ENTRY_STRUCT"]

    return {
        "analyst": name,
        "total": total,
        "span_days": span_days,
        "buckets": dict(buckets),
        "deterministic_pct": round(100 * resolved / total, 1) if total else 0,
        "signals_per_wk": round(signals / weeks, 1) if weeks else 0,
        "noise_rate_pct": round(100 * noise / total, 1) if total else 0,
        "ambiguity_rate_pct": round(100 * ambig / total, 1) if total else 0,
        "struct_signals": struct,
        "ambig_signals": ambig,
        "entry_struct": entries,
        "signal_struct_fidelity_pct": round(100 * struct / signals, 1) if signals else 0,
        "samples": samples,
    }


def run_real_waxui_parser(name: str = "waxui") -> dict:
    """Run the ACTUAL bot WaxuiParser (is_noise + extract_details) on fresh pull."""
    sys.path.insert(0, str(Path(__file__).parent))
    from parsers.waxui import WaxuiParser

    msgs = json.load(open(HIST / f"{name}.json"))
    noise = extracted = fell_through = 0
    actions = Counter()
    for m in msgs:
        text = clean(m.get("content") or "")
        if not text:
            noise += 1
            continue
        if WaxuiParser.is_noise(text):
            noise += 1
            continue
        sig = WaxuiParser.extract_details(text, m.get("id", ""), m.get("timestamp", ""))
        if sig:
            extracted += 1
            actions[sig.action] += 1
        else:
            fell_through += 1
    total = len(msgs)
    resolved = noise + extracted
    return {
        "total": total,
        "noise_shortcircuit": noise,
        "regex_extracted": extracted,
        "fell_through_to_gemini": fell_through,
        "real_parser_deterministic_pct": round(100 * resolved / total, 1) if total else 0,
        "extracted_actions": dict(actions),
    }


def main() -> None:
    names = sys.argv[1:] or ["waxui", "ace", "luigi"]
    out = {}
    for n in names:
        res = analyze(n)
        out[n] = res
        print(f"\n{'='*66}\n{n.upper()}  ({res['total']} msgs, {res['span_days']}d span)")
        print(f"  deterministic parse %: {res['deterministic_pct']}")
        print(f"  actionable signals/wk: {res['signals_per_wk']}  "
              f"(struct={res['struct_signals']}, ambig={res['ambig_signals']})")
        print(f"  noise rate: {res['noise_rate_pct']}%   ambiguity rate: {res['ambiguity_rate_pct']}%")
        print(f"  entry(struct)={res['entry_struct']}  "
              f"signal struct fidelity: {res['signal_struct_fidelity_pct']}%")
        print(f"  buckets: {res['buckets']}")
        for b, ss in res["samples"].items():
            if ss:
                print(f"    [{b}] e.g.:")
                for s in ss[:3]:
                    print(f"        {s}")

    if "waxui" in names:
        rp = run_real_waxui_parser("waxui")
        out["waxui_real_parser"] = rp
        print(f"\n{'='*66}\nWAXUI — REAL bot parser on fresh pull:")
        print(f"  {rp}")

    outfile = Path("/private/tmp/claude-501/-Users-tray-Desktop-Trading/"
                   "f7bbf60a-d0cf-4f79-bb18-b7984786dc33/scratchpad/parseability_results.json")
    outfile.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {outfile}")


if __name__ == "__main__":
    main()
