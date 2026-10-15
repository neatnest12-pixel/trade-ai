import numpy as np
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)


def prediction_metrics(actual:list[int],probabilities:list[float])->dict[str,float|None]:
    if not actual:return {"number_of_predictions":0}
    y=np.array(actual); p=np.array(probabilities); pred=(p>=.5).astype(int)
    return {"number_of_predictions":len(y),"directional_accuracy":accuracy_score(y,pred),
      "precision":precision_score(y,pred,zero_division=0),"recall":recall_score(y,pred,zero_division=0),
      "f1":f1_score(y,pred,zero_division=0),"roc_auc":roc_auc_score(y,p) if len(np.unique(y))>1 else None,
      "brier_score":brier_score_loss(y,p),"log_loss":log_loss(y,p,labels=[0,1])}


def paper_performance(rows: list[object]) -> dict[str, float | int | None]:
    """Paper-only summary. Unevaluated predictions are never treated as outcomes."""
    decisions = [row.decision for row in rows]
    evaluated = [row for row in rows if row.evaluated_at is not None]
    correct = sum(bool(row.direction_correct) for row in evaluated)
    incorrect = len(evaluated) - correct
    returns = [1.0 if row.direction_correct else -1.0 for row in evaluated]
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    return {
        "label": "PAPER / UNVALIDATED",
        "predictions": len(rows), "buy_signals": decisions.count("BUY"),
        "sell_signals": decisions.count("SELL"),
        "no_trade": sum(decision in {"NO_TRADE", "NO TRADE"} for decision in decisions),
        "correct": correct, "incorrect": incorrect,
        "win_rate": correct / len(evaluated) if evaluated else None,
        "average_win": float(np.mean(wins)) if wins else None,
        "average_loss": float(np.mean(losses)) if losses else None,
        "expectancy": float(np.mean(returns)) if returns else None,
        "max_paper_drawdown_r": abs(drawdown),
        "evaluated": len(evaluated),
    }
