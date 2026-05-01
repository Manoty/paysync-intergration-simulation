"""
Tixora Integration Simulation
==============================
Simulates the Tixora ticketing system's payment flow.

Scenario:
  Customer selects 2x concert tickets (KES 1,500 each)
  Tixora calls PaySync to initiate payment
  Tixora polls until payment confirmed or failed.
  Tixora confirms or cancels the ticket reservation.
"""

import sys
import uuid
import logging
from paysync_client import PaySyncClient, PaySyncError

logger = logging.getLogger('tixora')

# ── Tixora configuration ───────────────────────────────────────────────────────
PAYSYNC_BASE_URL = "http://127.0.0.1:8000"
SOURCE_SYSTEM    = "tixora"


# ── Tixora internal models (simulated in memory) ───────────────────────────────

class TicketReservation:
    """Simulates a Tixora ticket reservation record."""

    def __init__(self, order_id, event, quantity, unit_price, phone):
        self.order_id         = order_id
        self.event            = event
        self.quantity         = quantity
        self.unit_price       = unit_price
        self.total_amount     = quantity * unit_price
        self.phone            = phone
        self.status           = 'reserved'      # reserved → confirmed | cancelled
        self.paysync_reference = None           # Set after PaySync responds

    def confirm(self):
        self.status = 'confirmed'
        logger.info(
            f"✅ TICKET CONFIRMED | order={self.order_id} | "
            f"event={self.event} | qty={self.quantity}"
        )

    def cancel(self, reason):
        self.status = 'cancelled'
        logger.warning(
            f"❌ TICKET CANCELLED | order={self.order_id} | reason={reason}"
        )

    def __str__(self):
        return (
            f"Order {self.order_id} | {self.event} x{self.quantity} | "
            f"KES {self.total_amount} | Status: {self.status}"
        )


# ── Tixora payment flow ────────────────────────────────────────────────────────

class TixoraPaymentFlow:
    """
    Encapsulates how Tixora handles payments via PaySync.

    Notice: zero M-Pesa knowledge here.
    Tixora only speaks PaySync's language: initiate, poll, react.
    """

    def __init__(self):
        self.client = PaySyncClient(
            base_url=PAYSYNC_BASE_URL,
            source_system=SOURCE_SYSTEM,
        )

    def process_ticket_purchase(self, reservation: TicketReservation):
        """
        Full payment flow for a ticket purchase.

        Step 1: Initiate payment via PaySync
        Step 2: Store PaySync reference on reservation
        Step 3: Poll until terminal state
        Step 4: Confirm or cancel based on outcome
        """
        logger.info(
            f"\n{'='*60}\n"
            f"TIXORA: Processing ticket purchase\n"
            f"  Order:    {reservation.order_id}\n"
            f"  Event:    {reservation.event}\n"
            f"  Quantity: {reservation.quantity}\n"
            f"  Total:    KES {reservation.total_amount}\n"
            f"  Phone:    {reservation.phone}\n"
            f"{'='*60}"
        )

        # ── Step 1: Initiate payment ──────────────────────────────────────────
        try:
            result = self.client.initiate_payment(
                amount=reservation.total_amount,
                phone_number=reservation.phone,
                external_reference=reservation.order_id,
            )
        except PaySyncError as e:
            logger.error(f"PaySync unavailable: {e}")
            reservation.cancel(f"Payment system unavailable: {e}")
            return reservation

        if not result.success:
            logger.error(f"Payment initiation failed: {result.failure_reason}")
            reservation.cancel(f"Payment initiation failed: {result.failure_reason}")
            return reservation

        # ── Step 2: Store PaySync reference ───────────────────────────────────
        # Tixora saves this reference in its own database
        # so it can look up payment status at any time
        reservation.paysync_reference = result.reference
        logger.info(
            f"PaySync reference stored | "
            f"order={reservation.order_id} | ref={result.reference}"
        )

        logger.info(
            "📱 STK Push sent to customer's phone. "
            "Waiting for M-Pesa PIN entry..."
        )

        # ── Step 3: Poll until terminal state ─────────────────────────────────
        # In production: this would be a background job
        # For simulation: we block here to see the full flow
        try:
            final_result = self.client.poll_until_complete(
                reference=result.reference,
                max_wait_seconds=120,
                poll_interval_seconds=5,
            )
        except PaySyncError as e:
            logger.error(f"Error polling payment status: {e}")
            reservation.cancel(f"Could not verify payment: {e}")
            return reservation

        # ── Step 4: React to final status ─────────────────────────────────────
        if final_result.status == 'success':
            reservation.confirm()

        elif final_result.status == 'failed':
            reservation.cancel(
                final_result.failure_reason or "Payment was not completed."
            )

        else:
            # Still pending after max_wait — treat as timeout
            logger.warning(
                f"Payment status still pending after timeout | "
                f"ref={result.reference}"
            )
            # In production: don't cancel — let a background job recheck
            # For simulation: we cancel to complete the demo
            reservation.cancel("Payment confirmation timed out.")

        logger.info(f"\nFinal reservation state: {reservation}")
        return reservation


# ── Simulation runner ──────────────────────────────────────────────────────────

def run_tixora_simulation():
    """
    Runs three scenarios:
    1. Normal successful purchase
    2. Duplicate order (idempotency test)
    3. Second customer, different order
    """
    flow = TixoraPaymentFlow()

    # ── Scenario 1: Standard ticket purchase ──────────────────────────────────
    print("\n" + "="*60)
    print("SCENARIO 1: Standard ticket purchase")
    print("="*60)

    reservation_1 = TicketReservation(
        order_id=f"TIXORA_ORDER_{uuid.uuid4().hex[:8].upper()}",
        event="Blanck Canvas Concert — Nairobi",
        quantity=2,
        unit_price=1500,
        phone="254708374149",   # Safaricom sandbox test number
    )
    flow.process_ticket_purchase(reservation_1)

    # ── Scenario 2: Same order submitted twice (idempotency) ──────────────────
    if reservation_1.paysync_reference:
        print("\n" + "="*60)
        print("SCENARIO 2: Duplicate order submission (idempotency test)")
        print("="*60)

        # Tixora's network glitched — they send the same order again
        duplicate_reservation = TicketReservation(
            order_id=reservation_1.order_id,   # Same order ID
            event="Blanck Canvas Concert — Nairobi",
            quantity=2,
            unit_price=1500,
            phone="254708374149",
        )
        try:
            result = flow.client.initiate_payment(
                amount=duplicate_reservation.total_amount,
                phone_number=duplicate_reservation.phone,
                external_reference=duplicate_reservation.order_id,
            )
            logger.info(
                f"Duplicate order response | "
                f"ref={result.reference} | "
                f"message={result.message}"
            )
            # Should return the SAME reference — no duplicate created
            assert result.reference == reservation_1.paysync_reference, \
                "ERROR: Different reference returned for duplicate order!"
            logger.info("✅ Idempotency confirmed — same reference returned.")
        except PaySyncError as e:
            logger.error(f"PaySyncError on duplicate: {e}")

    # ── Scenario 3: Different customer, different order ────────────────────────
    print("\n" + "="*60)
    print("SCENARIO 3: Second customer purchase")
    print("="*60)

    reservation_2 = TicketReservation(
        order_id=f"TIXORA_ORDER_{uuid.uuid4().hex[:8].upper()}",
        event="Sauti Sol Live — Kasarani",
        quantity=1,
        unit_price=3000,
        phone="254708374149",
    )
    flow.process_ticket_purchase(reservation_2)


if __name__ == '__main__':
    run_tixora_simulation()