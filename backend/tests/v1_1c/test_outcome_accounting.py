from decimal import Decimal

from app.v1_1.contract import STARTING_BALANCE, evaluate_candle
from app.v1_1c.contract import freeze_trade


def trade(side: str):
    return freeze_trade(prediction_id="p", symbol="EURUSD", timeframe="M1", prediction_timestamp="t",
                        candle_timestamp="c", decision=side, entry_price=Decimal("1.1"),
                        atr_14_price=Decimal(".001"), balance_before_trade=STARTING_BALANCE,
                        confidence=Decimal("50"))


def test_buy_sell_profit_loss_and_breakeven_accounting():
    buy = trade("BUY")
    sell = trade("SELL")
    buy_win = evaluate_candle(buy, high=Decimal("1.101"), low=Decimal("1.1"), close=Decimal("1.101"), exit_timestamp="t", elapsed_candles=5, peak_balance=STARTING_BALANCE)
    buy_loss = evaluate_candle(buy, high=Decimal("1.1"), low=Decimal("1.099"), close=Decimal("1.099"), exit_timestamp="t", elapsed_candles=5, peak_balance=STARTING_BALANCE)
    sell_win = evaluate_candle(sell, high=Decimal("1.1"), low=Decimal("1.099"), close=Decimal("1.099"), exit_timestamp="t", elapsed_candles=5, peak_balance=STARTING_BALANCE)
    sell_loss = evaluate_candle(sell, high=Decimal("1.101"), low=Decimal("1.1"), close=Decimal("1.101"), exit_timestamp="t", elapsed_candles=5, peak_balance=STARTING_BALANCE)
    flat = evaluate_candle(buy, high=Decimal("1.1005"), low=Decimal("1.0995"), close=Decimal("1.1"), exit_timestamp="t", elapsed_candles=5, peak_balance=STARTING_BALANCE)
    assert [outcome.realized_r for outcome in (buy_win, buy_loss, sell_win, sell_loss, flat)] == [Decimal("1"), Decimal("-1"), Decimal("1"), Decimal("-1"), Decimal("0")]
    assert flat.realized_pnl_rupees == Decimal("0") and flat.balance_after_trade == STARTING_BALANCE


def test_time_exit_uses_completed_candle_close_not_entry():
    outcome = evaluate_candle(trade("BUY"), high=Decimal("1.1006"), low=Decimal("1.0997"), close=Decimal("1.1005"), exit_timestamp="t", elapsed_candles=5, peak_balance=STARTING_BALANCE)
    assert outcome.status == "TIME_EXIT"
    assert outcome.exit_price == Decimal("1.1005")
    assert outcome.realized_r == Decimal("0.5")
    assert outcome.realized_pnl_rupees == Decimal("12.5")
