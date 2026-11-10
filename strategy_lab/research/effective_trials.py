"""Multiple-testing inputs for the Deflated Sharpe Ratio: raw and effective number of trials, the cross-trial
Sharpe variance V in the units of the tested Sharpe, effective sample length, and DSR records per scope.

References
* Bailey & Lopez de Prado (2012), "The Sharpe Ratio Efficient Frontier", J. of Risk 15(2) - PSR, MinTRL.
* Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio", J. of Portfolio Management 40(5):
      SR0 = sqrt(V) * ((1 - g) * Phi^-1(1 - 1/N) + g * Phi^-1(1 - 1/(N e)))
  V = variance of the trials' Sharpe estimates, in the SAME units (per observation, same horizon) as the tested SR.
* Lopez de Prado (2019), "A Data Science Solution to the Multiple-Testing Crisis in Financial Research",
  J. of Financial Data Science 1(1) - effective number of trials from clustering (ONC) of trial returns.
* Li & Ji (2005), Heredity 95 - effective number of tests from the eigenvalues of the correlation matrix.
* Lo (2002), "The Statistics of Sharpe Ratios", FAJ 58(4) - sampling variance (1 + SR^2/2)/T, serial correlation.

Why V9 produced DSR = 0.00 for every candidate: V was the variance of annualised Sharpe values of ALL ledger rows
(3-month window rungs, full-period rungs, holdout rows, -16..+4 annualised). Short-window Sharpe estimates are
dominated by sampling noise (variance ~1/T_i with T_i ~ 60 days), so V was ~two orders of magnitude larger than the
dispersion a 6-year out-of-sample series can have; SR0 became ~10 annualised and no strategy can clear that.
Here V comes from Sharpe estimates in the same units, period and horizon as the tested series (other walk-forward
OOS evaluations on the validation period), rebuilt for the tested horizon by the method of moments:
V = max(0, Var(SR_i) - mean(noise_i)) + 1/T_test (cross-trial variance = true dispersion + sampling noise).
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

from . import validation

EULER_GAMMA = validation.EULER_GAMMA
INSUFFICIENT = validation.INSUFFICIENT
MIN_TRIAL_OBS = 120          # trials with a shorter evaluation horizon are not used for V
TRADING_DAYS = 252
METHOD_VERSION = "DSR_V10"     # record layout version; V10.1 adds variants/reliability (additive)


# ------------------------------------------------------------------------------------------------ sample stats
def sharpe_stats(returns) -> dict:
    """Per-observation Sharpe, T, skewness and NON-excess kurtosis (3 for a normal) of a return series."""
    m = validation._moments(returns)
    return {"sr": float(m["sr"]), "T": int(m["n"]), "skew": float(m["skew"]), "kurt": float(m["kurt"]),
            "std": float(m["std"])}


def effective_obs(returns, max_lag: int | None = None) -> dict:
    """Effective number of independent observations under serial correlation (Bartlett-weighted autocorrelations,
    as in the Newey-West / Lo (2002) adjustment): T_eff = T / (1 + 2 * sum_k (1 - k/(q+1)) rho_k).

    Negative autocorrelation would give T_eff > T; it is capped at T (never claims more data than observed)."""
    x = np.asarray(returns, dtype=float)
    x = x[np.isfinite(x)]
    T = len(x)
    out = {"T": T, "T_eff": float(T), "max_lag": 0, "rho": [], "inflation": 1.0}
    if T < 10 or x.std() == 0:
        return out
    q = int(max_lag if max_lag is not None else max(1, math.floor(4 * (T / 100.0) ** (2.0 / 9.0))))
    q = min(q, T // 4)
    xc = x - x.mean()
    den = float((xc * xc).sum())
    rho = [float((xc[k:] * xc[:-k]).sum() / den) for k in range(1, q + 1)]
    infl = 1.0 + 2.0 * sum((1 - k / (q + 1.0)) * r for k, r in enumerate(rho, start=1))
    infl = max(infl, 1.0)
    out.update(T_eff=float(T / infl), max_lag=q, rho=rho, inflation=float(infl))
    return out


def sr_sampling_variance(sr: float, T: float, skew: float = 0.0, kurt: float = 3.0) -> float:
    """Asymptotic variance of a per-observation Sharpe estimate (Mertens 2002 / Bailey-LdP 2012):
    (1 - g3 SR + (g4 - 1)/4 SR^2) / T. Reduces to (1 + SR^2/2)/T for normal returns (Lo 2002)."""
    if not T or T <= 1:
        return math.inf
    return max(1e-18, 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr) / float(T)


# ------------------------------------------------------------------------------------------------ SR0 and DSR
def expected_max_sr(n_trials: float, variance: float) -> float:
    """BLdP (2014) eq. for E[max SR] of N skill-less trials. N may be fractional (effective N). For 1 <= N < 2 the
    asymptotic formula is interpolated linearly between E[max of 1] = 0 and its N = 2 value."""
    if variance is None or variance <= 0 or n_trials is None or n_trials <= 1:
        return 0.0
    if n_trials < 2:
        return (n_trials - 1.0) * expected_max_sr(2.0, variance)
    z1 = validation.norm_ppf(1.0 - 1.0 / n_trials)
    z2 = validation.norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(variance) * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def psr(sr: float, T: float, skew: float, kurt: float, sr0: float) -> float:
    """PSR(SR0) = Phi((SR - SR0) sqrt(T - 1) / sqrt(1 - g3 SR + (g4-1)/4 SR^2)), BLdP (2012) eq. 9; T may be T_eff."""
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    denom = max(denom, 1e-12)
    return validation.norm_cdf((sr - sr0) * math.sqrt(max(T - 1.0, 0.0)) / math.sqrt(denom))


# ------------------------------------------------------------------------------------------------ V
def trial_variance(trial_srs: Iterable, trial_T: Iterable, T_target: float, *, min_T: int = MIN_TRIAL_OBS,
                   units: str = "per_observation_daily") -> dict:
    """Cross-trial Sharpe variance for the tested horizon ``T_target``.

    ``trial_srs`` are per-observation Sharpe estimates (same observation frequency as the tested series, e.g.
    daily), ``trial_T`` their sample lengths. Method of moments:
        Var(SR_i) = Var(true SR) + E[sampling noise_i],  noise_i = (1 + SR_i^2/2) / T_i
        V(T_target) = max(0, Var(SR_i) - mean(noise_i)) + 1 / T_target
    The 1/T_target term is the null sampling variance of the tested estimate itself, so V never falls below what a
    skill-less trial on this horizon would show."""
    srs = np.asarray(list(trial_srs), dtype=float)
    Ts = np.asarray(list(trial_T), dtype=float)
    ok = np.isfinite(srs) & np.isfinite(Ts) & (Ts >= min_T)
    used, Tu = srs[ok], Ts[ok]
    floor = 1.0 / float(T_target) if T_target and T_target > 1 else None
    out = {"units": units, "method": "method_of_moments_rescaled_to_T_test", "n_trials_input": int(len(srs)),
           "n_used": int(ok.sum()), "n_excluded_short_or_unknown_T": int((~ok).sum()), "min_T": int(min_T),
           "T_target": None if T_target is None else float(T_target), "null_floor": floor}
    if floor is None:
        out.update(V=None, status=INSUFFICIENT)
        return out
    if len(used) < 2:
        out.update(V=floor, raw_variance=None, mean_noise=None, true_dispersion=0.0, status="NULL_FLOOR_ONLY",
                   note="minder dan 2 vergelijkbare trials: V = nulvariantie 1/T van de geteste reeks")
        return out
    raw = float(np.var(used, ddof=1))
    noise = float(np.mean((1.0 + 0.5 * used ** 2) / Tu))
    disp = max(0.0, raw - noise)
    out.update(V=disp + floor, raw_variance=raw, mean_noise=noise, true_dispersion=disp, status="OK",
               median_T=float(np.median(Tu)))
    return out


# ------------------------------------------------------------------------------------------------ effective N
def _corr(frame_or_matrix) -> np.ndarray:
    if isinstance(frame_or_matrix, pd.DataFrame):
        f = frame_or_matrix.fillna(0.0)
        f = f.loc[:, f.std(ddof=0) > 0]
        if f.shape[1] == 0:
            return np.zeros((0, 0))
        c = np.corrcoef(f.to_numpy(float), rowvar=False)
    else:
        c = np.asarray(frame_or_matrix, dtype=float)
    c = np.atleast_2d(np.nan_to_num(c, nan=0.0))
    np.fill_diagonal(c, 1.0)
    return np.clip((c + c.T) / 2.0, -1.0, 1.0)


def effective_n_eigen(corr) -> float:
    """Li & Ji (2005): M_eff = sum_i f(|lambda_i|), f(x) = 1[x >= 1] + (x - floor(x)). Identity -> N, all-ones -> 1."""
    c = _corr(corr)
    if c.size == 0:
        return 0.0
    lam = np.abs(np.linalg.eigvalsh(c))
    return float(sum((1.0 if x >= 1 else 0.0) + (x - math.floor(x)) for x in lam))


def _silhouette(d: np.ndarray, labels: np.ndarray) -> np.ndarray:
    labels = np.asarray(labels)
    uniq, inv = np.unique(labels, return_inverse=True)
    if len(uniq) < 2:
        return np.zeros(len(labels))
    onehot = np.eye(len(uniq))[inv]
    sums = d @ onehot
    cnt = onehot.sum(axis=0)
    own = sums[np.arange(len(labels)), inv]
    own_cnt = cnt[inv] - 1
    a = np.where(own_cnt > 0, own / np.maximum(own_cnt, 1), 0.0)
    mean_other = sums / cnt
    mean_other[np.arange(len(labels)), inv] = np.inf
    b = mean_other.min(axis=1)
    m = np.maximum(a, b)
    return np.where((own_cnt > 0) & (m > 0), (b - a) / np.where(m > 0, m, 1), 0.0)


def effective_n_onc(corr, max_k: int | None = None, n_init: int = 5, seed: int = 0) -> dict:
    """ONC-style clustering (Lopez de Prado 2019): distance d = sqrt((1 - rho)/2), k-means on the rows of d for
    k = 2..max_k with ``n_init`` restarts, choose the k with the highest silhouette t-stat mean(s)/std(s).
    When every pair is highly correlated (min rho >= 0.9) the trials are one cluster. Returns k and labels."""
    from scipy.cluster.vq import kmeans2
    c = _corr(corr)
    n = c.shape[0]
    if n == 0:
        return {"k": 0, "labels": [], "quality": None, "method": "onc"}
    if n == 1:
        return {"k": 1, "labels": [0], "quality": None, "method": "onc"}
    off = c[~np.eye(n, dtype=bool)]
    if off.min() >= 0.9:
        return {"k": 1, "labels": [0] * n, "quality": None, "method": "onc", "note": "alle paren rho >= 0.9"}
    d = np.sqrt(np.clip((1.0 - c) / 2.0, 0.0, 1.0))
    max_k = min(n - 1, max_k or max(2, min(40, n // 2)))
    rng = np.random.default_rng(seed)
    best = {"k": n, "labels": list(range(n)), "quality": -math.inf}
    for k in range(2, max_k + 1):
        for _ in range(n_init):
            try:
                _, lab = kmeans2(d, k, minit="++", seed=int(rng.integers(0, 2 ** 31 - 1)))
            except Exception:
                continue
            if len(np.unique(lab)) < 2:
                continue
            s = _silhouette(d, lab)
            q = float(s.mean() / s.std()) if s.std() > 0 else float(s.mean() * 1e6)
            if q > best["quality"]:
                best = {"k": int(len(np.unique(lab))), "labels": lab.tolist(), "quality": q}
    best["method"] = "onc"
    return best


def effective_n(frame_or_corr, seed: int = 0) -> dict:
    """Effective number of independent trials from their return series (columns) or a correlation matrix.
    Reports eigenvalue (Li-Ji) and ONC cluster estimates; ``n_eff`` takes the LARGER of the two (more trials =
    larger SR0 = conservative), never more than the raw count."""
    c = _corr(frame_or_corr)
    n = c.shape[0]
    if n == 0:
        return {"n_raw": 0, "n_eff": 0.0, "eigen": 0.0, "onc_k": 0, "method": "none"}
    eig = effective_n_eigen(c)
    onc = effective_n_onc(c, seed=seed) if n >= 3 else {"k": n if n == 1 or c[0, 1] < 0.9 else 1}
    n_eff = float(min(n, max(eig, onc["k"])))
    return {"n_raw": int(n), "n_eff": n_eff, "eigen": eig, "onc_k": int(onc["k"]),
            "mean_abs_corr": float(np.abs(c[~np.eye(n, dtype=bool)]).mean()) if n > 1 else 0.0,
            "method": "max(eigen Li-Ji 2005, ONC clusters LdP 2019)"}


def two_level_effective_n(groups: list[dict], between: dict | None) -> dict:
    """Search-level effective N when not every trial has a stored return series.

    ``groups``: one per candidate (signature|market|timeframe) with ``n_trials`` (distinct configurations) and
    optionally ``n_eff_within`` (measured from the candidate's own trial series). Unmeasured candidates use the
    median measured within-ratio (1.0 = raw count if nothing was measured). ``between``: effective_n() over one
    representative series per candidate (where available); its ratio n_eff / n_raw is applied to the total.
        N_eff = ratio_between * sum_c m_c
    Approximation (documented): assumes the redundancy among candidates with a stored series is representative."""
    ratios = [g["n_eff_within"] / g["n_trials"] for g in groups
              if g.get("n_eff_within") and g.get("n_trials") and g["n_trials"] > 1]
    within_ratio = float(np.median(ratios)) if ratios else 1.0
    total, measured = 0.0, 0
    for g in groups:
        n = max(1, int(g.get("n_trials") or 1))
        if g.get("n_eff_within"):
            total += min(float(g["n_eff_within"]), n)
            measured += 1
        else:
            total += max(1.0, n * within_ratio)
    between_ratio = 1.0
    if between and between.get("n_raw", 0) >= 2:
        between_ratio = float(between["n_eff"]) / float(between["n_raw"])
    n_raw = int(sum(max(1, int(g.get("n_trials") or 1)) for g in groups))
    n_eff = float(min(n_raw, max(1.0, between_ratio * total)))
    return {"n_raw": n_raw, "n_eff": n_eff, "n_groups": len(groups), "groups_measured_within": measured,
            "within_ratio_default": within_ratio, "between_ratio": between_ratio,
            "between_n_series": int((between or {}).get("n_raw", 0)),
            "method": "two_level: ratio_between * sum(within effective trials)"}


# ------------------------------------------------------------------------------------------------ DSR record
LIMITATIONS = (
    "DSR veronderstelt dat de trials uit dezelfde verdeling komen en dat V hun spreiding goed beschrijft.",
    "Effectieve N is een schatting (correlatie van trial-rendementen); bij weinig opgeslagen reeksen wordt een "
    "verhouding uit gemeten kandidaten toegepast op ongemeten kandidaten.",
    "Seriele afhankelijkheid is verwerkt via T_eff (Bartlett); regime-afhankelijkheid niet.",
    "De poort gebruikt V_null = 1/T_eff (spreiding van een vaardigheidsloze trial); de spreiding over trials "
    "(V_paper) wordt gerapporteerd als gevoeligheid (value_paper_v).",
    "Trials die nooit in het logboek kwamen (handmatig onderzoek buiten de app) zijn niet meegeteld.",
)


def dsr_record(returns, *, n_raw: int, n_eff: float | None, variance: dict, scope: str,
               annualization: float = TRADING_DAYS, use_effective_n: bool = True, v_policy: str = "null",
               extra: dict | None = None) -> dict:
    """Full, auditable DSR result for one tested return series and one multiple-testing scope.

    Two V's are computed and both reported:
    * V_paper - the cross-trial variance of Sharpe estimates (BLdP 2014 definition, :func:`validation_variance`).
    * V_null  - the sampling variance of a skill-less trial's Sharpe on this horizon, 1/T_eff (Mertens 2002 at
                SR = 0; autocorrelation through T_eff). This is the dispersion BLdP's SR0 assumes: E[max] of N
                skill-less trials.
    ``v_policy="null"`` (default, used by the gate) uses V_null; ``"paper"`` uses max(V_paper, V_null).
    Why the gate uses V_null: V_paper equals V_null only when the trials have no skill. When trials have genuinely
    different true Sharpe ratios (cost-dominated losers, or several skilled variants) V_paper measures that
    real dispersion, not luck, and SR0 then penalises the best strategy for the existence of other strategies
    (the V9 failure: SR0 ~ 4.7 annualised). Multiplicity is carried by the (effective) number of trials N, which
    counts every trial of the search. The V_paper-based DSR is always reported as ``value_paper_v``."""
    st = sharpe_stats(returns)
    eo = effective_obs(returns)
    V_paper = variance.get("V")
    V_null = 1.0 / max(eo["T_eff"], 1.0) if st["T"] else None
    V = V_null if v_policy == "null" else (max(V_paper or 0.0, V_null or 0.0) or None)
    N = float(n_eff) if (use_effective_n and n_eff) else float(n_raw)
    out = {"scope": scope, "method": METHOD_VERSION, "status": "OK", "value": None, "v_policy": v_policy,
           "observed_sr": st["sr"], "observed_sr_annualized": st["sr"] * math.sqrt(annualization),
           "T": st["T"], "T_eff": eo["T_eff"], "autocorr_inflation": eo["inflation"],
           "skew": st["skew"], "kurtosis": st["kurt"], "raw_n": int(n_raw),
           "effective_n": None if n_eff is None else float(n_eff), "n_used": N, "V": V, "V_null": V_null,
           "V_paper": V_paper, "V_detail": variance,
           "units": "SR per observation (daily); V in the same units for horizon T",
           "limitations": list(LIMITATIONS), **(extra or {})}
    if st["T"] < validation.MIN_OBS or st["std"] == 0 or V is None:
        out.update(status=INSUFFICIENT, sr0=None)
        return out
    sr0 = expected_max_sr(N, V)
    out["sr0"] = sr0
    out["sr0_annualized"] = sr0 * math.sqrt(annualization)
    out["value"] = psr(st["sr"], eo["T_eff"], st["skew"], st["kurt"], sr0)
    out["value_raw_n"] = psr(st["sr"], eo["T_eff"], st["skew"], st["kurt"], expected_max_sr(float(n_raw), V))
    out["value_iid_T"] = psr(st["sr"], st["T"], st["skew"], st["kurt"], sr0)
    out["variants"] = dsr_variants(st, eo, n_raw=n_raw, n_eff=N, V_paper=V_paper,
                                   V_paper_status=variance.get("status"), annualization=annualization)
    out["gate_variant"] = GATE_VARIANT if v_policy == "null" else "paper_max"
    out["reliability"] = reliability(st, eo, V_paper_status=variance.get("status"))
    if V_paper:
        vp = max(V_paper, V_null)
        out["sr0_paper_v_annualized"] = expected_max_sr(N, vp) * math.sqrt(annualization)
        out["value_paper_v"] = psr(st["sr"], eo["T_eff"], st["skew"], st["kurt"], expected_max_sr(N, vp))
    return out


# ------------------------------------------------------------------------------------------------ V10.1 variants
VARIANTS = {
    "paper_dsr": "Paper-DSR (BLdP 2014): V = spreiding van Sharpe-schattingen tussen trials, ruwe N, T",
    "nc_dsr": "Null-calibrated DSR (NC-DSR): V = 1/T_eff (vaardigheidsloze trial), effectieve N, T_eff",
    "dep_dsr": "Afhankelijkheidsbewuste DSR: V = spreiding tussen trials, effectieve N, T_eff",
}
GATE_VARIANT = "nc_dsr"
MIN_T_EFF = 60               # below this the Gaussian approximation of the SR estimate is not trusted
MIN_INDEPENDENT_TRADES = 30


def reliability(st: dict, eo: dict, *, n_trades: int | None = None, V_paper_status: str | None = None) -> dict:
    """When a DSR is not interpretable it is INSUFFICIENT_EVIDENCE, never a FAIL. Reasons (Dutch, for the UI):
    too few effective observations, too few trades, PSR denominator not positive (extreme skew/kurtosis for the
    observed SR: the asymptotic variance formula breaks down), paper variants without a cross-trial V."""
    reasons = []
    if eo["T_eff"] < MIN_T_EFF:
        reasons.append(f"effectieve steekproef {eo['T_eff']:.0f} < {MIN_T_EFF} waarnemingen")
    if n_trades is not None and n_trades < MIN_INDEPENDENT_TRADES:
        reasons.append(f"{n_trades} trades < {MIN_INDEPENDENT_TRADES}")
    denom = 1.0 - st["skew"] * st["sr"] + (st["kurt"] - 1.0) / 4.0 * st["sr"] ** 2
    if denom <= 0.05:
        reasons.append("scheefheid/kurtosis maken de PSR-variantie onbetrouwbaar")
    paper_ok = V_paper_status == "OK"
    return {"reliable": not reasons, "reasons_nl": reasons, "paper_v_available": paper_ok,
            "notes_nl": [] if paper_ok else ["minder dan 2 vergelijkbare trials: paper-V niet schatbaar, "
                                              "paper-varianten = INSUFFICIENT_EVIDENCE"]}


def dsr_variants(st: dict, eo: dict, *, n_raw: float, n_eff: float, V_paper: float | None,
                 V_paper_status: str | None = "OK", annualization: float = TRADING_DAYS) -> dict:
    """The three DSR definitions side by side (same SR, skew, kurtosis). Each carries SR0, N, V, T, the PSR value,
    the standard error of the SR estimate and a 95% interval. Paper variants need a cross-trial V."""
    sr, sk, ku = st["sr"], st["skew"], st["kurt"]
    V_null = 1.0 / max(eo["T_eff"], 1.0)
    spec = {"paper_dsr": (V_paper, float(n_raw), float(st["T"])),
            "nc_dsr": (V_null, float(n_eff), float(eo["T_eff"])),
            "dep_dsr": (V_paper, float(n_eff), float(eo["T_eff"]))}
    out = {}
    for name, (V, N, T) in spec.items():
        rec = {"label": VARIANTS[name], "V": V, "N": N, "T_used": T, "gate": name == GATE_VARIANT}
        if V is None or (name != "nc_dsr" and V_paper_status != "OK") or T <= 2:
            rec.update(status=INSUFFICIENT, value=None, sr0=None)
        else:
            se = math.sqrt(sr_sampling_variance(sr, T, sk, ku))
            sr0 = expected_max_sr(N, V)
            rec.update(status="OK", sr0=sr0, sr0_annualized=sr0 * math.sqrt(annualization),
                       value=psr(sr, T, sk, ku, sr0), se_sr=se,
                       sr_ci95=[sr - 1.96 * se, sr + 1.96 * se],
                       sr_ci95_annualized=[(sr - 1.96 * se) * math.sqrt(annualization),
                                           (sr + 1.96 * se) * math.sqrt(annualization)])
        out[name] = rec
    return out


def gate(records: dict, min_dsr: float) -> dict:
    """Single multiplicity gate. The global (search-level) record is the gate: it contains every trial of the
    family record, so it is the stricter one and the family record would only add a nested, weaker condition.
    The family value is reported, never multiplied in (no double penalty)."""
    g = records.get("global") or {}
    f = records.get("family") or {}
    if g.get("status") != "OK" or g.get("value") is None:
        return {"status": INSUFFICIENT, "scope": "global", "value": None, "threshold": min_dsr}
    ok = g["value"] >= min_dsr
    return {"status": "PASS" if ok else "FAIL", "scope": "global", "value": g["value"], "threshold": min_dsr,
            "family_value": f.get("value"), "note": "gate = globale DSR (strenger, omvat de familie); "
                                                    "familie-DSR alleen ter informatie"}


# ------------------------------------------------------------------------------------------------ ledger glue
def _bdays(start, end) -> int | None:
    try:
        return int(np.busday_count(str(start)[:10], str(end)[:10]))
    except Exception:
        return None


def ledger_trials(ledger, family: str | None = None, until: str | None = None) -> list[dict]:
    """Rows usable for multiple testing: one per ledger entry with a Sharpe and a horizon. Annualised
    ``sharpe_daily`` is converted to per-day; T = ``n_days`` when recorded, else business days in [start, end) for
    full-period rows (window rungs without n_days are excluded from V: their real horizon is unknown)."""
    import json
    import sqlite3
    from contextlib import closing
    sql = "SELECT hypothesis_id, family, symbol, timeframe, start, end, kind, metrics_json, statistics_json FROM entries"
    where, args = [], []
    if family is not None:
        where.append("family=?")
        args.append(family)
    if until is not None:
        where.append("created_at<=?")
        args.append(str(until))
    if where:
        sql += " WHERE " + " AND ".join(where)
    with closing(sqlite3.connect(ledger.path, timeout=15)) as db:
        rows = db.execute(sql, args).fetchall()
    out = []
    for hid, fam, sym, tf, s, e, kind, mj, sj in rows:
        m = json.loads(mj) if mj else {}
        stt = json.loads(sj) if sj else {}
        fac = (stt or {}).get("factory") or {}
        sr_ann = m.get("sharpe_daily") if isinstance(m, dict) else None
        T = m.get("n_days") if isinstance(m, dict) else None
        if T is None and s and e and fac.get("trial_rung") not in ("r0_vensters",) and fac.get("stage") != "L2":
            T = _bdays(s, e)
        out.append({"hypothesis_id": hid, "family": fam, "symbol": sym, "timeframe": tf, "kind": kind,
                    "sr": (float(sr_ann) / math.sqrt(TRADING_DAYS)) if isinstance(sr_ann, (int, float)) else None,
                    "T": T, "group": f"{fac.get('signature') or fam}|{sym}|{tf}",
                    "n_eff_within": fac.get("n_eff_within"), "rep_series": fac.get("rep_series"),
                    "run_id": fac.get("run_id"), "stage": fac.get("stage")})
    return out


def series_from_compact(c: dict | None) -> pd.Series | None:
    if not c or not c.get("values"):
        return None
    idx = pd.date_range(c["start"], periods=len(c["values"]), freq=c.get("freq", "W-MON"))
    return pd.Series(c["values"], index=idx, dtype=float)


def compact_weekly(daily: pd.Series, capital: float) -> dict | None:
    """Weekly P&L / capital, rounded: small enough to keep one representative series per candidate in the ledger."""
    if daily is None or len(daily) < 10:
        return None
    s = daily.copy()
    s.index = pd.to_datetime(s.index)
    w = s.resample("W-MON").sum() / float(capital)
    return {"freq": "W-MON", "start": w.index[0].strftime("%Y-%m-%d"), "values": [round(float(v), 7) for v in w]}


def search_effective_n(rows: list[dict], seed: int = 0) -> dict:
    """Group ledger rows per candidate and combine within- and between-candidate redundancy."""
    groups: dict = {}
    reps: dict = {}
    for r in rows:
        g = groups.setdefault(r["group"], {"key": r["group"], "ids": set(), "n_eff_within": None})
        g["ids"].add(r["hypothesis_id"] or id(r))
        if r.get("n_eff_within"):
            g["n_eff_within"] = max(g["n_eff_within"] or 0.0, float(r["n_eff_within"]))
        if r.get("rep_series") and r["group"] not in reps:
            s = series_from_compact(r["rep_series"])
            if s is not None and s.std() > 0:
                reps[r["group"]] = s
    glist = [{"key": k, "n_trials": len(v["ids"]), "n_eff_within": v["n_eff_within"]} for k, v in groups.items()]
    between = None
    if len(reps) >= 2:
        frame = pd.concat(reps, axis=1).fillna(0.0)
        between = effective_n(frame, seed=seed)
    out = two_level_effective_n(glist, between)
    out["between"] = {k: v for k, v in (between or {}).items()}
    return out


def validation_variance(rows: list[dict], T_test: int) -> dict:
    """V for the tested walk-forward OOS series. Preferred source: Sharpe estimates of OTHER out-of-sample
    evaluations on the validation period (stage L5 rows, all rounds) - same period, same horizon, same units as the
    tested SR. Fallback: all search trials with a known horizon, rescaled to T_test (method of moments). Last
    resort: the null floor 1/T_test. The search-wide variance is always reported as a sensitivity."""
    oos = [r for r in rows if r.get("stage") == "L5" and r.get("sr") is not None]
    srch = [r for r in rows if r.get("sr") is not None]
    v_oos = trial_variance([r["sr"] for r in oos], [r["T"] if r["T"] is not None else np.nan for r in oos], T_test)
    v_all = trial_variance([r["sr"] for r in srch], [r["T"] if r["T"] is not None else np.nan for r in srch], T_test)
    if v_oos["status"] == "OK" and v_oos["n_used"] >= 5:
        chosen, src = v_oos, "validation_oos_evaluations"
    elif v_all["status"] == "OK":
        chosen, src = v_all, "search_trials_rescaled"
    else:
        chosen, src = v_oos, "null_floor"
    return {**chosen, "source": src, "sensitivity_search_trials_V": v_all.get("V"),
            "sensitivity_validation_oos_V": v_oos.get("V") if v_oos["status"] == "OK" else None}


def multiple_testing_records(returns, ledger, *, family: str | None, extra_trials: list[dict] | None = None,
                             seed: int = 0, rows: list[dict] | None = None) -> dict:
    """DSR records for scope 'family' (all ledger trials of this family, all markets/timeframes/rounds) and 'global'
    (every trial in the ledger = search level). N = two-level effective N of the scope; V from
    :func:`validation_variance` of the scope (global V when the family has too few OOS evaluations)."""
    rows_all = (rows if rows is not None else (ledger_trials(ledger) if ledger is not None else [])) +         list(extra_trials or [])
    T_test = sharpe_stats(returns)["T"]
    out = {}
    v_global = validation_variance(rows_all, T_test)
    for scope, rws in (("global", rows_all), ("family", [r for r in rows_all if r["family"] == family])):
        if not rws:
            rws = [{"hypothesis_id": "self", "group": "self", "sr": None, "T": None, "family": family}]
        en = search_effective_n(rws, seed=seed)
        var = v_global if scope == "global" else validation_variance(rws, T_test)
        if scope == "family" and var["source"] != "validation_oos_evaluations":
            var = {**v_global, "note": "familie heeft < 5 OOS-evaluaties: globale V gebruikt"}
        out[scope] = dsr_record(returns, n_raw=max(1, en["n_raw"]), n_eff=max(1.0, en["n_eff"]), variance=var,
                                scope=scope, extra={"effective_n_detail": {k: v for k, v in en.items()
                                                                           if k != "between"}})
    return out
