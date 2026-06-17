"""Grizzlies eval harness — runs the live SignalRouter against 501 validated rows.

Goals:
  1. Produce the REAL production accuracy number (not the classify_signals.py audit number)
  2. Per-tier attribution: which tier (noise / library-tier1 / library-tier2 / regex / gemini /
     confidence-block / none) produced each verdict
  3. Per-class recall & precision on canonical labels (ENTRY / EXIT / TRIM / NOISE)
  4. Miss list with reasons so we can see exactly what to fix

Gemini is STUBBED by default (returns None) so iterations are free and deterministic.
Pass --live-gemini to hit the real API (costs $$, needs GEMINI_API_KEY).

Inputs:
  /tmp/analyst_signals/grizzlies_train.jsonl  (501 rows built earlier)

Outputs (in same dir as this script):
  results.jsonl            per-row results
  report.md                human-readable summary
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Optional

# Ensure Trading/ is on path
sys.path.insert(0, os.path.expanduser("~/Desktop/Trading"))

# Silence bot logs unless --verbose
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from config import Config  # noqa: E402
from parsers.base import SignalAction  # noqa: E402


LABELED_ROWS = Path("/tmp/analyst_signals/grizzlies_train.jsonl")
OUT_DIR = Path(__file__).parent
RESULTS = OUT_DIR / "results.jsonl"
REPORT = OUT_DIR / "report.md"

# Canonical labels
CANONICAL = ["ENTRY", "EXIT", "TRIM", "NOISE"]

# ParsedSignal.action (SignalAction enum .value) → canonical label
ACTION_TO_CANONICAL = {
    SignalAction.ENTRY.value: "ENTRY",
    SignalAction.EXIT.value:  "EXIT",
    SignalAction.TRIM.value:  "TRIM",
    SignalAction.INFO.value:  "NOISE",
    # STOP_HIT treated as EXIT for accounting (close event)
    SignalAction.STOP_HIT.value: "EXIT",
}


def action_to_label(action_val: Optional[str]) -> str:
    if action_val is None:
        return "NOISE"  # Router returned None → treated as "drop / noise"
    return ACTION_TO_CANONICAL.get(action_val, "NOISE")


def build_router(stub_gemini: bool):
    """Build a SignalRouter, optionally with Gemini disabled."""
    cfg = Config()

    # Patch the Grizzlies channel id so we can route synthetic messages through it.
    # Config is frozen, so we monkey-patch the class's channel_to_analyst / watched_channels
    # via object.__setattr__. Simpler: set the env var before Config() is constructed.
    # Our approach: if the user has no channel configured, inject a synthetic id.

    from signal_router import SignalRouter
    router = SignalRouter(cfg)

    if stub_gemini:
        # Force Gemini unavailable so we see library + regex coverage only
        router.gemini_parser._available = False

    return router, cfg


def run_eval(stub_gemini: bool = True, verbose: bool = False) -> dict:
    if verbose:
        logging.getLogger().setLevel(logging.INFO)

    # Load labeled rows
    rows = [json.loads(ln) for ln in LABELED_ROWS.open(encoding="utf-8")]
    print(f"Loaded {len(rows)} labeled rows")

    router, cfg = build_router(stub_gemini=stub_gemini)

    # Figure out what channel id to route under — force grizzlies channel.
    # If not configured, inject a synthetic id and patch the config mappings.
    gc = cfg.discord_channel_grizzlies or "grizzlies_synthetic_channel"

    # Patch the config's channel_to_analyst and watched_channels to include our synthetic
    # id (frozen dataclass → use object.__setattr__ via monkey patch of property targets).
    if not cfg.discord_channel_grizzlies:
        # Monkey-patch the router's internal references.
        # SignalRouter reads self.config.channel_to_analyst and self.config.watched_channels
        # every call, so we need to shim those.
        class _ShimConfig:
            def __init__(self, real):
                self._real = real
                self.discord_channel_grizzlies = gc
                self.discord_channel_waxui = real.discord_channel_waxui
                self.discord_channel_em = real.discord_channel_em
                self.discord_channel_ecs = real.discord_channel_ecs
                self.discord_channel_eva = real.discord_channel_eva
                self.discord_channel_nando = real.discord_channel_nando
                self.discord_channel_zabes = real.discord_channel_zabes

            def __getattr__(self, name):
                return getattr(self._real, name)

            @property
            def channel_to_analyst(self):
                m = dict(self._real.channel_to_analyst)
                m[gc] = "grizzlies"
                return m

            @property
            def watched_channels(self):
                return list(self._real.watched_channels) + [gc]

        router.config = _ShimConfig(cfg)

    results = []
    tier_counts: Counter = Counter()

    for i, r in enumerate(rows):
        content = r.get("content") or ""
        embed_text = r.get("embed_text") or ""
        # If the message was primarily an embed (analyst bot posts), fold the embed text into content
        # so the router sees it. route_message accepts an `embeds` list of Discord embed dicts,
        # but our JSONL has pre-flattened embed_text. Simplest: append embed text to content
        # so the parser sees it, mirroring what the production extract would look like.
        if embed_text and embed_text not in content:
            composed = (content + "\n" + embed_text).strip()
        else:
            composed = content

        # Reply context
        referenced = r.get("reply_context") or ""
        is_reply = str(r.get("is_reply") or "").strip().upper() == "YES"
        ref_to_pass = referenced if is_reply and referenced else None

        try:
            signal = router.route_message(
                channel_id=gc,
                message_id=str(r.get("message_id") or f"synth_{i}"),
                content=composed,
                timestamp=str(r.get("timestamp") or ""),
                embeds=None,
                referenced_message=ref_to_pass,
            )
        except Exception as e:
            logging.exception("Router raised for row %d", i)
            signal = None

        predicted = action_to_label(signal.action if signal else None)
        tier = _classify_tier(signal, composed, r)
        tier_counts[tier] += 1

        rec = {
            "message_id": r["message_id"],
            "is_reply": is_reply,
            "content_preview": composed[:160].replace("\n", " ⏎ "),
            "reply_context_preview": (referenced or "")[:120].replace("\n", " ⏎ "),
            "ground_truth": r["ground_truth"],
            "predicted": predicted,
            "agreement": predicted == r["ground_truth"],
            "tier": tier,
            "signal": {
                "action": signal.action if signal else None,
                "ticker": signal.ticker if signal else None,
                "asset_type": signal.asset_type if signal else None,
                "strike": signal.strike if signal else None,
                "expiry": signal.expiry if signal else None,
                "confidence": signal.confidence if signal else None,
            } if signal else None,
        }
        results.append(rec)

        if (i + 1) % 50 == 0:
            print(f"  progress: {i+1}/{len(rows)}")

    # Write results
    with RESULTS.open("w", encoding="utf-8") as f:
        for rec in results:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Compute metrics
    metrics = compute_metrics(results, tier_counts, stub_gemini=stub_gemini)
    write_report(metrics, results)
    print_summary(metrics)

    return metrics


def _classify_tier(signal, content: str, row: dict) -> str:
    """Best-effort tier attribution.

    The router doesn't expose the tier directly, so we infer from the signal's
    shape + content. This is approximate — an exact attribution would require
    router instrumentation. For the baseline it's good enough to surface gross
    patterns.
    """
    if signal is None:
        # Could be: noise short-circuit, library NOISE, regex None + Gemini None, or conf-block
        # Heuristic: if confidence info exists on a separate signal -> already captured.
        # We distinguish NOISE short-circuit separately by re-checking is_noise.
        try:
            from parsers.grizzlies import GrizzliesParser
            if GrizzliesParser.is_noise(content):
                return "noise_short_circuit"
        except Exception:
            pass
        return "none_or_conf_block"

    # Signal exists. Infer tier from confidence range (approximate):
    #   regex → 0.88–0.92 (see parsers/grizzlies.py)
    #   library match + regex → max(0.92, regex_conf)
    #   library match + Gemini → max(0.9, gemini_conf)
    #   Tier-3 Gemini only → whatever Gemini returns
    # This is FRAGILE. TODO: instrument the router to emit tier.
    conf = signal.confidence or 0
    if conf >= 0.88:
        return "library_or_regex"
    return "gemini"


def compute_metrics(results: list[dict], tier_counts: Counter, stub_gemini: bool) -> dict:
    total = len(results)
    agree = sum(1 for r in results if r["agreement"])
    accuracy = 100 * agree / max(total, 1)

    gt_counts = Counter(r["ground_truth"] for r in results)
    pred_counts = Counter(r["predicted"] for r in results)

    per_class = {}
    for cls in CANONICAL:
        tp = sum(1 for r in results if r["ground_truth"] == cls and r["predicted"] == cls)
        fn = sum(1 for r in results if r["ground_truth"] == cls and r["predicted"] != cls)
        fp = sum(1 for r in results if r["ground_truth"] != cls and r["predicted"] == cls)
        per_class[cls] = {
            "recall":    100 * tp / max(tp + fn, 1),
            "precision": 100 * tp / max(tp + fp, 1),
            "tp": tp, "fn": fn, "fp": fp,
            "truth_total": tp + fn,
            "pred_total":  tp + fp,
        }

    confusion = Counter()
    for r in results:
        if not r["agreement"]:
            confusion[(r["predicted"], r["ground_truth"])] += 1

    # Critical to the use case: missed actionable (ENTRY/EXIT/TRIM -> NOISE)
    actionable = ["ENTRY", "EXIT", "TRIM"]
    missed_actionable = sum(
        1 for r in results if r["ground_truth"] in actionable and r["predicted"] == "NOISE"
    )
    wrong_direction = sum(
        1 for r in results
        if r["ground_truth"] in {"ENTRY", "EXIT"}
        and r["predicted"] in {"ENTRY", "EXIT"}
        and r["ground_truth"] != r["predicted"]
    )
    false_trade = sum(
        1 for r in results
        if r["ground_truth"] == "NOISE" and r["predicted"] in actionable
    )

    return {
        "stub_gemini": stub_gemini,
        "total": total,
        "accuracy": accuracy,
        "agree": agree,
        "disagree": total - agree,
        "gt_distribution": dict(gt_counts),
        "pred_distribution": dict(pred_counts),
        "per_class": per_class,
        "confusion": [{"predicted": a, "truth": b, "count": n}
                      for (a, b), n in sorted(confusion.items(), key=lambda x: -x[1])],
        "tier_counts": dict(tier_counts),
        "kpi": {
            "missed_actionable": missed_actionable,
            "wrong_direction": wrong_direction,
            "false_trade_from_noise": false_trade,
        },
    }


def write_report(metrics: dict, results: list[dict]) -> None:
    lines = []
    lines.append("# Grizzlies eval — baseline")
    lines.append("")
    lines.append(f"- Gemini stubbed: **{metrics['stub_gemini']}**")
    lines.append(f"- Rows evaluated: {metrics['total']}")
    lines.append(f"- Accuracy: **{metrics['accuracy']:.1f}%** ({metrics['agree']}/{metrics['total']})")
    lines.append("")
    lines.append("## KPIs for 'never miss a trade'")
    lines.append(f"- Missed actionable (truth=ENTRY/EXIT/TRIM, predicted=NOISE): **{metrics['kpi']['missed_actionable']}**")
    lines.append(f"- Wrong-direction closes (ENTRY↔EXIT): **{metrics['kpi']['wrong_direction']}**")
    lines.append(f"- False trades from noise (truth=NOISE, predicted=actionable): **{metrics['kpi']['false_trade_from_noise']}**")
    lines.append("")
    lines.append("## Per-class recall / precision")
    lines.append("| Class | Truth total | Pred total | TP | FN | FP | Recall | Precision |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for cls in CANONICAL:
        m = metrics["per_class"][cls]
        lines.append(
            f"| {cls} | {m['truth_total']} | {m['pred_total']} | {m['tp']} | {m['fn']} | {m['fp']} "
            f"| {m['recall']:.1f}% | {m['precision']:.1f}% |"
        )
    lines.append("")
    lines.append("## Confusion (predicted → truth)")
    lines.append("| predicted | truth | count |")
    lines.append("|---|---|---|")
    for c in metrics["confusion"]:
        lines.append(f"| {c['predicted']} | {c['truth']} | {c['count']} |")
    lines.append("")
    lines.append("## Tier attribution (approximate — based on confidence)")
    for t, n in sorted(metrics["tier_counts"].items(), key=lambda x: -x[1]):
        lines.append(f"- {t}: {n}")
    lines.append("")
    lines.append("## Miss samples — by failure mode (up to 10 each)")
    failure_modes = {
        "missed_entry":  lambda r: r["ground_truth"] == "ENTRY" and r["predicted"] != "ENTRY",
        "missed_exit":   lambda r: r["ground_truth"] == "EXIT"  and r["predicted"] != "EXIT",
        "missed_trim":   lambda r: r["ground_truth"] == "TRIM"  and r["predicted"] != "TRIM",
        "false_trade":   lambda r: r["ground_truth"] == "NOISE" and r["predicted"] != "NOISE",
        "wrong_direction": lambda r: r["ground_truth"] in {"ENTRY","EXIT"} and r["predicted"] in {"ENTRY","EXIT"} and r["ground_truth"] != r["predicted"],
    }
    for mode, pred in failure_modes.items():
        misses = [r for r in results if pred(r)]
        lines.append(f"### {mode} ({len(misses)} total)")
        for r in misses[:10]:
            lines.append(f"- **{r['ground_truth']}** → got **{r['predicted']}** (tier={r['tier']}) | "
                         f"msg={r['message_id']} | reply={r['is_reply']}")
            lines.append(f"  - content: `{r['content_preview']}`")
            if r.get("reply_context_preview"):
                lines.append(f"  - reply_to: `{r['reply_context_preview']}`")
        lines.append("")

    REPORT.write_text("\n".join(lines), encoding="utf-8")


def print_summary(metrics: dict) -> None:
    print()
    print("=" * 70)
    print(f"GRIZZLIES EVAL  (gemini_stubbed={metrics['stub_gemini']})")
    print("=" * 70)
    print(f"Accuracy: {metrics['accuracy']:.1f}% ({metrics['agree']}/{metrics['total']})")
    print()
    print("Per-class recall/precision:")
    print(f"{'':>8}  {'truth':>6} {'pred':>6} {'tp':>4} {'fn':>4} {'fp':>4} {'recall':>8} {'prec':>8}")
    for cls in CANONICAL:
        m = metrics["per_class"][cls]
        print(f"  {cls:6}: {m['truth_total']:>6} {m['pred_total']:>6} "
              f"{m['tp']:>4} {m['fn']:>4} {m['fp']:>4} "
              f"{m['recall']:>7.1f}% {m['precision']:>7.1f}%")
    print()
    print("KPIs:")
    print(f"  missed actionable (→NOISE): {metrics['kpi']['missed_actionable']}")
    print(f"  wrong-direction ENTRY↔EXIT: {metrics['kpi']['wrong_direction']}")
    print(f"  false trades (NOISE→act):    {metrics['kpi']['false_trade_from_noise']}")
    print()
    print("Tier attribution (approximate):")
    for t, n in sorted(metrics["tier_counts"].items(), key=lambda x: -x[1]):
        print(f"  {t:25} {n}")
    print()
    print(f"Detailed results: {RESULTS}")
    print(f"Report:           {REPORT}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-gemini", action="store_true",
                    help="Hit the real Gemini API (costs money). Default: stubbed.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    run_eval(stub_gemini=not args.live_gemini, verbose=args.verbose)


if __name__ == "__main__":
    main()
