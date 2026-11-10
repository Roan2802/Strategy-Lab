import pandas as pd, json
from pathlib import Path
from zoneinfo import ZoneInfo
cache=Path("strategy_lab/data_cache"); tz=ZoneInfo("America/New_York")
pairs={"thanksgiving":("2024-11-28","2025-11-27"),"day_after":("2024-11-29","2025-11-28"),"mlk":("2025-01-20","2026-01-19"),"presidents":("2025-02-17","2026-02-16")}
def runs(v):
    res=[];s=p=None
    for x in sorted(v):
        if s is None: s=p=x
        elif x==p+1: p=x
        else: res.append((s,p+1)); s=p=x
    if s is not None: res.append((s,p+1))
    return [[f"{a//60:02d}:{a%60:02d}",f"{b//60:02d}:{b%60:02d}"] for a,b in res]
out={}
for n in ["XAUUSD","US30","US500","USATECHIDXUSD"]:
    d=pd.read_parquet(cache/f"{n}_M1_bidask.parquet",columns=["bid_close"])
    d=d[d.index>="2024-11-20"]; l=d.index.tz_convert(tz)
    day=l.strftime("%Y-%m-%d"); m=l.hour*60+l.minute
    df=pd.DataFrame({"day":day,"m":m})
    out[n]={k:[runs(df[df.day==a].m), runs(df[df.day==b].m)] for k,(a,b) in pairs.items()}
    print(n,{k:("SAME" if v[0]==v[1] else "DIFF",v[1]) for k,v in out[n].items()})
