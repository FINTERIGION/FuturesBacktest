"""Futures strategy base class for all SA backtest strategies."""

from datetime import date, datetime

import backtrader as bt


def _to_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, 'date') and callable(value.date):
        return value.date()
    return value


class FuturesStrategyBase(bt.Strategy):
    """
    Futures strategy base class.

    Provides common helpers such as signal logging and trade logging.
    Subclasses implement the actual logic in ``next()``.

    The backtest engine injects the following params via cerebro.addstrategy():
      - contract_multiplier : contract multiplier (default 20 tons/lot)
      - trade_size          : lots per trade (default 1)
      - margin_rate         : margin ratio (default 0.10)
      - execute_on_contracts: route orders to calendar contracts (default False)
      - contract_by_date    : {date: contract_code} used when executing on contracts

    Indicators always read ``self.data`` (OI-weighted series, datas[0]).
    When ``execute_on_contracts`` is True, buy/sell/close go to the calendar
    contract and rolls happen in ``next_open`` (old open / new open).

    Subclasses can directly use:
      self.buy_signal()    open long
      self.sell_signal()   open short
      self.close_signal()  close position
    """

    params = (
        ('contract_multiplier', 20),
        ('trade_size', 1),
        ('margin_rate', 0.10),
        ('printlog', False),
        ('execute_on_contracts', False),
        ('contract_by_date', None),
    )

    def __init__(self):
        # Signal list (used by the plotting module): [(date, price, direction), ...]
        # direction: 'buy' | 'sell' | 'close'
        self.signal_log = []
        self._pending_order = None
        self._pending_refs = set()
        self._exec_data = None
        self._exec_code = None
        self._roll_order_refs = set()
        self._contract_by_date = {}
        self._roll_done_on = None
        self._protect_stop_order = None
        self._stop_distance = None
        self._stop_price = None

    # ------------------------------------------------------------------
    # Lifecycle callbacks
    # ------------------------------------------------------------------

    def start(self):
        raw = self.p.contract_by_date or {}
        self._contract_by_date = {_to_date(k): v for k, v in raw.items()}
        if self.p.execute_on_contracts:
            self._maybe_roll()

    def prenext_open(self):
        if self.p.execute_on_contracts:
            self._maybe_roll()

    def next_open(self):
        if self.p.execute_on_contracts:
            self._maybe_roll()

    def stop(self):
        leftovers = {
            getattr(feed, '_name', ''): sz
            for feed, sz in self._positions_by_data().items()
            if getattr(feed, '_name', '') != self._exec_code
        }
        if leftovers:
            print(f"  [WARN] Leftover positions on non-calendar contracts: {leftovers}")

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return

        is_roll = self._is_roll_order(order)
        self._roll_order_refs.discard(order.ref)

        date = self.datas[0].datetime.date(0)

        if order.status == order.Completed:
            if self._is_protect_stop(order):
                self._protect_stop_order = None
                self._stop_distance = None
            elif is_roll and self._stop_distance:
                psz = int(self.getposition(order.data).size)
                if psz:
                    self.arm_protect_stop(
                        order.executed.price,
                        is_long=psz > 0,
                        size=abs(psz),
                        data=order.data,
                    )
            if not is_roll:
                direction = 'buy' if order.isbuy() else 'sell'
                self.signal_log.append({
                    'date': date,
                    'price': order.executed.price,
                    'direction': direction,
                    'size': order.executed.size,
                    'comm': order.executed.comm,
                })
            if self.p.printlog:
                tag = 'ROLL ' if is_roll else ''
                cname = getattr(order.data, '_name', '') or ''
                print(
                    f"  [{date}] {tag}Filled: {'BUY' if order.isbuy() else 'SELL'} "
                    f"{cname} price={order.executed.price:.2f} "
                    f"size={order.executed.size} "
                    f"commission={order.executed.comm:.2f}"
                )
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            if self._is_protect_stop(order):
                if self._protect_stop_order is order:
                    self._protect_stop_order = None
            if self.p.printlog:
                print(f"  [{date}] Order not filled: {order.getstatusname()}")

        self._clear_pending_ref(order.ref)

    def notify_trade(self, trade):
        if not trade.isclosed:
            return
        if self.p.printlog:
            date = self.datas[0].datetime.date(0)
            print(f"  [{date}] Trade closed: pnl={trade.pnl:.2f}  net_pnl={trade.pnlcomm:.2f}")

    # ------------------------------------------------------------------
    # Order routing (weighted = signals, calendar contract = execution)
    # ------------------------------------------------------------------

    def buy(self, data=None, **kwargs):
        data = self._resolve_exec_data(data)
        if self.p.execute_on_contracts and data is None:
            return None
        return super().buy(data=data, **kwargs)

    def sell(self, data=None, **kwargs):
        data = self._resolve_exec_data(data)
        if self.p.execute_on_contracts and data is None:
            return None
        return super().sell(data=data, **kwargs)

    def close(self, data=None, **kwargs):
        if data is not None:
            return super().close(data=data, **kwargs)
        if not self.p.execute_on_contracts:
            return super().close(data=data, **kwargs)
        self._sync_exec_pointer()
        last = None
        for feed in self._contract_datas():
            if self.getposition(feed).size:
                last = super().close(data=feed, **kwargs)
        return last

    def _resolve_exec_data(self, data):
        if data is not None:
            return data
        if self.p.execute_on_contracts:
            self._sync_exec_pointer()
            return self._exec_data
        return None

    def _today(self):
        try:
            return self.datas[0].datetime.date(0)
        except Exception:
            return None

    def _target_code(self, dt=None):
        dt = _to_date(dt or self._today())
        if dt is None:
            return None
        return self._contract_by_date.get(dt)

    def _data_by_code(self, code):
        if not code:
            return None
        try:
            return self.getdatabyname(code)
        except Exception:
            return None

    def _contract_datas(self):
        if not self.p.execute_on_contracts or len(self.datas) < 2:
            return []
        return list(self.datas[1:])

    def _sync_exec_pointer(self):
        """Point execution at today's calendar contract without placing orders."""
        if not self.p.execute_on_contracts:
            return
        code = self._target_code()
        data = self._data_by_code(code)
        if data is None:
            return
        self._exec_data = data
        self._exec_code = code

    def _is_protect_stop(self, order):
        if order is None:
            return False
        if order is getattr(self, '_protect_stop_order', None):
            return True
        info = getattr(order, 'info', None)
        if info is None:
            return False
        try:
            return bool(info.get('is_protect_stop', False))
        except Exception:
            return bool(getattr(info, 'is_protect_stop', False))

    def cancel_protect_stop(self):
        order = self._protect_stop_order
        self._protect_stop_order = None
        if order is None:
            return
        if getattr(order, 'alive', lambda: False)():
            self._cancel_broker_order(order)

    def arm_protect_stop(self, fill_price, is_long, size, data=None, distance=None):
        dist = self._stop_distance if distance is None else distance
        if not dist or size <= 0:
            return None
        self._stop_distance = float(dist)
        stop_price = float(fill_price) - dist if is_long else float(fill_price) + dist
        return self.place_protect_stop(stop_price, size=int(size), data=data)

    def place_protect_stop(self, stop_price, size, data=None):
        """Resting stop on the traded contract. Does not block next()."""
        self.cancel_protect_stop()
        data = self._resolve_exec_data(data)
        if self.p.execute_on_contracts and data is None:
            return None
        stop_price = float(stop_price)
        size = abs(int(size))
        if size <= 0:
            return None
        pos = int(self.getposition(data).size) if data is not None else int(self.position.size)
        if pos > 0:
            order = super().sell(
                data=data, size=size, exectype=bt.Order.Stop, price=stop_price
            )
        elif pos < 0:
            order = super().buy(
                data=data, size=size, exectype=bt.Order.Stop, price=stop_price
            )
        else:
            return None
        if order is None:
            return None
        self._mark_protect_stop(order)
        self._protect_stop_order = order
        self._stop_price = stop_price
        return order

    def _mark_protect_stop(self, order):
        if order is None:
            return
        addinfo = getattr(order, 'addinfo', None)
        if callable(addinfo):
            order.addinfo(is_protect_stop=True)

    def _fill_protect_stop_if_touched(self):
        """If today's contract range already pierced the resting stop, fill now.

        Backtrader only evaluates a newly submitted Stop on the next broker
        cycle. A cloud-hosted stop would be live on the fill bar, so this
        catches same-bar touches at the stop (or the open if it gapped).
        """
        order = self._protect_stop_order
        if order is None or not getattr(order, 'alive', lambda: False)():
            if order is not None and not getattr(order, 'alive', lambda: False)():
                self._protect_stop_order = None
            return False
        data = getattr(order, 'data', None)
        if data is None:
            return False
        try:
            popen = float(data.open[0])
            phigh = float(data.high[0])
            plow = float(data.low[0])
            pclose = float(data.close[0])
            stop = float(order.created.price)
        except Exception:
            return False
        if order.issell():
            hit = popen <= stop or plow <= stop
        else:
            hit = popen >= stop or phigh >= stop
        if not hit:
            return False
        broker = self.broker
        try_exec = getattr(broker, '_try_exec_stop', None)
        if not callable(try_exec):
            return False
        try_exec(order, popen, phigh, plow, stop, pclose)
        if getattr(order, 'alive', lambda: True)():
            return False
        for attr in ('pending', 'submitted'):
            queue = getattr(broker, attr, None)
            if not queue:
                continue
            try:
                queue.remove(order)
            except ValueError:
                pass
        self._protect_stop_order = None
        return True

    def _is_roll_order(self, order):
        if order is None:
            return False
        if order.ref in self._roll_order_refs:
            return True
        info = getattr(order, 'info', None)
        if info is None:
            return False
        try:
            return bool(info.get('is_roll', False))
        except Exception:
            return bool(getattr(info, 'is_roll', False))

    def _mark_roll(self, order):
        if order is None:
            return
        self._roll_order_refs.add(order.ref)
        addinfo = getattr(order, 'addinfo', None)
        if callable(addinfo):
            order.addinfo(is_roll=True)

    def _track_pending(self, order):
        if order is None:
            return
        self._pending_order = order
        self._pending_refs.add(order.ref)

    def _clear_pending_ref(self, ref):
        self._pending_refs.discard(ref)
        if not self._pending_refs:
            self._pending_order = None
        elif self._pending_order is not None and getattr(self._pending_order, 'ref', None) == ref:
            # Other tracked orders are still live; keep next() blocked.
            self._pending_order = True

    def _order_signed_size(self, order):
        raw = getattr(order, 'created', None)
        size = abs(int(getattr(raw, 'size', None) or order.size or 0))
        if not size:
            return 0
        return size if order.isbuy() else -size

    def _alive_broker_orders(self):
        orders = []
        seen = set()
        for attr in ('submitted', 'pending'):
            queue = getattr(self.broker, attr, None)
            if not queue:
                continue
            for order in list(queue):
                if order is None or id(order) in seen:
                    continue
                seen.add(id(order))
                orders.append(order)
        return orders

    def _cancel_broker_order(self, order):
        """Cancel an order in ``submitted`` or ``pending``.

        Backtrader's ``broker.cancel`` only searches ``pending``. Signal
        orders from the previous ``next()`` are still in ``submitted``
        when ``next_open`` runs, so they must be removed from that queue.
        """
        if order is None:
            return False
        broker = self.broker
        submitted = getattr(broker, 'submitted', None)
        if submitted is not None:
            try:
                submitted.remove(order)
            except ValueError:
                pass
            else:
                order.cancel()
                broker.notify(order)
                return True
        return bool(broker.cancel(order))

    def _positions_by_data(self):
        held = {}
        for feed in self._contract_datas():
            size = int(self.getposition(feed).size)
            if size:
                held[feed] = size
        return held

    def _maybe_roll(self):
        """Keep all exposure on today's calendar contract.

        Runs at every session open. On a roll date this is the calendar
        switch (old open / new open). On other days it is a safety sweep
        so a leftover on an old feed cannot sit until expiry.

        Pending signal orders from the previous session are still in the
        broker's ``submitted`` queue when this runs (they fill only after
        ``next_open``). Those on the wrong contract are cancelled and the
        intended net size is placed on the target so they fill at this
        open instead of opening a zombie on the old contract.
        """
        if not self.p.execute_on_contracts:
            return

        today = self._today()
        if today is None or today == self._roll_done_on:
            return
        self._roll_done_on = today

        new_code = self._target_code(today)
        if not new_code:
            return

        new_data = self._data_by_code(new_code)
        if new_data is None:
            if self.p.printlog:
                print(f"  [{today}] No feed for {new_code}, keeping {self._exec_code}")
            return

        held = self._positions_by_data()
        pos_target = int(self.getposition(new_data).size)
        pos_others = sum(sz for feed, sz in held.items() if feed is not new_data)

        wrong_pending = 0
        for order in self._alive_broker_orders():
            if self._is_roll_order(order):
                continue
            if order.status not in (order.Submitted, order.Accepted, order.Partial):
                continue
            data = getattr(order, 'data', None)
            if data is None or data is self.datas[0]:
                continue
            if data is new_data:
                continue
            if self._is_protect_stop(order):
                self._cancel_broker_order(order)
                if self._protect_stop_order is order:
                    self._protect_stop_order = None
                continue
            wrong_pending += self._order_signed_size(order)
            self._cancel_broker_order(order)

        move = pos_others + wrong_pending

        old_code = self._exec_code
        self._exec_data = new_data
        self._exec_code = new_code

        if not pos_others and not wrong_pending:
            return

        for feed, size in held.items():
            if feed is new_data or not size:
                continue
            close_ord = super().close(data=feed)
            self._mark_roll(close_ord)

        open_ord = None
        if move > 0:
            open_ord = super().buy(data=new_data, size=move)
        elif move < 0:
            open_ord = super().sell(data=new_data, size=abs(move))

        if pos_others:
            self._mark_roll(open_ord)
        elif open_ord is not None:
            self._track_pending(open_ord)

        if self.p.printlog:
            print(
                f"  [{today}] Roll {old_code} -> {new_code} "
                f"others={pos_others} pending={wrong_pending} move={move} "
                f"on_target={pos_target}"
            )

    # ------------------------------------------------------------------
    # Utility methods (for subclasses)
    # ------------------------------------------------------------------

    def buy_signal(self, size=None, data=None):
        """Open long at market (futures long entry)"""
        if self._pending_order is None:
            self._track_pending(
                self.buy(data=data, size=size if size is not None else self.p.trade_size)
            )

    def sell_signal(self, size=None, data=None):
        """Open short at market (futures short entry)"""
        if self._pending_order is None:
            self._track_pending(
                self.sell(data=data, size=size if size is not None else self.p.trade_size)
            )

    def close_signal(self, data=None):
        """Close the current position (all calendar contracts if data is None)."""
        self.cancel_protect_stop()
        if self._pending_order is None:
            if data is not None:
                self._track_pending(self.close(data=data))
                return
            if self.p.execute_on_contracts:
                last = None
                for feed in self._contract_datas():
                    if self.getposition(feed).size:
                        last = super().close(data=feed)
                        self._track_pending(last)
                if last is None:
                    self._track_pending(self.close())
                return
            self._track_pending(self.close())

    def get_position_size(self) -> int:
        """Return the current net position size (positive=long, negative=short, 0=flat)"""
        if self.p.execute_on_contracts:
            if len(self.datas) > 1:
                return int(sum(self.getposition(d).size for d in self.datas[1:]))
            if self._exec_data is not None:
                return int(self.getposition(self._exec_data).size)
        return self.position.size
