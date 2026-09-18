from edgehunter.research.btc_intraday import IntradayObservation
from edgehunter.research.learned_intraday import fit_logistic, predict_up


def row(index: int, rising: bool) -> IntradayObservation:
    return IntradayObservation(
        slug=f"btc-{index}", epoch=index * 300, horizon_seconds=120,
        open_price=100.0, spot_price=101.0 if rising else 99.0,
        vol_per_second=0.0002, up_price=0.55 if rising else 0.45,
        down_price=0.45 if rising else 0.55, outcome_up=int(rising),
        effective_horizon_seconds=180,
    )


def test_regularized_model_learns_only_from_rows_before_declared_cutoff():
    rows = [row(index, index % 2 == 0) for index in range(40)]
    model = fit_logistic(rows, horizon_seconds=120, ridge=1.0, training_end_epoch=rows[-1].epoch)
    assert model.training_rows == 40
    assert predict_up(model, row(100, True)) > 0.9
    assert predict_up(model, row(101, False)) < 0.1


def test_fit_rejects_future_training_row():
    rows = [row(index, index % 2 == 0) for index in range(40)]
    try:
        fit_logistic(rows, horizon_seconds=120, ridge=1.0, training_end_epoch=rows[-2].epoch)
    except ValueError as error:
        assert "cutoff" in str(error)
    else:
        raise AssertionError("future row was accepted")
