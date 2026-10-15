import unittest,tempfile,sqlite3
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from app.v3_5.broker_context import snapshot
from app.v3_5.context_recorder import ContextRecorder,isolated_record,observe_committed

class RecorderTests(unittest.TestCase):
    def test_append_idempotency_and_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'context.sqlite3';store=ContextRecorder(p,fixture=True)
            r=snapshot('2026-10-04T13:00:00Z',broker='ICMarkets',expected_server='ICMarketsSC-Demo',observation_id='fixture')
            args={'authority':'GENERIC_TEMPLATE','evidence':{'source':'TEST_ONLY'},'kind':'FIXTURE'}
            self.assertEqual(store.append(r,**args)['status'],'RECORDED')
            self.assertEqual(store.append(r,**args)['status'],'ALREADY_RECORDED')
            d=sqlite3.connect(p)
            try:
                self.assertEqual(d.execute('select count(*) from context_records').fetchone()[0],1)
                self.assertEqual(d.execute('pragma quick_check').fetchone()[0],'ok')
                self.assertEqual(d.execute('pragma foreign_key_check').fetchall(),[])
                for sql in ['update context_records set symbol="EURUSD"','delete from context_records']:
                    with self.assertRaisesRegex(sqlite3.IntegrityError,'APPEND_ONLY'):d.execute(sql)
            finally:d.close()
    def test_failure_isolation(self):
        class Broken:
            def append(self,*a,**k):raise OSError('unavailable')
        r=snapshot('2026-10-04T13:00:00Z',broker='ICMarkets',expected_server='ICMarketsSC-Demo')
        self.assertEqual(isolated_record(lambda:r,Broken())['status'],'SHADOW_RECORD_FAILED')
        self.assertEqual(isolated_record(lambda:1/0,Broken())['status'],'SHADOW_RECORD_FAILED')
    def test_unknown_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=ContextRecorder(Path(tmp)/'context.sqlite3',fixture=True)
            r=snapshot('2026-10-04T13:00:00Z',broker='ICMarkets',expected_server='ICMarketsSC-Demo')
            self.assertEqual(isolated_record(lambda:r,store,authority='MAGIC',evidence={})['status'],'SHADOW_RECORD_FAILED')
    def test_malformed_context_cannot_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=ContextRecorder(Path(tmp)/'context.sqlite3',fixture=True)
            self.assertEqual(isolated_record(lambda:object(),store,authority='GENERIC_TEMPLATE',evidence={})['status'],'SHADOW_RECORD_FAILED')
            with closing(sqlite3.connect(store.path)) as db:
                self.assertEqual(db.execute('select count(*) from context_records').fetchone()[0],0)
    def test_fixture_cannot_target_project(self):
        from app.v3_5.context_recorder import ROOT
        with self.assertRaisesRegex(ValueError,'CAMPAIGN_STORE_FORBIDDEN'):
            ContextRecorder(ROOT/'runtime/forbidden_fixture.sqlite3',fixture=True)
    def test_missing_references(self):
        r=snapshot('2026-10-04T13:00:00Z',broker='ICMarkets',expected_server='ICMarketsSC-Demo')
        self.assertEqual(r.context['spread_state'],'UNKNOWN');self.assertEqual(r.context['volatility_state'],'UNKNOWN')
    def test_postcommit_hook_isolated_and_input_unchanged(self):
        import copy
        cycle={'data_training_eligible':True,'snapshot':{'decision_time_utc':'2026-10-04T13:00:00Z','feature_values':{},'snapshot_sha256':'fixture'},'observation_id':'fixture'}
        before=copy.deepcopy(cycle)
        with patch('app.v3_5.context_recorder.ContextRecorder') as cls:
            observe_committed(cycle);self.assertEqual(cls.return_value.append.call_count,1)
            cls.side_effect=OSError('unavailable');observe_committed(cycle)
        self.assertEqual(cycle,before)

if __name__=='__main__':unittest.main()
