from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional
import time
import uuid

import config


@dataclass
class OrderRequest:
    symbol: str
    exchange: str
    side: str
    quantity: int
    order_type: str = "MARKET"  # MARKET, LIMIT, SL-M
    product: str = "MIS"
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    tag: str = "AUTO"


@dataclass
class OrderResult:
    ok: bool
    order_id: str = ""
    status: str = ""
    filled_quantity: int = 0
    average_price: Optional[float] = None
    error: str = ""
    request: Optional[dict] = None
    timestamp: str = ""
    paper: bool = True


class OrderManager:
    def __init__(self, kite, live_mode: bool = None, logger=None):
        self.kite = kite
        self.live_mode = bool(getattr(config, "LIVE_MODE", False) if live_mode is None else live_mode)
        self.logger = logger

    def place_order(self, request: OrderRequest) -> OrderResult:
        request.side = request.side.upper()
        request.order_type = request.order_type.upper()
        request.product = request.product.upper()
        if request.quantity <= 0:
            return self._failed(request, "quantity must be positive")
        if request.order_type == "LIMIT" and request.price is None:
            return self._failed(request, "LIMIT order requires price")
        if request.order_type == "SL-M" and request.trigger_price is None:
            return self._failed(request, "SL-M order requires trigger_price")

        if not self.live_mode:
            fill_price = request.price or request.trigger_price
            return OrderResult(
                ok=True,
                order_id=f"PAPER-{uuid.uuid4().hex[:12]}",
                status="COMPLETE",
                filled_quantity=request.quantity,
                average_price=fill_price,
                request=asdict(request),
                timestamp=datetime.now().isoformat(timespec="seconds"),
                paper=True,
            )

        try:
            kwargs = {
                "variety": getattr(self.kite, "VARIETY_REGULAR", "regular"),
                "exchange": request.exchange,
                "tradingsymbol": request.symbol,
                "transaction_type": request.side,
                "quantity": request.quantity,
                "product": request.product,
                "order_type": request.order_type,
                "tag": request.tag,
            }
            if request.price is not None:
                kwargs["price"] = request.price
            if request.trigger_price is not None:
                kwargs["trigger_price"] = request.trigger_price
            order_id = self.kite.place_order(**kwargs)
            if request.order_type == "SL-M":
                return self._wait_for_accept(str(order_id), request)
            return self._wait_for_fill(str(order_id), request)
        except Exception as exc:
            return self._failed(request, str(exc), paper=False)

    def place_entry(self, symbol: str, exchange: str, quantity: int,
                    order_type: str = None, price: float = None) -> OrderResult:
        return self.place_order(OrderRequest(
            symbol=symbol,
            exchange=exchange,
            side="BUY",
            quantity=quantity,
            order_type=order_type or getattr(config, "ENTRY_ORDER_TYPE", "MARKET"),
            product=getattr(config, "ORDER_PRODUCT", "MIS"),
            price=price,
            tag="ENTRY",
        ))

    def place_exit(self, symbol: str, exchange: str, quantity: int,
                   order_type: str = None, price: float = None) -> OrderResult:
        return self.place_order(OrderRequest(
            symbol=symbol,
            exchange=exchange,
            side="SELL",
            quantity=quantity,
            order_type=order_type or getattr(config, "EXIT_ORDER_TYPE", "MARKET"),
            product=getattr(config, "ORDER_PRODUCT", "MIS"),
            price=price,
            tag="EXIT",
        ))

    def place_stop_loss(self, symbol: str, exchange: str, quantity: int,
                        trigger_price: float) -> OrderResult:
        return self.place_order(OrderRequest(
            symbol=symbol,
            exchange=exchange,
            side="SELL",
            quantity=quantity,
            order_type="SL-M",
            product=getattr(config, "ORDER_PRODUCT", "MIS"),
            trigger_price=trigger_price,
            tag="SL",
        ))

    def cancel_order(self, order_id: str) -> bool:
        if not order_id:
            return False
        if not self.live_mode or order_id.startswith("PAPER-"):
            return True
        try:
            self.kite.cancel_order(variety=getattr(self.kite, "VARIETY_REGULAR", "regular"), order_id=order_id)
            return True
        except Exception as exc:
            if self.logger:
                self.logger.exception("cancel_order failed: %s", exc)
            return False

    def modify_stop_loss(self, order_id: str, trigger_price: float) -> bool:
        if not order_id:
            return False
        if not self.live_mode or order_id.startswith("PAPER-"):
            return True
        try:
            self.kite.modify_order(
                variety=getattr(self.kite, "VARIETY_REGULAR", "regular"),
                order_id=order_id,
                trigger_price=trigger_price,
                order_type="SL-M",
            )
            return True
        except Exception as exc:
            if self.logger:
                self.logger.exception("modify_stop_loss failed: %s", exc)
            return False

    def _wait_for_fill(self, order_id: str, request: OrderRequest) -> OrderResult:
        timeout = float(getattr(config, "ORDER_FILL_TIMEOUT_SECONDS", 10))
        poll = float(getattr(config, "ORDER_FILL_POLL_SECONDS", 0.5))
        deadline = time.time() + max(timeout, 0)
        latest = {}

        while True:
            try:
                history = self.kite.order_history(order_id)
                if history:
                    latest = history[-1]
            except Exception as exc:
                return self._failed(request, f"order submitted but fill check failed for {order_id}: {exc}", paper=False)

            status = str(latest.get("status") or "").upper()
            filled = int(latest.get("filled_quantity") or 0)
            average_price = latest.get("average_price")
            if average_price in ("", 0):
                average_price = None

            if status == "COMPLETE" and filled >= request.quantity:
                return OrderResult(
                    ok=True,
                    order_id=order_id,
                    status=status,
                    filled_quantity=filled,
                    average_price=float(average_price) if average_price is not None else None,
                    request=asdict(request),
                    timestamp=datetime.now().isoformat(timespec="seconds"),
                    paper=False,
                )

            if status in ("REJECTED", "CANCELLED"):
                reason = latest.get("status_message") or latest.get("status_message_raw") or status
                return self._failed(request, f"order {order_id} {status}: {reason}", paper=False)

            if time.time() >= deadline:
                if request.order_type == "LIMIT" and status in ("OPEN", "TRIGGER PENDING", "VALIDATION PENDING"):
                    self.cancel_order(order_id)
                return self._failed(
                    request,
                    f"order {order_id} not filled within {timeout:g}s (status={status or 'UNKNOWN'}, filled={filled})",
                    paper=False,
                )

            time.sleep(max(poll, 0.1))

    def _wait_for_accept(self, order_id: str, request: OrderRequest) -> OrderResult:
        timeout = float(getattr(config, "ORDER_FILL_TIMEOUT_SECONDS", 10))
        poll = float(getattr(config, "ORDER_FILL_POLL_SECONDS", 0.5))
        deadline = time.time() + max(timeout, 0)
        latest = {}

        while True:
            try:
                history = self.kite.order_history(order_id)
                if history:
                    latest = history[-1]
            except Exception as exc:
                return self._failed(request, f"order submitted but accept check failed for {order_id}: {exc}", paper=False)

            status = str(latest.get("status") or "").upper()
            filled = int(latest.get("filled_quantity") or 0)
            average_price = latest.get("average_price")
            if average_price in ("", 0):
                average_price = None

            if status in ("OPEN", "TRIGGER PENDING", "COMPLETE"):
                return OrderResult(
                    ok=True,
                    order_id=order_id,
                    status=status,
                    filled_quantity=filled,
                    average_price=float(average_price) if average_price is not None else None,
                    request=asdict(request),
                    timestamp=datetime.now().isoformat(timespec="seconds"),
                    paper=False,
                )

            if status in ("REJECTED", "CANCELLED"):
                reason = latest.get("status_message") or latest.get("status_message_raw") or status
                return self._failed(request, f"order {order_id} {status}: {reason}", paper=False)

            if time.time() >= deadline:
                return self._failed(
                    request,
                    f"order {order_id} not accepted within {timeout:g}s (status={status or 'UNKNOWN'})",
                    paper=False,
                )

            time.sleep(max(poll, 0.1))

    def _failed(self, request: OrderRequest, error: str, paper: bool = True) -> OrderResult:
        return OrderResult(
            ok=False,
            status="REJECTED",
            error=error,
            request=asdict(request),
            timestamp=datetime.now().isoformat(timespec="seconds"),
            paper=paper,
        )
