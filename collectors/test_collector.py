import unittest
from game_collector import merge_sample, parse_kv
class CollectorTests(unittest.TestCase):
    def test_error_does_not_retain_online_or_health(self):
        current=merge_sample({'online':12,'status':'ok','total':50},{'collection_error':True,'status':'unknown','online':-1},False,100)
        self.assertEqual(current['online'],-1)
        self.assertEqual(current['status'],'unknown')
        self.assertEqual(current['total'],50)
    def test_midnight_resets_today_but_not_alltime(self):
        old={'peak_today':40,'peak_all':80,'peak_day':'2020-01-01','peak_source':'sessions'}
        cur=merge_sample(old,{'online':3,'status':'ok','online_source':'sessions'},True,1793247000)
        self.assertEqual(cur['peak_today'],3)
        self.assertEqual(cur['peak_all'],80)
    def test_invalid_or_missing_values_not_invented(self):
        self.assertEqual(parse_kv('online=-1\nstatus=unknown\nnoise'),{'online':-1,'status':'unknown'})
if __name__=='__main__':unittest.main()
