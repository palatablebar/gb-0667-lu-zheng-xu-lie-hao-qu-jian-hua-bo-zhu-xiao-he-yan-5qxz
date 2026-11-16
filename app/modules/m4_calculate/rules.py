from app.intervals import overlaps,normalize,count,intersect_all,difference

def retire(state,row,allow_retired=False):
    batch=row['batch'];lo=row['lo'];hi=row['hi'];owner=row['owner']
    # 同事件换版重试：旧版名义账已回退，允许覆盖其原注销区间，幂等由注销墙并集保证
    if not allow_retired and overlaps(state['retired'].get(batch,[]),lo,hi):return 'RETIRED'
    holdings=state['holders'].get(batch,{}).get(owner,[])
    # 支持由多个相邻/分段区间共同覆盖目标（换版回退后持仓可能被切分）
    if count(intersect_all(holdings,[(lo,hi)]))!=hi-lo:return 'NOT_OWNED'
    state['holders'][batch][owner]=difference(holdings,[(lo,hi)])
    state['retired'][batch]=normalize(state['retired'].get(batch,[])+[(lo,hi)])
    return None

def retirement_rows(state):
    return [{'batch':batch,'start':str(a).zfill(state.get('widths',{}).get(batch,1)),'end':str(b-1).zfill(state.get('widths',{}).get(batch,1)),'quantity':str(b-a),'status':'已注销'} for batch,intervals in sorted(state['retired'].items()) for a,b in intervals]
