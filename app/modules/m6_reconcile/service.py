from .rules import check_state,batch_audit

def execute(intake,model,quarantine):
    state=model['state']
    check=check_state(state)
    accepted=len(model['events']);isolated=quarantine['count']
    rows=[{'account':'输入事件','value':intake['input']},{'account':'有效事件','value':accepted},{'account':'隔离事件','value':isolated},{'account':'签发证书','value':check['issued']},{'account':'可用证书','value':check['available']},{'account':'已注销（注销墙）','value':check['retired']}]
    superseded=[r for r in model['quarantine'] if r['code']=='SUPERSEDED']
    blocked=[r for r in model.get('blocked',[])]
    audit=batch_audit(state)
    return check|{'rows':rows,'input':intake['input'],'accepted':accepted,'isolated':isolated,
                  'row_delta':intake['input']-accepted-isolated,
                  'audit':audit,'versions_used':model.get('versions_used',[]),
                  'superseded':len(superseded),'wall_blocked':blocked,
                  'balanced':check['balanced'] and intake['input']==accepted+isolated}
