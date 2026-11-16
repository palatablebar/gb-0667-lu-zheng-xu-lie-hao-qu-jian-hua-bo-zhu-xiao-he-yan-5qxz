import json
from collections import defaultdict
from app.reasons import issue
from app.intervals import normalize,covered,overlaps,subtract,intersect_all,difference

def fingerprint(row):
    # 业务指纹不含备注列：同一补正重试仅备注不同仍视为同一事件
    business={k:v for k,v in row['raw'].items() if k!='note'}
    return json.dumps(business,sort_keys=True,ensure_ascii=False)

def deduplicate(rows):
    groups=defaultdict(list)
    for row in rows:groups[row['event_id']].append(row)
    accepted=[];bad=[]
    for key,items in sorted(groups.items()):
        variants={fingerprint(x) for x in items}
        if len(variants)>1:
            bad.extend(issue(x,'CONFLICT') for x in items);continue
        accepted.append(items[0]);bad.extend(issue(x,'DUPLICATE') for x in items[1:])
    return sorted(accepted,key=lambda r:(r['utc'],r['event_id'])),bad

def allocate(state,row):
    batch=row['batch'];owner=row['owner'];lo=row['lo'];hi=row['hi']
    holders=state['holders'].setdefault(batch,{})
    issued=state['issued'].setdefault(batch,[])
    current=holders.get(owner,[])
    if row['action']=='issue':
        if overlaps(issued,lo,hi):return 'OVERLAP'
        state['issued'][batch]=normalize(issued+[(lo,hi)])
        state.setdefault('widths',{})[batch]=max(state.get('widths',{}).get(batch,0),row.get('width',1))
        state.setdefault('issuers',{}).setdefault(batch,set()).add(owner)
        holders[owner]=normalize(current+[(lo,hi)])
    else:
        if not row['target'] or row['target']==owner:return 'OWNER'
        if not covered(current,lo,hi):return 'NOT_OWNED'
        # 注销墙硬约束：名义持仓中仍持有的部分若含已注销序号，禁止划回可用池
        # （已不在持有人余额里的墙内序号自然无法再被划拨）
        if intersect_all(current,intersect_all(state.get('wall',{}).get(batch,[]),[(lo,hi)])):return 'RETIRED'
        holders[owner]=subtract(current,lo,hi)
        holders[row['target']]=normalize(holders.get(row['target'],[])+[(lo,hi)])
    return None

def build_ladders(rows):
    """同事件号按版本归梯：同指纹重试仅留一条（其余DUPLICATE）；
    同版本异值仅隔离该异值版本组（CONFLICT），其余干净版本保留；版本按号降序。
    全局生效顺序在replay中按（事件UTC, 版本号, 事件号）统一排序，本函数不决定先后。"""
    groups=defaultdict(list)
    for row in rows:groups[row['event_id']].append(row)
    ladders=[];bad=[]
    for event_id,items in sorted(groups.items()):
        by_version=defaultdict(list)
        for row in items:by_version[row['version']].append(row)
        versions=[]
        for version,group in sorted(by_version.items(),reverse=True):
            fingerprints={fingerprint(r) for r in group}
            if len(fingerprints)>1:
                # 仅隔离异值版本组；同事件号的其它干净版本仍保留，证据不丢失
                bad.extend(issue(r,'CONFLICT') for r in group);continue
            chosen=group[0]
            bad.extend(issue(r,'DUPLICATE') for r in group[1:])
            versions.append(chosen)
        if versions:ladders.append({'event_id':event_id,'versions':versions})
    return ladders,bad

def _replay(state,row,allow_retired=False):
    from app.modules.m4_calculate.rules import retire
    if row['action']=='retire':return retire(state,row,allow_retired=allow_retired)
    return allocate(state,row)

def _wall_add(state,row):
    wall=state['wall'].setdefault(row['batch'],[])
    state['wall'][row['batch']]=normalize(wall+[(row['lo'],row['hi'])])

def _touches_wall(state,row):
    return bool(intersect_all(state.get('wall',{}).get(row['batch'],[]),[(row['lo'],row['hi'])]))

def _rollback(state,row,replacement=None):
    """仅回退名义账，注销墙保持不动，保证注销事实单调不复活。
    transfer换版时，被新版承接区间不返还原持有人；retire换版时旧区间先全额返还，
    再由新retire自行扣减；凡回退前已在名义注销集中的序号一律不复活。"""
    batch=row['batch'];lo=row['lo'];hi=row['hi'];holders=state['holders'].setdefault(batch,{})
    retired_before=list(state['retired'].get(batch,[]))
    if row['action']=='issue':
        state['issued'][batch]=subtract(state['issued'].get(batch,[]),lo,hi)
        holders[row['owner']]=subtract(holders.get(row['owner'],[]),lo,hi)
    elif row['action']=='transfer':
        cutters=list(retired_before)
        if replacement is not None:cutters.append((replacement['lo'],replacement['hi']))
        holders[row['target']]=subtract(holders.get(row['target'],[]),lo,hi)
        holders[row['owner']]=normalize(holders.get(row['owner'],[])+difference([(lo,hi)],cutters))
    else:
        # 名义注销回退：原区间全额返还持有人；是否真正可再划拨由注销墙（wall）独立裁决
        state['retired'][batch]=subtract(state['retired'].get(batch,[]),lo,hi)
        holders[row['owner']]=normalize(holders.get(row['owner'],[])+[(lo,hi)])

def replay(rows):
    """跨版本更正重放。
    所有干净版本按（事件UTC, 事件号, 版本号）全局排序：补正在其自身到达时刻生效，
    不溯及改写历史事件。换版先回退同事件当前生效版的名义账；失败则恢复旧版，旧版证据全保留。
    注销墙累积所有曾生效版本的合法注销区间（并集幂等），注销不可复活。"""
    ladders,bad=build_ladders(rows)
    stream=sorted((row for ladder in ladders for row in ladder['versions']),
                  key=lambda r:(r['utc'],r['version'],r['event_id']))
    state={'holders':{},'issued':{},'retired':{},'wall':{},'issuers':{}}
    accepted=[];versions_used=[];applied_map={}
    for candidate in stream:
        current=applied_map.get(candidate['event_id'])
        retry=current is not None and current['version']<candidate['version']
        if current is not None and current['version']>=candidate['version']:
            continue  # 理论上不会到达：构建阶段已去重，防御性保留
        if retry:_rollback(state,current,replacement=candidate)
        code=_replay(state,candidate,allow_retired=retry)
        wall_hit=code is not None and code!='OVERLAP' and _touches_wall(state,candidate)
        if code is None:
            applied_map[candidate['event_id']]=candidate
            if candidate['action']=='retire':_wall_add(state,candidate)
            if retry:
                versions_used.append({'event_id':candidate['event_id'],'adopted':candidate['version'],'offered':current['version'],'reason':None,'line':candidate['line']})
                bad.append(issue(current,'SUPERSEDED',f"由版本{candidate['version']}取代"))
            continue
        # 换版失败：恢复旧版名义账，旧版继续生效，证据不丢失
        if retry:
            _replay(state,current)
            if wall_hit:
                bad.append(issue(candidate,'WALL_BLOCKED',f'{code}：区间已计入注销墙，注销不可复活'))
            else:
                bad.append(issue(candidate,'VERSION_FALLBACK',f'{code}：换版失败，沿用版本{current["version"]}'))
            versions_used.append({'event_id':candidate['event_id'],'adopted':current['version'],'offered':candidate['version'],'reason':code,'line':current['line']})
        elif wall_hit:
            bad.append(issue(candidate,'WALL_BLOCKED',f'{code}：区间已计入注销墙，注销不可复活'))
        else:
            bad.append(issue(candidate,code))
    accepted=sorted(applied_map.values(),key=lambda r:(r['utc'],r['event_id']))
    formal,blocked=adjudicate(state)
    state['formal']=formal
    return {'state':state,'events':accepted,'quarantine':bad,'versions_used':versions_used,'blocked':blocked}

def adjudicate(state):
    """正式持仓裁决：名义持仓减去注销墙；墙∩签发=已注销，注销不可复活。
    返回正式持仓与所有名义区间撞上注销墙的拦截证据（含补正划回已注销序号）。"""
    formal={};blocked=[]
    wall=state.get('wall',{})
    for batch,owners in state['holders'].items():
        formal[batch]={}
        cutter=wall.get(batch,[])
        for owner,intervals in owners.items():
            formal[batch][owner]=difference(intervals,cutter)
            for lo,hi in intersect_all(intervals,cutter):
                blocked.append({'batch':batch,'owner':owner,'lo':lo,'hi':hi})
    return formal,blocked
