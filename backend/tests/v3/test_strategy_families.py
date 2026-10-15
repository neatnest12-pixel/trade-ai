from app.v3.strategy_families import STRATEGY_POLICY_HASH, evaluate

BASE={'states':{'M1':'BULLISH','H1':'BULLISH','H6':'BULLISH','H12':'BULLISH'},'normalized_atr':.001,'close':2,'prior_high':3,'prior_low':1}
def test_trend_buy_sell_and_fail():
 assert evaluate(BASE,'TREND').side=='BUY'; x={**BASE,'states':{**BASE['states'],'M1':'BEARISH','H1':'BEARISH','H6':'BEARISH','H12':'BEARISH'}}; assert evaluate(x,'TREND').side=='SELL'; assert not evaluate({**BASE,'states':{}},'TREND').setup
def test_breakout_and_pullback():
 assert evaluate({**BASE,'close':4},'BREAKOUT').side=='BUY'; assert evaluate({**BASE,'close':.5},'BREAKOUT').side=='SELL'; assert evaluate({**BASE,'m1_return':-.01,'continuation':True},'PULLBACK').side=='BUY'
def test_mean_reversion_volatility_and_policy():
 x={**BASE,'regime':'RANGE','deviation':.01,'m1_return':-.01}; assert evaluate(x,'MEAN_REVERSION').side=='BUY'; assert evaluate({**BASE,'range_expansion':True},'VOLATILITY').side=='BUY'; assert len(STRATEGY_POLICY_HASH)==64
