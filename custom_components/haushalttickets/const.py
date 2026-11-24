"""Constants for Haushalttickets."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "haushalttickets"

# Fired on the event bus for every change on the server.
EVENT_TICKET: Final = "haushalttickets_event"
# One per person who should hear about a change; an automation delivers it.
EVENT_NOTIFICATION: Final = "haushalttickets_notification"
# Buttons in mobile app notifications ("HAUSHALTTICKETS_APPROVE_7", ...).
EVENT_NOTIFICATION_ACTION: Final = "mobile_app_notification_action"
ACTION_PREFIX: Final = "HAUSHALTTICKETS_"

# LLM API with the ticket tools; mcp_server serves it at /api/mcp/haushalttickets.
LLM_API_ID: Final = "haushalttickets"
LLM_API_NAME: Final = "Haushalttickets"

# The Kanban card, served by the integration and loaded on every dashboard.
CARD_URL: Final = "/haushalttickets_static/haushalttickets-card.js"

# The server user Home Assistant automations and scripts act as.
AUTOMATION_USER_ID: Final = "automation"

SCAN_INTERVAL: Final = timedelta(seconds=10)
REQUEST_TIMEOUT: Final = 10

TICKET_TYPES: Final = ("repair", "purchase", "home_assistant")
STATUSES: Final = (
    "new",
    "discussing",
    "in_progress",
    "waiting_for_parts",
    "acceptance",
    "done",
    "rejected",
)
URGENCIES: Final = ("someday", "this_week", "burning")
ROLES: Final = ("admin", "adult", "child", "ai_agent")

# Ticket lists in entity attributes are capped to keep states small.
MAX_ATTRIBUTE_TICKETS: Final = 25

SERVICE_CREATE_TICKET: Final = "create_ticket"
SERVICE_SET_STATUS: Final = "set_status"
SERVICE_ADD_COMMENT: Final = "add_comment"
SERVICE_ASSIGN: Final = "assign"
SERVICE_SET_ROLE: Final = "set_role"
SERVICE_SEND_DUE_REMINDERS: Final = "send_due_reminders"

ATTR_TYPE: Final = "type"
ATTR_TITLE: Final = "title"
ATTR_DESCRIPTION: Final = "description"
ATTR_URGENCY: Final = "urgency"
ATTR_DUE_DATE: Final = "due_date"
ATTR_ASSIGNEES: Final = "assignees"
ATTR_AREA_ID: Final = "area_id"
ATTR_DEVICE_ID: Final = "device_id"
ATTR_EXTRA_FIELDS: Final = "extra_fields"
ATTR_TICKET_ID: Final = "ticket_id"
ATTR_STATUS: Final = "status"
ATTR_COMMENT: Final = "comment"
ATTR_PERSON: Final = "person"
ATTR_USER: Final = "user"
ATTR_ROLE: Final = "role"
