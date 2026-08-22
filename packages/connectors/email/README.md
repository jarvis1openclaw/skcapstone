# Email connector boundary

The `sklegal_email` package provides a simulation-only email action adapter.
It validates normalized recipients, binds the destination digest into the
shared connector action, and reconciles delivered or bounced receipts without
opening an SMTP or HTTP transport. Live delivery requires a separate approved
follow-up card.
