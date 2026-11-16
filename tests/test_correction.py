"""跨版本补正：版本梯、换版回退、注销墙不可复活、重试幂等、正式持仓裁决。"""
import pytest
from copy import deepcopy
from app.config import ROOT
from app.pipeline import run
from app import store
from app.modules.m1_intake.rules import parse
from app.modules.m2_contract.service import execute as contract
from app.modules.m3_model.service import execute as model
from app.modules.m3_model.rules import build_ladders, fingerprint
from app.errors import DomainError

CORRECTION = (ROOT/'sample_data/correction.csv').read_bytes()

def _rows(raw=CORRECTION):
    return contract(parse(raw)['rows'])['rows']

def _find(rows, event_id, version):
    return next(r for r in rows if r['event_id']==event_id and r['version']==version)

@pytest.fixture
def correction():
    return run({'csv':CORRECTION.decode('utf-8-sig')})

# ---------- 1. 月结采用最新补正结果，但注销事实不复活 ----------

def test_month_end_uses_latest_correction(correction):
    rec=correction['reconcile']
    # C002 换版成功：最新版（华南工厂）生效，旧版（长桥制造）被取代
    assert any(v['event_id']=='C002' and v['adopted']==2 and v['offered']==1 and v['reason'] is None
               for v in rec['versions_used'])
    owners_09001={h['owner'] for h in correction['model']['rows'] if h['batch']=='09001'}
    assert '华南工厂' in owners_09001

def test_retirement_never_revived(correction):
    # 注销墙总量：10+5+10+10+15=50；正式可用 150；签发 200
    rec=correction['reconcile']
    assert rec['retired']=='50'
    assert rec['available']=='150'
    assert rec['issued']=='200'
    assert rec['balanced'] is True
    # 补正 C008 把已注销段改为划拨，被墙拦截，墙内10张仍是注销而非可用
    blocked_codes=[q['code'] for q in correction['quarantine']['rows']]
    assert 'WALL_BLOCKED' in blocked_codes

def test_wall_is_monotonic_union(correction):
    # C012 补正扩展注销（10→15），并集为15；C010 同区间重复注销仍只算10
    audit={a['batch']:a for a in correction['reconcile']['audit']}
    assert audit['09005']['retired']=='15'
    assert audit['09004']['retired']=='10'
    # 名义注销只反映当前生效事件（45）；注销墙额外保留被取代的C008旧版注销事实（+5=50）
    assert correction['reconcile']['nominal_retired']=='45'
    assert int(correction['reconcile']['retired'])>=int(correction['reconcile']['nominal_retired'])

# ---------- 2. 逐区间版本与可用/已划拨/已注销核对 ----------

def test_per_interval_audit_conservation(correction):
    for a in correction['reconcile']['audit']:
        issued=int(a['issued']);available=int(a['available']);retired=int(a['retired'])
        # 守恒：签发=可用+已注销；已划拨是可用池中外部持有人占用，不超过可用
        assert issued==available+retired
        assert int(a['transferred'])<=available
        assert a['version']=='最新补正生效版'

def test_audit_covers_every_batch(correction):
    batches={a['batch'] for a in correction['reconcile']['audit']}
    assert batches=={'09001','09002','09003','09004','09005'}

# ---------- 3. 同一补正重试幂等：不重复占用、不重复注销 ----------

def test_identical_retry_is_duplicate_not_conflict(correction):
    # C002 v2 出现两次且仅备注不同：业务指纹相同 → 一条DUPLICATE，无CONFLICT
    c002=[q for q in correction['quarantine']['rows'] if q['raw']['event_id']=='C002']
    assert any(q['code']=='DUPLICATE' for q in c002)
    assert not any(q['code']=='CONFLICT' for q in c002)

def test_retry_does_not_double_hold():
    rows=_rows()
    base=model(rows)
    extra=[deepcopy(r) for r in rows if r['event_id']=='C002' and r['version']==2]
    doubled=model(rows+extra)
    assert doubled['available']==base['available']
    assert doubled['retired_wall']==base['retired_wall']

def test_repeated_retirement_not_double_counted():
    rows=_rows()
    out=model(rows)
    r010=[h for h in out['rows'] if h['batch']=='09004']
    # 09004 签发20、墙注销10，正式可用恒为10，重复注销不放大
    assert sum(int(h['quantity']) for h in r010)==10

# ---------- 4. 换版失败保留旧版，不丢记录/原始证据 ----------

def test_failed_correction_keeps_old_version(correction):
    # C006 v2 改持有人（非持仓）失败，回退并沿用 v1
    used={v['event_id']:v for v in correction['reconcile']['versions_used']}
    assert used['C006']['adopted']==1 and used['C006']['offered']==2
    assert used['C006']['reason']=='NOT_OWNED'
    # 旧版仍然是生效注销事件，证据未丢
    retired_events=[e['event_id'] for e in correction['calculate']['events']]
    assert 'C006' in retired_events

def test_superseded_versions_retain_evidence(correction):
    sup=[q for q in correction['quarantine']['rows'] if q['code']=='SUPERSEDED']
    # C002、C010、C012 旧版均留痕，且保留原始行号与原值
    assert {q['raw']['event_id'] for q in sup} >= {'C002','C010','C012'}
    assert all(q['line']>0 and q['raw'] for q in sup)

def test_conflicting_version_group_isolates_only_that_group():
    raw=CORRECTION.decode('utf-8-sig').splitlines()
    header=raw[0]
    # 给 C006 增加一条同版本号但异值的记录；v1 干净版本必须仍可生效
    evil='C006,09002,retire,别家公司,,8000000000000002000,8000000000000002004,2026-11-04T11:00:00+08:00,2,恶意异值'
    payload='\n'.join([header]+raw[1:]+[evil])
    out=run({'csv':payload})
    codes=[q['code'] for q in out['quarantine']['rows']]
    assert 'CONFLICT' in codes
    # v1 旧版保留，批号 09002 仍有5张注销
    audit={a['batch']:a for a in out['reconcile']['audit']}
    assert audit['09002']['retired']=='5'

# ---------- 5. 正式持仓 = 名义持仓 − 注销墙 ----------

def test_formal_holdings_subtract_wall():
    out=model(_rows())
    formal=sum(int(h['quantity']) for h in out['rows'])
    nominal=sum(int(h['quantity']) for h in out['nominal_rows'])
    assert nominal>=formal
    assert out['available']==str(formal)
    assert out['retired_wall']=='50'

def test_wall_blocked_transfer_keeps_serial_retired():
    # C004：把墙内10张名义划回持有人 → WALL_BLOCKED，正式持仓不含这10张
    out=model(_rows())
    blocked=[b for b in out['blocked']]
    assert any(b['code']=='WALL_BLOCKED' for b in blocked)

def test_shrinking_retirement_cannot_revive_serials():
    from app.modules.m2_contract.rules import convert
    cols=dict(event_id='E1',batch='b',action='issue',owner='o',target='',start='1',end='20',
              time='2026-11-01T08:00:00+08:00',version='1',note='')
    def mk(line,**ov):return convert({'line':line,'raw':cols|ov})
    issue_row=mk(1)
    retire_v1=mk(2,event_id='R1',action='retire',start='1',end='10',time='2026-11-02T08:00:00+08:00')
    retire_v2=mk(3,event_id='R1',action='retire',start='1',end='5',time='2026-11-04T08:00:00+08:00',version='2')
    out=model([issue_row,retire_v1,retire_v2])
    # 名义账：新版只注销5张，名义可用15；注销墙保留旧版10张，正式可用仍为10
    assert out['nominal_available']=='15'
    assert out['retired_wall']=='10'
    assert out['available']=='10'

# ---------- 6. 指纹与版本梯单元规则 ----------

def test_fingerprint_ignores_note():
    rows=_rows()
    a=_find(rows,'C002',2)
    b=deepcopy(a);b['raw']=dict(a['raw']);b['raw']['note']='不同备注'
    assert fingerprint(a)==fingerprint(b)

def test_ladder_orders_versions_desc():
    ladders,bad=build_ladders(_rows())
    c002=next(l for l in ladders if l['event_id']=='C002')
    assert [r['version'] for r in c002['versions']]==[2,1]

# ---------- 7. 持久化：复核与证据 ----------

def test_correction_run_persists_and_reviews(correction):
    saved=store.save(correction)
    rid=correction['manifest']['run_id']
    again=store.save(correction)
    assert saved['created'] and not again['created']
    reviewed=store.review(rid,saved['revision'])
    assert reviewed['revision']==saved['revision']+1
    with pytest.raises(DomainError):store.review(rid,saved['revision'])
