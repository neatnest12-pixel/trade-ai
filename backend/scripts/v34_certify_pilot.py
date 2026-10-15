"""Independent pilot price-path verification. Only writes a certification artifact/gate."""
from __future__ import annotations
import hashlib
import json
import math
from datetime import timedelta

from app.v3.certification import POLICY_HASH, require_certified_outcome, require_safety, utc
from app.v3.certified_resolver import MT5Ticks, evaluate
from app.v3.certified_store import atomic_json, connect, persist
from v34_recertify import CAMPAIGN, DB, OUT


def verify_path(setup, rows, source):
    """Separately calculate first entry and first SL/TP/timeout for each RR."""
    decision = utc(setup['decision_timestamp'])
    ticks = source.ticks(decision, decision+timedelta(hours=6,minutes=2), setup['offset_seconds'])
    entry = None
    expected = {}
    previous = decision.timestamp()*1000
    side = 1 if setup['direction']=='BUY' else -1
    for stamp,bid,ask in ticks:
        if stamp<=decision.timestamp()*1000:continue
        assert 0<bid<=ask and math.isfinite(ask)
        assert previous<=stamp and stamp-previous<=(60000 if entry is None else 300000)
        previous=stamp
        if entry is None:
            entry=(stamp,ask if side==1 else bid)
            continue
        if stamp<=entry[0]:continue
        price=bid if side==1 else ask
        displacement=(price-entry[1])*side/setup['stop_distance']
        for row in rows:
            rr=row['rr']
            if rr in expected:continue
            reason=None
            if stamp<=entry[0]+21600000:
                if displacement<=-1:reason='SL_HIT'
                elif displacement>=rr:reason='TP_HIT'
            if reason is None and stamp>=entry[0]+21600000:reason='TIME_EXIT'
            if reason:expected[rr]=(stamp,price,displacement,reason)
        if len(expected)==len(rows):break
    for row in rows:
        require_certified_outcome(row)
        stamp,price,r,reason=expected[row['rr']]
        assert abs(utc(row['entry_timestamp']).timestamp()*1000-entry[0])<.01
        assert row['entry_price']==entry[1]
        assert abs(utc(row['exit_timestamp']).timestamp()*1000-stamp)<.01
        assert row['exit_price']==price and row['exit_reason']==reason
        assert abs(row['realized_r']-r)<1e-10
        assert row['net_r'] is None and row['commission_cost_r'] is None


def main():
    require_safety()
    db=connect(DB)
    # No broker call: independently replay the actual persisted bounded tick evidence.
    class CacheOnly:
        COPY_TICKS_ALL=1
        def copy_ticks_range(self,*_):raise AssertionError('PILOT_EVIDENCE_MISSING')
    source=MT5Ticks(CacheOnly(),db)
    ids=db.execute('SELECT DISTINCT observation_id FROM outcomes ORDER BY observation_id').fetchall()
    if not 1<=len(ids)<=25:raise RuntimeError('EXPECTED_SMALL_PILOT')
    dates=set();sides=set();checked=0
    before=hashlib.sha256(OUT.read_bytes()).hexdigest()
    for (oid,) in ids:
        setup=json.loads(db.execute('SELECT payload FROM setups WHERE id=?',(oid,)).fetchone()[0])
        rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM outcomes WHERE observation_id=? ORDER BY rr',(oid,))]
        verify_path(setup,rows,source)
        replay=evaluate(setup,source)[2]
        assert persist(db,replay)==0
        dates.add(setup['decision_timestamp'][:10]);sides.add(setup['direction']);checked+=len(rows)
    assert hashlib.sha256(OUT.read_bytes()).hexdigest()==before
    result={'status':'PASS','independent_price_path_check':True,'restart_replay_new_contracts':0,
            'setups':len(ids),'contracts':checked,'dates':sorted(dates),'directions':sorted(sides),
            'max_tick_window':source.max_window_ticks,'policy_hash':POLICY_HASH,
            'cost_status':'PRE_COMMISSION_ONLY','training_allowed':False}
    atomic_json(CAMPAIGN/'pilot_certification.json',result)
    with db:db.execute("INSERT OR REPLACE INTO metadata VALUES('pilot_certified',?)",(POLICY_HASH,))
    print(json.dumps(result))
    db.close()

if __name__=='__main__':main()
