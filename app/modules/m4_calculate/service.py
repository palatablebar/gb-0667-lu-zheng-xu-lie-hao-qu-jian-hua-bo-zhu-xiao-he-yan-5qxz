from .rules import retire,retirement_rows
from app.intervals import count,intersect_all

def apply_retirement(state,row):return retire(state,row)

def execute(model):
    state=model['state']
    rows=retirement_rows(state)
    wall_rows=[]
    for batch,intervals in sorted(state.get('wall',{}).items()):
        for a,b in intervals:
            wall_rows.append({'batch':batch,'start':str(a).zfill(state.get('widths',{}).get(batch,1)),'end':str(b-1).zfill(state.get('widths',{}).get(batch,1)),'quantity':str(b-a),'status':'注销墙·不可复活'})
    wall_total=sum(count(intersect_all(state.get('wall',{}).get(batch,[]),segs)) for batch,segs in state['issued'].items())
    return {'rows':wall_rows,'nominal_rows':rows,
            'retired':str(wall_total),'nominal_retired':str(sum(int(r['quantity']) for r in rows)),
            'events':[{'event_id':r['event_id'],'owner':r['owner'],'quantity':r['quantity'],'line':r['line'],'version':r.get('version',1),'status':'已注销'} for r in model['events'] if r['action']=='retire']}
