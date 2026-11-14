"""Summarise exa_agent_compare output: coverage, precision of returned URLs, cost, latency, agent quote honesty."""
import json, statistics as st, sys
R = json.load(open(sys.argv[1]))
ARMS = [a for a in ["current", "current_sec", "agent_low", "agent_medium"] if a in R[0]["arms"]]
def cats(r, arm): return [j["cat"] for j in r["arms"][arm]["judged"]]
def found(r, arm, kinds): return any(c in kinds for c in cats(r, arm))
n = len(R)
print(f"{n} claims from {len({r['domain'] for r in R})} different outlets\n")
print(f"{'':14} {'orig found':>11} {'any found':>10} {'urls/claim':>11} {'verified urls':>14} {'unreadable':>11} {'cost/claim':>11} {'secs/claim':>11}")
rows = {}
for a in ARMS:
    o = sum(found(r, a, {"original"}) for r in R); anyf = sum(found(r, a, {"original", "corroborating"}) for r in R)
    allc = [c for r in R for c in cats(r, a)]
    cost = [r["arms"][a].get("cost", 0) or 0 for r in R]
    secs = [(r["arms"][a].get("secs") or r["arms"][a].get("secs_search") or 0) + r["arms"][a].get("secs_verify", 0) for r in R]
    ver = sum(c in ("original", "corroborating") for c in allc); unr = sum(c == "unreadable" for c in allc)
    rows[a] = (o, anyf)
    print(f"{a:14} {o:>5}/{n} {o/n:>4.0%} {anyf:>4}/{n} {anyf/n:>4.0%} {len(allc)/n:>11.1f} {ver:>6}/{len(allc)} {ver/max(len(allc),1):>5.0%} {unr/max(len(allc),1):>10.0%} {'$%.3f' % (sum(cost)/n):>11} {st.median(secs):>8.0f}s med")
print("\ncost: current = Exa Search responses' costDollars only (planner + verify LLM calls not included, same for all arms)")

# overlap
def s(r, a): return found(r, a, {"original", "corroborating"})
print("\nWHO FINDS WHAT (any verified evidence):")
import itertools
cur_only = [r for r in R if s(r, "current") and not s(r, "agent_medium")]
med_only = [r for r in R if s(r, "agent_medium") and not s(r, "current")]
both = [r for r in R if s(r, "current") and s(r, "agent_medium")]
none = [r for r in R if not s(r, "current") and not s(r, "agent_medium")]
union = [r for r in R if s(r, "current") or s(r, "agent_medium") or s(r, "agent_low")]
print(f"  current vs agent_medium: both {len(both)} | only current {len(cur_only)} | only agent {len(med_only)} | neither {len(none)} | union of all three {len(union)}/{n}")
low_only = [r for r in R if s(r, "agent_low") and not s(r, "current")]
print(f"  agent_low finds what current misses: {len(low_only)} claims; current finds what agent_low misses: {len([r for r in R if s(r,'current') and not s(r,'agent_low')])}")

# agent honesty
for a in ("agent_low", "agent_medium"):
    cl = [c for r in R for c in r["arms"][a].get("claimed", [])]
    withq = [c for c in cl if c.get("quote")]
    onpage = [c for c in withq if c.get("quote_on_page") is not None]
    good = [c for c in onpage if c["quote_on_page"] >= 90]
    kinds = {"original": 0, "corroborating": 0}
    for c in cl: kinds[c.get("kind")] = kinds.get(c.get("kind"), 0) + 1
    errs = sum(1 for r in R if r["arms"][a].get("error"))
    fnd = sum(1 for r in R if r["arms"][a].get("found"))
    print(f"\n{a}: agent said found=true for {fnd}/{n} claims, returned {len(cl)} sources ({kinds}); errors {errs}; searches/run mean {st.mean([r['arms'][a].get('searches') or 0 for r in R]):.1f}")
    print(f"   quotes handed over: {len(withq)}; checkable on the page: {len(onpage)}; verbatim on the page (fuzzy>=90): {len(good)} ({len(good)/max(len(onpage),1):.0%}); agent-claimed 'original' that we verified as original: "
          f"{sum(1 for r in R for c in r['arms'][a].get('claimed',[]) if c.get('kind')=='original' and any(j['url']==c['url'] and j['cat']=='original' for j in r['arms'][a]['judged']))}/{sum(1 for c in cl if c.get('kind')=='original')}")
