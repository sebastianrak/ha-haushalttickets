"""Constants for the Haushalttickets feasibility spike."""

DOMAIN = "haushalttickets_spike"

LLM_API_ID = DOMAIN
LLM_API_NAME = "Haushalttickets (spike)"

EVENT_TICKET_CREATED = f"{DOMAIN}_ticket_created"

TICKET_TYPES = ["repair", "purchase", "home_assistant"]
TICKET_STATUSES = [
    "new",
    "discussing",
    "in_progress",
    "waiting_for_parts",
    "acceptance",
    "done",
    "rejected",
]
