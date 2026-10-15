// Run against tsc's CommonJS output in the offline evidence directory.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {geometry,positionSize,decisionIndex}=require(process.env.VISUALIZER_MODEL_JS);
test('BUY risk and reward geometry',()=>assert.equal(geometry('BUY',10,9,12).rr,2));
test('SELL risk and reward geometry',()=>assert.equal(geometry('SELL',10,12,6).rr,2));
for(const [name,args] of Object.entries({zero:['BUY',10,10,11],wrongSL:['BUY',10,11,12],wrongTP:['SELL',10,11,12],noTrade:['NO_TRADE',10,9,12],nonFinite:['BUY',NaN,9,12]}))
  test(`reject ${name}`,()=>assert.equal(geometry(...args),null));
const metadata={symbol:'EURUSD',account_currency:'USD',tick_size:.00001,tick_value_loss:1,volume_min:.01,volume_max:100,volume_step:.01};
test('FX broker tick sizing',()=>assert.equal(positionSize(10000,1,1.1,1.099,metadata).lots,1));
test('metal sizing uses its own tick metadata',()=>assert.equal(positionSize(10000,1,2600,2590,{...metadata,symbol:'XAUUSD',tick_size:.01,tick_value_loss:1}).lots,.1));
test('JPY sizing does not assume EUR pip value',()=>assert.equal(positionSize(10000,1,150,149,{...metadata,symbol:'USDJPY',tick_size:.001,tick_value_loss:.67}).lots,.14));
test('missing metadata',()=>assert.equal(positionSize(10000,1,10,9,null),null));
test('zero tick value',()=>assert.equal(positionSize(10000,1,10,9,{...metadata,tick_value_loss:0}),null));
test('zero risk',()=>assert.equal(positionSize(10000,1,10,10,metadata),null));
test('decision marker interpolates chart time',()=>assert.equal(decisionIndex([{time:100},{time:160}],130),.5));
test('out of range marker not fabricated',()=>assert.equal(decisionIndex([{time:100},{time:160}],99),null));
test('later candles do not alter frozen geometry',()=>{const p=Object.freeze(geometry('BUY',10,9,12));decisionIndex([{time:100},{time:160},{time:220}],130);assert.equal(p.rr,2)});
