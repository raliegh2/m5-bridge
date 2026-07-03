from pathlib import Path
import pandas as pd, numpy as np, json, zipfile, warnings, gc
from numba import njit
warnings.filterwarnings('ignore')

DATA=Path('research/data')
OUT=Path('research/v13_m5_m1_fast_out')
OUT.mkdir(parents=True, exist_ok=True)
START=pd.Timestamp('2016-07-03')
TRAIN_END=pd.Timestamp('2021-12-31 23:59:59')
CONF_END=pd.Timestamp('2022-12-31 23:59:59')
END=pd.Timestamp('2026-07-03 23:59:59')
START_BAL=5000.0
RISK_PCT=0.002

SYMBOLS={
    'GBPUSD':('GBPUSD_M1_201601040000_202607031748(1).csv','GBPUSD_M5_201601040000_202607031745(1).csv',0.0001),
    'GBPJPY':('GBPJPY_M1_201601040000_202607031748(1).csv','GBPJPY_M5_201601040000_202607031745(1).csv',0.01),
    'EURUSD':('EURUSD_M1_201601040000_202607031745(1).csv','EURUSD_M5_201601040000_202607031745(1).csv',0.0001),
}


def read(path):
    df=pd.read_csv(path,sep='\t',usecols=['<DATE>','<TIME>','<OPEN>','<HIGH>','<LOW>','<CLOSE>','<TICKVOL>','<SPREAD>'])
    df.columns=['date','clock','open','high','low','close','tickvol','spread']
    df['time']=pd.to_datetime(df.date+' '+df.clock,format='%Y.%m.%d %H:%M:%S')
    return df.drop(columns=['date','clock']).sort_values('time').reset_index(drop=True)

def ema(s,n): return s.ewm(span=n,adjust=False).mean()

def atr(df,n=14):
    pc=df.close.shift(1)
    tr=pd.concat([(df.high-df.low).abs(),(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False).mean()

def rsi(close,n=14):
    d=close.diff()
    up=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean()
    dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean()
    return 100-100/(1+up/(dn+1e-12))

def adx(df,n=14):
    h,l,c=df.high,df.low,df.close
    up=h.diff(); dn=-l.diff()
    plus_dm=np.where((up>dn)&(up>0),up,0.0)
    minus_dm=np.where((dn>up)&(dn>0),dn,0.0)
    pc=c.shift(1)
    tr=pd.concat([(h-l).abs(),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    av=tr.ewm(alpha=1/n,adjust=False).mean()
    plus=100*pd.Series(plus_dm,index=df.index).ewm(alpha=1/n,adjust=False).mean()/(av+1e-12)
    minus=100*pd.Series(minus_dm,index=df.index).ewm(alpha=1/n,adjust=False).mean()/(av+1e-12)
    dx=100*(plus-minus).abs()/(plus+minus+1e-12)
    return dx.ewm(alpha=1/n,adjust=False).mean()

def macdh(c):
    m=ema(c,12)-ema(c,26)
    return m-ema(m,9)

@njit
def sim(open_,high,low,close,atrp,spreadp,tmin,idxs,dirs,sl_mult,rr,hold,risk,start_bal):
    bal=start_bal; peak=bal; maxdd=0.; gw=0.; gl=0.; wins=0; losses=0; n=len(open_); last=-1
    R=np.empty(len(idxs)); P=np.empty(len(idxs)); Ei=np.empty(len(idxs),np.int64); Xi=np.empty(len(idxs),np.int64); D=np.empty(len(idxs),np.int64); c=0
    for kk in range(len(idxs)):
        i=idxs[kk]
        if i<=last or i>=n-2: continue
        d=dirs[kk]; ent_i=i+1; ent=open_[ent_i]
        stop=atrp[i]*sl_mult
        if stop<3.0: stop=3.0
        if spreadp[i]>min(3.5,0.18*stop): continue
        sl=ent-d*stop; tp=ent+d*stop*rr; end=ent_i+hold
        if end>=n: end=n-1
        exit_i=end; r=0.; hit=False
        for j in range(ent_i,end+1):
            if tmin[j]>=20*60:
                exit_i=j; r=d*(close[j]-ent)/stop; hit=True; break
            if d==1:
                if low[j]<=sl: exit_i=j; r=-1.; hit=True; break
                if high[j]>=tp: exit_i=j; r=rr; hit=True; break
            else:
                if high[j]>=sl: exit_i=j; r=-1.; hit=True; break
                if low[j]<=tp: exit_i=j; r=rr; hit=True; break
        if not hit:
            exit_i=end; r=d*(close[exit_i]-ent)/stop
        r -= spreadp[i]/stop
        pnl=bal*risk*r; bal+=pnl
        if bal>peak: peak=bal
        dd=(peak-bal)/peak*100.; maxdd=max(maxdd,dd)
        if pnl>0: wins+=1; gw+=pnl
        else: losses+=1; gl+=-pnl
        R[c]=r; P[c]=pnl; Ei[c]=ent_i; Xi[c]=exit_i; D[c]=d; c+=1; last=exit_i
    pf=gw/gl if gl>0 else (999. if gw>0 else 0.)
    return bal,wins,losses,pf,maxdd,R[:c],P[:c],Ei[:c],Xi[:c],D[:c]

def prep(sym,m1file,m5file,pip):
    m1=read(DATA/m1file); m5=read(DATA/m5file)
    freq=m1.time.diff().dropna().dt.total_seconds().median()/60
    m1=m1[(m1.time>=START)&(m1.time<=END)].reset_index(drop=True)
    m5=m5[(m5.time>=START-pd.Timedelta(days=30))&(m5.time<=END)].reset_index(drop=True)
    m1['ema9']=ema(m1.close,9); m1['ema20']=ema(m1.close,20); m1['atrp']=atr(m1,14)/pip; m1['rsi']=rsi(m1.close,14); m1['macdh']=macdh(m1.close)
    m1['hi30']=m1.high.shift(1).rolling(30).max(); m1['lo30']=m1.low.shift(1).rolling(30).min(); m1['hi60']=m1.high.shift(1).rolling(60).max(); m1['lo60']=m1.low.shift(1).rolling(60).min()
    m1['bodyp']=(m1.close-m1.open).abs()/pip; m1['spreadp']=m1.spread/10.0; m1['feat_time']=m1.time.dt.floor('5min')-pd.Timedelta(minutes=5)
    for n in [20,50,100,200]: m5[f'ema{n}']=ema(m5.close,n)
    m5['adx']=adx(m5,14); m5['atr5p']=atr(m5,14)/pip; m5['macdh5']=macdh(m5.close); m5['slope50']=(m5.ema50-m5.ema50.shift(12))/(12*pip)
    feats=m5[['time','ema20','ema50','ema100','ema200','adx','atr5p','macdh5','slope50']].copy()
    feats.columns=['feat_time','m5ema20','m5ema50','m5ema100','m5ema200','m5adx','m5atrp','m5macdh','m5slope50']
    df=m1.merge(feats,on='feat_time',how='left')
    df['hour']=df.time.dt.hour.astype(np.int16); df['minute']=df.time.dt.minute.astype(np.int16); df['dow']=df.time.dt.dayofweek.astype(np.int16)
    return df, {'freq':freq,'rows':len(df),'start':str(df.time.iloc[0]),'end':str(df.time.iloc[-1])}

def sigs(df,strat,p):
    sess=((df.hour>=7)&(df.hour<=17)) if p['sess']=='lny' else ((df.hour>=7)&(df.hour<=11) if p['sess']=='london' else ((df.hour>=12)&(df.hour<=17)))
    sess=sess & (df.dow<5) & (df.hour<20)
    up=((df.m5ema50>df.m5ema200)&(df.m5slope50>0)&(df.m5adx>=p['adx'])) if p['trend']=='slow' else ((df.m5ema20>df.m5ema100)&(df.m5macdh>0)&(df.m5adx>=p['adx']))
    dn=((df.m5ema50<df.m5ema200)&(df.m5slope50<0)&(df.m5adx>=p['adx'])) if p['trend']=='slow' else ((df.m5ema20<df.m5ema100)&(df.m5macdh<0)&(df.m5adx>=p['adx']))
    ok=sess & (df.spreadp<=3.0) & (df.atrp>=0.7) & (df.m5atrp>=2.5)
    if strat=='breakout':
        hi=df.hi30 if p['lb']==30 else df.hi60; lo=df.lo30 if p['lb']==30 else df.lo60
        long=up & (df.close>hi) & (df.macdh>0) & df.rsi.between(45,75)
        short=dn & (df.close<lo) & (df.macdh<0) & df.rsi.between(25,55)
    elif strat=='pullback':
        long=up & ((df.close.shift(1)<df.ema20.shift(1))|(df.low.shift(1)<=df.ema20.shift(1))) & (df.close>df.ema9) & (df.close>df.open) & df.rsi.between(38,70)
        short=dn & ((df.close.shift(1)>df.ema20.shift(1))|(df.high.shift(1)>=df.ema20.shift(1))) & (df.close<df.ema9) & (df.close<df.open) & df.rsi.between(30,62)
    else:
        long=up & (df.rsi.shift(1)<p['rlow']) & (df.rsi>p['rcross']) & (df.close>df.ema9) & (df.close>df.open)
        short=dn & (df.rsi.shift(1)>100-p['rlow']) & (df.rsi<100-p['rcross']) & (df.close<df.ema9) & (df.close<df.open)
    direc=np.zeros(len(df),np.int64); signal=(long|short)&ok; direc[long&ok]=1; direc[short&ok]=-1
    return signal.fillna(False).to_numpy(bool), direc

def run(df,strat,p,mask,collect=False):
    signal,d=sigs(df,strat,p); signal &= mask
    idx=np.flatnonzero(signal).astype(np.int64); dirs=d[idx].astype(np.int64)
    if len(idx)==0:
        out={'net_profit':0,'ending_balance':START_BAL,'trades':0,'wins':0,'losses':0,'profit_factor':0,'max_drawdown_percent':0,'win_rate':0,'total_r':0,'avg_r':0}
        if collect: out['trades_df']=pd.DataFrame()
        return out
    tmin=(df.hour.to_numpy(np.int64)*60+df.minute.to_numpy(np.int64))
    bal,w,l,pf,dd,R,P,E,X,D=sim(df.open.to_numpy(float),df.high.to_numpy(float),df.low.to_numpy(float),df.close.to_numpy(float),df.atrp.to_numpy(float),df.spreadp.to_numpy(float),tmin,idx,dirs,float(p['sl']),float(p['rr']),int(p['hold']),RISK_PCT,START_BAL)
    out={'ending_balance':bal,'net_profit':bal-START_BAL,'trades':len(R),'wins':int(w),'losses':int(l),'profit_factor':float(pf),'max_drawdown_percent':float(dd),'win_rate':w/len(R) if len(R) else 0,'total_r':float(R.sum()) if len(R) else 0,'avg_r':float(R.mean()) if len(R) else 0}
    if collect:
        times=df.time.to_numpy(); out['trades_df']=pd.DataFrame({'entry_time':times[E],'exit_time':times[X],'direction':D,'r':R,'pnl':P,'strategy':strat})
    return out
