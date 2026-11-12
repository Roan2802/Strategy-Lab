"""Pick one eligible and one ineligible factory candidate for the shadow-route E2E (read-only, no HTTP, no shadow store).
Usage: shadow_probe.py <out.json>"""
import json, sys
from strategy_lab import factory_api, shadow_forward
from strategy_lab.research import factory

store, vault = factory_api.store(), factory_api.vault()
runs = [r.get("run_id") or r.get("id") for r in store.runs(50)]
ok = bad = None
for rid in runs:
    for cand in store.candidates(rid):
        cid = cand["cand_id"]
        detail = factory.candidate_detail(store, cid, rid, vault)
        if not detail: continue
        chk = shadow_forward.eligibility(detail)
        pick = {"run": rid, "id": cid, "name": cand.get("name") or cand.get("cand_id"), "problems": chk["problems"]}
        if chk["eligible"] and not ok and not detail.get("duplicate"): ok = pick
        if not chk["eligible"] and not bad: bad = pick
        if ok and bad: break
    if ok and bad: break
json.dump({"eligible": ok, "ineligible": bad}, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
print(json.dumps({"eligible": ok, "ineligible": bad}, ensure_ascii=False)[:800])
