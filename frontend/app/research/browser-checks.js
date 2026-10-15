// UI-only fixtures intercepted inside this browser session. Run with
// playwright-cli run-code; never load these through the production API.
async page => {
  const response = await page.request.get('http://127.0.0.1:3010/api/research?timeframe=M1&symbol=EURUSD');
  if (!response.ok()) throw Error('READ_ONLY_SOURCE_FAILED');
  const actual = await response.json();
  if (!actual.market.candles.length) throw Error('HISTORICAL_CANDLES_REQUIRED');
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.setViewportSize({width:1440,height:1100});
  try {
    for (const direction of ['BUY','SELL']) {
      const fixture = JSON.parse(JSON.stringify(actual));
      const price = fixture.market.candles.at(-1).close;
      const sign = direction === 'BUY' ? 1 : -1;
      fixture.campaign.setup.direction = direction;
      fixture.campaign.setup.plan = {entry:price,stop:price-sign*.001,target:price+sign*.002,risk_distance:.001,reward_distance:.002,rr:2};
      fixture.campaign.setup.plan_reason = 'UI_TEST_FIXTURE_NOT_A_REAL_TRADE_PLAN';
      fixture.campaign.setup.snapshot_hash = 'UI_TEST_' + direction;
      fixture.market.status = 'STALE';
      fixture.market.metadata = {symbol:'EURUSD',account_currency:'USD',digits:5,point:.00001,tick_size:.00001,tick_value_loss:1,volume_min:.01,volume_max:100,volume_step:.01,contract_size:100000};
      await page.route('**/api/research?**', route => route.fulfill({json:fixture}));
      await page.reload();
      await page.getByText('UI TEST FIXTURE NOT A REAL TRADE PLAN',{exact:true}).waitFor();
      await page.getByLabel('Account balance').fill('10000');
      await page.getByLabel('Entry',{exact:true}).fill('1.1');
      await page.getByLabel('Stop loss',{exact:true}).fill('1.099');
      if ((await page.locator('.rv-size').innerText()) !== '1.0000 lots') throw Error('POSITION_SIZE_RENDER');
      const before = await page.locator('.rv-setup').innerText();
      await page.locator('.rv-chart-wrap').hover();
      await page.mouse.wheel(0,-200);
      if (before !== await page.locator('.rv-setup').innerText()) throw Error('PLAN_CHANGED_ON_CHART_INTERACTION');
      await page.locator('.rv-workspace').screenshot({path:`runtime/v34_prospective_validation_r1/offline_visualizer/fixture-${direction}.png`});
      await page.unroute('**/api/research?**');
    }
    if (errors.length) throw Error(errors.join(';'));
    const wrongMethod=await page.request.post('http://127.0.0.1:3010/api/research');
    if(wrongMethod.status()!==405)throw Error('WRITE_METHOD_NOT_REJECTED');
    const wrongFrame=await page.request.get('http://127.0.0.1:3010/api/research?timeframe=H4');
    if(wrongFrame.status()!==400)throw Error('INVALID_TIMEFRAME_NOT_REJECTED');
  } finally {
    await page.unroute('**/api/research?**');
    await page.reload();
  }
}
