from .rules import replay
from app.intervals import count,intersect_all

def _fmt(state,batch,value):return str(value).zfill(state.get('widths',{}).get(batch,1))

def _holdings(state,source):
    rows=[]
    for batch,owners in sorted(state[source].items()):
        for owner,intervals in sorted(owners.items()):
            for a,b in intervals:
                rows.append({'batch':batch,'owner':owner,'start':_fmt(state,batch,a),'end':_fmt(state,batch,b-1),'quantity':str(b-a)})
    return rows

def execute(rows):
    out=replay(rows)
    state=out['state']
    # 名义持仓（最新补正重放结果）与正式持仓（扣除注销墙）并列留痕
    nominal_rows=_holdings(state,'holders')
    holdings=_holdings(state,'formal')
    wall_rows=[]
    for batch,intervals in sorted(state['wall'].items()):
        for a,b in intervals:
            wall_rows.append({'batch':batch,'start':_fmt(state,batch,a),'end':_fmt(state,batch,b-1),'quantity':str(b-a),'status':'注销墙·不可复活'})
    nominal_available=sum(count(v) for owners in state['holders'].values() for v in owners.values())
    formal_available=sum(count(v) for owners in state['formal'].values() for v in owners.values())
    wall_total=sum(count(intersect_all(state['wall'].get(batch,[]),segs)) for batch,segs in state['issued'].items())
    blocked=[]
    for item in out['blocked']:
        blocked.append({'batch':item['batch'],'owner':item['owner'],'start':_fmt(state,item['batch'],item['lo']),'end':_fmt(state,item['batch'],item['hi']-1),'quantity':str(item['hi']-item['lo']),'code':'WALL_BLOCKED','detail':'补正划回已注销序号，注销墙拦截'})
    return {'rows':holdings,'nominal_rows':nominal_rows,'wall_rows':wall_rows,'events':out['events'],'state':state,
            'quarantine':out['quarantine'],'versions_used':out['versions_used'],'blocked':blocked,
            'issued':str(sum(count(v) for v in state['issued'].values())),
            'available':str(formal_available),'nominal_available':str(nominal_available),'retired_wall':str(wall_total)}
