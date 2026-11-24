"""Actions for automations, scripts and dashboards."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    Context,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.util import dt as dt_util
import probatio

from .api import AUTOMATION, Acting, ApiError, CannotConnect, InvalidAuth
from .const import (
    ATTR_AREA_ID,
    ATTR_ASSIGNEES,
    ATTR_COMMENT,
    ATTR_DESCRIPTION,
    ATTR_DEVICE_ID,
    ATTR_DUE_DATE,
    ATTR_EXTRA_FIELDS,
    ATTR_PERSON,
    ATTR_ROLE,
    ATTR_STATUS,
    ATTR_TICKET_ID,
    ATTR_TITLE,
    ATTR_TYPE,
    ATTR_URGENCY,
    ATTR_USER,
    DOMAIN,
    ROLES,
    SERVICE_ADD_COMMENT,
    SERVICE_ASSIGN,
    SERVICE_CREATE_TICKET,
    SERVICE_SEND_DUE_REMINDERS,
    SERVICE_SET_ROLE,
    SERVICE_SET_STATUS,
    STATUSES,
    TICKET_TYPES,
    URGENCIES,
)
from .coordinator import HaushaltticketsCoordinator, ticket_summary
from .notifications import due_notifications, fire

_TICKET_ID = probatio.All(probatio.Coerce(int), probatio.Range(min=1))

CREATE_TICKET_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_TYPE): probatio.In(TICKET_TYPES),
        probatio.Required(ATTR_TITLE): probatio.All(
            cv.string, probatio.Length(min=1, max=200)
        ),
        probatio.Optional(ATTR_DESCRIPTION): cv.string,
        probatio.Optional(ATTR_URGENCY): probatio.In(URGENCIES),
        probatio.Optional(ATTR_DUE_DATE): cv.date,
        probatio.Optional(ATTR_ASSIGNEES): cv.entity_ids,
        probatio.Optional(ATTR_AREA_ID): cv.string,
        probatio.Optional(ATTR_DEVICE_ID): cv.string,
        probatio.Optional(ATTR_EXTRA_FIELDS): dict,
    }
)
SET_STATUS_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_TICKET_ID): _TICKET_ID,
        probatio.Required(ATTR_STATUS): probatio.In(STATUSES),
    }
)
ADD_COMMENT_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_TICKET_ID): _TICKET_ID,
        probatio.Required(ATTR_COMMENT): probatio.All(
            cv.string, probatio.Length(min=1, max=20000)
        ),
    }
)
ASSIGN_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_TICKET_ID): _TICKET_ID,
        probatio.Required(ATTR_ASSIGNEES): cv.entity_ids,
    }
)
SET_ROLE_SCHEMA = probatio.All(
    probatio.Schema(
        {
            # A person, or a user without person such as the AI account.
            probatio.Exclusive(ATTR_PERSON, "target"): cv.entity_domain("person"),
            probatio.Exclusive(ATTR_USER, "target"): cv.string,
            probatio.Required(ATTR_ROLE): probatio.In(ROLES),
        }
    ),
    cv.has_at_least_one_key(ATTR_PERSON, ATTR_USER),
)

# Fields set through dedicated options; extra_fields must not override them.
_RESERVED_FIELDS = frozenset(
    {"type", "title", "description", "urgency", "due_date", "assignee_ids",
     "ha_area_id", "ha_device_id", "requested_by_id"}
)  # fmt: skip


def get_coordinator(hass: HomeAssistant) -> HaushaltticketsCoordinator:
    """The coordinator of the loaded config entry."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="not_loaded"
    )


async def acting_for(hass: HomeAssistant, context: Context) -> Acting:
    """The person who triggered the call; automations act as "Automation"."""
    if context.user_id:
        user = await hass.auth.async_get_user(context.user_id)
        if user is not None and not user.system_generated:
            return Acting(user.id, "ui")
    return AUTOMATION


def user_ids_for_persons(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    """Map person entities to the Home Assistant users linked to them."""
    user_ids = []
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is None or state.domain != "person":
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_a_person",
                translation_placeholders={"entity_id": entity_id},
            )
        if not (user_id := state.attributes.get("user_id")):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="person_without_user",
                translation_placeholders={"entity_id": entity_id},
            )
        user_ids.append(user_id)
    return user_ids


async def call_server[T](
    coordinator: HaushaltticketsCoordinator,
    request: Callable[[], Awaitable[T]],
    *,
    users_changed: bool = False,
) -> T:
    """Run a request and translate server errors into Home Assistant errors."""
    try:
        result = await request()
    except InvalidAuth as err:
        coordinator.config_entry.async_start_reauth(coordinator.hass)
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from err
    except CannotConnect as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err
    except ApiError as err:
        placeholders = {"code": err.code, "detail": err.detail}
        if err.status == 403:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="forbidden",
                translation_placeholders=placeholders,
            ) from err
        if err.status == 404:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_found",
                translation_placeholders=placeholders,
            ) from err
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="rejected",
            translation_placeholders=placeholders,
        ) from err
    if users_changed:
        await coordinator.async_refresh_users()
    else:
        await coordinator.async_request_refresh()
    return result


async def _create_ticket(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    coordinator = get_coordinator(hass)
    acting = await acting_for(hass, call.context)
    data = call.data
    extra = dict(data.get(ATTR_EXTRA_FIELDS, {}))
    if reserved := sorted(_RESERVED_FIELDS & extra.keys()):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="reserved_fields",
            translation_placeholders={"fields": ", ".join(reserved)},
        )
    body: dict[str, Any] = {
        **extra,
        "type": data[ATTR_TYPE],
        "title": data[ATTR_TITLE],
        "assignee_ids": user_ids_for_persons(hass, data.get(ATTR_ASSIGNEES, [])),
    }
    for option, field in (
        (ATTR_DESCRIPTION, "description"),
        (ATTR_URGENCY, "urgency"),
        (ATTR_AREA_ID, "ha_area_id"),
        (ATTR_DEVICE_ID, "ha_device_id"),
    ):
        if option in data:
            body[field] = data[option]
    if ATTR_DUE_DATE in data:
        body["due_date"] = data[ATTR_DUE_DATE].isoformat()

    ticket = await call_server(
        coordinator, lambda: coordinator.client.create_ticket(acting, body)
    )
    return ticket_summary(ticket, coordinator.data)


async def _set_status(call: ServiceCall) -> ServiceResponse:
    coordinator = get_coordinator(call.hass)
    acting = await acting_for(call.hass, call.context)
    ticket = await call_server(
        coordinator,
        lambda: coordinator.client.set_status(
            acting, call.data[ATTR_TICKET_ID], call.data[ATTR_STATUS]
        ),
    )
    return ticket_summary(ticket, coordinator.data)


async def _add_comment(call: ServiceCall) -> None:
    coordinator = get_coordinator(call.hass)
    acting = await acting_for(call.hass, call.context)
    await call_server(
        coordinator,
        lambda: coordinator.client.add_comment(
            acting, call.data[ATTR_TICKET_ID], call.data[ATTR_COMMENT]
        ),
    )


async def _send_due_reminders(call: ServiceCall) -> ServiceResponse:
    """One summary per person: overdue, due today and due tomorrow."""
    hass = call.hass
    coordinator = get_coordinator(hass)
    await coordinator.async_refresh()
    if not coordinator.last_update_success or coordinator.data is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(coordinator.last_exception)},
        )
    notifications = due_notifications(
        coordinator.data.tickets,
        coordinator.data,
        dt_util.now().date(),
        hass.config.language,
    )
    fire(hass, notifications)
    return {"notifications": len(notifications)}


async def _assign(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    coordinator = get_coordinator(hass)
    acting = await acting_for(hass, call.context)
    user_ids = user_ids_for_persons(hass, call.data[ATTR_ASSIGNEES])
    ticket = await call_server(
        coordinator,
        lambda: coordinator.client.set_assignees(
            acting, call.data[ATTR_TICKET_ID], user_ids
        ),
    )
    return ticket_summary(ticket, coordinator.data)


async def user_id_for_name(hass: HomeAssistant, name_or_id: str) -> str:
    """The Home Assistant user with this id or (case-insensitive) name."""
    wanted = name_or_id.strip().casefold()
    users = [
        user
        for user in await hass.auth.async_get_users()
        if not user.system_generated
        and (user.id == name_or_id.strip() or (user.name or "").casefold() == wanted)
    ]
    if len(users) != 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="user_not_found" if not users else "user_ambiguous",
            translation_placeholders={"user": name_or_id},
        )
    return users[0].id


async def _set_role(call: ServiceCall) -> None:
    hass = call.hass
    coordinator = get_coordinator(hass)
    acting = await acting_for(hass, call.context)
    if ATTR_PERSON in call.data:
        (user_id,) = user_ids_for_persons(hass, [call.data[ATTR_PERSON]])
    else:
        user_id = await user_id_for_name(hass, call.data[ATTR_USER])
    await call_server(
        coordinator,
        lambda: coordinator.client.set_role(acting, user_id, call.data[ATTR_ROLE]),
        users_changed=True,
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_TICKET,
        _create_ticket,
        schema=CREATE_TICKET_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_STATUS,
        _set_status,
        schema=SET_STATUS_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ADD_COMMENT, _add_comment, schema=ADD_COMMENT_SCHEMA
    )
    # Home Assistant admins only; the server also requires its own admin role.
    async_register_admin_service(
        hass, DOMAIN, SERVICE_SET_ROLE, _set_role, schema=SET_ROLE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_DUE_REMINDERS,
        _send_due_reminders,
        schema=probatio.Schema({}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ASSIGN,
        _assign,
        schema=ASSIGN_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
