"""Stats router — GET /api/stats/{symbol}."""

from datetime import datetime, timedelta, timezone

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query, status

from analytics.data_store import DEFAULT_DB_PATH, get_stats_cache, upsert_stats_cache
from analytics.stats_lib import (
    StatsBundle,
    WeeklyCurrentState,
    compute_all,
    compute_weekly_current_state,
)
from web.api.deps import get_db, require_token
from web.api.models.stats import (
    ADRResponse,
    DOWPatternRow,
    FlipRiskConditionedRow,
    HourlyExtremeRow,
    P1P2DOWRow,
    P1P2Response,
    SessionRow,
    StatsResponse,
    WeeklyCurrentStateResponse,
    WeeklyFlipRiskConditionedResponse,
    WeeklyP1OvershootResponse,
    WeeklyP1P2Response,
    WeeklyP2TimingResponse,
    WeeklyWickWarningResponse,
)

router = APIRouter(dependencies=[Depends(require_token)])


def _wcs_to_response(wcs: WeeklyCurrentState) -> WeeklyCurrentStateResponse:
    return WeeklyCurrentStateResponse(
        current_isodow=wcs.current_isodow,
        current_dow=wcs.current_dow,
        weekly_open=wcs.weekly_open,
        current_price=wcs.current_price,
        move_pct=wcs.move_pct,
        move_bucket=wcs.move_bucket,
        low_still_ahead_conditioned=wcs.low_still_ahead_conditioned,
        high_still_ahead_conditioned=wcs.high_still_ahead_conditioned,
    )


_MYT = timezone(timedelta(hours=8))


def _bundle_to_response(bundle: StatsBundle) -> StatsResponse:
    """Convert a StatsBundle dataclass into a StatsResponse Pydantic model."""
    # P1/P2
    by_dow_list = [
        P1P2DOWRow(dow=dow, p1_low_pct=pct, sample_days=bundle.p1p2.sample_days)
        for dow, pct in bundle.p1p2.by_dow.items()
    ]
    p1p2_resp = P1P2Response(
        overall_p1_low_pct=bundle.p1p2.overall_p1_low_pct,
        by_dow=by_dow_list,
        sample_days=bundle.p1p2.sample_days,
    )

    # Hourly
    hourly_list = [
        HourlyExtremeRow(
            hour_myt=row.hour_myt,
            high_pct=row.high_pct,
            low_pct=row.low_pct,
        )
        for row in bundle.hourly.rows
    ]

    # ADR
    adr_resp = ADRResponse(
        adr_14=bundle.adr.adr_14,
        adr_30=bundle.adr.adr_30,
        today_range_pct=bundle.adr.today_range_pct,
        today_consumed_pct=bundle.adr.today_consumed_pct,
    )

    # DOW
    dow_list = [
        DOWPatternRow(
            dow=row.dow,
            avg_range_pct=row.avg_range_pct,
            bull_pct=row.bull_pct,
            sample_days=row.sample_days,
            avg_return_pct=row.avg_return_pct,
        )
        for row in bundle.dow.rows
    ]

    # Sessions
    session_list = [
        SessionRow(session=row.session, high_pct=row.high_pct, low_pct=row.low_pct)
        for row in bundle.sessions.rows
    ]

    # Weekly P1/P2
    weekly_resp = WeeklyP1P2Response(
        overall_p1_low_pct=bundle.weekly_p1p2.overall_p1_low_pct,
        low_day=bundle.weekly_p1p2.low_day,
        high_day=bundle.weekly_p1p2.high_day,
        sample_weeks=bundle.weekly_p1p2.sample_weeks,
        low_by_dow=bundle.weekly_p1p2.low_by_dow,
        high_by_dow=bundle.weekly_p1p2.high_by_dow,
    )

    # Weekly P2 timing
    p2_timing_resp = WeeklyP2TimingResponse(
        low_still_ahead_by_dow=bundle.weekly_p2_timing.low_still_ahead_by_dow,
        high_still_ahead_by_dow=bundle.weekly_p2_timing.high_still_ahead_by_dow,
        low_flip_risk_by_dow=bundle.weekly_p2_timing.low_flip_risk_by_dow,
        high_flip_risk_by_dow=bundle.weekly_p2_timing.high_flip_risk_by_dow,
    )

    # Weekly flip risk conditioned
    flip_risk_resp = WeeklyFlipRiskConditionedResponse(
        rows=[
            FlipRiskConditionedRow(
                p1_direction=r.p1_direction,
                isodow=r.isodow,
                dow_label=r.dow_label,
                flip_pct=r.flip_pct,
                sample_count=r.sample_count,
            )
            for r in bundle.weekly_flip_risk_conditioned.rows
        ]
    )

    # Weekly wick warning
    wick_resp = WeeklyWickWarningResponse(
        wick_gt_body_pct=bundle.weekly_wick_warning.wick_gt_body_pct,
        sample_count=bundle.weekly_wick_warning.sample_count,
    )

    # Weekly P1 overshoot
    overshoot_resp = WeeklyP1OvershootResponse(
        median_of_adr=bundle.weekly_p1_overshoot.median_of_adr,
        p25_of_adr=bundle.weekly_p1_overshoot.p25_of_adr,
        p75_of_adr=bundle.weekly_p1_overshoot.p75_of_adr,
        sample_count=bundle.weekly_p1_overshoot.sample_count,
    )

    return StatsResponse(
        symbol=bundle.symbol,
        days=bundle.days,
        computed_at_ms=bundle.computed_at_ms,
        p1p2=p1p2_resp,
        hourly_extremes=hourly_list,
        adr=adr_resp,
        dow_patterns=dow_list,
        sessions=session_list,
        weekly_p1p2=weekly_resp,
        weekly_p2_timing=p2_timing_resp,
        weekly_flip_risk_conditioned=flip_risk_resp,
        weekly_wick_warning=wick_resp,
        weekly_p1_overshoot=overshoot_resp,
    )


@router.get("/stats/{symbol}", response_model=StatsResponse)
def get_stats(
    symbol: str,
    days: int = Query(default=180, ge=30, le=365),
    db: duckdb.DuckDBPyConnection = Depends(get_db),
) -> StatsResponse:
    """Return BrighterData-style statistical context for a symbol.

    Results are cached by (symbol, days, MYT date) — fresh once per day.
    """
    now_myt = datetime.now(tz=_MYT)
    date_str = now_myt.strftime("%Y-%m-%d")

    # Cache hit path
    cached = get_stats_cache(db, symbol, days, date_str)
    if cached is not None:
        try:
            return StatsResponse.model_validate_json(cached)
        except Exception:
            pass  # corrupted cache — fall through to recompute

    # Cache miss — compute
    try:
        bundle = compute_all(db, symbol, days)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    response = _bundle_to_response(bundle)

    # Write to cache via a brief RW connection (shared db param is read-only)
    try:
        with duckdb.connect(str(DEFAULT_DB_PATH)) as rw_conn:
            upsert_stats_cache(
                rw_conn, symbol, days, date_str, response.model_dump_json()
            )
    except Exception:
        pass  # never fail the response due to cache write failure

    # Inject live current-week state — always fresh, never cached
    try:
        wcs = compute_weekly_current_state(db, symbol, response.adr.adr_14, days)
        if wcs is not None:
            response = response.model_copy(
                update={"weekly_current_state": _wcs_to_response(wcs)}
            )
    except Exception:
        pass  # non-fatal — degrade gracefully

    return response
