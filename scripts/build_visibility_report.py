#!/usr/bin/env python3
"""Combine reviewed benchmark evidence with an authored, evidence-backed narrative.

No provider calls or invented metrics. Copies only allowlisted fields from raw
captures; never publishes account labels, browser identifiers, screenshots or auth.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.visibility.service import validate

LABELS = {"chatgpt":"ChatGPT", "gemini":"Gemini", "claude":"Claude", "muse":"Meta Muse"}


def fraction(n, d):
    return {"n": n if d else None, "d": d}


def build(narrative, directories):
    report = dict(narrative)
    provider_notes = report.pop("provider_notes")
    report["providers"], report["samples"] = [], []
    for provider, label in LABELS.items():
        folder = Path(directories[provider]).resolve()
        run = json.loads((folder / "run.json").read_text())
        score = json.loads((folder / "scorecard.json").read_text())
        rows = [r for r in run["rows"] if r["platform"] == provider]
        excluded = {tuple(r["key"]) for r in score["excluded"]}
        scored = [r for r in rows if r["status"] == "completed" and (provider, r["promptId"], r["sample"]) not in excluded]
        prompts = {r["id"]:r for r in run["protocol"]["prompts"]}
        ranked = [r for r in scored if prompts[r["promptId"]]["rankedRecommendations"]]
        legacy = score.get("scoreSchemaVersion",1) < 2
        citation_rows = scored if legacy else [r for r in scored if r.get("citationCaptureComplete") is True]
        facts = [f for r in scored for f in r["annotations"]["facts"]]
        states = Counter()
        for r in rows:
            status = r["status"]
            if status == "pending":
                raise ValueError("Resolve every pending row as completed, blocked or error before publishing.")
            if status == "completed" and r not in scored:
                status = "excluded"
            states[status] += 1
            answer = ""
            if r.get("answerFile"):
                path = (folder / r["answerFile"]).resolve()
                if folder not in path.parents:
                    raise ValueError("Unsafe raw evidence path")
                captured = json.loads(path.read_text())
                answer = captured.get("answer", "")
            annotations = r.get("annotations") or {}
            notes = "\n".join(x for x in [r.get("reason"), annotations.get("notes"), *[f"{f['verdict'].upper()}: {f['claim']} — {f.get('source','Source not recorded')}" for f in annotations.get("facts", [])]] if x)
            report["samples"].append(dict(provider=provider,prompt_id=r["promptId"],trial=r["sample"],status=status,prompt=r["prompt"],answer=answer,notes=notes,model=r.get("model") or "",mode=r.get("mode") or "",started_at=r.get("startedAt") or "",completed_at=r.get("completedAt") or "",search_evidence=r.get("searchEvidence") or "",citation_complete=r.get("citationCaptureComplete"),citations=r.get("citations") or []))
        p = dict(id=provider,label=label,observed_date=folder.name,model="; ".join(sorted({r["model"] for r in scored})) or "Awaiting access",mode="; ".join(sorted({r["mode"] for r in scored})) or "Not run",cohort=f"{score['protocolVersion']} / {score['protocolSha256']} / score schema {score.get('scoreSchemaVersion',1)}",notes=provider_notes[provider],citation_basis="legacy" if legacy and scored else "complete_only" if scored else "unavailable",planned=len(rows),scored=len(scored),blocked=states["blocked"],errors=states["error"],excluded=states["excluded"],mentions=fraction(sum(r["annotations"]["mentioned"] for r in scored),len(scored)),top_three=fraction(sum(r["annotations"]["recommendedRank"] is not None and r["annotations"]["recommendedRank"] <= 3 for r in ranked),len(ranked)),citations=fraction(sum(any(urlsplit(u).hostname in {"enchantedhavens.com","www.enchantedhavens.com"} for u in r["citations"]) for r in citation_rows),len(citation_rows)),facts=fraction(sum(f["verdict"]=="correct" for f in facts),len(facts)),incorrect_facts=sum(f["verdict"]=="incorrect" for f in facts),unverified_facts=sum(f["verdict"]=="unverified" for f in facts),retired_errors=sum(r["annotations"]["retiredPropertyError"] for r in scored))
        if p["scored"] != score["platforms"][provider]["scored"]:
            raise ValueError("Captured evidence differs from the reviewed scorecard")
        report["providers"].append(p)
    validate(report)
    return report


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--narrative",required=True)
    parser.add_argument("--run",required=True,help="Default dated run directory for every provider")
    parser.add_argument("--provider-run",action="append",default=[],help="provider=/absolute/directory, for an explicitly labeled combined baseline")
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    directories={p:args.run for p in LABELS}
    for value in args.provider_run:
        provider,path=value.split("=",1)
        if provider not in LABELS:parser.error("Unknown provider")
        directories[provider]=path
    report=build(json.loads(Path(args.narrative).read_text()),directories)
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"saved":args.output,"samples":len(report["samples"]),"scored":sum(p["scored"] for p in report["providers"])}))
