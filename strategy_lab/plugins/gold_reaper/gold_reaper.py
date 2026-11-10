#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GoldReaper_v2_Pass3436_Robuust -- single-file Python parity validation v10.

Reference: user-supplied GoldReaper_v2_Pass3436_Robuust(1).mq5, v2.00.
The MQL5 reference is ITSELF a clean-room estimate of a commercial EA.

This file aims to reproduce the available MQL5 strategy rules using an event-driven
Python broker emulator. It is NOT guaranteed to produce tick-for-tick MT5-equivalent
orders. For a strict parity claim, compare every order/deal/SL modification against
an MT5 run with the same source, broker specs, tick stream and news cache.

Run:
    python gold_reaper.py --self-test
    python gold_reaper.py --data XAUUSD_ticks.csv --mode ticks --output results
    python gold_reaper.py --data XAUUSD_M1.csv --mode m1 --spread-points 2 --output results
    python gold_reaper.py --data XAUUSD_M1.csv --mode m1 --output results --progress-interval 2 --stall-warning-seconds 30

Diagnostics: live CSV-percent progress bar with ETA, order/entry counts and data date;
results/run.log contains timestamps, periodic status, inactivity and stall warnings.
results/run_state.json contains an atomic checkpoint updated every 30 seconds.
Ctrl+C gracefully saves current partial results to results/*.csv and summary.json.
Use --log-events for all PLACE/FILL/CANCEL events (large log files for long tests).

Tick CSV columns: time (or datetime/timestamp), bid, ask; optional spread ignored.
M1 CSV columns: time, open, high, low, close; optional spread (in points).
Time inputs are BROKER SERVER local timestamps, not UTC, unless your MT5 data
already uses a UTC server clock. No external market-data API or MT5 is required.

M1 input has fundamentally ambiguous intraminute stop/fill ordering; synthetic
OHLC ticks are only for exploratory validation and are NOT an exact MT5 replay.
All code and strategy configurations are contained in this one .py file.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
import threading
import time
import traceback
import zipfile
from contextlib import ExitStack
from bisect import bisect_right
from collections import deque, Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Iterable, Optional, Iterator

TF_SECONDS = {'M1': 60, 'M5': 300, 'M15': 900, 'H1': 3600, 'H4': 14400, 'D1': 86400}
MIN_DIST_PCT = (0, .00400, .00335, .00276, .00610, .00123, .00128, .00128, .00120, .00070)
MAX_BUY = (0, 2, 2, 2, 3, 2, 2, 3, 4, 3)
MAX_SELL = (0, 2, 2, 2, 3, 2, 2, 3, 3, 3)
STRATEGY_WEIGHT = (0, .50, .50, .50, .50, .979, .899, .979, 1.043, .50)
DD_LOTS_10 = (0, .05, .11, .09, .04, .04, .02, .04, .02, .025)
DD_LOTS_20 = (0, .11, .22, .18, .10, .07, .06, .09, .07, .05)
DD_LOTS_50 = (0, .27, .55, .48, .25, .19, .13, .24, .18, .125)
PERFORMANCE_MULT = (0, 1.21, 1.54, 1.10, 1.43, .99, .935, 1.10, .99, 1.0)
CAPACITY_REARM = (False, True, False, False, False, True, True, False, True, False)
CAP_REARM_DELAY = (0, 1020, 0, 0, 0, 60, 500, 0, 120, 0)
NEW_PIVOT_REARM_DELAY = (0, 0, 0, 0, 0, 0, 0, 0, 0, 105)
NEW_PIVOT_REARM_MAX = (0, 99, 99, 99, 99, 99, 99, 99, 99, 1)
NFP_EXCEPTIONS = {(2016,7):8,(2017,3):10,(2017,12):8,(2018,3):9,(2018,12):7,
                  (2019,3):8,(2020,1):10,(2020,5):8,(2020,7):2,(2021,1):8,
                  (2021,10):8,(2022,1):7,(2022,7):8,(2023,3):10,(2023,12):8,
                  (2024,3):8,(2025,1):10,(2025,7):3,(2025,12):16}


@dataclass
class Settings:
    # Default values copied from the MQ5 source, including the pass-3436 preset.
    profile: str = 'Auto'                 # Auto, Manual, Extreme_conservative, Very_conservative, Conservative, Moderate, Aggressive, Very_aggressive
    risk: str = 'Max_drawdown'            # Max_drawdown, Max_risk_per_strategy, Fixed_lot
    max_dd: float = 10.0
    protect_dd: bool = True
    growth: str = 'Best_risk_reward'      # Original, Profit_focused, Best_risk_reward
    daily_dd: float = 0.0
    pending_policy: str = 'Pending_Original'  # Pending_Original, Pending_EndOfDay, Pending_MaxAge, Pending_Day_And_Age
    pending_cutoff_hour: int = 18
    pending_max_age_hours: int = 6
    max_pending_each_side: int = 0
    close_positions_at_cutoff: bool = False
    position_cutoff_hour: int = 20
    entry_offset_factor: float = .70
    stop_loss_factor: float = .75
    take_profit_factor: float = 1.50
    trail_distance_factor: float = 1.50
    be_trigger_factor: float = .75
    fakeout_filter: str = 'Medium'
    use_hl_trailing: bool = False
    max_spread_points: float = 500.0
    nfp: bool = True
    fomc: bool = True
    cpi: bool = True
    fixed_lot: float = .01
    max_risk_per_strategy: float = 1.0
    magic: int = 2802
    run_strategies: tuple = (1,2,3,4,5,6,7,8,9)
    allow_buy: bool = True
    allow_sell: bool = True
    only_up: bool = True
    use_weighted_lots: bool = True
    use_variable_values: bool = True
    adjust_lots_to_variable_values: bool = True
    check_margin: bool = True
    virtual_expiration: bool = True
    use_robust_edge_filters: bool = True
    use_strategy_specific_rearm: bool = True
    cap_rearm_policy: str = 'CapRearm_On_New_Pivot'
    bootstrap_historical_levels: bool = False
    fast_minute_logic: bool = True
    failed_order_retry_seconds: int = 300
    # Constants of the source, explicitly represented for auditing.
    balance_dd_governor: bool = True
    max_balance_dd_lot_factor: float = .12
    time_trail_s9: bool = True
    friday_stop_hour: int = 25
    nfp_minutes_before: int = 100
    nfp_minutes_after: int = 60
    broker_gmt_winter: int = 2
    broker_gmt_summer: int = 3
    # Broker-specification inputs: MUST MATCH the source MT5 symbol.
    point: float = .01
    digits: int = 2
    tick_size: float = .01
    tick_value_loss: float = 1.0 # 1.0 USD per one $0.01 tick / 1 XAUUSD lot (100oz)
    contract_size: float = 100.0
    volume_min: float = .01
    volume_max: float = 100.0
    volume_step: float = .01
    stops_level_points: float = 0.0
    leverage: float = 100.0
    initial_balance: float = 10000.0
    commission_per_lot_side: float = 0.0
    # Data semantics: synthetic OHLC is NOT equivalent to broker tick execution.
    spread_points: float = 2.0
    # M1 high/low are not executable quotes; assume barrier-price fills within an OHLC leg.
    m1_fill_model: str = "level"  # level (recommended) or vertex (legacy, inaccurate)
    # Switch on only for CSV M1 feed, NOT real ticks.
    input_mode: str = "ticks"
    round_m1_quotes: bool = True  # MT5 symbol rates are quantized to its digits
    news_csv: Optional[str] = None
    # Can optionally emulate non-24h broker sessions: e.g., 01:02--23:59.
    session_start: str = '01:02'
    session_end: str = '24:00'


@dataclass
class Strategy:
    s: int
    entry_tf: str
    check_tf: str
    left: int
    right: int
    countback: int
    expiry_h: int
    buy_offset: float
    sell_offset: float
    sl: float
    tp: float
    be_start: float
    be_extra: float
    fixed_start: float
    fixed_size: float
    hl_enabled: bool
    hl_tf: str
    hl_left: int
    hl_right: int
    hl_extra: float
    tp_recovery: bool
    recovery_start: float
    recovery_size: float
    time_trail: bool
    time_trail_min: int
    time_trail_size: float
    manage_min: int
    min_dist_pct: float = 0.0
    max_buy: int = 0
    max_sell: int = 0
    hl_countback: int = 250


def build_strategies(p: Settings) -> dict[int, Strategy]:
    # These nine parameter sets are copied directly from InitConfigs() / SetCfg().
    rows = [
        (1,'D1','M15',24,3,105,35,-275,160,6096,1449,950,120,0,0,True,'H4',3,2,27.5,True,0,701,False,0,0,5),
        (2,'D1','H1',15,3,210,480,-170,70,1000,4098,500,100,1375,441,True,'H1',1,5,29,False,0,0,False,0,0,1),
        (3,'D1','H1',7,2,20,432,-130,120,700,3296,425,0,400,492,False,'H1',0,0,0,False,0,0,False,0,0,1),
        (4,'H4','H1',12,8,90,192,-50,125,1000,6126,650,250,1875,1590,True,'H1',10,6,51.5,False,0,0,False,0,0,5),
        (5,'H1','M15',30,19,110,30,-92,85,4076,692,225,62,312,381,False,'H1',0,0,0,True,1500,193,False,0,0,1),
        (6,'H1','M5',26,24,140,20,-96,121,8416,666,300,67,0,0,False,'H1',0,0,0,True,1688,291,False,0,0,1),
        (7,'H1','M15',25,23,145,60,-7.7,111.5,1732,1116,300,23.5,700,344,True,'H1',11,17,59,False,0,0,False,0,0,1),
        (8,'H1','M15',26,20,235,55,-100,121.5,1356,856,225,0,250,920,True,'H1',12,2,17.5,False,0,0,False,0,0,1),
        (9,'H1','M15',7,5,200,15,-158,153,4102,1420,200,52.5,388,465,True,'H1',1,9,49,True,2025,263,True,60,2942,1),
    ]
    out = {}
    for r in rows:
        c = Strategy(*r)
        c.min_dist_pct = MIN_DIST_PCT[c.s]
        c.max_buy = min(MAX_BUY[c.s], p.max_pending_each_side) if p.max_pending_each_side else MAX_BUY[c.s]
        c.max_sell = min(MAX_SELL[c.s], p.max_pending_each_side) if p.max_pending_each_side else MAX_SELL[c.s]
        c.buy_offset *= p.entry_offset_factor
        c.sell_offset *= p.entry_offset_factor
        c.sl *= p.stop_loss_factor
        c.tp *= p.take_profit_factor
        c.fixed_size *= p.trail_distance_factor
        c.hl_extra *= p.trail_distance_factor
        c.time_trail_size *= p.trail_distance_factor
        c.be_start *= p.be_trigger_factor
        out[c.s] = c
    return out


@dataclass
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    def add(self, price: float):
        if price > self.high: self.high = price
        if price < self.low: self.low = price
        self.close = price


class Series:
    """MT5-equivalent shift index for existent bars (weekends/gaps skipped)."""
    def __init__(self, seconds: int):
        self.seconds = seconds
        self.closed: deque[Bar] = deque(maxlen=1536)
        self.cur: Optional[Bar] = None
        self.next_at: Optional[datetime] = None

    def floor(self, t: datetime) -> datetime:
        start = t.replace(hour=0, minute=0, second=0, microsecond=0)
        seconds = t.hour*3600 + t.minute*60 + t.second
        return start + timedelta(seconds=(seconds // self.seconds)*self.seconds)

    def push(self, t: datetime, bid: float) -> bool:
        if self.cur is not None:
            if t < self.cur.time:
                raise ValueError(f'Input ticks not chronologically sorted: {t} < {self.cur.time}')
            if t < self.next_at:
                self.cur.add(bid)
                return False
        key = self.floor(t)
        if self.cur is not None:
            self.closed.append(self.cur)
        self.cur = Bar(key, bid, bid, bid, bid)
        self.next_at = key + timedelta(seconds=self.seconds)
        return True

    def bar(self, shift: int) -> Optional[Bar]:
        if shift == 0:
            return self.cur
        return self.closed[-shift] if 0 < shift <= len(self.closed) else None

    def count(self) -> int:
        return len(self.closed) + int(self.cur is not None)

    def shift_at(self, t: datetime) -> int:
        if self.cur is not None and self.cur.time == t:
            return 0
        # 1,536 linear comparisons: performed only at an entry-check boundary.
        for shift in range(1, len(self.closed)+1):
            if self.closed[-shift].time == t:
                return shift
        return -1


@dataclass
class Level:
    s: int
    buy: bool
    pivot_time: datetime
    confirmed_time: datetime
    pivot_price: float
    ticket: int = 0
    placed_at: Optional[datetime] = None
    raw_lots: float = 0.0
    retry_after: Optional[datetime] = None
    cap_suppressed: bool = False
    cap_suppressed_at: Optional[datetime] = None
    used: bool = True


@dataclass
class Order:
    id: int
    level: int
    s: int
    buy: bool
    created: datetime
    price: float
    lots: float
    sl: float
    tp: float
    expires: Optional[datetime] = None


@dataclass
class Position:
    id: int
    order_id: int
    s: int
    buy: bool
    opened: datetime
    entry: float
    lots: float
    sl: float
    tp: float
    opening_fee: float = 0.0



class CsvReadProgress:
    """Exact number of bytes consumed by csv.DictReader (no full-file pre-scan)."""
    def __init__(self, path: str):
        self.path = Path(path)
        if self.path.suffix.lower() == '.zip':
            with zipfile.ZipFile(self.path) as z:
                entries=[x for x in z.infolist() if x.filename.lower().endswith('.csv') and not x.is_dir()]
                if len(entries)!=1:
                    raise ValueError('ZIP must contain exactly one CSV (found %d)'%len(entries))
                self.total_bytes = entries[0].file_size
        else:
            self.total_bytes = self.path.stat().st_size
        self.bytes_read = 0
        self.input_lines = 0
        self.columns = []

    @property
    def fraction(self):
        if self.total_bytes <= 0:
            return 0.0
        return min(1.0, self.bytes_read / self.total_bytes)


class RunDiagnostics:
    """Console progress, persistent logfile, heartbeat, stall warnings, recovery checkpoints.

    All run artifacts stay inside --output. Python's logging, thread and csv modules
    are standard-library only, so this is still a single-file program.
    """
    def __init__(self, output: str, csv_progress: CsvReadProgress, mode: str,
                 progress_interval: float=3.0, stall_seconds: float=30.0,
                 checkpoint_seconds: float=30.0, idle_days: int=5,
                 show_progress: bool=True, event_logging: bool=False):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.csv = csv_progress
        self.mode = mode
        self.progress_interval = max(.2, progress_interval)
        self.stall_seconds = max(5.0, stall_seconds)
        self.checkpoint_seconds = max(5.0, checkpoint_seconds)
        self.idle_days = max(1, idle_days)
        self.show_progress = show_progress
        self.event_logging = event_logging
        self.start = time.monotonic()
        self.last_tick_wall = self.start
        self.last_progress_wall = self.start
        self.last_checkpoint_wall = self.start
        self.last_print_wall = self.start
        self.tick_count = 0
        self.last_tick = None
        self.last_fill_at = None
        self.last_fills = 0
        self.last_calendar_day = None
        self.warned_no_trade_at = None
        self.warned_stall_at = None
        self.stage = 'starting'
        self.stop_requested = threading.Event()
        self.lock = threading.RLock()
        self.logger = logging.getLogger('goldreaper_run')
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        for old in self.logger.handlers[:]:
            old.close()
            self.logger.removeHandler(old)
        fmt = logging.Formatter('%(asctime)s | %(levelname)-7s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        file_handler = logging.FileHandler(self.output / 'run.log', mode='w', encoding='utf-8')
        file_handler.setFormatter(fmt)
        file_handler.setLevel(logging.DEBUG)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(fmt)
        console_handler.setLevel(logging.INFO)
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.tty = bool(sys.stderr.isatty())
        self.thread = threading.Thread(target=self._watch, name='GoldReaperWatchdog', daemon=True)

    def start_watchdog(self):
        self.thread.start()

    def _watch(self):
        # Checks wall-clock time, not data timestamps: catches a blocked or very slow tick.
        while not self.stop_requested.wait(min(5.0, self.stall_seconds / 4)):
            now = time.monotonic()
            idle = now - self.last_tick_wall
            if idle >= self.stall_seconds and (self.warned_stall_at is None or
                                               now - self.warned_stall_at >= self.stall_seconds):
                self.warned_stall_at = now
                self.logger.warning('NO PROGRESS for %.0fs: phase=%s, ticks=%s, csv=%0.2f%%, last_market_time=%s. '
                                    'This may mean a slow computation, disk I/O or a stalled input.',
                                    idle, self.stage, f'{self.tick_count:,}', 100*self.csv.fraction, self.last_tick)
                frame = sys._current_frames().get(threading.main_thread().ident)
                if frame is not None:
                    frames = ''.join(traceback.format_stack(frame)[-9:]).rstrip()
                    self.logger.debug('Main-thread stack during stall:\n%s', frames)

    @staticmethod
    def _fmt_seconds(seconds: float) -> str:
        if not math.isfinite(seconds) or seconds < 0:
            return '?'
        seconds = round(seconds)
        days, rem = divmod(seconds, 86400)
        h, rem = divmod(rem, 3600)
        m, sec = divmod(rem, 60)
        return f'{days}d {h}h {m}m' if days else (f'{h}h {m}m' if h else f'{m}m {sec}s')

    def _current(self, engine):
        elapsed = max(.001, time.monotonic()-self.start)
        frac = self.csv.fraction
        eta = (elapsed/frac-elapsed) if frac>=.01 and elapsed>=3 else float('nan')
        return {'status': self.stage,
                'csv_read_pct': round(frac*100, 3),
                'bytes_read': self.csv.bytes_read,
                'bytes_total': self.csv.total_bytes,
                'input_lines': self.csv.input_lines,
                'price_ticks_processed': self.tick_count,
                'data_time': str(self.last_tick),
                'elapsed_seconds': round(elapsed, 1),
                'estimated_remaining_seconds': round(eta, 1) if math.isfinite(eta) else None,
                'speed_ticks_per_second': round(self.tick_count/elapsed, 1),
                'pending_open': len(engine.orders),
                'positions_open': len(engine.positions),
                'pending_placed': engine.stats['placed'],
                'pending_filled': engine.stats['filled'],
                'pending_canceled': engine.stats['canceled'],
                'trades_closed': len(engine.trades),
                'pivot_levels_detected': engine.stats['pivots'],
                'strategy_scans': engine.stats['strategy_scans'],
                'scans_without_candidates': engine.stats['scans_without_candidates'],
                'entries_blocked_by_margin': engine.stats['entry_margin_blocked'],
                'candidate_skip_reasons_cumulative': {k: v for k, v in engine.stats.items() if k.startswith('skip_')},
                'entries_blocked_by_risk': engine.stats['entry_risk_blocked'],
                'risk_block_reason': engine.risk_last_reason,
                'realized_balance_drawdown_pct': round(engine.realized_dd(),3),
                'account_drawdown_pct': round(engine.account_dd(),3),
                'peak_closed_balance': round(engine.dd_balance_hwm,2),
                'maxdd_locked': engine.maxdd_locked,
                'balance': round(engine.balance, 2),
                'equity': round(engine.equity, 2),
                'no_trade_warning_threshold_weekdays': self.idle_days}

    def checkpoint(self, engine, force=False):
        now = time.monotonic()
        if not force and now-self.last_checkpoint_wall < self.checkpoint_seconds:
            return
        self.last_checkpoint_wall = now
        f = self.output/'run_state.json'
        tmp = self.output/'run_state.json.tmp'
        try:
            with tmp.open('w', encoding='utf-8') as fh:
                json.dump(self._current(engine), fh, indent=2)
            tmp.replace(f)
        except OSError as ex:
            self.logger.warning('Could not write state checkpoint: %s', ex)

    def report(self, engine, force=False):
        now = time.monotonic()
        if not force and now-self.last_print_wall < self.progress_interval:
            return
        self.last_print_wall = now
        d = self._current(engine)
        frac = self.csv.fraction
        barwidth = 22
        bar = '#' * int(frac * barwidth) + '-' * (barwidth - int(frac * barwidth))
        eta = self._fmt_seconds(d['estimated_remaining_seconds']) if d['estimated_remaining_seconds'] is not None else 'calibrating'
        current = f' | data={str(self.last_tick)[:16]}' if self.last_tick else ''
        line = (f'[{bar}] {frac*100:6.2f}% | {self.tick_count:,} price ticks | '
                f'{d["speed_ticks_per_second"]:,.0f} ticks/s | ETA {eta}' + current +
                f' | pending={d["pending_open"]} | filled={d["pending_filled"]}')
        if self.show_progress:
            if self.tty:
                # Modern Windows Terminal / PowerShell handles CR and ANSI line clear.
                print('\r\x1b[2K' + line, end='' if not force else '\n', file=sys.stderr, flush=True)
            else:
                print(line, file=sys.stderr, flush=True)
        if now-self.last_progress_wall >= max(15, self.progress_interval*4) or force:
            self.last_progress_wall=now
            self.logger.info('PROGRESS %.2f%% | %s price ticks | %.0f ticks/s | ETA %s | '
                             'placed=%s filled=%s cancelled=%s closed=%s pending=%s positions=%s '
                             'pivots=%s scans=%s no_candidate=%s risk_blocked=%s margin_blocked=%s '
                             'skip_spread=%s skip_distance=%s skip_side=%s skip_suppressed=%s',
                             100*frac, f'{self.tick_count:,}', d['speed_ticks_per_second'], eta,
                             d['pending_placed'],d['pending_filled'],d['pending_canceled'],d['trades_closed'],
                             d['pending_open'],d['positions_open'],d['pivot_levels_detected'],
                             d['strategy_scans'],d['scans_without_candidates'],
                             d['entries_blocked_by_risk'],d['entries_blocked_by_margin'],
                             engine.stats['skip_spread'],engine.stats['skip_entry_distance'],
                             engine.stats['skip_stop_entry_side'],engine.stats['skip_suppressed_level'])
        self.checkpoint(engine)

    @staticmethod
    def _weekdays(start: date, end: date):
        if start>=end:
            return 0
        days=(end-start).days
        whole, remainder=divmod(days,7)
        return whole*5 + sum((start.weekday()+i)%7<5 for i in range(remainder))

    def note_no_new_trades(self, engine):
        if self.last_tick is None or self.last_calendar_day == self.last_tick.date():
            return
        self.last_calendar_day = self.last_tick.date()
        filled = engine.stats['filled']
        if filled != self.last_fills:
            self.last_fills = filled
            self.last_fill_at = self.last_tick
            self.warned_no_trade_at = None
            return
        if engine.first_tick is None or (self.last_tick-engine.first_tick).days < 45:
            return  # Large D1 pivot lookback needs initial warmup.
        anchor = engine.last_fill_datetime or engine.first_tick
        days = self._weekdays(anchor.date(), self.last_tick.date())
        if days>=self.idle_days and (self.warned_no_trade_at is None or
                                   self._weekdays(self.warned_no_trade_at, self.last_tick.date())>=self.idle_days):
            self.warned_no_trade_at = self.last_tick.date()
            if days>=self.idle_days*2 and not engine.orders and engine.stats['entry_risk_blocked']:
                self.logger.warning('RISK DIAGNOSIS: peak_balance=%.2f balance=%.2f DD=%.2f%% hard_locked=%s last_risk_reason=%s; high risk blocks can prevent ANY new orders even though pivots update.',engine.dd_balance_hwm,engine.balance,engine.account_dd(),engine.maxdd_locked,engine.risk_last_reason)
            self.logger.warning('NO NEW FILLS for %d weekdays (data timestamps). Still processing normally. '
                                'Pending=%d, open positions=%d, placed=%d, pivots=%d; '
                                'this is not automatically a hang.',
                                days,len(engine.orders),len(engine.positions),engine.stats['placed'],engine.stats['pivots'])

    def tick_done(self, engine, t, count:int=1):
        self.tick_count += count
        self.last_tick = t
        if (self.tick_count & 1023) != 0:
            return
        self.last_tick_wall = time.monotonic()
        self.stage = 'backtesting'
        self.note_no_new_trades(engine)
        self.report(engine)

    def stop(self):
        self.stop_requested.set()
        if self.thread.is_alive():
            self.thread.join(timeout=1)
        for h in self.logger.handlers:
            h.flush()


def _dt(s: str) -> datetime:
    s = s.strip().replace('T', ' ').replace('Z', '')
    if not s: raise ValueError('Missing timestamp')
    if s.isdigit(): return datetime(1970,1,1) + timedelta(seconds=int(s))
    s = s.replace('/', '-')
    # ISO 8601's C implementation is dramatically faster than repeated strptime
    # attempts and supports the conventional 'YYYY-MM-DD HH:MM:SS' input.
    if len(s) >= 10 and s[4] == '-' and s[7] == '-':
        try: return datetime.fromisoformat(s)
        except ValueError: pass
    if len(s) >= 10 and s[4] == '.' and s[7] == '.':
        try: return datetime.fromisoformat(s[:4]+'-'+s[5:7]+'-'+s[8:])
        except ValueError: pass
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M',
                '%Y.%m.%d %H:%M:%S', '%Y.%m.%d %H:%M', '%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M:%S.%f'):
        try: return datetime.strptime(s, fmt)
        except ValueError: pass
    return datetime.fromisoformat(s)


def _rows(path: str, progress: Optional[CsvReadProgress]=None) -> Iterator[dict]:
    """Stream UTF-8 CSV or ZIP with exactly one CSV, updating accurate progress."""
    with ExitStack() as stack:
        if Path(path).suffix.lower()=='.zip':
            z=stack.enter_context(zipfile.ZipFile(path))
            names=[x for x in z.namelist() if x.lower().endswith('.csv') and not x.endswith('/')]
            if len(names)!=1:
                raise ValueError('ZIP must contain exactly one CSV; found %d'%len(names))
            fh=stack.enter_context(z.open(names[0]))
        else:
            fh=stack.enter_context(open(path,'rb'))
        header=fh.readline().decode('utf-8-sig')
        delim=max((';',',','\t'),key=header.count)
        if progress is not None:
            progress.bytes_read += len(header.encode('utf-8-sig'))
            progress.input_lines += 1
        def counted_lines():
            yield header
            for raw_line in fh:
                if progress is not None:
                    progress.bytes_read += len(raw_line)
                    progress.input_lines += 1
                yield raw_line.decode('utf-8-sig')
        reader=csv.DictReader(counted_lines(),delimiter=delim)
        if reader.fieldnames is None:raise ValueError('CSV header required')
        if progress is not None:progress.columns=list(reader.fieldnames)
        for raw in reader:
            yield {(k or '').strip().lower().replace(' ','_').strip('<>'): (v or '').strip()
                   for k,v in raw.items()}


def market_feed(path: str, mode: str, p: Settings, shift_hours: float=0,
                progress: Optional[CsvReadProgress]=None) -> Iterator[tuple[datetime,float,float]]:
    """Tick feed is authoritative; OHLC interpolation is an explicitly approximate mode."""
    last: Optional[datetime] = None
    for line_no, row in enumerate(_rows(path, progress=progress), 2):
        tvalue = next((row.get(k) for k in ('time','datetime','timestamp','date_time','gmt_time','date') if row.get(k)), None)
        if tvalue is None:
            raise ValueError('Need time/datetime/timestamp CSV column')
        if row.get('date') and row.get('time') and len(tvalue)<=12:
            tvalue = row['date']+' '+row['time']
        t = _dt(tvalue) + timedelta(hours=shift_hours)
        if last is not None and t < last:
            raise ValueError(f'Input out of chronological order on line {line_no}: {t} < {last}')
        last = t
        if mode == 'ticks':
            bid = float(row.get('bid') or row.get('price'))
            ask = float(row['ask']) if row.get('ask') else bid + p.spread_points*p.point
            yield t, bid, ask
        else:
            o,h,l,c = (float(row[n]) for n in ('open','high','low','close'))
            if not (l <= min(o,c) <= max(o,c) <= h):
                raise ValueError(f'Invalid OHLC on line {line_no}')
            # Dukascopy/MT5 M1 exports often contain SPREAD=0 as a missing
            # quote field. Never silently assume zero bid/ask spread.
            raw_spread = row.get('spread')
            spread_pts = float(raw_spread) if raw_spread not in (None,'') else 0.0
            if spread_pts <= 0:
                spread_pts = p.spread_points
            spr = spread_pts * p.point
            # The order here is an assumption, NOT the unique historical tick path.
            path_prices = (o,l,h,c) if c >= o else (o,h,l,c)
            offsets = (0,15,35,59)
            for sec,bid in zip(offsets,path_prices):
                yield t.replace(second=0, microsecond=0) + timedelta(seconds=sec),bid,bid+spr


def m1_bars_fast(path: str, p: Settings, shift_hours: float=0,
                 progress: Optional[CsvReadProgress]=None) -> Iterator[tuple[datetime,float,float,float,float,float]]:
    """Fast, streaming Dukascopy M1 reader, one bar per row (not four generator tuples).

    Parses ordinary unquoted numeric CSV/TSV lines with the stdlib only. The
    less-common quoted-field case uses csv.reader as a safe fallback. Order
    semantics and the four original OHLC synthetic vertices are unchanged.
    """
    with ExitStack() as stack:
        if Path(path).suffix.lower()=='.zip':
            z=stack.enter_context(zipfile.ZipFile(path))
            entries=[x for x in z.namelist() if x.lower().endswith('.csv') and not x.endswith('/')]
            if len(entries)!=1:
                raise ValueError('ZIP must contain exactly one CSV; found %d' % len(entries))
            fh=stack.enter_context(z.open(entries[0]))
        else:
            fh=stack.enter_context(open(path,'rb'))
        rawheader=fh.readline()
        if progress:
            progress.bytes_read+=len(rawheader);progress.input_lines+=1
        header=rawheader.decode('utf-8-sig').strip()
        delim=max((';',',','\t'),key=header.count)
        keys=[k.strip().lower().replace(' ','_').strip('<>') for k in header.split(delim)]
        if progress: progress.columns=keys
        def getidx(*names):
            for n in names:
                if n in keys:return keys.index(n)
            return None
        di=getidx('date')
        ti=getidx('time','datetime','timestamp','date_time','gmt_time')
        if ti is None:ti=di
        if ti is None:raise ValueError('Missing time/date header')
        oi,hi,li,ci=[getidx(name) for name in ('open','high','low','close')]
        if any(i is None for i in (oi,hi,li,ci)):
            raise ValueError('Need OPEN,HIGH,LOW,CLOSE headers for M1')
        si=getidx('spread')
        vi=getidx('tickvol','tick_volume','tick_volume_m1','tick_volume_m1','tick_vol','tickvolume')
        fallback_spread=p.spread_points*p.point
        shift=timedelta(hours=shift_hours) if shift_hours else None
        previous=None
        for line_no,raw in enumerate(fh,2):
            if progress:
                progress.bytes_read+=len(raw); progress.input_lines+=1
            line=raw.decode('utf-8-sig').rstrip('\r\n')
            if not line:continue
            # Fast numeric fields; preserve quoted-field correctness if present.
            f=next(csv.reader([line],delimiter=delim)) if '"' in line else line.split(delim)
            tval=f[ti]
            if di is not None and di!=ti and len(tval)<=12:
                tval=f[di]+' '+tval
            t=_dt(tval)
            if shift:t+=shift
            if previous is not None and t<previous:
                raise ValueError(f'Input out of chronological order on line {line_no}: {t} < {previous}')
            previous=t
            o,h,l,c=(float(f[i]) for i in (oi,hi,li,ci))
            if p.round_m1_quotes:
                # Custom symbols store quotes at SYMBOL_DIGITS precision.
                # The raw Dukascopy CSV may contain an extra decimal.
                scale=10**p.digits
                o,h,l,c=(math.floor(v*scale+.5000000001)/scale for v in (o,h,l,c))
            if not (l<=min(o,c)<=max(o,c)<=h):
                raise ValueError(f'Invalid OHLC on line {line_no}')
            spr=fallback_spread
            if si is not None and f[si]:
                spread_pts=float(f[si])
                if spread_pts>0:spr=spread_pts*p.point
            # Previous feed always snaps each M1 bar to the beginning of minute.
            tv=max(1,int(float(f[vi]))) if vi is not None and f[vi] else 4
            yield t.replace(second=0,microsecond=0),o,h,l,c,spr,tv


def usd_dst(t: date) -> bool:
    def nth_sunday(y,m,n):
        d = date(y,m,1)
        return 1 + (6-d.weekday())%7 + (n-1)*7
    return date(t.year,3,nth_sunday(t.year,3,2)) <= t < date(t.year,11,nth_sunday(t.year,11,1))


def nfp_server_time(y: int, month: int, p: Settings) -> datetime:
    first_friday = 1 + (4-date(y,month,1).weekday())%7
    day = NFP_EXCEPTIONS.get((y,month), first_friday+7 if month==1 and first_friday<=2 else first_friday)
    summer = usd_dst(date(y,month,day))
    hour = (12 if summer else 13) + (p.broker_gmt_summer if summer else p.broker_gmt_winter)
    return datetime(y,month,day,hour,30)


class GoldReaper:
    def __init__(self, p: Optional[Settings]=None):
        self.p = p or Settings()
        self.validate_settings()
        def parse_session_time(value):
            h,m=map(int, value.split(':'))
            return h*60+m
        self._session_start_min = parse_session_time(self.p.session_start)
        self._session_end_min = parse_session_time(self.p.session_end)
        self._auto_cap_cache = None
        self._perf_cache_key = None
        self._perf_cache_normalizer = 1.0
        self._cached_nfp_month = None
        self._cached_nfp_times = ()
        self.cfg = build_strategies(self.p)
        self.bars = {k: Series(v) for k,v in TF_SECONDS.items()}
        self.levels: list[Level] = []
        # Index only candidate levels for each strategy. Reused registry slots
        # are removed from their previous strategy index in add_level().
        self.level_indices: dict[int,set[int]]={k:set() for k in range(1,10)}
        self._last_broken_scan: Optional[datetime] = None
        self.orders: dict[int,Order] = {}
        self.positions: dict[int,Position] = {}
        self.order_history: list[dict] = []
        self.trades: list[dict] = []
        self.management_history: list[dict] = []
        self.stats = Counter()
        self.oid = 0
        self.pid = 0
        self.now: Optional[datetime] = None
        self.bid = 0.0; self.ask = 0.0
        self.balance = self.p.initial_balance
        self.initial_balance = self.balance
        self.equity = self.balance
        self.highest_balance = self.balance
        self._auto_cap_cache = self.auto_cap() if self.p.profile=='Auto' else None
        self.dd_balance_hwm = self.balance
        self.day_key: Optional[date] = None
        self.day_start_balance = self.balance
        self.daily_dd_locked = False
        self.maxdd_locked = False
        self.recovery_mode = False
        self.last_logic_minute: Optional[datetime] = None
        self.last_management_minute: Optional[datetime] = None
        self.last_check: dict[int,Optional[datetime]] = {s:None for s in range(1,10)}
        self.last_entry: dict[int,Optional[datetime]] = {s:None for s in range(1,10)}
        self.last_fake: dict[str,Optional[datetime]] = {tf:None for tf in ('M1','M5','M15','H1')}
        self.hl_cache: dict[tuple[int,bool,datetime],float] = {}
        self.last_session_open = False
        self.last_news_block = False
        self.last_news_action: Optional[tuple[str,datetime]] = None
        self.news_events: list[tuple[datetime,str]] = self.read_news()
        self.started = False
        self.first_tick: Optional[datetime] = None
        self.last_tick: Optional[datetime] = None
        self.equity_peak = self.balance
        self.max_equity_dd = 0.0
        self.last_fill_datetime = None
        self.risk_last_reason = 'none'
        self.prev_bid = None
        self.prev_ask = None
        self.prev_time = None
        self.diagnostics: Optional[RunDiagnostics] = None

    def validate_settings(self):
        p=self.p
        if not (0<=p.pending_cutoff_hour<=23 and 0<=p.position_cutoff_hour<=23):
            raise ValueError('cutoff hours must be 0...23')
        if not (1<=p.pending_max_age_hours<=240 and 0<=p.max_pending_each_side<=10):
            raise ValueError('invalid pending max age / cap')
        if min(p.entry_offset_factor,p.stop_loss_factor,p.take_profit_factor,
               p.trail_distance_factor,p.be_trigger_factor,p.max_spread_points) <= 0:
            raise ValueError('entry/exit factors and max spread must be positive')
        if (p.point<=0 or p.volume_step<=0 or p.tick_size<=0 or p.leverage<=0
                or p.tick_value_loss<=0 or p.contract_size<=0 or p.digits<0):
            raise ValueError('invalid broker symbol specifications')

    def read_news(self) -> list[tuple[datetime,str]]:
        if not self.p.news_csv: return []
        events=[]
        for r in _rows(self.p.news_csv):
            t = next((r.get(k) for k in ('event_time','time','datetime','timestamp') if r.get(k)),None)
            kind = (r.get('kind') or r.get('event') or r.get('event_kind') or '').upper()
            impact=(r.get('importance') or '').upper()
            if not t or kind not in ('NFP','CPI','FOMC'): continue
            if impact and impact not in ('3','HIGH'): continue
            events.append((_dt(t),kind))
        return sorted(set(events))

    @property
    def price(self) -> float: return (self.bid+self.ask)/2
    def normprice(self,price:float) -> float: return round(price,self.p.digits)
    def pips(self,base:float,reference:float=0) -> float:
        ratio = (reference or self.price or 2000)/2000 if self.p.use_variable_values else 1.0
        return base*ratio*self.p.point

    def normvol(self,v:float, floor:bool=False) -> float:
        p=self.p
        if floor and v < p.volume_min-1e-12: return 0.0
        v = min(p.volume_max,v)
        if not floor: v=max(p.volume_min,v)
        steps = math.floor(v/p.volume_step+(1e-9 if floor else .5))
        v = steps*p.volume_step
        if floor and v<p.volume_min-1e-12: return 0.0
        decimals = 4 if p.volume_step < .001 else 3 if p.volume_step < .01 else 2
        return round(v,decimals)

    def auto_score(self):
        bal=max(self.balance,self.highest_balance) if self.p.only_up else self.balance
        if self.p.risk=='Max_drawdown':
            return bal*min(50,max(1,self.p.max_dd))/30
        if self.p.risk=='Fixed_lot':
            return bal*max(.001,self.p.fixed_lot)/.01
        return bal*max(.10,self.p.max_risk_per_strategy)

    def auto_cap(self):
        score=self.auto_score()
        if self.p.risk=='Max_drawdown':
            return 2 if score<600 else 3 if score<1000 else 5 if score<1500 else 7 if score<3000 else 8
        return 2 if score<5000 else 3 if score<15000 else 5 if score<30000 else 7 if score<60000 else 8

    def enabled(self,s:int) -> bool:
        profile=self.p.profile
        if profile=='Auto': return s<=self._auto_cap_cache
        if profile=='Manual': return s in self.p.run_strategies
        return s<= {'Extreme_conservative':2,'Very_conservative':3,'Conservative':5,'Moderate':7,
                    'Aggressive':8,'Very_aggressive':9}.get(profile,0)

    def anchor_lots(self,s:int,dd:float)->float:
        if dd<=0:return 0.0
        a,b,c=DD_LOTS_10[s],DD_LOTS_20[s],DD_LOTS_50[s]
        if not self.p.use_weighted_lots:
            a=sum(DD_LOTS_10[1:9])/8; b=sum(DD_LOTS_20[1:9])/8; c=sum(DD_LOTS_50[1:9])/8
        if dd<=10:return a*dd/10
        if dd<=20:return a+(b-a)*(dd-10)/10
        if dd<=50:return b+(c-b)*(dd-20)/30
        return c*dd/50

    def performance_mult(self,s:int) -> float:
        if self.p.growth=='Original':return 1.0
        # In the MQL5 source weights are constant for a fixed set of enabled
        # strategies; only Auto switching active cap changes the normalizer.
        key=self._auto_cap_cache if self.p.profile=='Auto' else -1
        if key!=self._perf_cache_key:
            wsum=0.0;msum=0.0
            for k in range(1,10):
                if not self.enabled(k):continue
                w=self.anchor_lots(k,min(50,max(1,self.p.max_dd))) if self.p.risk=='Max_drawdown' else \
                  STRATEGY_WEIGHT[k] if self.p.risk=='Max_risk_per_strategy' and self.p.use_weighted_lots else 1.0
                w=max(.000001,w)
                wsum+=w;msum+=w*PERFORMANCE_MULT[k]
            self._perf_cache_normalizer=msum/wsum if wsum>0 and msum>0 else 1.0
            self._perf_cache_key=key
        return PERFORMANCE_MULT[s]/self._perf_cache_normalizer

    def realized_dd(self)->float:
        return max(0,(self.dd_balance_hwm-self.balance)/self.dd_balance_hwm*100) if self.dd_balance_hwm else 0

    def account_dd(self)->float:
        return max(0,(self.dd_balance_hwm-self.equity)/self.dd_balance_hwm*100) if self.dd_balance_hwm else 0

    def lot_factor(self)->float:
        if not self.p.protect_dd or not self.p.balance_dd_governor or self.p.max_dd<=0:return 1.0
        start=max(0,self.p.max_dd*.5)
        full=max(start+.01, min(self.p.max_dd*.78,self.p.max_dd))
        minfactor=.12 if self.p.growth=='Best_risk_reward' else .20
        dd=self.realized_dd()
        if dd<=start:return 1
        if dd>=full:return minfactor
        return 1-(1-minfactor)*((dd-start)/(full-start))

    def loss_per_lot(self,distance_price:float)->float:
        return max(0,distance_price/self.p.tick_size*self.p.tick_value_loss)

    def lots_for(self,s:int,sl_distance:float)->float:
        p=self.p; bal=self.balance
        if p.risk=='Max_drawdown':
            sizing=max(self.highest_balance,bal) if p.only_up else bal
            dd=min(50,max(1,p.max_dd))
            vol=self.anchor_lots(s,dd)*max(.05,sizing/10000)
            if p.adjust_lots_to_variable_values and p.use_variable_values and self.price>0:
                vol*=1090.42/self.price
            if p.growth!='Original':vol*=self.performance_mult(s)
            vol=min(99.,vol)
        elif p.risk=='Max_risk_per_strategy':
            vol= bal*p.max_risk_per_strategy/100/max(1e-15,self.loss_per_lot(sl_distance))
            if p.use_weighted_lots:vol*=STRATEGY_WEIGHT[s]
            if p.growth!='Original':vol*=self.performance_mult(s)
        else:
            vol=p.fixed_lot
            if p.use_weighted_lots:vol*=STRATEGY_WEIGHT[s]
            if p.adjust_lots_to_variable_values and p.use_variable_values and self.price>0:
                vol*=2000/self.price
        fac=self.lot_factor()
        vol*=fac
        if fac<1 and p.protect_dd and vol<p.volume_min-1e-12:return 0.
        return self.normvol(vol)

    def mark_equity(self):
        floating=0.0
        for pos in self.positions.values():
            out=self.bid if pos.buy else self.ask
            floating+=(out-pos.entry if pos.buy else pos.entry-out)*pos.lots*self.p.contract_size
        self.equity=self.balance+floating
        self.equity_peak=max(self.equity_peak,self.equity)
        if self.equity_peak:
            self.max_equity_dd=max(self.max_equity_dd,max(0,(self.equity_peak-self.equity)/self.equity_peak*100))

    def committed_risk(self,exclude:int=0)->tuple[float,float]:
        posrisk=0.; orderrisk=0.
        fallback=self.dd_balance_hwm*self.p.max_dd/100
        for pos in self.positions.values():
            if pos.sl<=0:posrisk+=fallback;continue
            current=self.bid if pos.buy else self.ask
            dist=(current-pos.sl) if pos.buy else (pos.sl-current)
            posrisk+=self.loss_per_lot(dist)*pos.lots if dist>0 else 0
        for order in self.orders.values():
            if order.id==exclude:continue
            r=self.loss_per_lot(abs(order.price-order.sl))*order.lots if order.sl>0 else fallback
            orderrisk+=r
        return posrisk,orderrisk

    def cap_volume(self,desired:float,sl_distance:float,exclude:int=0)->float:
        if not self.p.protect_dd or desired<=0 or sl_distance<=0:return desired
        if self.maxdd_locked:
            self.risk_last_reason='MAXDD_LOCK'
            return 0.
        loss=self.loss_per_lot(sl_distance)
        if loss<=0:
            self.risk_last_reason='INVALID_LOSS_PER_LOT'
            return 0.
        hard_ceiling=max(.01,self.p.max_dd-max(.10,min(.50,self.p.max_dd*.02)))
        ddmoney=max(0,self.dd_balance_hwm-self.equity)
        posrisk,orderrisk=self.committed_risk(exclude)
        allowed=self.dd_balance_hwm*hard_ceiling/100-ddmoney-posrisk-orderrisk
        if allowed<=0:
            self.risk_last_reason='DD_BUDGET_EXHAUSTED'
            return 0.
        allowed*=.85
        maxvol=allowed/loss
        if maxvol<self.p.volume_min:
            self.risk_last_reason='NOT_ENOUGH_RISK_BUDGET_FOR_MIN_LOT'
        else:
            self.risk_last_reason='OK'
        return desired if maxvol>=desired else self.normvol(maxvol,floor=True)

    def session_open(self)->bool:
        now=self.now
        if not now or now.weekday()>=5:return False
        n=now.hour*60+now.minute
        start,end=self._session_start_min,self._session_end_min
        return (start<=n<end) if start<end else (n>=start or n<end)

    def spread_ok(self):return (self.ask-self.bid)/self.p.point <= self.p.max_spread_points
    def pending_uses_day(self):return self.p.pending_policy in ('Pending_EndOfDay','Pending_Day_And_Age')
    def pending_uses_age(self):return self.p.pending_policy in ('Pending_MaxAge','Pending_Day_And_Age')

    def news_block(self):
        if self.now is None:return None
        now=self.now
        choices=[]
        for ev,kind in self.news_events:
            if not getattr(self.p,kind.lower()): continue
            if ev-timedelta(minutes=100)<=now<=ev+timedelta(minutes=60):
                choices.append((abs((ev-now).total_seconds()),ev,kind))
        if self.p.nfp and not choices:
            key = (now.year, now.month)
            if key != self._cached_nfp_month:
                origin = now.year*12+now.month-1
                evs=[]
                for off in (-1,0,1):
                    y,m=divmod(origin+off,12)
                    evs.append(nfp_server_time(y,m+1,self.p))
                self._cached_nfp_times=tuple(evs)
                self._cached_nfp_month=key
            for ev in self._cached_nfp_times:
                if ev-timedelta(minutes=self.p.nfp_minutes_before)<=now<=ev+timedelta(minutes=self.p.nfp_minutes_after):
                    choices.append((abs((ev-now).total_seconds()),ev,'NFP'))
        return min(choices)[1:] if choices else None

    def clear_ticket(self,ticket:int, suppress:bool=False):
        for lev in self.levels:
            if lev.used and lev.ticket==ticket:
                lev.ticket=0; lev.placed_at=None;lev.raw_lots=0
                if suppress:
                    lev.cap_suppressed=True;lev.cap_suppressed_at=self.now

    def delete_order(self,ticket:int,reason:str,suppress:bool=False,retire:bool=False):
        o=self.orders.pop(ticket,None)
        if o:
            self.order_history.append(dict(time=self.now,event='CANCEL',ticket=ticket,strategy=o.s,
                                          side='BUY_STOP' if o.buy else 'SELL_STOP',price=o.price,
                                          lots=o.lots,sl=o.sl,tp=o.tp,reason=reason))
            self.stats['canceled']+=1
            if self.diagnostics and self.diagnostics.event_logging:
                self.diagnostics.logger.info('ORDER_CANCEL t=%s ticket=%s strat=%s reason=%s',
                                             self.now, ticket, o.s, reason)
        self.clear_ticket(ticket,suppress)
        if retire and o is not None:
            if 0<=o.level<len(self.levels):self.levels[o.level].used=False

    def delete_all_pending(self,reason):
        for oid in list(self.orders):self.delete_order(oid,reason)

    def close_position(self,pid:int,price:float,reason:str):
        pos=self.positions.pop(pid,None)
        if pos is None:return
        gross=(price-pos.entry if pos.buy else pos.entry-price)*pos.lots*self.p.contract_size
        close_fee=pos.lots*self.p.commission_per_lot_side
        self.balance+=gross-close_fee
        if self.p.profile == 'Auto':
            self._auto_cap_cache = self.auto_cap()
        self.trades.append(dict(open_time=pos.opened,close_time=self.now,position=pid,order=pos.order_id,
                                strategy=pos.s,side='BUY' if pos.buy else 'SELL',lots=pos.lots,
                                entry=pos.entry,exit=price,sl=pos.sl,tp=pos.tp,gross=round(gross,4),
                                commission=round(pos.opening_fee+close_fee,4),
                                net=round(gross-close_fee-pos.opening_fee,4),balance=round(self.balance,4),reason=reason))
        self.stats['closed']+=1
        self.mark_equity()

    def close_all_positions(self,reason):
        if not self.session_open():return
        for pid in list(self.positions):
            pos=self.positions.get(pid)
            if pos:self.close_position(pid,self.bid if pos.buy else self.ask,reason)

    def _m1_crossing(self) -> bool:
        """An M1 OHLC vertex stands in for a continuous intrabar price leg.

        The opening vertex (second=0) is a possible real gap; fill it at the
        opening quote. The three following vertices are only extrema sampled
        from a synthetic path and should NOT be used as if they were fill quotes.
        """
        return (self.p.input_mode=='m1' and self.p.m1_fill_model=='level' and
                self.now.second in (20,40,59) and self.prev_time is not None and
                self.prev_time.replace(second=0)==self.now.replace(second=0))

    def _fill_stop_order(self, oid: int, entry: float):
        o=self.orders.pop(oid,None)
        if o is None:return
        self.pid+=1
        fee=o.lots*self.p.commission_per_lot_side
        self.balance-=fee
        self.positions[self.pid]=Position(self.pid,oid,o.s,o.buy,self.now,entry,o.lots,o.sl,o.tp,fee)
        self.order_history.append(dict(time=self.now,event='FILL',ticket=oid,strategy=o.s,
                                       side='BUY_STOP' if o.buy else 'SELL_STOP',price=entry,lots=o.lots,
                                       sl=o.sl,tp=o.tp,reason=f'POSITION_{self.pid}'))
        self.stats['filled']+=1
        self.last_fill_datetime=self.now
        if self.diagnostics and self.diagnostics.event_logging:
            self.diagnostics.logger.info('ORDER_FILL t=%s ticket=%s strat=%s entry=%.2f lot=%.2f',
                                         self.now,oid,o.s,entry,o.lots)

    def _broker_execute_m1_segment(self):
        """Interpolate OHLC vertex-to-vertex as a monotone price leg.

        Each pending entry, SL, and TP is treated as a threshold crossing, not
        a fill at the OHLC high/low. A new stop order may reach its TP in the
        same leg. The segment ordering is an *assumption*, not reconstructed ticks.
        Do not use this path for true tick data or for bar opening gaps.
        """
        # Expired orders cannot fill during the current processed segment.
        for oid,o in list(self.orders.items()):
            if o.expires and self.now>=o.expires:
                self.delete_order(oid,'BROKER_EXPIRY')
        start_bid,start_ask=self.prev_bid,self.prev_ask
        end_bid,end_ask=self.bid,self.ask
        is_trading=self.session_open()
        pos=0.0
        EPS=1e-10
        for _ in range(2048):
            candidates=[]
            def traverse(start,end,barrier,is_up):
                if barrier<=0:return None
                a=start+(end-start)*pos
                if is_up:
                    if end+EPS<barrier:return None
                    if a>=barrier-EPS:return pos
                else:
                    if end-EPS>barrier:return None
                    if a<=barrier+EPS:return pos
                d=end-start
                if abs(d)<EPS:return None
                f=(barrier-start)/d
                return max(pos,min(1.0,f)) if pos-EPS<=f<=1+EPS else None

            for pid,p in self.positions.items():
                if p.buy:
                    if p.sl>0:
                        q=traverse(start_bid,end_bid,p.sl,False)
                        if q is not None:candidates.append((q,0,'SL',pid,p.sl))
                    if p.tp>0:
                        q=traverse(start_bid,end_bid,p.tp,True)
                        if q is not None:candidates.append((q,0,'TP',pid,p.tp))
                else:
                    if p.sl>0:
                        q=traverse(start_ask,end_ask,p.sl,True)
                        if q is not None:candidates.append((q,0,'SL',pid,p.sl))
                    if p.tp>0:
                        q=traverse(start_ask,end_ask,p.tp,False)
                        if q is not None:candidates.append((q,0,'TP',pid,p.tp))
            if is_trading:
                for oid,o in self.orders.items():
                    if o.buy:
                        q=traverse(start_ask,end_ask,o.price,True)
                    else:
                        q=traverse(start_bid,end_bid,o.price,False)
                    if q is not None:candidates.append((q,1,'ENTRY',oid,o.price))
            if not candidates:break
            f,priority,kind,ident,barrier=min(candidates,key=lambda x:(x[0],x[1],x[3]))
            pos=f
            if kind=='ENTRY':
                self._fill_stop_order(ident,barrier)
            else:
                self.close_position(ident,barrier,kind)
        else:
            raise RuntimeError('Runaway M1 intra-segment order triggering (2048 events)')
        self.mark_equity()

    def broker_execute(self):
        # Flat account with no pending orders has no price-dependent state
        # transitions. MQL5 broker matching similarly finds nothing to do.
        if not self.orders and not self.positions:return
        if self._m1_crossing():
            self._broker_execute_m1_segment()
            return
        """Broker triggers run on every tick BEFORE EA minute-gated rules."""
        # Protective stop and TP on Bid for buys, Ask for sells.
        for pid,pos in list(self.positions.items()):
            px=self.bid if pos.buy else self.ask
            if pos.sl>0 and ((pos.buy and px<=pos.sl) or (not pos.buy and px>=pos.sl)):
                self.close_position(pid,pos.sl if self.p.input_mode=='m1' and self.p.m1_fill_model=='level' else px,'SL')
            elif pos.tp>0 and ((pos.buy and px>=pos.tp) or (not pos.buy and px<=pos.tp)):
                self.close_position(pid,pos.tp if self.p.input_mode=='m1' and self.p.m1_fill_model=='level' else px,'TP')
        if not self.session_open():return
        for oid,o in list(self.orders.items()):
            if o.expires and self.now>=o.expires:
                self.delete_order(oid,'BROKER_EXPIRY');continue
            active = self.ask>=o.price if o.buy else self.bid<=o.price
            if not active:continue
            if oid not in self.orders:continue
            self._fill_stop_order(oid,o.price if self.p.input_mode=='m1' and self.p.m1_fill_model=='level' else (self.ask if o.buy else self.bid))
        self.mark_equity()

    def hard_risk_update(self):
        self.dd_balance_hwm=max(self.dd_balance_hwm,self.balance)
        if self.day_key!=self.now.date():
            self.day_key=self.now.date()
            self.day_start_balance=self.balance
            self.daily_dd_locked=False
        if self.p.daily_dd>0 and not self.daily_dd_locked:
            buffer=min(.10,max(0,self.p.daily_dd-.01))
            trigger=max(.01,self.p.daily_dd-buffer)
            if (self.day_start_balance-self.equity)/self.day_start_balance*100>=trigger:
                self.daily_dd_locked=True
                self.delete_all_pending('DAILY_DD_LOCK')
                self.close_all_positions('DAILY_DD_LOCK')
        if not self.p.protect_dd:return
        d=self.account_dd()
        target=self.p.max_dd
        trigger=max(.01,target-max(.1,target*.1))
        reserve=max(.10,min(.50,target*.02))
        ceiling=max(trigger,target-reserve)
        if not self.recovery_mode and not self.maxdd_locked and d>=trigger:
            self.maxdd_locked=True;self.recovery_mode=True
            self.delete_all_pending('MAXDD_EARLY_STOP')
            self.close_all_positions('MAXDD_EARLY_STOP')
        if self.maxdd_locked:
            self.delete_all_pending('MAXDD_LOCK')
            if not self.positions and not self.orders and self.account_dd()<ceiling:
                self.maxdd_locked=False
            return
        if self.recovery_mode:
            if self.account_dd()<=target*.70:self.recovery_mode=False
            elif self.account_dd()>=ceiling:
                self.maxdd_locked=True
                self.delete_all_pending('MAXDD_RECOVERY_STOP')
                self.close_all_positions('MAXDD_RECOVERY_STOP')

    def pivot_at(self,tf:str,shift:int,left:int,right:int,high:bool)->bool:
        ser=self.bars[tf]
        if shift<=right or ser.count()<=shift+left+2:return False
        bar=ser.bar(shift)
        if not bar:return False
        v=bar.high if high else bar.low
        if v<=0:return False
        for k in range(1,left+1):
            old=ser.bar(shift+k)
            if old is None or ((v<=old.high) if high else (v>=old.low)):return False
        for k in range(1,right+1):
            new=ser.bar(shift-k)
            if new is None or ((v<new.high) if high else (v>new.low)):return False
        return True

    def find_level(self,s:int,buy:bool,pivottime:datetime):
        return any(z.used and z.s==s and z.buy==buy and z.pivot_time==pivottime for z in self.levels)

    def within_countback(self,lev:Level):
        sh=self.bars[self.cfg[lev.s].entry_tf].shift_at(lev.pivot_time)
        return 0<=sh<=self.cfg[lev.s].countback

    def broken(self,lev:Level):
        ser=self.bars[self.cfg[lev.s].entry_tf]
        closed=ser.bar(1)
        if not closed or closed.time<=lev.confirmed_time:return False
        return closed.close>lev.pivot_price+self.p.point*.25 if lev.buy else closed.close<lev.pivot_price-self.p.point*.25

    def candidate_entry(self,lev:Level)->float:
        c=self.cfg[lev.s]
        offset=c.buy_offset if lev.buy else c.sell_offset
        return self.normprice(lev.pivot_price+self.pips(offset,self.price))

    def rearm_suppressed(self,s:int,buy:bool,reason:str):
        if reason=='NEW_PIVOT':
            delay=NEW_PIVOT_REARM_DELAY[s]; count=NEW_PIVOT_REARM_MAX[s]
        else: delay=0; count=99
        for _ in range(count):
            cand=[(self.candidate_entry(self.levels[i]),i) for i in self.level_indices[s]
                  if (z:=self.levels[i]).used and z.s==s and z.buy==buy and z.cap_suppressed and not z.ticket
                  and (z.cap_suppressed_at is None or (self.now-z.cap_suppressed_at).total_seconds()>=delay*60)
                  and self.within_countback(z) and not self.broken(z)]
            if not cand:break
            _,idx=(min(cand) if buy else max(cand))
            self.levels[idx].cap_suppressed=False
            self.levels[idx].cap_suppressed_at=None

    def add_level(self,s:int,buy:bool,shift:int):
        c=self.cfg[s];ser=self.bars[c.entry_tf]
        piv=ser.bar(shift)
        confirmed=ser.bar(shift-c.right-1)
        if piv is None or confirmed is None or self.find_level(s,buy,piv.time):return
        if (not self.p.use_strategy_specific_rearm or not CAPACITY_REARM[s]) and \
           self.p.cap_rearm_policy in ('CapRearm_On_New_Pivot','CapRearm_NewPivot_Session'):
            self.rearm_suppressed(s,buy,'NEW_PIVOT')
        lev=Level(s,buy,piv.time,confirmed.time,piv.high if buy else piv.low)
        # MQL5 level registry prefers the first free slot, otherwise high-water.
        ix=next((i for i,z in enumerate(self.levels) if not z.used),None)
        if ix is None:
            if len(self.levels)>=4096:return
            ix=len(self.levels)
            self.levels.append(lev)
        else:
            previous=self.levels[ix]
            self.level_indices[previous.s].discard(ix)
            self.levels[ix]=lev
        self.level_indices[s].add(ix)
        self.stats['pivots']+=1

    def detect_pivots(self,s:int):
        c=self.cfg[s]; sh=c.right+1
        if self.pivot_at(c.entry_tf,sh,c.left,c.right,True):self.add_level(s,True,sh)
        if self.pivot_at(c.entry_tf,sh,c.left,c.right,False):self.add_level(s,False,sh)

    def level_eligible(self,idx:int)->Optional[float]:
        lev=self.levels[idx];s=lev.s
        if not lev.used or not self.enabled(s) or (lev.buy and not self.p.allow_buy) or \
           (not lev.buy and not self.p.allow_sell):
            return None
        if lev.cap_suppressed and not lev.ticket:
            self.stats['skip_suppressed_level']+=1
            return None
        if not self.within_countback(lev) or self.broken(lev):
            self.stats['skip_old_or_broken_level']+=1
            return None
        entry=self.candidate_entry(lev)
        if entry<=0:
            self.stats['skip_invalid_entry_price']+=1
            return None
        if lev.ticket and lev.ticket in self.orders:return entry
        if lev.retry_after is not None and self.now<lev.retry_after:
            self.stats['skip_retry_cooldown']+=1
            return None
        if not self.spread_ok():
            self.stats['skip_spread']+=1
            return None
        if not self.entry_distance_gate(lev):
            self.stats['skip_entry_distance']+=1
            return None
        if not self.side_valid(lev.buy,entry):
            self.stats['skip_stop_entry_side']+=1
            return None
        return entry

    def entry_distance_gate(self,lev:Level):
        current=self.ask if lev.buy else self.bid
        if current<=0:return False
        dist=(lev.pivot_price-current)/current if lev.buy else (current-lev.pivot_price)/current
        return dist>=self.cfg[lev.s].min_dist_pct

    def side_valid(self,buy:bool,entry:float):
        mingap=self.p.stops_level_points*self.p.point
        return entry>self.ask+mingap if buy else entry<self.bid-mingap

    def edge_blocked(self,s:int,buy:bool):
        if self.p.growth!='Best_risk_reward' or not self.p.use_robust_edge_filters:return False
        return (s==5 and self.now.month==10) or (s==5 and not buy and self.now.hour==21) or \
               (s==6 and buy and self.now.hour==16)

    def consume_broken(self):
        # ConsumeBrokenLevels in MT5 is globally idempotent at a fixed market
        # timestamp. All signal/pivot discovery is done before entry processing.
        # Scanning it once rather than once *per strategy* saves work.
        if self._last_broken_scan==self.now:return
        self._last_broken_scan=self.now
        for i,lev in enumerate(self.levels):
            if not lev.used or not self.broken(lev):continue
            if lev.ticket:self.delete_order(lev.ticket,'LEVEL_BROKEN_CLOSE')
            lev.used=False

    def capacity_rearm(self,s:int):
        if not self.p.use_strategy_specific_rearm or not CAPACITY_REARM[s]:return
        c=self.cfg[s]
        for buy in (True,False):
            cap=c.max_buy if buy else c.max_sell
            active=sum(1 for o in self.orders.values() if o.s==s and o.buy==buy)
            free=max(0,cap-active)
            for _ in range(free):
                eligible=[(self.candidate_entry(self.levels[i]),i) for i in self.level_indices[s]
                          if (z:=self.levels[i]).used and z.s==s and z.buy==buy and z.cap_suppressed and not z.ticket
                          and z.cap_suppressed_at and (self.now-z.cap_suppressed_at).total_seconds()>=CAP_REARM_DELAY[s]*60
                          and self.within_countback(z) and not self.broken(z)]
                if not eligible:break
                _,i=(min(eligible) if buy else max(eligible))
                self.levels[i].cap_suppressed=False
                self.levels[i].cap_suppressed_at=None

    def build_desired(self,s:int)->set[int]:
        out=set()
        c=self.cfg[s]
        for buy in (True,False):
            eligible=[]
            for i in sorted(self.level_indices[s]):
                z=self.levels[i]
                if not z.used or z.s!=s or z.buy!=buy:continue
                entry=self.level_eligible(i)
                if entry is not None:eligible.append((entry,i))
            eligible.sort(key=lambda x:x[0],reverse=not buy)
            cap=c.max_buy if buy else c.max_sell
            out.update(i for _,i in eligible[:cap])
        return out

    def place(self,idx:int,entry:float,sl:float,tp:float,lots:float,desired:float)->bool:
        lev=self.levels[idx]
        if len(self.orders)>100000:raise RuntimeError('Unexpected active order explosion')
        if not self.side_valid(lev.buy,entry):return False
        if lots<self.p.volume_min-1e-10:return False
        self.oid+=1;ticket=self.oid
        expires=None
        if self.p.pending_policy!='Pending_Original':
            if self.pending_uses_day():
                expires=self.now.replace(hour=self.p.pending_cutoff_hour,minute=0,second=0,microsecond=0)
            if self.pending_uses_age():
                age=self.now+timedelta(hours=self.p.pending_max_age_hours)
                expires=min(age,expires) if expires is not None else age
            if expires and expires<=self.now+timedelta(seconds=10):expires=None
        o=Order(ticket,idx,lev.s,lev.buy,self.now,entry,lots,sl,tp,expires)
        self.orders[ticket]=o
        lev.ticket=ticket;lev.placed_at=self.now;lev.raw_lots=desired;lev.retry_after=None
        self.order_history.append(dict(time=self.now,event='PLACE',ticket=ticket,strategy=lev.s,
                                      side='BUY_STOP' if lev.buy else 'SELL_STOP',price=entry,
                                      lots=lots,sl=sl,tp=tp,reason='LEVEL_ENTRY'))
        self.stats['placed']+=1
        if self.diagnostics and self.diagnostics.event_logging:
            self.diagnostics.logger.info('ORDER_PLACE t=%s ticket=%s strat=%s price=%.2f lot=%.2f',
                                         self.now, ticket, lev.s, entry, lots)
        return True

    def margin_available(self,lots:float,entry:float):
        if not self.p.check_margin:return True
        required=lots*entry*self.p.contract_size/self.p.leverage
        committed=sum(z.lots*self.price*self.p.contract_size/self.p.leverage for z in self.positions.values())
        return self.equity-committed >= required*1.01

    def process_level(self,idx:int,desired:set[int]):
        lev=self.levels[idx]
        if not lev.used or not self.enabled(lev.s):return
        if (lev.buy and not self.p.allow_buy) or (not lev.buy and not self.p.allow_sell):return
        if self.edge_blocked(lev.s,lev.buy):
            self.stats['skip_robust_edge_filter']+=1
            if lev.ticket:self.delete_order(lev.ticket,'ROBUST_EDGE_FILTER')
            return
        if self.broken(lev) or not self.within_countback(lev):
            if lev.ticket:self.delete_order(lev.ticket,'LEVEL_RETIRED')
            lev.used=False
            return
        if lev.ticket and lev.ticket not in self.orders:
            lev.ticket=0;lev.placed_at=None;lev.raw_lots=0
        if idx not in desired:
            if lev.ticket:
                self.delete_order(lev.ticket,'MAX_PENDING_CAP',suppress=True)
            elif not lev.cap_suppressed:
                lev.cap_suppressed=True;lev.cap_suppressed_at=self.now
            return
        if lev.cap_suppressed and not lev.ticket:return
        c=self.cfg[lev.s]
        if lev.ticket:
            if self.p.pending_policy=='Pending_Original' and self.p.virtual_expiration and c.expiry_h>0 and \
               lev.placed_at and (self.now-lev.placed_at).total_seconds()>=c.expiry_h*3600:
                self.delete_order(lev.ticket,'VIRTUAL_EXPIRY')
            else:
                desired_lots=self.lots_for(lev.s,self.pips(c.sl))
                baseline=lev.raw_lots or self.orders[lev.ticket].lots
                changed=baseline>0 and abs(desired_lots-baseline)/baseline>.15
                old_enough=lev.placed_at and (self.now-lev.placed_at).total_seconds()>=6*3600
                if changed and old_enough:self.delete_order(lev.ticket,'RAW_LOTSIZE_CHANGE_GT15PCT')
                else:return
        if lev.retry_after and self.now<lev.retry_after:
            self.stats['skip_retry_cooldown']+=1
            return
        if not self.spread_ok():
            self.stats['skip_spread']+=1
            return
        if not self.entry_distance_gate(lev):
            self.stats['skip_entry_distance']+=1
            return
        entry=self.candidate_entry(lev)
        if not self.side_valid(lev.buy,entry):
            self.stats['skip_stop_entry_side']+=1
            return
        sl_dist=self.pips(max(0,c.sl),self.price)
        tp_dist=self.pips(max(0,c.tp),self.price)
        sl=self.normprice(entry-sl_dist if lev.buy else entry+sl_dist)
        tp=self.normprice(entry+tp_dist if lev.buy else entry-tp_dist)
        desired_lots=self.lots_for(lev.s,sl_dist)
        lots=self.cap_volume(desired_lots,sl_dist)
        if lots<=0:
            self.stats['entry_risk_blocked']+=1
            self.stats['risk_'+self.risk_last_reason]+=1
            return
        if not self.margin_available(lots,entry):
            self.stats['entry_margin_blocked']+=1
            lev.retry_after=self.now+timedelta(seconds=max(60,self.p.failed_order_retry_seconds))
            return
        self.place(idx,entry,sl,tp,lots,desired_lots)

    def process_entries(self,s:int):
        if not self.session_open() or not self.enabled(s):return
        self.stats['strategy_scans']+=1
        self.consume_broken()
        self.capacity_rearm(s)
        desired=self.build_desired(s)
        if not desired:
            self.stats['scans_without_candidates']+=1
        for i in sorted(self.level_indices[s]):
            z=self.levels[i]
            if z.used and z.s==s:self.process_level(i,desired)

    def hl_pivot(self,s:int,buy:bool)->float:
        c=self.cfg[s]
        key=(s,buy,self.now.replace(second=0,microsecond=0))
        if key in self.hl_cache:return self.hl_cache[key]
        val=0.0; ser=self.bars[c.hl_tf]
        maxsh=min(c.hl_countback+c.hl_right+2,ser.count()-c.hl_left-2)
        for sh in range(c.hl_right+1,maxsh+1):
            if self.pivot_at(c.hl_tf,sh,c.hl_left,c.hl_right,not buy):
                bar=ser.bar(sh)
                val=bar.low if buy else bar.high
                break
        self.hl_cache[key]=val
        if len(self.hl_cache)>500:self.hl_cache.clear()
        return val

    def improve_sl(self,buy:bool,old:float,new:float)->bool:
        return new>0 and (old<=0 or (new>old+self.p.point*.5 if buy else new<old-self.p.point*.5))

    def move_tp(self,buy:bool,old:float,new:float)->bool:
        return new>0 and old>0 and (new<old-self.p.point*.5 if buy else new>old+self.p.point*.5)

    def manage_position(self,pos:Position):
        c=self.cfg[pos.s];now=self.now
        current=self.bid if pos.buy else self.ask
        favorable=current-pos.entry if pos.buy else pos.entry-current
        adverse=pos.entry-current if pos.buy else current-pos.entry
        sl=pos.sl;tp=pos.tp
        reason=[]
        if c.be_start>=0 and favorable>=self.pips(c.be_start):
            candidate=self.normprice(pos.entry+self.pips(c.be_extra) if pos.buy else pos.entry-self.pips(c.be_extra))
            if self.improve_sl(pos.buy,sl,candidate):sl=candidate;reason.append('BE')
        if c.fixed_size>0 and favorable>=self.pips(c.fixed_start):
            dist=self.pips(c.fixed_size)
            candidate=self.normprice(current-dist if pos.buy else current+dist)
            if self.improve_sl(pos.buy,sl,candidate):sl=candidate;reason.append('FIXED_TRAIL')
        if self.p.use_hl_trailing and c.hl_enabled:
            pivot=self.hl_pivot(pos.s,pos.buy)
            if pivot>0:
                dist=self.pips(c.hl_extra)
                candidate=self.normprice(pivot-dist if pos.buy else pivot+dist)
                if (candidate<current if pos.buy else candidate>current) and self.improve_sl(pos.buy,sl,candidate):
                    sl=candidate;reason.append('HL_TRAIL')
        if self.p.time_trail_s9 and c.time_trail and (now-pos.opened).total_seconds()>=c.time_trail_min*60:
            dist=self.pips(c.time_trail_size)
            candidate=self.normprice(current-dist if pos.buy else current+dist)
            if self.improve_sl(pos.buy,sl,candidate):sl=candidate;reason.append('TIME_TRAIL')
        if c.tp_recovery and adverse>=self.pips(c.recovery_start):
            dist=self.pips(c.recovery_size)
            candidate=self.normprice(current+dist if pos.buy else current-dist)
            if self.move_tp(pos.buy,tp,candidate):tp=candidate;reason.append('RECOVERY_TP')
        if abs(sl-pos.sl)>self.p.point*.5 or abs(tp-pos.tp)>self.p.point*.5:
            # Broker may reject a modify if freeze/stops-level rules are violated.
            valid=(sl<=current-self.p.stops_level_points*self.p.point if pos.buy else sl>=current+self.p.stops_level_points*self.p.point)
            valid &= (tp>=current+self.p.stops_level_points*self.p.point if pos.buy else tp<=current-self.p.stops_level_points*self.p.point)
            if valid:
                self.management_history.append(dict(time=now,position=pos.id,strategy=pos.s,
                                                    old_sl=pos.sl,new_sl=sl,old_tp=pos.tp,new_tp=tp,
                                                    reason='|'.join(reason)))
                pos.sl=sl;pos.tp=tp;self.stats['modify']+=1

    def manage_positions(self):
        if not self.session_open():return
        minute=self.now.replace(second=0,microsecond=0)
        if minute==self.last_management_minute:return
        self.last_management_minute=minute
        for pos in reversed(list(self.positions.values())):
            if pos.id in self.positions and self.enabled(pos.s) and self.now.minute%self.cfg[pos.s].manage_min==0:
                self.manage_position(pos)

    def fakeout_tf(self,tf:str,only_s4:bool):
        ser=self.bars[tf]
        b=ser.bar(0)
        if b is None or self.last_fake[tf]==b.time:return
        self.last_fake[tf]=b.time
        past=ser.bar(1)
        if not past:return
        for pos in reversed(list(self.positions.values())):
            if only_s4 and pos.s!=4:continue
            if not only_s4 and pos.s==4 and tf=='M1':continue
            if not (past.time<=pos.opened<b.time):continue
            losing=past.close<pos.entry if pos.buy else past.close>pos.entry
            against=past.close<past.open if pos.buy else past.close>past.open
            if losing and against:
                self.close_position(pos.id,self.bid if pos.buy else self.ask,'FAKEOUT_'+tf)
                self.stats['fakeout']+=1

    def process_fakeout(self):
        if self.p.fakeout_filter=='Off' or not self.session_open():return
        self.fakeout_tf('M1',False)
        self.fakeout_tf('M5',True)
        if self.p.fakeout_filter in ('Medium','High'):
            self.fakeout_tf('M15',False)
        if self.p.fakeout_filter=='High':self.fakeout_tf('H1',False)

    def pending_lifecycle(self):
        if self.p.pending_policy=='Pending_Original' and not self.p.close_positions_at_cutoff:return
        daycut=(self.pending_uses_day() and self.now.hour>=self.p.pending_cutoff_hour) or \
               (self.p.close_positions_at_cutoff and self.now.hour>=self.p.position_cutoff_hour)
        for oid,o in list(self.orders.items()):
            agecut=self.pending_uses_age() and (self.now-o.created).total_seconds()>=self.p.pending_max_age_hours*3600
            if daycut or agecut:self.delete_order(oid,'DAY_END_PENDING' if daycut else 'MAX_PENDING_AGE',retire=True)
        if daycut:
            for lev in self.levels:
                if lev.used and (not lev.ticket or lev.ticket not in self.orders):lev.used=False

    def tick(self,t:datetime,bid:float,ask:float):
        if bid<=0 or ask<bid:raise ValueError(f'Invalid Bid/Ask at {t}: {bid}, {ask}')
        self.prev_bid,self.prev_ask,self.prev_time = self.bid,self.ask,self.now
        self.now=t;self.bid=bid;self.ask=ask
        if self.first_tick is None:self.first_tick=t
        self.last_tick=t
        if self.started:
            self.broker_execute()
        else:
            self.mark_equity()
        changed={k:s.push(t,bid) for k,s in self.bars.items()}
        if not self.started:
            # OnInit marks current bars as already observed, NOT as a new signal.
            for s,c in self.cfg.items():
                self.last_entry[s]=self.bars[c.entry_tf].bar(0).time
                self.last_check[s]=self.bars[c.check_tf].bar(0).time
            for tf in self.last_fake:
                self.last_fake[tf]=self.bars[tf].bar(0).time
            self.last_session_open=self.session_open()
            self.started=True
            return
        self.hard_risk_update()
        minute=t.replace(second=0,microsecond=0)
        if self.p.fast_minute_logic and minute==self.last_logic_minute:return
        self.last_logic_minute=minute
        self.pending_lifecycle()
        if self.p.close_positions_at_cutoff and t.hour>=self.p.position_cutoff_hour:
            self.close_all_positions('EOD_POSITION_CLOSE')
        block_entries=self.pending_uses_day() and t.hour>=self.p.pending_cutoff_hour
        block_positions=self.p.close_positions_at_cutoff and t.hour>=self.p.position_cutoff_hour
        if not block_entries and not block_positions:
            for s,c in self.cfg.items():
                if not self.enabled(s):continue
                bar=self.bars[c.entry_tf].bar(0)
                if bar and bar.time!=self.last_entry[s]:
                    self.last_entry[s]=bar.time
                    self.detect_pivots(s)
        session=self.session_open()
        news=self.news_block()
        if news is not None and self.last_news_action!=news:
            self.last_news_action=news
            self.delete_all_pending('NEWS_'+news[1])
            self.close_all_positions('NEWS_'+news[1])
        friday=(self.p.friday_stop_hour<=23 and t.weekday()==4 and t.hour>=self.p.friday_stop_hour)
        if friday:
            self.delete_all_pending('FRIDAY_STOP')
            self.close_all_positions('FRIDAY_STOP')
        self.process_fakeout()
        self.manage_positions()
        session_reopened=session and not self.last_session_open
        news_released=news is None and self.last_news_block
        if news is None and not friday and not self.daily_dd_locked and not self.maxdd_locked and \
           session and not block_entries and not block_positions:
            if self.balance > self.highest_balance:
                self.highest_balance = self.balance
                if self.p.profile == 'Auto':
                    self._auto_cap_cache = self.auto_cap()
            for s,c in self.cfg.items():
                if not self.enabled(s):continue
                cb=self.bars[c.check_tf].bar(0)
                check_bar=cb is not None and cb.time!=self.last_check[s]
                if check_bar:self.last_check[s]=cb.time
                if session_reopened and not self.p.use_strategy_specific_rearm and \
                   self.p.cap_rearm_policy=='CapRearm_NewPivot_Session':
                    self.rearm_suppressed(s,True,'SESSION_REOPEN')
                    self.rearm_suppressed(s,False,'SESSION_REOPEN')
                if check_bar or session_reopened or news_released:
                    self.process_entries(s)
        self.last_session_open=session
        self.last_news_block=news is not None
        self.mark_equity()
        self.stats['ticks']+=1

    def tick_m1_vertex(self,t:datetime,bid:float,ask:float):
        """Process an interior M1 vertex without repeated minute-bar boundary scans.

        Equivalent to tick() for seconds=15/35/59 when fast_minute_logic is on;
        broker matching, risk state, and all six OHLC bar updates still occur.
        The first bar must take the normal tick path to initialize EA state.
        """
        if self.last_logic_minute is None or not self.p.fast_minute_logic:
            return self.tick(t,bid,ask)
        if bid<=0 or ask<bid:
            raise ValueError(f'Invalid Bid/Ask at {t}: {bid}, {ask}')
        self.prev_bid,self.prev_ask,self.prev_time = self.bid,self.ask,self.now
        self.now=t;self.bid=bid;self.ask=ask;self.last_tick=t
        self.broker_execute()
        for series in self.bars.values():
            # Synthetic OHLC vertices never cross a timeframe boundary.
            # Update OHLC values just as Series.push() would.
            series.cur.add(bid)
        # Within a single minute, if there are no positions, no orders and
        # equity did not change, the drawdown daily/peak state is constant.
        if self.positions or self.orders:self.hard_risk_update()

    def prewarm_m1_bar(self,t:datetime,o:float,h:float,l:float,c:float):
        """Load pre-start historical OHLC for pivot lookbacks, without trading.

        MetaTrader's Strategy Tester may make historical bars accessible before
        the selected test start. Do not trade during prewarm and do not bootstrap
        historical pending levels; the MQ5 source defaults bootstrap=False.
        """
        for series in self.bars.values():
            series.push(t,o)
            bar=series.cur
            if h>bar.high:bar.high=h
            if l<bar.low:bar.low=l
            bar.close=c

    def run_m1_fast(self,source:Iterable[tuple[datetime,float,float,float,float,float]],
                    diagnostics:Optional[RunDiagnostics]=None,
                    start_date:Optional[date]=None,end_date:Optional[date]=None):
        """Replay minute OHLC control points using MT5-style timestamps and tick volumes.

        For a four-tick M1 bar the MT5 tester historically uses 00/20/40/59
        second checkpoints.  A one-tick flat M1 bar uses only second 30.
        The distinction matters: it changes entries, news/session timing and
        order lifetime.  Tick-volume 2/3 are modeled conservatively, with
        explicit synthetic timings, not claimed to be tick-equivalent.
        """
        self.diagnostics=diagnostics
        warmup_bars=0
        for record in source:
            t,o,h,l,c,spr=record[:6]
            tv=record[6] if len(record)>6 else 4
            if end_date is not None and t.date()>end_date:break
            if start_date is not None and t.date()<start_date:
                self.prewarm_m1_bar(t,o,h,l,c)
                warmup_bars+=1
                if diagnostics:diagnostics.tick_done(self,t,max(1,min(4,tv)))
                continue
            if warmup_bars:
                if diagnostics:diagnostics.logger.info('PREWARM: %s M1 bars loaded for historical pivots; no pre-start trades placed.',f'{warmup_bars:,}')
                warmup_bars=0
            if tv<=1:
                # In MT5 1-minute OHLC the single recorded tick arrives at :30.
                vt=t.replace(second=30)
                self.tick(vt,c,c+spr)
                emitted=1
            else:
                q=(o,l,h,c) if c>=o else (o,h,l,c)
                if tv==2:
                    schedule=((0,o),(30,c))
                elif tv==3:
                    schedule=((0,q[0]),(20,q[1]),(40,q[3]))
                else:
                    schedule=((0,q[0]),(20,q[1]),(40,q[2]),(59,q[3]))
                second,price=schedule[0]
                vt=t.replace(second=second)
                self.tick(vt,price,price+spr)
                for second,price in schedule[1:]:
                    vt=t.replace(second=second)
                    self.tick_m1_vertex(vt,price,price+spr)
                emitted=len(schedule)
            if diagnostics:diagnostics.tick_done(self,vt,emitted)
        if diagnostics:
            diagnostics.last_tick_wall=time.monotonic()
            diagnostics.stage='finalizing'
            diagnostics.report(self,force=True)
        return self

    def run(self,source:Iterable[tuple[datetime,float,float]],
            diagnostics: Optional[RunDiagnostics]=None):
        self.diagnostics = diagnostics
        for t,bid,ask in source:
            self.tick(t,bid,ask)
            if diagnostics:
                diagnostics.tick_done(self,t)
        if diagnostics:
            diagnostics.last_tick_wall = time.monotonic()
            diagnostics.stage='finalizing'
            diagnostics.report(self,force=True)
        return self

    def summary(self)->dict:
        return dict(source='GoldReaper_v2_Pass3436_Robuust.mq5',period_start=str(self.first_tick),
                    period_end=str(self.last_tick),balance_initial=self.initial_balance,
                    balance_final=round(self.balance,2),equity_final=round(self.equity,2),
                    net_profit=round(self.balance-self.initial_balance,2),
                    trades_closed=len(self.trades),pending_open=len(self.orders),positions_open=len(self.positions),
                    total_pending_placed=self.stats['placed'],total_pending_filled=self.stats['filled'],
                    total_pending_canceled=self.stats['canceled'],max_equity_drawdown_pct=round(self.max_equity_dd,4),
                    simulated_m1_fill_model=self.p.m1_fill_model if self.p.input_mode=='m1' else None,
                    assumed_m1_spread_points=self.p.spread_points if self.p.input_mode=='m1' else None,
                    peak_closed_balance=round(self.dd_balance_hwm,2),
                    current_balance_dd_pct=round(self.realized_dd(),3),
                    risk_block_reasons={k:v for k,v in self.stats.items() if k.startswith('risk_')},
                    hard_drawdown_locked=self.maxdd_locked,
                    recovery_mode=self.recovery_mode,
                    risk_last_reason=self.risk_last_reason,
                    fills_by_strategy=dict(Counter(z['strategy'] for z in self.order_history if z['event']=='FILL')))

    def save(self,root:str):
        root=Path(root)
        root.mkdir(parents=True,exist_ok=True)
        def write(name,rows):
            if not rows:return
            with (root/name).open('w',encoding='utf-8',newline='') as fh:
                out=csv.DictWriter(fh,fieldnames=list(rows[0]),extrasaction='ignore')
                out.writeheader()
                for row in rows:out.writerow(row)
        write('pending_order_events.csv',self.order_history)
        write('closed_trades.csv',self.trades)
        write('position_modifications.csv',self.management_history)
        import json
        with (root/'summary.json').open('w',encoding='utf-8') as fh:
            json.dump(self.summary(),fh,indent=2)
        # Year-by-year trade activity is a direct sanity check for a model that
        # trades in early history but later stalls due to a drawdown risk gate.
        byyear={}
        for tr in self.trades:
            y=tr['open_time'].year
            key=(y,int(tr['strategy']))
            if key not in byyear:byyear[key]=dict(year=y,strategy=key[1],trades=0,wins=0,net_profit=0.0)
            item=byyear[key];item['trades']+=1
            item['wins']+=int(tr['net']>0)
            item['net_profit']+=float(tr['net'])
        rows=[]
        for (y,strat),item in sorted(byyear.items()):
            rows.append(dict(year=y,strategy=strat,trades=item['trades'],wins=item['wins'],
                             winrate_pct=round(100*item['wins']/item['trades'],2),
                             net_profit=round(item['net_profit'],2)))
        write('year_strategy_activity.csv',rows)
        return root


def compare_entries(engine: GoldReaper, reference_csv: str) -> dict:
    """Strict identity audit against original MT5 entry deals (not rounded PnL).

    Accepts gold_reaper_test2_strategies_trades.csv or a CSV with columns
    entry_time,strat,side,entry_price. All entry timestamps must be in the
    same BROKER SERVER timezone as the input tick stream.
    """
    original = []
    for row in _rows(reference_csv):
        t = row.get('entry_time') or row.get('open_time')
        strat = row.get('strat') or row.get('strategy')
        side = row.get('side')
        price = row.get('entry_price') or row.get('entry')
        if not all((t,strat,side,price)):
            raise ValueError('Reference needs entry_time,strat,side,entry_price')
        dt=_dt(t)
        # MT5 reports often contain decades. Compare only the current run's
        # date range or the unmatched rows would dominate the parity result.
        if engine.first_tick and dt<engine.first_tick:continue
        if engine.last_tick and dt>engine.last_tick:continue
        original.append((dt, int(strat), side.strip().upper().replace('_STOP',''),
                         round(float(price),engine.p.digits)))
    actual = []
    for event in engine.order_history:
        if event['event']!='FILL': continue
        actual.append((event['time'],int(event['strategy']),
                       event['side'].replace('_STOP',''),round(float(event['price']),engine.p.digits)))
    a=Counter(actual); b=Counter(original)
    common=a & b
    missing=b-common
    unexpected=a-common
    ac=Counter((v[0].year,v[1]) for v in actual)
    rc=Counter((v[0].year,v[1]) for v in original)
    annual=[]
    for y,strat in sorted(ac.keys() | rc.keys()):
        pv=ac.get((y,strat),0);rv=rc.get((y,strat),0)
        annual.append(dict(year=y,strategy=strat,reference_trades=rv,python_trades=pv,
                           difference=pv-rv,relative_difference_pct=round(100*(pv-rv)/rv,2) if rv else None))
    result = dict(reference_entries=len(original), python_entries=len(actual),
                  reference_scope='same run period',
                  counts_by_year_strategy=annual,
                  comparison_notice='An MT5 report from a different EA/inputs is NOT an exact oracle; M1 OHLC does not encode tick timing.',
                  exact_matching_entries=sum(common.values()),
                  missing_reference_entries=sum(missing.values()),
                  unexpected_python_entries=sum(unexpected.values()),
                  exact_recall_pct=round(100*sum(common.values())/len(original),4) if original else 0,
                  exact_precision_pct=round(100*sum(common.values())/len(actual),4) if actual else 0,
                  examples_missing=[list(k) for k in list(missing.elements())[:10]],
                  examples_unexpected=[list(k) for k in list(unexpected.elements())[:10]],
                  verified_exact=not missing and not unexpected)
    return result


def _mt5_reference_orders(path: str, first: datetime, last: datetime) -> list[dict]:
    """Load pending orders from an original MT5 tester HTML or an orders CSV.

    The HTML format uses one <tr> per order; parse it as a stream so the
    60+ MB report does not have to be materialized in memory as a DOM.
    This parses *actual* MT5 order records, never the generated Python log.
    """
    import re
    import html
    records = []
    path = str(path)
    if Path(path).suffix.lower() == '.csv':
        source = _rows(path)
        for row in source:
            kind = (row.get('type') or row.get('side') or '').lower().replace('_',' ')
            if kind not in ('buy stop','sell stop'):
                continue
            rawtime = row.get('placed') or row.get('open time') or row.get('time')
            if not rawtime:
                raise ValueError('MT5 order CSV requires placed/time column')
            t = _dt(rawtime)
            if t < first or t > last:
                continue
            rawvolume = row.get('volumes') or row.get('lots') or '0'
            vol = float(rawvolume.split('/')[0].strip())
            completed = row.get('completed') or row.get('close time') or ''
            records.append(dict(time=t, completed=_dt(completed) if completed else None,
                                strategy=int(row.get('strategy') or 0),
                                side=kind.replace(' ','_').upper(), lots=vol,
                                price=float(row.get('price') or 0), sl=float(row.get('sl') or 0),
                                tp=float(row.get('tp') or 0), status=row.get('status','').lower()))
        return records
    if Path(path).suffix.lower() not in ('.html','.htm'):
        raise ValueError('MT5 reference must be a .html tester report or a pending-orders .csv')
    tag = re.compile(r'<td\b[^>]*>(.*?)</td>', flags=re.I)
    clean = re.compile(r'<[^>]+>')
    kind_re = re.compile(r'(?:buy|sell) stop',flags=re.I)
    strategy_re = re.compile(r'(?:Gold Reaper strat|strat(?:egy)?)\s*(\d+)',flags=re.I)
    with open(path,'rb') as sigfile:
        signature=sigfile.read(4)
    source_encoding='utf-16' if signature[:2] in (b'\xff\xfe',b'\xfe\xff') else 'utf-8-sig'
    with open(path,'r',encoding=source_encoding,errors='replace') as fh:
        for line in fh:
            if '<td' not in line or not kind_re.search(line):
                continue
            cells = [html.unescape(clean.sub('',z)).strip() for z in tag.findall(line)]
            if len(cells)<11 or cells[3].lower() not in ('buy stop','sell stop'):
                continue
            try:
                t = _dt(cells[0]); vol=float(cells[4].split('/')[0].strip())
                if t<first or t>last:continue
                match=strategy_re.search(cells[10])
                if not match:continue
                records.append(dict(time=t,completed=_dt(cells[8]),strategy=int(match.group(1)),
                                    side=cells[3].replace(' ','_').upper(),lots=vol,
                                    price=float(cells[5]),sl=float(cells[6] or 0),
                                    tp=float(cells[7] or 0),status=cells[9].lower()))
            except (ValueError,IndexError):
                continue
    return records


def compare_mt5_orders(engine: GoldReaper, source_path: str) -> dict:
    """Order-by-order strict comparison; never classify this as full tick parity.

    It reports two independent definitions of a match:
      - exact tuple: time, strategy, direction, order price, lots, SL, TP;
      - timing tuple: time, strategy, direction only.
    The latter is useful for diagnosing price/lot broker discrepancies.
    MT5 stop-order tickets cannot be compared because they are independent IDs.
    """
    if not engine.first_tick or not engine.last_tick:
        raise ValueError('The run has no processed prices')
    reference = _mt5_reference_orders(source_path,engine.first_tick,engine.last_tick)
    actual = [x for x in engine.order_history if x['event']=='PLACE']
    def tuple_for(x,kind):
        base=(x['time'],int(x['strategy']),x['side'])
        if kind=='timing':return base
        if kind=='price':return base+(round(float(x['price']),engine.p.digits),)
        if kind=='entry':return base+(round(float(x['price']),engine.p.digits),
                                      round(float(x['lots']),3))
        return base+(round(float(x['price']),engine.p.digits),
                     round(float(x['lots']),3),round(float(x['sl']),engine.p.digits),
                     round(float(x['tp']),engine.p.digits))
    matched={}
    for mode in ('timing','price','entry','full'):
        a=Counter(tuple_for(x,mode) for x in actual)
        b=Counter(tuple_for(x,mode) for x in reference)
        n=sum((a&b).values())
        matched[mode+'_matches']=n
        matched[mode+'_recall_pct']=round(100*n/len(reference),3) if reference else 0.0
    full_actual=Counter(tuple_for(x,'full') for x in actual)
    full_ref=Counter(tuple_for(x,'full') for x in reference)
    missing=full_ref-full_actual;unexpected=full_actual-full_ref
    ref_fills=[]
    for x in reference:
        if x['status']!='filled' or x['completed'] is None:
            continue
        if engine.first_tick<=x['completed']<=engine.last_tick:
            ref_fills.append((x['completed'],x['strategy'],x['side'],
                              round(x['price'],engine.p.digits),round(x['lots'],3)))
    act_fills=[(x['time'],int(x['strategy']),x['side'],
                round(float(x['price']),engine.p.digits),round(float(x['lots']),3))
               for x in engine.order_history if x['event']=='FILL']
    fill_matches=sum((Counter(ref_fills)&Counter(act_fills)).values())
    # Group counts per strategy even when timestamp/price drift.
    ref_count=Counter((x['time'].year,x['strategy']) for x in reference)
    py_count=Counter((x['time'].year,int(x['strategy'])) for x in actual)
    groups=[]
    for year,strat in sorted(ref_count.keys()|py_count.keys()):
        groups.append(dict(year=year,strategy=strat,mt5_orders=ref_count[(year,strat)],
                           python_orders=py_count[(year,strat)],
                           count_diff=py_count[(year,strat)]-ref_count[(year,strat)]))
    result=dict(reference_source=str(source_path),
                scope_start=str(engine.first_tick),scope_end=str(engine.last_tick),
                reference_orders=len(reference),python_orders=len(actual),
                **matched,
                reference_fills=len(ref_fills),python_fills=len(act_fills),
                exact_fill_matches=fill_matches,
                exact_fill_recall_pct=round(100*fill_matches/len(ref_fills),3) if ref_fills else 0.0,
                order_fills_fully_matched=(len(reference)==len(actual)==matched['full_matches']
                                          and len(ref_fills)==len(act_fills)==fill_matches),
                full_mt5_parity_proven=False,
                explanation=('Matching orders/fills does not validate every exit, SL modification, '
                             'swap, session, broker rejection, and simulated quote. '
                             'Without the identical MT5 tester bid/ask event stream and symbol '
                             'specification, exact execution is not provable.'),
                per_year_strategy=groups,
                first_missing_reference_orders=[list(x) for x in list(missing.elements())[:25]],
                first_unexpected_python_orders=[list(x) for x in list(unexpected.elements())[:25]])
    return result



def _mt5_reference_deals(path: str, first: datetime, last: datetime) -> dict:
    """Stream original MT5 Strategy Tester DEALS table (not order requests).

    Returns distinct entry and exit deals. Uses source deal price/profit, rather
    than pretending that a pending order's requested price was its fill price.
    No position ID is inferred because MT5 HTML does not expose position IDs.
    """
    import re
    import html
    if Path(path).suffix.lower() not in ('.html', '.htm'):
        raise ValueError('Strict MT5 deal comparison requires the original .html tester report')
    with open(path, 'rb') as fh:
        sig=fh.read(4)
    enc='utf-16' if sig[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig'
    cell_re=re.compile(r'<td\b[^>]*>(.*?)</td>',re.I)
    clean_re=re.compile(r'<[^>]+>')
    strategy_re=re.compile(r'(?:Gold Reaper strat|strat(?:egy)?)\s*(\d+)',re.I)
    ins=[]; outs=[]; in_deals_section=False
    count_all=0
    with open(path,'r',encoding=enc,errors='replace') as fh:
        for line in fh:
            if not in_deals_section:
                if '<b>Deals</b>' in line:
                    in_deals_section=True
                continue
            if '<td' not in line or '<tr ' not in line:
                continue
            cells=[html.unescape(clean_re.sub('',v)).strip() for v in cell_re.findall(line)]
            if len(cells)<13 or cells[4].lower() not in ('in','out'):
                continue
            try:
                t=_dt(cells[0])
                if not first<=t<=last:continue
                side=cells[3].upper()
                if side not in ('BUY','SELL'):continue
                lots=float(cells[5].replace(' ',''))
                price=float(cells[6].replace(' ',''))
                deal_id=int(cells[1]); order_id=int(cells[7])
                commission=float(cells[8].replace(' ',''))
                swap=float(cells[9].replace(' ',''))
                profit=float(cells[10].replace(' ',''))
                balance=float(cells[11].replace(' ',''))
                d=dict(time=t,side=side,lots=lots,price=price,
                       deal_id=deal_id,order_id=order_id,
                       commission=commission,swap=swap,profit=profit,
                       balance=balance,comment=cells[12])
                if cells[4].lower()=='in':
                    match=strategy_re.search(cells[12])
                    d['strategy']=int(match.group(1)) if match else 0
                    ins.append(d)
                else:
                    outs.append(d)
                count_all+=1
            except (ValueError,IndexError):
                continue
    if not in_deals_section:
        raise ValueError('Original HTML does not contain the Deals section')
    return dict(entries=ins,exits=outs,parsed=count_all)


def compare_mt5_deals(engine: GoldReaper, source_path: str) -> dict:
    """Compare real executed MT5 deals against simulated Python executions.

    Independent multiset comparison: entries include strategy, fills/exits use
    observed prices, not requested pending prices. Exits have no strategy field
    in MT5 HTML, so no per-strategy attribution is guessed from the exit table.
    Commission/swap differences are reported separately and remain unmatched.
    """
    if engine.first_tick is None or engine.last_tick is None:
        raise ValueError('Run has no simulated prices')
    records=_mt5_reference_deals(source_path,engine.first_tick,engine.last_tick)
    ref_in=records['entries']; ref_out=records['exits']
    py_in=[x for x in engine.order_history if x['event']=='FILL']
    py_out=engine.trades
    digits=engine.p.digits
    def amount(v,n):return round(float(v),n)
    def tref_in(x,kind):
        base=(x['time'],x['strategy'],x['side'])
        if kind=='time':return base
        if kind=='price':return base+(amount(x['price'],digits),)
        return base+(amount(x['price'],digits),amount(x['lots'],3))
    def tpy_in(x,kind):
        side='BUY' if x['side']=='BUY_STOP' else 'SELL'
        base=(x['time'],int(x['strategy']),side)
        if kind=='time':return base
        if kind=='price':return base+(amount(x['price'],digits),)
        return base+(amount(x['price'],digits),amount(x['lots'],3))
    def tref_out(x,kind):
        base=(x['time'],x['side'])
        if kind=='time':return base
        if kind=='price':return base+(amount(x['price'],digits),)
        if kind=='volume':return base+(amount(x['price'],digits),amount(x['lots'],3))
        return base+(amount(x['price'],digits),amount(x['lots'],3),amount(x['profit']+x['swap']+x['commission'],2))
    def tpy_out(x,kind):
        side='SELL' if x['side']=='BUY' else 'BUY'
        base=(x['close_time'],side)
        if kind=='time':return base
        if kind=='price':return base+(amount(x['exit'],digits),)
        if kind=='volume':return base+(amount(x['exit'],digits),amount(x['lots'],3))
        return base+(amount(x['exit'],digits),amount(x['lots'],3),amount(x['net'],2))
    out=dict(reference_source=str(source_path),scope_start=str(engine.first_tick),scope_end=str(engine.last_tick),
             reference_deal_entries=len(ref_in),reference_deal_exits=len(ref_out),
             python_deal_entries=len(py_in),python_deal_exits=len(py_out),
             full_parity_proven=False,
             warning='M1-OHLC cannot uniquely reconstruct the intraminute Bid/Ask tick path, so all-match is not a proof of MT5 parity.')
    for kind in ('time','price','complete'):
        ref=Counter(tref_in(x,kind) for x in ref_in)
        sim=Counter(tpy_in(x,kind) for x in py_in)
        n=sum((ref&sim).values())
        out['entry_'+kind+'_matches']=n
        out['entry_'+kind+'_recall_pct']=round(n/len(ref_in)*100,3) if ref_in else None
        if kind=='complete':
            out['entry_first_missing']=[str(x) for x in list((ref-sim).elements())[:15]]
            out['entry_first_unexpected']=[str(x) for x in list((sim-ref).elements())[:15]]
    for kind in ('time','price','volume','complete'):
        ref=Counter(tref_out(x,kind) for x in ref_out)
        sim=Counter(tpy_out(x,kind) for x in py_out)
        n=sum((ref&sim).values())
        out['exit_'+kind+'_matches']=n
        out['exit_'+kind+'_recall_pct']=round(n/len(ref_out)*100,3) if ref_out else None
        if kind=='complete':
            out['exit_first_missing']=[str(x) for x in list((ref-sim).elements())[:15]]
            out['exit_first_unexpected']=[str(x) for x in list((sim-ref).elements())[:15]]
    out['reference_net_on_exits']=round(sum(x['profit']+x['swap']+x['commission'] for x in ref_out),2)
    out['python_net_on_exits']=round(sum(float(x['net']) for x in py_out),2)
    out['profit_difference']=round(out['python_net_on_exits']-out['reference_net_on_exits'],2)
    out['all_executed_deals_identical']=(len(ref_in)==len(py_in)==out['entry_complete_matches'] and
                                          len(ref_out)==len(py_out)==out['exit_complete_matches'])
    return out


def self_test():
    p=Settings()
    c=build_strategies(p)
    assert len(c)==9
    assert c[3].sl==525
    assert c[1].buy_offset==-192.5
    assert c[9].time_trail_size==4413
    assert GoldReaper(p).auto_cap()==8
    assert nfp_server_time(2025,12,p)==datetime(2025,12,16,15,30)
    e=GoldReaper(p)
    t=datetime(2026,2,2,1)
    e.tick(t,2000,2000.2)
    e.tick(t+timedelta(minutes=1),2000.5,2000.7)
    assert e.bars['M1'].bar(1).close==2000
    e.levels.append(Level(1,True,t,t,2005))
    assert e.candidate_entry(e.levels[0])==e.normprice(2005+e.pips(-192.5))
    assert e.normvol(.126)==.13
    assert e.normvol(.004,floor=True)==0
    # OHLC leg crosses entry and TP in the same upward movement.
    me=GoldReaper(Settings(input_mode='m1',m1_fill_model='level'))
    start=datetime(2025,1,6,10,0,0)
    me.now=start+timedelta(seconds=20)
    me.prev_time=start
    me.prev_bid=2000.0; me.prev_ask=2000.2
    me.bid=2004.0;me.ask=2004.2
    me.orders[1]=Order(1,-1,5,True,start,2001.0,.10,1997.0,2003.0)
    me.broker_execute()
    assert me.stats['filled']==1 and me.stats['closed']==1, (me.stats,me.trades)
    assert me.trades[0]['entry']==2001 and me.trades[0]['exit']==2003,(me.trades,)
    # The same jump as a genuine tick must fill at the observed quote.
    te=GoldReaper(Settings(input_mode='ticks'))
    te.now=start+timedelta(seconds=15);te.bid=2004.;te.ask=2004.2
    te.orders[2]=Order(2,-1,5,True,start,2001.,.10,1997.,2003.)
    te.broker_execute()
    assert te.stats['filled']==1 and te.positions[1].entry==2004.2
    print('SELF-TEST OK: settings, price thresholds, within-bar entry/exit, tick-vs-M1 fills.')


def main(argv:Optional[list[str]]=None):
    ap=argparse.ArgumentParser(description='Single-file GoldReaper_v2_Pass3436_Robuust Python port')
    ap.add_argument('--data',help='Dukascopy/MT5 CSV with tick bid/ask or M1 OHLC')
    ap.add_argument('--mode',choices=['ticks','m1'],default='ticks')
    ap.add_argument('--output',default='gold_reaper_results')
    ap.add_argument('--spread-points',type=float,default=2.,
                    help='MT5 tester symbol spread in points; calibrated estimate 2 for this reference')
    ap.add_argument('--point',type=float,default=.01,
                    help='MT5 SYMBOL_POINT (default 0.01 for a 2-digit gold symbol)')
    ap.add_argument('--digits',type=int,default=2,
                    help='MT5 SYMBOL_DIGITS (default 2, even if input CSV has extra decimals)')
    ap.add_argument('--tick-size',type=float,
                    help='MT5 SYMBOL_TRADE_TICK_SIZE; defaults to --point')
    ap.add_argument('--tick-value-loss',type=float,
                    help='MT5 SYMBOL_TRADE_TICK_VALUE_LOSS; defaults to tick_size * contract_size for USD XAUUSD')
    ap.add_argument('--contract-size',type=float,default=100.,
                    help='MT5 SYMBOL_TRADE_CONTRACT_SIZE (default 100 ounces per standard XAUUSD lot)')
    ap.add_argument('--commission-per-lot-side',type=float,default=0.,
                    help='Estimated USD trading commission per 1 lot per side, applied on entry and exit')
    ap.add_argument('--stops-level-points',type=float,default=0.,
                    help='MT5 SYMBOL_TRADE_STOPS_LEVEL in points')
    ap.add_argument('--volume-min',type=float,default=.01)
    ap.add_argument('--volume-step',type=float,default=.01)
    ap.add_argument('--leverage',type=float,default=100.)
    ap.add_argument('--start-balance',type=float,default=10000.)
    ap.add_argument('--session-start',default='01:02',
                    help='Broker symbol trading session start; 01:02 calibrated from reference placements')
    ap.add_argument('--session-end',default='24:00')
    ap.add_argument('--news-csv',help='MQL5 calendar cache columns event_time, kind, importance')
    ap.add_argument('--data-shift-hours',type=float,default=0.0,
                    help='Shift UTC dataset timestamps to broker-server time (e.g., 2 or 3; verify DST)')
    ap.add_argument('--verify-trades',help='MT5 exported trade CSV for exact entry_time/strategy/side/price comparison')
    ap.add_argument('--verify-mt5-report',help='Original MT5 Strategy Tester HTML or pending-order CSV; compare placement and fills')
    ap.add_argument('--start-date',help='Simulation start YYYY-MM-DD; pre-start M1 bars are used only for indicator warmup')
    ap.add_argument('--end-date',help='Last trading date YYYY-MM-DD (inclusive)')
    ap.add_argument('--no-round-m1-quotes',action='store_true',
                    help='Disable quantization of input M1 prices to MT5 symbol digits')
    ap.add_argument('--m1-legacy',action='store_true',
                    help='Use the original slower four-ticks-per-row Python path for regression comparison')
    ap.add_argument('--m1-fill-model',choices=['level','vertex'],default='level',
                    help='level=execute intrabar stop/TP crossings at barrier; vertex=legacy unrealistic OHLC fills')
    ap.add_argument('--progress-interval',type=float,default=3.0,
                    help='Seconds between progress bar updates (default 3)')
    ap.add_argument('--stall-warning-seconds',type=float,default=30.0,
                    help='Log warning if no ticks processed for N wall-clock seconds (default 30)')
    ap.add_argument('--checkpoint-seconds',type=float,default=30.0,
                    help='Write results/run_state.json every N seconds (default 30)')
    ap.add_argument('--idle-trading-days',type=int,default=5,
                    help='Warn if N consecutive weekdays have no new fills (default 5)')
    ap.add_argument('--log-events',action='store_true',
                    help='Write every PLACE/FILL/CANCEL event to run.log (can be large and slower)')
    ap.add_argument('--no-progress',action='store_true',
                    help='Disable the animated bar; summary/progress still written to run.log')
    ap.add_argument('--self-test',action='store_true')
    args=ap.parse_args(argv)
    if args.self_test:
        self_test()
        return
    if not args.data:
        ap.error('provide --data, or use --self-test')
    data_path=Path(args.data).expanduser()
    if not data_path.is_file():
        ap.error(f'Data file not found: {data_path.resolve()} -- put it in this folder or pass the full path')
    if data_path.stat().st_size==0:
        ap.error(f'Data file is empty: {data_path.resolve()}')
    tick_size=args.tick_size if args.tick_size is not None else args.point
    tick_value_loss=args.tick_value_loss if args.tick_value_loss is not None else tick_size*args.contract_size
    p=Settings(spread_points=args.spread_points,point=args.point,digits=args.digits,
               tick_size=tick_size,tick_value_loss=tick_value_loss,
               contract_size=args.contract_size,stops_level_points=args.stops_level_points,
               commission_per_lot_side=args.commission_per_lot_side,
               volume_min=args.volume_min,volume_step=args.volume_step,leverage=args.leverage,
               initial_balance=args.start_balance,session_start=args.session_start,
               input_mode=args.mode,m1_fill_model=args.m1_fill_model,
               round_m1_quotes=not args.no_round_m1_quotes,
               session_end=args.session_end,news_csv=args.news_csv)
    if (args.start_date or args.end_date) and args.mode!='m1':
        ap.error('--start-date/--end-date currently require --mode m1')
    if args.m1_legacy and (args.start_date or args.end_date):
        ap.error('--start-date/--end-date require the optimized M1 engine, omit --m1-legacy')
    try:
        start_date=date.fromisoformat(args.start_date) if args.start_date else None
        end_date=date.fromisoformat(args.end_date) if args.end_date else None
    except ValueError:
        ap.error('Invalid date; use YYYY-MM-DD')
    if start_date and end_date and start_date>end_date:
        ap.error('--start-date must not exceed --end-date')
    progress=CsvReadProgress(str(data_path))
    diag=RunDiagnostics(args.output,progress,args.mode,
                        progress_interval=args.progress_interval,
                        stall_seconds=args.stall_warning_seconds,
                        checkpoint_seconds=args.checkpoint_seconds,
                        idle_days=args.idle_trading_days,
                        show_progress=not args.no_progress,event_logging=args.log_events)
    e=None
    diag.logger.info('START data=%s | size=%.2f MiB | mode=%s | output=%s',
                     data_path.resolve(),progress.total_bytes/(1024*1024),args.mode,diag.output.resolve())
    diag.logger.info('Monitoring: run.log, run_state.json, progress bar, stalled-run watchdog. '
                     'The status displays CSV bytes consumed and simulated price ticks.')
    diag.logger.info('BROKER SYMBOL: digits=%d point=%.6f tick_size=%.6f tick_value_loss=%.4f '
                     'contract_size=%.1f spread_fallback=%.2f points, leverage=%.0f, '
                     'stops_level=%s points, commission=$%.2f per lot/side',
                     p.digits,p.point,p.tick_size,p.tick_value_loss,p.contract_size,
                     p.spread_points,p.leverage,p.stops_level_points,p.commission_per_lot_side)
    diag.logger.warning('For MT5 parity these SYMBOL fields, session/news settings and tick stream '
                        'must match the actual MT5 tester. CSV quote decimals alone do NOT determine SYMBOL_DIGITS.')
    if args.mode=='m1':
        diag.logger.info('M1 intrabar fill model=%s; MT5 1-minute OHLC orders/SL/TP fill at requested barrier levels.',p.m1_fill_model)
        diag.logger.warning('M1 SPREAD=0 or blank is treated as unavailable; fallback %.1f points at %.5f point = %.5f gold price units. This spread assumption affects PnL.',p.spread_points,p.point,p.spread_points*p.point)
        diag.logger.warning('M1 bar tick volumes control 1-4 synthetic tick events per row. '
                            'This is NOT tick-exact, and decades of M1 may be very slow.')
    if p.news_csv is None and (p.fomc or p.cpi):
        diag.logger.warning('No --news-csv supplied: explicit CPI/FOMC event cache is unavailable. '
                            'Only built-in NFP dates are applied.')
    diag.start_watchdog()
    try:
        e=GoldReaper(p)
        diag.stage='reading CSV / backtesting'
        if args.mode=='m1' and not args.m1_legacy:
            diag.logger.info('FAST M1 ALWAYS ON: streaming rows and reusing intraminute bars; '+
                             'tickvolume-aware execution with price-barrier fills.')
        if start_date:
            diag.logger.info('MT5 TEST START: %s; all earlier M1 rows prewarm pivots without trading.',start_date)
        if end_date:
            diag.logger.info('MT5 TEST END: %s inclusive.',end_date)
        # IMPORTANT: the optimized M1 order/exit simulator must be used
        # consistently even if --end-date is absent. Prior versions silently
        # fell back to the legacy tick renderer, ignored --start-date and
        # changed all fill times and backtest outcomes for full-history runs.
        if args.mode == 'm1' and not args.m1_legacy:
            e.run_m1_fast(m1_bars_fast(str(data_path),p,args.data_shift_hours,progress),
                          diagnostics=diag,start_date=start_date,end_date=end_date)
        else:
            e.run(market_feed(str(data_path),args.mode,p,args.data_shift_hours,progress),
                  diagnostics=diag)
        if diag.tick_count == 0:
            raise ValueError('CSV contained a header but no usable price rows. Check the time and OHLC/Bid/Ask columns.')
        diag.stage='saving CSV results'
        diag.logger.info('Simulation completed. Saving trade/order/management CSVs...')
        root=e.save(args.output)
        if args.verify_mt5_report:
            diag.logger.info('Comparing every pending-order placement to MT5 report: %s',args.verify_mt5_report)
            audit=compare_mt5_orders(e,args.verify_mt5_report)
            with (root/'mt5_order_parity.json').open('w',encoding='utf-8') as fh:
                json.dump(audit,fh,indent=2,default=str)
            diag.logger.info('MT5 order parity: full=%s/%s, time=%s/%s, exact fill=%s/%s',
                             audit['full_matches'],audit['reference_orders'],
                             audit['timing_matches'],audit['reference_orders'],
                             audit['exact_fill_matches'],audit['reference_fills'])
            if not audit['order_fills_fully_matched']:
                diag.logger.warning('MT5 ORDER PARITY NOT EXACT. Details in mt5_order_parity.json')
            diag.logger.info('Comparing executed MT5 Deals (entries/exits/realized PnL)...')
            deal_audit=compare_mt5_deals(e,args.verify_mt5_report)
            with (root/'mt5_deal_parity.json').open('w',encoding='utf-8') as fh:
                json.dump(deal_audit,fh,indent=2,default=str)
            diag.logger.info('MT5 DEAL PARITY: entries=%s/%s, exits=%s/%s, net USD difference=%s',
                             deal_audit['entry_complete_matches'],deal_audit['reference_deal_entries'],
                             deal_audit['exit_complete_matches'],deal_audit['reference_deal_exits'],
                             deal_audit['profit_difference'])
            if not deal_audit['all_executed_deals_identical']:
                diag.logger.warning('MT5 DEAL PARITY NOT EXACT: see mt5_deal_parity.json')
        if args.verify_trades:
            diag.logger.info('Starting reference trade comparison...')
            result=compare_entries(e,args.verify_trades)
            with (root/'entry_parity.json').open('w',encoding='utf-8') as fh:
                json.dump(result,fh,indent=2,default=str)
            if result['counts_by_year_strategy']:
                with (root/'reference_vs_python_by_year.csv').open('w',encoding='utf-8',newline='') as fh:
                    rows=result['counts_by_year_strategy']
                    writer=csv.DictWriter(fh,fieldnames=list(rows[0]))
                    writer.writeheader();writer.writerows(rows)
            diag.logger.info('MT5 entry parity: %s',json.dumps(result,default=str))
        diag.stage='done'
        diag.checkpoint(e,force=True)
        diag.logger.info('COMPLETE: %s',json.dumps(e.summary(),default=str))
        diag.logger.info('Saved output to %s',root.absolute())
        print(json.dumps(e.summary(),indent=2))
        print(f'Wrote events and summary to {root.absolute()}')
    except KeyboardInterrupt:
        diag.stage='interrupted'
        diag.logger.warning('INTERRUPTED by Ctrl+C. Saving partial results where available...')
        if e is not None:
            diag.checkpoint(e,force=True)
            try:
                e.save(args.output)
                diag.logger.warning('Partial trade/order CSVs + summary.json saved in %s',diag.output.resolve())
            except Exception:
                diag.logger.exception('Failed to save partial results')
        raise SystemExit(130)
    except Exception:
        diag.stage='failed'
        diag.logger.exception('FAILED: full Python traceback below. Check your CSV time/column format and last reported bar.')
        if e is not None:
            diag.checkpoint(e,force=True)
            try:
                e.save(args.output)
                diag.logger.error('Partial results saved in %s',diag.output.resolve())
            except Exception:
                diag.logger.exception('Could not save partial results')
        raise SystemExit(1)
    finally:
        diag.stop()


if __name__=='__main__':
    main()
