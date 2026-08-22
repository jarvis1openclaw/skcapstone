# Calendar connector boundary

The `sklegal_calendar` package provides a simulation-only calendar write
adapter. It derives the event digest used as the artifact hash, derives the
destination digest from the exact calendar_id, detects conflicts against
existing events, and returns synthetic write receipts bound to the exact
event version. It opens no network, SMTP, HTTP, or file transport. Live
calendar delivery requires a separate approved follow-up card.
