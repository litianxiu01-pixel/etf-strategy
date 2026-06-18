import json,math,numpy as np

D='data/market_regime'
with open(f'{D}/combined_daily.json') as f: combined=json.load(f)
with open(f'{D}/etf_universe.json') as f: universe=json.load(f)
UC=set(universe['etfs'].keys()); TOTAL=100000; REBAL=10

def closes(code,idx,n=120):
    r=[]; e=min(idx,len(combined)-1)
    for i in range(max(0,e-n+1),e+1):
        if code in combined[i]['etfs']: r.append(combined[i]['etfs'][code]['close'])
    return r

def volumes(code,idx,n=120):
    r=[]; e=min(idx,len(combined)-1)
    for i in range(max(0,e-n+1),e+1):
        if code in combined[i]['etfs']: r.append(combined[i]['etfs'].get('volume',0)or 0)
    return r

def regime(idx):
    ars,ps=[],[]
    for c in UC:
        cl=closes(c,idx,120)
        if len(cl)<61: continue
        ars.append((cl[-1]/cl[-61]-1)*100)
        ps.append(1 if cl[-1]>np.mean(cl[-60:]) else 0)
    if not ars: return 'neutral'
    ar=np.mean(ars); p=sum(ps)/len(ps)*100
    if ar>0 and p>60: return 'bull'
    elif ar<-3 or p<30: return 'bear'
    return 'neutral'

def select(idx):
    rg=regime(idx); cands=[]
    for c in UC:
        ac=universe['etfs'][c].get('assetClass','')
        if rg=='bear' and ac in ['gold','bond']: continue
        cl=closes(c,idx,120); vl=volumes(c,idx,120)
        if len(cl)<61: continue
        if np.mean(cl[-20:])<=np.mean(cl[-60:]): continue
        if len(vl)>=22:
            av=np.mean(vl[-22:-2])
            if av>0 and vl[-1]<av*1.2: continue
        ret60=(cl[-1]/cl[-61]-1)*100
        r20=np.diff(np.array(cl[-21:]))/np.array(cl[-21:-1])
        vol20=np.std(r20)*math.sqrt(252)*100 if len(r20)>0 else 30
        cands.append({'c':c,'score':ret60-vol20*0.2,'sec':c[:6]})
    cands.sort(key=lambda x:x['score'],reverse=True)
    n={'bull':5,'neutral':3,'bear':2}[rg]
    sel=[]; used=set()
    for c in cands:
        if len(sel)>=n: break
        if c['sec'] not in used or len(used)>=3: sel.append(c); used.add(c['sec'])
    return sel,rg

S=200; E=len(combined)-1
cash=TOTAL; pos={}; eq=[]; trades=[]

for idx in range(S,E+1):
    date=combined[idx]['date']
    
    for c in list(pos.keys()):
        p=pos[c]; cl=closes(c,idx,5)
        if len(cl)<1: continue
        cp=cl[-1]; pnl=(cp/p['e']-1)*100
        if pnl<=-p['sl']:
            cash+=p['sh']*cp; trades.append({'c':c,'pnl':pnl,'t':'STOP'})
            del pos[c]; continue
        if not p.get('tp1') and pnl>=p['tp1v']:
            sh=p['sh']//2; cash+=sh*cp; p['sh']-=sh; p['tp1']=True
            trades.append({'c':c,'pnl':pnl,'t':'TP1'})
        if pnl>=p['tp2']:
            cash+=p['sh']*cp; trades.append({'c':c,'pnl':pnl,'t':'TP2'})
            del pos[c]; continue

    if (idx-S)%REBAL==0:
        sel,rg=select(idx)
        nc={s['c'] for s in sel}
        oc=set(pos.keys())
        
        for c in oc-nc:
            p=pos[c]; cl=closes(c,idx,5)
            if len(cl)<1: continue
            cp=cl[-1]; pnl=(cp/p['e']-1)*100
            cash+=p['sh']*cp; trades.append({'c':c,'pnl':pnl,'t':'OUT'})
            del pos[c]
        
        tb=nc-oc
        if tb:
            ra={'bull':0.90,'neutral':0.75,'bear':0.30}
            mp={'bull':0.20,'neutral':0.25,'bear':0.20}
            tp=len(pos)+len(tb)
            dp=TOTAL*ra.get(rg,0.5)
            pp=min(dp/max(tp,1),TOTAL*mp.get(rg,0.25))
            for s in sel:
                if s['c'] not in tb: continue
                cl=closes(s['c'],idx,5)
                if len(cl)<1: continue
                ep=cl[-1]
                cc=closes(s['c'],idx,20)
                trs=[abs(cc[i]-cc[i-1]) for i in range(1,len(cc))]
                atr_pct=(np.mean(trs[-14:])/ep*100) if len(cc)>=15 and ep>0 else 2.0
                sl_pct=max(3.0,min(10.0,atr_pct*2))
                shares=int(pp/ep/100)*100
                cost=shares*ep
                if cost<=cash and shares>0:
                    cash-=cost
                    pos[s['c']]={'e':ep,'sh':shares,'sl':sl_pct,'tp1v':sl_pct,'tp2':sl_pct*2,'tp1':False}
    
    mkt=cash+sum(p['sh']*closes(c,idx,1)[-1] for c,p in pos.items() if len(closes(c,idx,1))>0)
    eq.append(mkt)

print(f"{combined[S]['date']} => {combined[E]['date']}")
tr=(eq[-1]/TOTAL-1)*100
dr=np.diff(eq)/np.array(eq[:-1]); dr=dr[~np.isnan(dr)]
sp=np.mean(dr)/np.std(dr)*math.sqrt(252) if len(dr)>1 and np.std(dr)>0 else 0
pk=eq[0]; mdd=0
for v in eq:
    if v>pk: pk=v
    dd=(v/pk-1)*100
    if dd<mdd: mdd=dd
print(f"收益:{tr:+.1f}%  夏普:{sp:.2f}  回撤:{mdd:.1f}%")

ts={}
for t in trades: ts[t['t']]=ts.get(t['t'],0)+1
print(f"\n交易{len(trades)}笔:",end=' ')
for k,v in sorted(ts.items()): print(f"{k}:{v}",end=' ')
pnls=[t['pnl'] for t in trades]
print(f"\n平均:{np.mean(pnls):+.2f}%  胜率:{sum(1 for p in pnls if p>0)/len(pnls)*100:.0f}%")
for typ in ['STOP','TP1','TP2']:
    tt=[t for t in trades if t['t']==typ]
    if tt: print(f"[{typ}] {len(tt)}笔 均价{np.mean([t['pnl']for t in tt]):+.1f}%")
    
# Compare: no sell rules (just hold & rebalance)
cash2=TOTAL; pos2={}; eq2=[]
for idx in range(S,E+1):
    for c in list(pos2.keys()):
        p=pos2[c]; cl=closes(c,idx,5)
        if len(cl)<1: continue; cp=cl[-1]; pnl=(cp/p['e']-1)*100
        if pnl<=-8.0:
            cash2+=p['sh']*cp; del pos2[c]
    if (idx-S)%REBAL==0:
        sel,rg=select(idx); nc={s['c'] for s in sel}; oc=set(pos2.keys())
        for c in oc-nc:
            p=pos2[c]; cl=closes(c,idx,5)
            if len(cl)<1: continue; cp=cl[-1]
            cash2+=p['sh']*cp; del pos2[c]
        tb=nc-oc
        if tb:
            ra={'bull':0.90,'neutral':0.75,'bear':0.30}
            mp={'bull':0.20,'neutral':0.25,'bear':0.20}
            tp=len(pos2)+len(tb); dp=TOTAL*ra.get(rg,0.5)
            pp=min(dp/max(tp,1),TOTAL*mp.get(rg,0.25))
            for s in sel:
                if s['c'] not in tb: continue
                cl=closes(s['c'],idx,5)
                if len(cl)<1: continue; ep=cl[-1]
                shares=int(pp/ep/100)*100; cost=shares*ep
                if cost<=cash2 and shares>0:
                    cash2-=cost
                    pos2[s['c']]={'e':ep,'sh':shares}
    mkt=cash2+sum(p['sh']*closes(c,idx,1)[-1] for c,p in pos2.items() if len(closes(c,idx,1))>0)
    eq2.append(mkt)

tr2=(eq2[-1]/TOTAL-1)*100
print(f"\n对比(无卖点): 收益{tr2:+.1f}%")
print(f"差值: {tr-tr2:+.1f}个百分点")
