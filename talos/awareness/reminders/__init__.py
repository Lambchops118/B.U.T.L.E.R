"""Durable reminders and the deterministic due-time worker.

A reminder is stored with an absolute ``due_at`` (the LLM parses the user's
natural-language time when creating it). The worker fires reminders whose time
has passed by raising an attention item through the normal notification egress
— spoken by default — so the system speaks up on its own at the right moment.
The LLM is never the clock: firing is a deterministic database poll.
"""
