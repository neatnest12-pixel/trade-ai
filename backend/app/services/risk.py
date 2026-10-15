from app.domain.schemas import RiskRequest


def calculate_risk(req:RiskRequest)->dict[str,float]:
    risk_amount=req.account_balance*req.risk_percent/100
    loss_per_unit=abs(req.entry-req.stop)*req.contract_size*req.quote_to_account_rate
    lots=risk_amount/loss_per_unit
    lot_step=0.01
    lots=int((lots / lot_step) + 1e-9) * lot_step
    return {"risk_amount":risk_amount,"position_size_lots":lots,"stop_distance":abs(req.entry-req.stop),
            "lot_step":lot_step,"notice":"Theoretical sizing only. No order is sent. Verify venue-specific contract and conversion specifications."}
