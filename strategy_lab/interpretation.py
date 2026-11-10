"""Plain-Dutch interpretation of a saved backtest/optimization run.

Every number comes from the stored manifest/result (trades or metrics). Facts that cannot be derived are
reported as such instead of being estimated.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone

DISCLAIMER_NL = ("Een historische simulatie is geen bewijs van toekomstige prestaties: het verleden herhaalt zich "
                 "niet automatisch en de uitkomst kan door toeval of aanpassing aan de data zijn ontstaan.")
EVIDENCE_LABELS_NL = {
    "HISTORICAL_BACKTEST": "Historische backtest — geen bewijs van toekomstige prestaties",
    "HISTORICAL_OOS": "Historische out-of-sample (holdout) test — sterker dan een gewone backtest, maar nog steeds "
                      "geen bewijs van toekomstige prestaties",
}
CLASSES_NL = ("Er zijn vier soorten bewijs: (1) historische backtest, (2) historische out-of-sample test op "
              "data die niet gebruikt is om te optimaliseren, (3) dagelijks vertraagde forwardtest op nieuwe "
              "data, (4) realtime papertrading — die laatste is in deze omgeving niet beschikbaar.")
MIN_TRADES_LIMITED, MIN_TRADES_OK = 30, 100


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value else None


def _eur(value):
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(value):
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + "%"


def _section(key, title, status, text, facts=None):
    return {"key": key, "title_nl": title, "status": status, "text_nl": text, "facts": facts or {}}


def _select_source(manifest, result):
    """Return (metrics, trades, evidence_class, notes, n_trials)."""
    notes = []
    trials = result.get("all_trials") or []
    if result.get("action") == "optimize" or trials or result.get("best_trials"):
        best = (result.get("best_trials") or [None])[0]
        if best is None:
            done = [t for t in trials if t.get("status") == "COMPLETED"]
            best = done[0] if done else None
        n_trials = len(trials) or None
        if best is None:
            return {}, [], "HISTORICAL_BACKTEST", ["Geen voltooide trial gevonden in deze optimalisatie."], n_trials
        test, train = best.get("testing_metrics") or {}, best.get("training_metrics") or {}
        trades = result.get("trades") or []
        if (_num(test.get("total")) or 0) > 0:
            notes.append(f"Beste trial {best.get('trial')} beoordeeld op het holdout-deel "
                         f"({_pct(_num((result.get('optimization') or {}).get('holdout_percent')) or 0)} van de data "
                         "die niet voor het optimaliseren is gebruikt).")
            return test, [], "HISTORICAL_OOS", notes, n_trials
        if train:
            notes.append(f"Beste trial {best.get('trial')}: het holdout-deel bevat geen trades, dus er is geen "
                         "bruikbare out-of-sample meting; getoond zijn de trainingsresultaten (in-sample).")
            return train, trades, "HISTORICAL_BACKTEST", notes, n_trials
        return {}, trades, "HISTORICAL_BACKTEST", ["Beste trial bevat geen metrics."], n_trials
    return result.get("metrics") or {}, result.get("trades") or [], "HISTORICAL_BACKTEST", notes, None


def _ms_to_dt(value):
    value = _num(value)
    return None if value is None else datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _period(manifest, closed):
    start, end = manifest.get("start_date"), manifest.get("end_date")
    try:
        s, e = date.fromisoformat(str(start)[:10]), date.fromisoformat(str(end)[:10])
        return (e - s).days + 1, f"{s} t/m {e}"
    except (TypeError, ValueError):
        pass
    if closed:
        s, e = closed[0][0], closed[-1][0]
        return (e - s).days + 1, f"{s.date()} t/m {e.date()}"
    return None, None


def _trade_rows(trades):
    rows = []
    for t in trades:
        pnl, closed = _num(t.get("PNL")), _ms_to_dt(t.get("closed_at"))
        if pnl is not None and closed is not None:
            rows.append((closed, pnl, _num(t.get("fee"))))
    rows.sort(key=lambda r: r[0])
    return rows


def _streak(pnls):
    longest = run = 0
    for p in pnls:
        run = run + 1 if p < 0 else 0
        longest = max(longest, run)
    return longest


def _drawdown(pnls, start_balance):
    equity = peak = start_balance
    dd_abs = dd_pct = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        drop = peak - equity
        if drop > dd_abs:
            dd_abs = drop
        if peak > 0:
            dd_pct = max(dd_pct, drop / peak * 100)
    return dd_abs, dd_pct


def interpret_run(manifest: dict, result: dict, extra: dict | None = None) -> dict:
    extra = extra or {}
    metrics, trades, evidence_class, notes, n_trials = _select_source(manifest, result)
    rows = _trade_rows(trades)
    balance = _num(metrics.get("starting_balance")) or _num(manifest.get("starting_balance")) or 0.0

    pnls = [r[1] for r in rows]
    if rows:
        count = len(rows)
        net = sum(pnls)
        wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
        win_rate = len(wins) / count
        gross_profit, gross_loss = sum(wins), sum(losses)
        fee_values = [r[2] for r in rows if r[2] is not None]
        fees = sum(fee_values) if fee_values else None
        dd_abs, dd_pct = _drawdown(pnls, balance)
        streak, largest_loss = _streak(pnls), (min(losses) if losses else 0.0)
        source = "trades"
    else:
        count = int(_num(metrics.get("total")) or 0)
        net = _num(metrics.get("net_profit"))
        win_rate = _num(metrics.get("win_rate"))
        gross_profit, gross_loss = _num(metrics.get("gross_profit")), _num(metrics.get("gross_loss"))
        fees = _num(metrics.get("fee"))
        mdd = _num(metrics.get("max_drawdown"))
        dd_pct = abs(mdd) if mdd is not None else None
        dd_abs = dd_pct / 100 * balance if dd_pct is not None and balance else None
        streak = _num(metrics.get("losing_streak"))
        largest_loss = _num(metrics.get("largest_losing_trade"))
        source = "metrics"
    profit_factor = (gross_profit / abs(gross_loss)) if gross_profit is not None and gross_loss else None
    avg_trade = net / count if net is not None and count else None
    ret_pct = net / balance * 100 if net is not None and balance else None
    top_trade = max(pnls) if pnls else _num(metrics.get("largest_winning_trade"))
    days, period_text = _period(manifest, rows)

    sections, failed = [], []

    # performance
    perf_facts = {"net_pnl": net, "return_pct": ret_pct, "trades": count, "win_rate_pct": None if win_rate is None else win_rate * 100,
                  "profit_factor": profit_factor, "average_trade": avg_trade, "starting_balance": balance or None,
                  "data_source": source}
    if count == 0 or net is None:
        sections.append(_section("performance", "Resultaat", "info",
                                 "Er zijn geen trades vastgelegd, dus er valt niets te beoordelen over het resultaat.", perf_facts))
    else:
        pf_text = (f"profit factor {profit_factor:.2f}" if profit_factor is not None else
                   ("profit factor niet te berekenen (geen verliezende trades)" if gross_loss == 0 else "profit factor niet beschikbaar"))
        text = (f"Netto resultaat {_eur(net)}" + (f" ({_pct(ret_pct)} van het startkapitaal {_eur(balance)})" if ret_pct is not None else "")
                + f" over {count} trades. Winstpercentage {_pct(win_rate * 100) if win_rate is not None else 'onbekend'}, "
                f"{pf_text}, gemiddelde trade {_eur(avg_trade)}.")
        sections.append(_section("performance", "Resultaat", "good" if net > 0 else "bad", text, perf_facts))

    # risk
    risk_facts = {"max_drawdown_abs": dd_abs, "max_drawdown_pct": dd_pct, "longest_losing_streak": streak, "largest_loss": largest_loss}
    if dd_pct is None and streak is None:
        sections.append(_section("risk", "Risico", "info", "Risicocijfers kunnen niet worden afgeleid uit de opgeslagen gegevens.", risk_facts))
    else:
        dd_text = (f"Grootste daling vanaf een top: {_eur(dd_abs)} ({_pct(dd_pct)}) " + ("berekend uit het trade-voor-trade verloop. " if source == "trades" else "volgens de opgeslagen metrics. ")) if dd_pct is not None else "Drawdown niet beschikbaar. "
        text = (dd_text + (f"Langste reeks verliezende trades: {int(streak)}. " if streak is not None else "")
                + (f"Grootste verlies op één trade: {_eur(largest_loss)}." if largest_loss is not None else ""))
        status = "info" if dd_pct is None else "good" if dd_pct <= 10 else "warn" if dd_pct <= 20 else "bad"
        sections.append(_section("risk", "Risico", status, text, risk_facts))

    # weak periods
    by_year, by_month = defaultdict(float), defaultdict(float)
    for closed, pnl, _ in rows:
        by_year[closed.year] += pnl
        by_month[f"{closed.year}-{closed.month:02d}"] += pnl
    wp_facts = {"pnl_by_year": dict(sorted(by_year.items())), "pnl_by_month": dict(sorted(by_month.items()))}
    if not rows:
        sections.append(_section("weak_periods", "Zwakke periodes", "info",
                                 "Geen trade-voor-trade gegevens opgeslagen, dus zwakke jaren en maanden zijn niet te bepalen.", wp_facts))
    elif days is not None and days < 62:
        sections.append(_section("weak_periods", "Zwakke periodes", "info",
                                 f"De periode ({days} dag(en)) is te kort om zwakke jaren of maanden te beoordelen.", wp_facts))
    else:
        worst_y, worst_m = min(by_year, key=by_year.get), min(by_month, key=by_month.get)
        negative = sorted(y for y, v in by_year.items() if v < 0)
        text = (f"Slechtste jaar: {worst_y} ({_eur(by_year[worst_y])}). Slechtste maand: {worst_m} ({_eur(by_month[worst_m])}). "
                + (f"Verliesgevende jaren: {', '.join(map(str, negative))}." if negative else "Geen enkel jaar sloot negatief af."))
        wp_facts.update(worst_year=worst_y, worst_month=worst_m, negative_years=negative)
        sections.append(_section("weak_periods", "Zwakke periodes", "warn" if negative else "good", text, wp_facts))

    # evidence
    ev_facts = {"trades": count, "period_days": days, "period": period_text, "symbol": manifest.get("symbol"),
                "timeframe": manifest.get("timeframe"), "trials_tested": n_trials}
    if count < MIN_TRADES_LIMITED:
        ev_status, ev_level = "bad", f"Zeer weinig bewijs: slechts {count} trades (minder dan {MIN_TRADES_LIMITED})."
    elif count <= MIN_TRADES_OK:
        ev_status, ev_level = "warn", f"Beperkt bewijs: {count} trades (tussen {MIN_TRADES_LIMITED} en {MIN_TRADES_OK})."
    else:
        ev_status, ev_level = "good", f"Redelijke hoeveelheid bewijs: {count} trades (meer dan {MIN_TRADES_OK})."
    ev_text = ev_level + (f" Periode: {period_text} ({days} dag(en))." if days else " Periodelengte onbekend.")
    ev_text += f" Getest op één markt ({manifest.get('symbol', 'onbekend')}) en één timeframe ({manifest.get('timeframe', 'onbekend')}); dat zegt niets over andere markten."
    if n_trials:
        ev_text += (f" Deze uitkomst is de beste van {n_trials} geteste trial(s); kiezen van de beste uit meerdere "
                    "pogingen overschat de prestatie.")
    ev_text += " " + " ".join(notes)
    sections.append(_section("evidence", "Hoeveelheid bewijs", ev_status, ev_text.strip(), ev_facts))

    # costs
    cost_facts = {"total_fees": fees, "net_pnl": net, "gross_pnl": None, "pnl_if_costs_doubled": None}
    flips = False
    if fees is None or net is None:
        sections.append(_section("costs", "Kosten", "info", "Kosten niet afzonderlijk vastgelegd; gevoeligheid voor hogere kosten is niet te berekenen.", cost_facts))
    else:
        gross, doubled = net + fees, net - fees
        flips = net > 0 and doubled < 0
        cost_facts.update(gross_pnl=gross, pnl_if_costs_doubled=doubled)
        text = (f"Betaalde kosten: {_eur(fees)}; resultaat voor kosten {_eur(gross)}, na kosten {_eur(net)}. "
                f"Bij verdubbelde kosten zou het resultaat {_eur(doubled)} zijn"
                + (" — de winst slaat dus om in verlies." if flips else "."))
        sections.append(_section("costs", "Kosten", "bad" if flips else ("warn" if net <= 0 else "good"), text, cost_facts))

    # failed checks
    top_share = (top_trade / net * 100) if (rows and net and net > 0 and top_trade and top_trade > 0) else None
    if net is not None and net < 0:
        failed.append("Negatief nettoresultaat.")
    if profit_factor is not None and profit_factor < 1.1:
        failed.append(f"Profit factor {profit_factor:.2f} is lager dan 1,1.")
    if count < MIN_TRADES_LIMITED:
        failed.append(f"Minder dan {MIN_TRADES_LIMITED} trades ({count}).")
    if dd_pct is not None and balance and dd_abs is not None and dd_abs / balance * 100 > 20:
        failed.append(f"Maximale drawdown is {_pct(dd_abs / balance * 100)} van het startkapitaal (grens 20%).")
    if top_share is not None and top_share > 50:
        failed.append(f"De winst hangt van één trade af: die levert {_pct(top_share)} van de nettowinst.")
    if flips:
        failed.append("Bij verdubbelde kosten wordt het resultaat negatief.")
    fc_text = ("Gefaalde controles: " + " ".join(failed)) if failed else "Geen van de heuristische controles is gefaald (dat is geen bewijs dat de strategie werkt)."
    sections.append(_section("failed_checks", "Gefaalde controles", "bad" if failed else "good", fc_text,
                             {"failed": failed, "top_trade_share_pct": top_share}))

    # missing research steps
    missing = []
    if not extra.get("optimization_done"):
        missing.append("Optimalisatie")
    if not extra.get("validation_done"):
        missing.append("Statistische validatie (walk-forward, Monte Carlo)")
    if not extra.get("forward_registered"):
        missing.append("Forwardtest")
    if extra:
        m_text = ("Nog niet gedaan voor deze strategie: " + ", ".join(missing) + "." if missing
                  else "Optimalisatie, validatie en forwardtest zijn al vastgelegd.")
    else:
        m_text = "Of er al optimalisatie, validatie of een forwardtest is gedaan kon niet worden gecontroleerd."
        missing = []
    m_text += " " + CLASSES_NL
    sections.append(_section("missing", "Ontbrekende stappen", "warn" if missing else "info", m_text,
                             {"missing": missing, "realtime_papertrading_available": False}))

    # headline and next steps
    if count == 0 or net is None:
        headline = "Geen trades vastgelegd: er is niets om te beoordelen."
    else:
        verdict = "winstgevend" if net > 0 else "verliesgevend"
        headline = (f"Historisch {verdict} ({_eur(net)}) op {count} trades; "
                    + ("bewijs is zwak" if ev_status == "bad" else "bewijs is beperkt" if ev_status == "warn" else "bewijs is redelijk")
                    + (f" en {len(failed)} controle(s) gefaald." if failed else "."))
    steps = []
    if count < MIN_TRADES_LIMITED:
        steps.append("Test over een langere periode om meer trades te krijgen.")
    if "Optimalisatie" in missing:
        steps.append("Voer een optimalisatie uit met een apart holdout-deel.")
    if any(m.startswith("Statistische") for m in missing):
        steps.append("Draai statistische validatie (walk-forward en Monte Carlo).")
    if "Forwardtest" in missing:
        steps.append("Registreer de strategie voor een dagelijks vertraagde forwardtest.")
    if flips:
        steps.append("Controleer de kostenaannames; de winst verdwijnt bij hogere kosten.")
    if net is not None and net < 0:
        steps.append("Heroverweeg de strategie-logica voordat je verder optimaliseert.")
    steps.append("Behandel dit resultaat als hypothese, niet als bewijs.")

    return {"evidence_class": evidence_class, "evidence_label_nl": EVIDENCE_LABELS_NL[evidence_class],
            "headline_nl": headline, "warning_nl": DISCLAIMER_NL, "sections": sections, "next_steps_nl": steps}


def research_status(manifest: dict, runs_root, *, ledger=None, lifecycle=None, forward_db=None) -> dict:
    """Cheap lookups by strategy name; every failed lookup counts as 'not found', never as an error."""
    import json
    import sqlite3
    from contextlib import closing
    from pathlib import Path

    strategy, symbol = manifest.get("strategy"), manifest.get("symbol")
    optimization = manifest.get("action") == "optimize"
    try:
        for path in Path(runs_root).glob("*/manifest.json"):
            if optimization:
                break
            other = json.loads(path.read_text(encoding="utf-8"))
            optimization = (other.get("action") == "optimize" and other.get("strategy") == strategy
                            and other.get("symbol") == symbol)
    except (OSError, ValueError):
        pass
    validation = False
    try:
        if ledger is not None:
            families = {strategy, f"plugin:{strategy}"}
            validation = any(e.get("family") in families and (not symbol or e.get("symbol") in (None, symbol))
                             for e in ledger.list(kind="VALIDATION", limit=1000))
    except Exception:
        pass
    forward = False
    try:
        if lifecycle is not None:
            record = lifecycle.get(strategy)
            forward = bool(record and any(h.get("to_status") == "FORWARD_TESTING" for h in record["history"]))
    except Exception:
        pass
    try:
        if not forward and forward_db and Path(forward_db).is_file():
            uri = Path(forward_db).resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
                for (blob,) in db.execute("SELECT configuration FROM frozen_strategies"):
                    if json.loads(blob).get("strategy") == strategy:
                        forward = True
                        break
    except Exception:
        pass
    return {"optimization_done": optimization, "validation_done": validation, "forward_registered": forward}
