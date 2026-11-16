from app.intervals import count,overlaps,intersect_all,normalize

def check_state(state):
    issued=sum(count(x) for x in state['issued'].values())
    nominal_retired=sum(count(x) for x in state.get('retired',{}).values())
    # 注销墙∩签发：所有曾生效版本注销事实的并集，单调不可复活
    wall=state.get('wall',{})
    wall_retired=sum(count(intersect_all(wall.get(batch,[]),segs)) for batch,segs in state['issued'].items())
    nominal_available=sum(count(v) for owners in state['holders'].values() for v in owners.values())
    formal=state.get('formal',state['holders'])
    available=sum(count(v) for owners in formal.values() for v in owners.values())
    collisions=[]
    for batch,owners in formal.items():
        spans=[(a,b,owner) for owner,values in owners.items() for a,b in values]
        for i,(a,b,owner) in enumerate(spans):
            for c,d,other in spans[i+1:]:
                if a<d and c<b:collisions.append({'batch':batch,'owner':owner,'other':other})
        # 正式持仓（已扣注销墙）不得再与注销墙重叠
        if overlaps(wall.get(batch,[]),a,b):collisions.append({'batch':batch,'owner':owner,'other':'已注销'})
    # 正式持仓守恒：签发=可用(去墙)+已注销(墙∩签发)
    transferred=issued-available-wall_retired
    balanced=issued==available+wall_retired and not collisions and wall_retired>=nominal_retired
    return {'issued':str(issued),'available':str(available),'nominal_available':str(nominal_available),
            'retired':str(wall_retired),'nominal_retired':str(nominal_retired),
            'transferred':str(transferred),'delta':str(issued-available-wall_retired),
            'collisions':collisions,'balanced':balanced}

def batch_audit(state):
    """逐区间核对：采用的事件版本、可用、已划拨（外部持有人占用）、已注销数量。
    守恒：签发=可用+已注销；已划拨为可用池中由非签发持有人占用的部分（含入可用）。"""
    wall=state.get('wall',{})
    issuers_map=state.get('issuers',{})
    rows=[]
    for batch in sorted(set(state['issued'])|set(state['holders'])|set(wall)):
        issued_segs=normalize(state['issued'].get(batch,[]))
        issued_q=count(issued_segs)
        formal=state.get('formal',{}).get(batch,{})
        nominal=state['holders'].get(batch,{})
        issuers=issuers_map.get(batch,set())
        available_q=sum(count(v) for v in formal.values())
        transferred_q=sum(count(segs) for owner,segs in formal.items() if owner not in issuers)
        wall_q=count(intersect_all(wall.get(batch,[]),issued_segs))
        owners='、'.join(sorted(nominal)) or '—'
        rows.append({'batch':batch,'version':'最新补正生效版','owner':owners,
                     'issued':str(issued_q),'available':str(available_q),
                     'transferred':str(transferred_q),'retired':str(wall_q)})
    return rows
