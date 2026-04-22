import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import caldav
import icalendar
import requests


ENV_PATHS = [
    Path(__file__).with_name(".env"),
    Path(__file__).resolve().parent.parent / ".env",
]
CONTACTS_PATH = Path(__file__).with_name("contacts.json")
ROOMS_PATH = Path(__file__).with_name("rooms.json")
WORK_CALENDAR_PATH = Path(__file__).with_name("work_calendar.json")
CALENDAR_SETTINGS_PATH = Path(__file__).with_name("calendar_settings.json")
DEFAULT_ROOM_SIGNAL_WORDS = ("перег", "комната", "meeting", "room", "бронь")
YANDEX_360_API_BASE = "https://api360.yandex.net/directory/v1"


def load_env_file(env_path: Path) -> bool:
    if not env_path.exists():
        return False

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return True


def load_env_files() -> None:
    for env_path in ENV_PATHS:
        load_env_file(env_path)


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing {name} in .env")
    return value


def optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def load_contacts() -> dict[str, dict[str, str]]:
    if not CONTACTS_PATH.exists():
        return {}
    return json.loads(CONTACTS_PATH.read_text(encoding="utf-8"))


def load_rooms() -> dict[str, dict[str, str]]:
    if not ROOMS_PATH.exists():
        return {}
    return json.loads(ROOMS_PATH.read_text(encoding="utf-8"))


def load_work_calendar() -> dict:
    if not WORK_CALENDAR_PATH.exists():
        raise SystemExit(f"Missing {WORK_CALENDAR_PATH.name}")
    return json.loads(WORK_CALENDAR_PATH.read_text(encoding="utf-8"))


def load_calendar_settings() -> dict:
    if not CALENDAR_SETTINGS_PATH.exists():
        return {}
    return json.loads(CALENDAR_SETTINGS_PATH.read_text(encoding="utf-8"))


def room_priority() -> list[str]:
    settings = load_calendar_settings()
    configured = settings.get("room_priority")
    if configured:
        return [normalize_lookup_text(value) for value in configured]
    return list(load_rooms().keys())


def small_room_keys() -> set[str]:
    settings = load_calendar_settings()
    return {normalize_lookup_text(value) for value in settings.get("small_rooms", [])}


def room_signal_words() -> tuple[str, ...]:
    settings = load_calendar_settings()
    words = settings.get("room_signal_words") or DEFAULT_ROOM_SIGNAL_WORDS
    return tuple(normalize_lookup_text(value) for value in words)


def normalize_lookup_text(value: str) -> str:
    return " ".join(value.lower().replace("ё", "е").split())


def yandex_360_headers() -> dict[str, str]:
    token = optional_env("YANDEX_360_OAUTH_TOKEN")
    if not token:
        raise SystemExit(
            "Missing YANDEX_360_OAUTH_TOKEN in .env. "
            "Grant directory:read_organization and directory:read_users, "
            "then add the OAuth token."
        )
    return {"Authorization": f"OAuth {token}"}


def yandex_360_get(path: str, params: dict[str, str | int] | None = None) -> dict:
    response = requests.get(
        f"{YANDEX_360_API_BASE}{path}",
        headers=yandex_360_headers(),
        params=params,
        timeout=30,
    )
    if response.status_code >= 400:
        raise SystemExit(
            f"Yandex 360 API request failed: HTTP {response.status_code} {response.text}"
        )
    return response.json()


def get_yandex_360_org_id() -> str:
    configured = optional_env("YANDEX_360_ORG_ID")
    if configured:
        return configured

    page_token = ""
    organizations = []
    while True:
        params = {"pageSize": 100}
        if page_token:
            params["pageToken"] = page_token
        data = yandex_360_get("/org", params=params)
        organizations.extend(data.get("organizations", []))
        page_token = data.get("nextPageToken") or ""
        if not page_token:
            break

    if not organizations:
        raise SystemExit("No Yandex 360 organizations are visible to this token.")
    if len(organizations) > 1:
        names = ", ".join(f"{item.get('id')}:{item.get('name')}" for item in organizations)
        raise SystemExit(
            "Multiple Yandex 360 organizations found. "
            f"Set YANDEX_360_ORG_ID in .env. Available: {names}"
        )
    return str(organizations[0]["id"])


def iter_yandex_360_users() -> Iterable[dict]:
    org_id = get_yandex_360_org_id()
    page_token = ""
    while True:
        params = {"pageSize": 100}
        if page_token:
            params["pageToken"] = page_token
        data = yandex_360_get(f"/org/{org_id}/users", params=params)
        for user in data.get("users", []):
            yield user
        page_token = data.get("nextPageToken") or ""
        if not page_token:
            break


def user_search_text(user: dict) -> str:
    name = user.get("name") or {}
    parts = [
        user.get("displayName", ""),
        user.get("email", ""),
        user.get("nickname", ""),
        name.get("first", ""),
        name.get("last", ""),
        name.get("middle", ""),
    ]
    for contact in user.get("contacts", []) or []:
        if contact.get("type") == "email":
            parts.append(contact.get("value", ""))
    for alias in user.get("aliases", []) or []:
        parts.append(alias)
    return normalize_lookup_text(" ".join(str(part) for part in parts if part))


def yandex_360_user_to_contact(user: dict) -> dict[str, str] | None:
    email = user.get("email")
    if not email:
        return None

    display_name = user.get("displayName")
    if not display_name:
        name = user.get("name") or {}
        display_name = " ".join(
            part for part in [name.get("first"), name.get("last")] if part
        )
    return {"name": display_name or email, "email": email.lower()}


def find_yandex_360_contact(query: str) -> dict[str, str]:
    normalized_query = normalize_lookup_text(query)
    query_tokens = normalized_query.split()
    matches = []

    for user in iter_yandex_360_users():
        if user.get("isDismissed") or not user.get("isEnabled", True):
            continue
        search_text = user_search_text(user)
        if all(token in search_text for token in query_tokens):
            contact = yandex_360_user_to_contact(user)
            if contact:
                matches.append(contact)

    if not matches:
        raise SystemExit(f"Corporate contact was not found: {query}")
    if len(matches) > 1:
        options = ", ".join(f"{item['name']} <{item['email']}>" for item in matches[:10])
        raise SystemExit(
            f"Corporate contact lookup is ambiguous for '{query}'. Matches: {options}"
        )
    return matches[0]


def resolve_attendees(names: list[str] | None) -> list[dict[str, str]]:
    if not names:
        return []

    contacts = load_contacts()
    resolved: list[dict[str, str]] = []
    missing: list[str] = []

    for name in names:
        key = name.strip().lower()
        if not key:
            continue
        if "@" in key:
            resolved.append({"name": name.strip(), "email": key})
            continue

        contact = contacts.get(key)
        if contact:
            resolved.append(contact)
        else:
            missing.append(name.strip())

    for name in missing:
        if optional_env("YANDEX_360_OAUTH_TOKEN"):
            resolved.append(find_yandex_360_contact(name))
        else:
            raise SystemExit(
                f"Contact was not found in contacts.json: {name}. "
                "Add an alias to contacts.json, pass a direct email, "
                "or configure YANDEX_360_OAUTH_TOKEN for Directory lookup."
            )

    unique: dict[str, dict[str, str]] = {}
    for attendee in resolved:
        unique[attendee["email"].lower()] = attendee
    return list(unique.values())


def resolve_room(room_name: str | None) -> dict[str, str] | None:
    if not room_name:
        return None

    rooms = load_rooms()
    key = room_name.strip().lower()
    if not key:
        return None

    if "@" in key:
        return {"name": room_name.strip(), "email": key}

    room = rooms.get(key)
    if not room:
        raise SystemExit(
            f"Unknown room: {room_name}. Add it to rooms.json "
            "or pass a resource email. See rooms.example.json."
        )
    return room


def event_overlaps(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


def event_uses_room(component, room: dict[str, str]) -> bool:
    room_name = room["name"].strip().lower()
    room_email = room["email"].strip().lower()

    location = component.get("location")
    if location and room_name in str(location).strip().lower():
        return True

    attendees = component.get("attendee")
    if not attendees:
        return False
    if not isinstance(attendees, list):
        attendees = [attendees]

    for attendee in attendees:
        identity = attendee_identity(attendee)
        if not identity:
            continue
        attendee_name, attendee_email = identity
        if attendee_email == room_email or attendee_name.strip().lower() == room_name:
            return True
    return False


def room_has_visible_conflict(
    principal,
    room: dict[str, str],
    start_dt: datetime,
    end_dt: datetime,
) -> bool:
    for calendar in iter_calendars(principal):
        try:
            events = calendar.search(
                start=start_dt,
                end=end_dt,
                event=True,
                expand=True,
            )
        except TypeError:
            events = calendar.date_search(start=start_dt, end=end_dt)

        for event in events:
            try:
                component = event.icalendar_component
                event_start = normalize_dt(component.get("dtstart").dt, get_timezone())
                event_end = normalize_dt(
                    component.get("dtend").dt if component.get("dtend") else None,
                    get_timezone(),
                )
                if not event_start or not event_end:
                    continue
                if event_overlaps(start_dt, end_dt, event_start, event_end) and event_uses_room(component, room):
                    return True
            except Exception:
                continue
    return False


def request_freebusy_xml(
    principal,
    room: dict[str, str],
    start_dt: datetime,
    end_dt: datetime,
) -> str:
    freebusy_ical = icalendar.Calendar()
    freebusy_ical.add("prodid", "-//Codex//Yandex Calendar Bridge//EN")
    freebusy_ical.add("version", "2.0")
    freebusy_ical.add("method", "REQUEST")
    freebusy_comp = icalendar.FreeBusy()
    freebusy_comp.add("uid", str(uuid.uuid4()))
    freebusy_comp.add("dtstamp", datetime.now(UTC))
    freebusy_comp.add("dtstart", start_dt)
    freebusy_comp.add("dtend", end_dt)
    freebusy_comp.add("attendee", f"mailto:{room['email']}")
    freebusy_ical.add_component(freebusy_comp)

    outbox = principal.schedule_outbox()
    response = principal.client.post(
        outbox.url,
        freebusy_ical.to_ical(),
        headers={"Content-Type": "text/calendar; charset=utf-8"},
    )
    raw = str(getattr(response, "raw", response))
    status = getattr(response, "status", None)
    if status and int(status) >= 400:
        raise RuntimeError(f"Free-busy HTTP status {status}: {raw}")
    return raw


def parse_freebusy_periods(raw_xml: str, tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    periods: list[tuple[datetime, datetime]] = []
    root = ET.fromstring(raw_xml)
    namespace = {"C": "urn:ietf:params:xml:ns:caldav"}
    for calendar_data in root.findall(".//C:calendar-data", namespace):
        payload = calendar_data.text
        if not payload:
            continue
        calendar = icalendar.Calendar.from_ical(payload)
        for component in calendar.walk("VFREEBUSY"):
            freebusy_values = component.get("FREEBUSY")
            if not freebusy_values:
                continue
            if not isinstance(freebusy_values, list):
                freebusy_values = [freebusy_values]
            for value in freebusy_values:
                start_value, end_value = value.dt
                start_busy = normalize_dt(start_value, tz)
                end_busy = normalize_dt(end_value, tz)
                if start_busy and end_busy:
                    periods.append((start_busy, end_busy))
    return periods


def request_attendee_busy(
    principal,
    email: str,
    start_dt: datetime,
    end_dt: datetime,
) -> list[tuple[datetime, datetime]]:
    raw_xml = request_freebusy_xml(
        principal,
        {"name": email, "email": email},
        start_dt,
        end_dt,
    )
    return parse_freebusy_periods(raw_xml, get_timezone())


def is_working_date(target_date: date, config: dict) -> bool:
    value = target_date.isoformat()
    if value in set(config.get("working_weekend_dates", [])):
        return True
    if value in set(config.get("non_working_dates", [])):
        return False
    return target_date.weekday() not in set(config.get("weekend_days", [5, 6]))


def parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def working_window_for_date(target_date: date, config: dict, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    if not is_working_date(target_date, config):
        return None
    hours = config.get("working_hours", {})
    start_time = parse_hhmm(hours.get("start", "10:00"))
    end_value = config.get("shortened_dates", {}).get(
        target_date.isoformat(),
        hours.get("end", "19:00"),
    )
    end_time = parse_hhmm(end_value)
    return (
        datetime.combine(target_date, start_time, tzinfo=tz),
        datetime.combine(target_date, end_time, tzinfo=tz),
    )


def merge_busy_periods(periods: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not periods:
        return []
    sorted_periods = sorted(periods, key=lambda item: item[0])
    merged = [sorted_periods[0]]
    for start_dt, end_dt in sorted_periods[1:]:
        last_start, last_end = merged[-1]
        if start_dt <= last_end:
            merged[-1] = (last_start, max(last_end, end_dt))
        else:
            merged.append((start_dt, end_dt))
    return merged


def has_conflict(
    start_dt: datetime,
    end_dt: datetime,
    busy_periods: list[tuple[datetime, datetime]],
) -> bool:
    return any(event_overlaps(start_dt, end_dt, busy_start, busy_end) for busy_start, busy_end in busy_periods)


def iter_candidate_slots(
    from_date: date,
    to_date: date,
    duration_minutes: int,
    config: dict,
    tz: ZoneInfo,
) -> Iterable[tuple[datetime, datetime]]:
    step_minutes = int(config.get("slot_step_minutes", 15))
    current_date = from_date
    duration = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=step_minutes)
    while current_date <= to_date:
        window = working_window_for_date(current_date, config, tz)
        if window:
            current_start, window_end = window
            while current_start + duration <= window_end:
                yield current_start, current_start + duration
                current_start += step
        current_date += timedelta(days=1)


def select_available_room_freebusy(
    principal,
    start_dt: datetime,
    end_dt: datetime,
    include_small: bool = True,
) -> dict[str, str] | None:
    rooms = load_rooms()
    small_rooms = small_room_keys()
    for key in room_priority():
        if not include_small and key in small_rooms:
            continue
        room = rooms.get(key)
        if room and not room_has_freebusy_conflict(principal, room, start_dt, end_dt):
            return room
    return None


def select_available_small_room_freebusy(
    principal,
    start_dt: datetime,
    end_dt: datetime,
) -> dict[str, str] | None:
    rooms = load_rooms()
    small_rooms = small_room_keys()
    for key in room_priority():
        if key not in small_rooms:
            continue
        room = rooms.get(key)
        if room and not room_has_freebusy_conflict(principal, room, start_dt, end_dt):
            return room
    return None


def collect_calendar_busy(
    principal,
    start_dt: datetime,
    end_dt: datetime,
    calendar_name: str | None,
) -> list[tuple[datetime, datetime]]:
    busy: list[tuple[datetime, datetime]] = []
    for event in collect_events(principal, start_dt, end_dt, calendar_name):
        if event.start and event.end:
            busy.append((event.start, event.end))
    return busy


def parse_candidate_dates(
    explicit_dates: list[str] | None,
    from_date_value: str | None,
    to_date_value: str | None,
    tz: ZoneInfo,
) -> tuple[date, date, set[date] | None]:
    if explicit_dates:
        dates = {date.fromisoformat(value) for value in explicit_dates}
        return min(dates), max(dates), dates

    today = datetime.now(tz).date()
    from_date = date.fromisoformat(from_date_value) if from_date_value else today
    to_date = date.fromisoformat(to_date_value) if to_date_value else from_date + timedelta(days=14)
    if to_date < from_date:
        raise SystemExit("--to-date must be on or after --from-date.")
    return from_date, to_date, None


def suggest_meeting_slots(
    principal,
    attendees: list[dict[str, str]],
    duration_minutes: int,
    from_date: date,
    to_date: date,
    explicit_dates: set[date] | None,
    calendar_name: str | None,
    limit: int,
    require_room: bool,
) -> None:
    if duration_minutes <= 0:
        raise SystemExit("--duration must be a positive number of minutes.")

    tz = get_timezone()
    config = load_work_calendar()
    search_start = datetime.combine(from_date, time.min, tzinfo=tz)
    search_end = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=tz)
    busy_periods = collect_calendar_busy(principal, search_start, search_end, calendar_name)

    for attendee in attendees:
        try:
            busy_periods.extend(
                request_attendee_busy(
                    principal,
                    attendee["email"],
                    search_start,
                    search_end,
                )
            )
        except Exception as exc:
            raise SystemExit(
                f"Cannot read free/busy for {attendee['name']} <{attendee['email']}>: {exc}"
            ) from exc

    busy_periods = merge_busy_periods(busy_periods)
    shown = 0
    attendee_text = ", ".join(f"{item['name']} <{item['email']}>" for item in attendees)
    safe_print(f"Participants: {attendee_text if attendee_text else 'only organizer'}")
    safe_print(f"Duration: {duration_minutes} min")
    safe_print(f"Working window: {config.get('working_hours', {}).get('start', '10:00')}-{config.get('working_hours', {}).get('end', '19:00')} {tz.key}")

    for start_dt, end_dt in iter_candidate_slots(from_date, to_date, duration_minutes, config, tz):
        if explicit_dates and start_dt.date() not in explicit_dates:
            continue
        if has_conflict(start_dt, end_dt, busy_periods):
            continue

        room = select_available_room_freebusy(
            principal,
            start_dt,
            end_dt,
            include_small=not require_room,
        )
        small_room = None
        if room and normalize_lookup_text(room["name"]) in small_room_keys():
            small_room = room
            room = None
        if not room:
            small_room = small_room or select_available_small_room_freebusy(
                principal,
                start_dt,
                end_dt,
            )

        if require_room and not room:
            continue

        room_text = (
            f"{room['name']} <{room['email']}>"
            if room
            else f"only small room available, confirm before adding: {small_room['name']} <{small_room['email']}>"
            if small_room
            else "no preferred room available"
        )
        safe_print(f"{start_dt:%Y-%m-%d %H:%M}-{end_dt:%H:%M} | room: {room_text}")
        shown += 1
        if shown >= max(limit, 1):
            return

    if shown == 0:
        room_suffix = " with an available room" if require_room else ""
        safe_print(f"No suitable slots found{room_suffix}.")


def room_has_freebusy_conflict(
    principal,
    room: dict[str, str],
    start_dt: datetime,
    end_dt: datetime,
) -> bool:
    raw_xml = request_freebusy_xml(principal, room, start_dt, end_dt)
    periods = parse_freebusy_periods(raw_xml, get_timezone())
    return any(event_overlaps(start_dt, end_dt, busy_start, busy_end) for busy_start, busy_end in periods)


def select_available_room(
    principal,
    start_dt: datetime,
    end_dt: datetime,
) -> dict[str, str] | None:
    rooms = load_rooms()
    small_rooms = small_room_keys()
    for key in room_priority():
        if key in small_rooms:
            continue
        room = rooms.get(key)
        if not room:
            continue
        try:
            has_conflict = room_has_freebusy_conflict(principal, room, start_dt, end_dt)
        except Exception:
            has_conflict = room_has_visible_conflict(principal, room, start_dt, end_dt)
        if not has_conflict:
            return room
    return None


def print_freebusy_response(
    principal,
    room: dict[str, str],
    start_dt: datetime,
    end_dt: datetime,
) -> None:
    raw_xml = request_freebusy_xml(principal, room, start_dt, end_dt)
    periods = parse_freebusy_periods(raw_xml, get_timezone())
    safe_print(f"Free-busy response for {room['name']} <{room['email']}>:")
    if not periods:
        safe_print("No busy periods.")
        return
    for busy_start, busy_end in periods:
        safe_print(f"BUSY: {busy_start:%Y-%m-%d %H:%M} - {busy_end:%H:%M}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read Yandex Calendar via CalDAV."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("calendars", help="List available calendars.")
    subparsers.add_parser("today", help="Show events for today.")

    day_parser = subparsers.add_parser("day", help="Show events for a specific date.")
    day_parser.add_argument(
        "--date",
        required=True,
        help="Date in YYYY-MM-DD format.",
    )
    day_parser.add_argument(
        "--calendar",
        help="Optional calendar name to filter by.",
    )

    today_parser = subparsers.choices["today"]
    today_parser.add_argument(
        "--calendar",
        help="Optional calendar name to filter by.",
    )

    create_parser = subparsers.add_parser("create", help="Create a calendar event.")
    create_parser.add_argument("--title", required=True, help="Event title.")
    create_parser.add_argument(
        "--start",
        required=True,
        help="Start datetime in YYYY-MM-DDTHH:MM format.",
    )
    create_parser.add_argument(
        "--end",
        required=True,
        help="End datetime in YYYY-MM-DDTHH:MM format.",
    )
    create_parser.add_argument(
        "--calendar",
        help="Calendar name to create the event in. Uses the first available calendar if omitted.",
    )
    create_parser.add_argument("--description", help="Optional event description.")
    create_parser.add_argument("--location", help="Optional event location.")
    create_parser.add_argument(
        "--room",
        help="Meeting room name from rooms.json or direct resource email. If omitted, auto-selects by priority.",
    )
    create_parser.add_argument(
        "--no-room",
        action="store_true",
        help="Do not auto-select a meeting room.",
    )
    create_parser.add_argument(
        "--attendee",
        action="append",
        help="Attendee name from contacts.json or direct email. Can be repeated.",
    )
    create_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the event payload without creating it.",
    )
    create_parser.add_argument(
        "--no-telemost",
        action="store_true",
        help="Do not add X-TELEMOST-REQUIRED:TRUE to the event payload.",
    )

    suggest_parser = subparsers.add_parser(
        "suggest",
        help="Suggest meeting slots using working hours, free-busy, and room availability.",
    )
    suggest_parser.add_argument(
        "--duration",
        type=int,
        required=True,
        help="Meeting duration in minutes.",
    )
    suggest_parser.add_argument(
        "--attendee",
        action="append",
        help="Attendee name from contacts.json or direct email. Can be repeated.",
    )
    suggest_parser.add_argument(
        "--date",
        action="append",
        help="Candidate date in YYYY-MM-DD format. Can be repeated.",
    )
    suggest_parser.add_argument(
        "--from-date",
        help="Start date in YYYY-MM-DD format. Defaults to today.",
    )
    suggest_parser.add_argument(
        "--to-date",
        help="End date in YYYY-MM-DD format. Defaults to two weeks after --from-date.",
    )
    suggest_parser.add_argument(
        "--calendar",
        default="Мои события",
        help="Organizer calendar to check for conflicts. Defaults to 'Мои события'.",
    )
    suggest_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum slots to show.",
    )
    suggest_parser.add_argument(
        "--require-room",
        action="store_true",
        help="Only show slots where a preferred room is available.",
    )

    delete_parser = subparsers.add_parser("delete", help="Delete events by exact title on a specific date.")
    delete_parser.add_argument("--title", required=True, help="Exact event title to delete.")
    delete_parser.add_argument(
        "--date",
        required=True,
        help="Date in YYYY-MM-DD format.",
    )
    delete_parser.add_argument(
        "--calendar",
        help="Calendar name to delete from. Searches all calendars if omitted.",
    )

    update_parser = subparsers.add_parser(
        "update",
        help="Update or reschedule an existing non-recurring event without touching Telemost fields.",
    )
    update_parser.add_argument("--title", help="Current exact event title.")
    update_parser.add_argument("--query", help="Case-insensitive part of the current event title.")
    update_parser.add_argument(
        "--date",
        required=True,
        help="Current event date in YYYY-MM-DD format.",
    )
    update_parser.add_argument(
        "--calendar",
        help="Calendar name to search. Searches all calendars if omitted.",
    )
    update_parser.add_argument(
        "--match-index",
        type=int,
        help="Choose one event when several events have the same title on the date.",
    )
    update_parser.add_argument(
        "--new-start",
        help="New start datetime in YYYY-MM-DDTHH:MM format.",
    )
    update_parser.add_argument(
        "--new-end",
        help="New end datetime in YYYY-MM-DDTHH:MM format.",
    )
    update_parser.add_argument(
        "--duration",
        type=int,
        help="New duration in minutes. If --new-start is used without --new-end, keeps old duration unless this is set.",
    )
    update_parser.add_argument("--new-title", help="New event title.")
    update_parser.add_argument("--description", help="Replace event description.")
    update_parser.add_argument(
        "--clear-description",
        action="store_true",
        help="Remove event description.",
    )
    update_parser.add_argument("--location", help="Replace event location.")
    update_parser.add_argument(
        "--clear-location",
        action="store_true",
        help="Remove event location.",
    )
    update_parser.add_argument(
        "--add-attendee",
        action="append",
        help="Add attendee by name from contacts.json or direct email. Can be repeated.",
    )
    update_parser.add_argument(
        "--remove-attendee",
        action="append",
        help="Remove attendee by name from contacts.json or direct email. Can be repeated.",
    )
    update_parser.add_argument(
        "--room",
        help="Replace the known room attendee/location with this room.",
    )
    update_parser.add_argument(
        "--no-room",
        action="store_true",
        help="Remove known room attendees and location.",
    )
    update_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print updated ICS without saving.",
    )

    contacts_parser = subparsers.add_parser(
        "contacts",
        help="Rank meeting contacts by how often they appear as attendees.",
    )
    contacts_parser.add_argument(
        "--from-date",
        help="Start date in YYYY-MM-DD format. Defaults to 90 days ago.",
    )
    contacts_parser.add_argument(
        "--to-date",
        help="End date in YYYY-MM-DD format. Defaults to today.",
    )
    contacts_parser.add_argument(
        "--calendar",
        help="Calendar name to analyze. Searches all calendars if omitted.",
    )
    contacts_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum contacts to show.",
    )

    rooms_parser = subparsers.add_parser(
        "rooms",
        help="Inspect recent events for locations and likely meeting-room resources.",
    )
    rooms_parser.add_argument(
        "--from-date",
        help="Start date in YYYY-MM-DD format. Defaults to 90 days ago.",
    )
    rooms_parser.add_argument(
        "--to-date",
        help="End date in YYYY-MM-DD format. Defaults to today.",
    )
    rooms_parser.add_argument(
        "--calendar",
        help="Calendar name to analyze. Searches all calendars if omitted.",
    )
    rooms_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum rows to show per section.",
    )

    room_freebusy_parser = subparsers.add_parser(
        "room-freebusy",
        help="Ask CalDAV scheduling free-busy for a room resource.",
    )
    room_freebusy_parser.add_argument("--room", required=True, help="Room name from rooms.json.")
    room_freebusy_parser.add_argument(
        "--start",
        required=True,
        help="Start datetime in YYYY-MM-DDTHH:MM format.",
    )
    room_freebusy_parser.add_argument(
        "--end",
        required=True,
        help="End datetime in YYYY-MM-DDTHH:MM format.",
    )

    inspect_parser = subparsers.add_parser(
        "inspect-telemost",
        help="Inspect existing events for Telemost/conference-related ICS fields.",
    )
    inspect_parser.add_argument(
        "--from-date",
        help="Start date in YYYY-MM-DD format. Defaults to 90 days ago.",
    )
    inspect_parser.add_argument(
        "--to-date",
        help="End date in YYYY-MM-DD format. Defaults to today.",
    )
    inspect_parser.add_argument(
        "--calendar",
        help="Calendar name to inspect. Searches all calendars if omitted.",
    )
    inspect_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum matching events to show.",
    )

    resolve_parser = subparsers.add_parser(
        "resolve-contact",
        help="Resolve a participant name using local contacts first, then Yandex 360 Directory.",
    )
    resolve_parser.add_argument("name", help="Name or email to resolve.")

    return parser


def build_client() -> caldav.DAVClient:
    load_env_files()
    return caldav.DAVClient(
        url=require_env("YANDEX_CALDAV_URL"),
        username=require_env("YANDEX_LOGIN"),
        password=require_env("YANDEX_APP_PASSWORD"),
    )


def organizer_from_env(require_login: bool = True) -> dict[str, str]:
    login = optional_env("YANDEX_LOGIN")
    if require_login and not login:
        login = require_env("YANDEX_LOGIN")
    fallback_email = login or "organizer@example.com"
    return {
        "name": optional_env("YANDEX_ORGANIZER_NAME") or fallback_email.split("@")[0],
        "email": fallback_email,
    }


def get_timezone() -> ZoneInfo:
    tz_name = os.getenv("YANDEX_TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"
    return ZoneInfo(tz_name)


@dataclass
class EventInfo:
    calendar_name: str
    title: str
    start: datetime | None
    end: datetime | None


@dataclass
class ContactInfo:
    name: str
    email: str
    meetings: int = 0
    calendars: set[str] | None = None
    recent_titles: list[str] | None = None


def calendar_display_name(calendar) -> str:
    name = getattr(calendar, "name", None)
    if callable(name):
        value = name()
        if value:
            return str(value)
    if isinstance(name, str) and name:
        return name
    url = getattr(calendar, "url", "")
    return str(url).rstrip("/").split("/")[-1] or "Unnamed calendar"


def iter_calendars(principal, selected_name: str | None = None) -> Iterable:
    calendars = principal.calendars()
    for calendar in calendars:
        display_name = calendar_display_name(calendar)
        if selected_name and display_name != selected_name:
            continue
        yield calendar


def get_calendar(principal, selected_name: str | None):
    calendars = list(iter_calendars(principal, selected_name))
    if selected_name and not calendars:
        raise SystemExit(f"Calendar '{selected_name}' was not found.")
    if not calendars:
        raise SystemExit("No calendars found in the account.")
    return calendars[0]


def normalize_dt(value, tz: ZoneInfo) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value.astimezone(tz)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=tz)
    return None


def event_title(event) -> str:
    try:
        component = event.icalendar_component
        summary = component.get("summary")
        if summary:
            return str(summary)
    except Exception:
        pass
    return "(no title)"


def event_payload_text(event) -> str:
    data = getattr(event, "data", "")
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def inspect_telemost_fields(
    principal,
    start_dt: datetime,
    end_dt: datetime,
    calendar_name: str | None,
    limit: int,
) -> None:
    shown = 0
    needles = ("telemost", "conference", "video", "zoom", "teams", "meet")

    for calendar in iter_calendars(principal, calendar_name):
        display_name = calendar_display_name(calendar)
        try:
            events = calendar.search(
                start=start_dt,
                end=end_dt,
                event=True,
                expand=True,
            )
        except TypeError:
            events = calendar.date_search(start=start_dt, end=end_dt)

        for event in events:
            if shown >= limit:
                return
            try:
                component = event.icalendar_component
                payload = event_payload_text(event)
                if not any(needle in payload.lower() for needle in needles):
                    continue

                dtstart = normalize_dt(component.get("dtstart").dt, get_timezone())
                if dtstart and not (start_dt <= dtstart < end_dt):
                    continue

                safe_print("---")
                safe_print(f"{display_name} | {dtstart:%Y-%m-%d %H:%M} | {event_title(event)}")
                for line in payload.splitlines():
                    lower = line.lower()
                    if any(needle in lower for needle in needles) or lower.startswith(
                        ("url", "location", "description", "x-")
                    ):
                        safe_print(line)
                shown += 1
            except Exception as exc:
                safe_print(f"Inspect error: {exc}")


def attendee_identity(attendee) -> tuple[str, str] | None:
    raw_value = str(attendee)
    email = raw_value.removeprefix("mailto:").strip()
    name = ""

    params = getattr(attendee, "params", None)
    if params:
        cn = params.get("CN")
        if cn:
            name = str(cn).strip()

    if not email and not name:
        return None
    if not name:
        name = email
    return name, email.lower()


def event_attendees(event) -> list[tuple[str, str]]:
    try:
        component = event.icalendar_component
        attendees = component.get("attendee")
        if not attendees:
            return []
        if not isinstance(attendees, list):
            attendees = [attendees]
        return [identity for attendee in attendees if (identity := attendee_identity(attendee))]
    except Exception:
        return []


def collect_events(principal, start_dt: datetime, end_dt: datetime, calendar_name: str | None) -> list[EventInfo]:
    tz = get_timezone()
    found: list[EventInfo] = []
    for calendar in iter_calendars(principal, calendar_name):
        display_name = calendar_display_name(calendar)
        try:
            events = calendar.search(
                start=start_dt,
                end=end_dt,
                event=True,
                expand=True,
            )
        except TypeError:
            events = calendar.date_search(start=start_dt, end=end_dt)

        for event in events:
            try:
                component = event.icalendar_component
                dtstart = normalize_dt(component.get("dtstart").dt, tz)
                dtend = normalize_dt(component.get("dtend").dt if component.get("dtend") else None, tz)
            except Exception:
                dtstart = None
                dtend = None

            found.append(
                EventInfo(
                    calendar_name=display_name,
                    title=event_title(event),
                    start=dtstart,
                    end=dtend,
                )
            )

    found.sort(key=lambda item: item.start or datetime.max.replace(tzinfo=tz))
    return [
        item
        for item in found
        if not item.start or start_dt <= item.start < end_dt
    ]


def find_raw_events_by_title(
    principal,
    start_dt: datetime,
    end_dt: datetime,
    calendar_name: str | None,
    title: str,
) -> list[tuple[str, object]]:
    matches: list[tuple[str, object]] = []
    for calendar in iter_calendars(principal, calendar_name):
        display_name = calendar_display_name(calendar)
        try:
            events = calendar.search(
                start=start_dt,
                end=end_dt,
                event=True,
                expand=True,
            )
        except TypeError:
            events = calendar.date_search(start=start_dt, end=end_dt)

        for event in events:
            if event_title(event) != title:
                continue

            component = event.icalendar_component
            dtstart = normalize_dt(component.get("dtstart").dt, get_timezone())
            if dtstart and not (start_dt <= dtstart < end_dt):
                continue
            matches.append((display_name, event))

    return matches


def find_raw_events_by_text(
    principal,
    start_dt: datetime,
    end_dt: datetime,
    calendar_name: str | None,
    title: str | None = None,
    query: str | None = None,
) -> list[tuple[str, object]]:
    if not title and not query:
        raise SystemExit("Pass --title or --query.")
    matches: list[tuple[str, object]] = []
    normalized_query = normalize_lookup_text(query or "")
    for calendar in iter_calendars(principal, calendar_name):
        display_name = calendar_display_name(calendar)
        try:
            events = calendar.search(
                start=start_dt,
                end=end_dt,
                event=True,
                expand=True,
            )
        except TypeError:
            events = calendar.date_search(start=start_dt, end=end_dt)

        for event in events:
            current_title = event_title(event)
            if title and current_title != title:
                continue
            if query and normalized_query not in normalize_lookup_text(current_title):
                continue

            component = event.icalendar_component
            dtstart = normalize_dt(component.get("dtstart").dt, get_timezone())
            if dtstart and not (start_dt <= dtstart < end_dt):
                continue
            matches.append((display_name, event))

    return matches


def ensure_single_match(matches: list[tuple[str, object]], match_index: int | None = None) -> tuple[str, object]:
    if not matches:
        raise SystemExit("No matching events found.")
    if match_index is not None:
        if match_index < 1 or match_index > len(matches):
            raise SystemExit(f"--match-index must be between 1 and {len(matches)}.")
        return matches[match_index - 1]
    if len(matches) > 1:
        safe_print("Multiple matching events found:")
        for index, (calendar_name, event) in enumerate(matches, start=1):
            component = event.icalendar_component
            start_dt = normalize_dt(component.get("dtstart").dt, get_timezone())
            end_dt = normalize_dt(component.get("dtend").dt if component.get("dtend") else None, get_timezone())
            safe_print(
                f"{index}. {calendar_name} | "
                f"{start_dt:%Y-%m-%d %H:%M} - {end_dt:%H:%M} | {event_title(event)}"
            )
        raise SystemExit("Pass --match-index to choose the event to update.")
    return matches[0]


def event_is_recurring(component) -> bool:
    return any(key in component for key in ("rrule", "rdate", "exdate", "recurrence-id"))


def replace_component_property(component, name: str, value) -> None:
    while name in component:
        component.pop(name)
    component.add(name, value)


def remove_component_property(component, name: str) -> None:
    while name in component:
        component.pop(name)


def remove_attendees_by_email(component, emails: set[str]) -> None:
    attendees = component.get("attendee")
    if not attendees:
        return
    if not isinstance(attendees, list):
        attendees = [attendees]
    component.pop("attendee")
    for attendee in attendees:
        identity = attendee_identity(attendee)
        if identity and identity[1].lower() in emails:
            continue
        component.add("attendee", attendee)


def add_attendees_to_component(component, attendees: list[dict[str, str]]) -> None:
    existing = {email for _, email in event_attendees_from_component(component)}
    for attendee in attendees:
        email = attendee["email"].lower()
        if email in existing:
            continue
        component.add(
            "attendee",
            f"mailto:{email}",
            parameters={
                "CN": attendee["name"],
                "ROLE": "REQ-PARTICIPANT",
                "PARTSTAT": "NEEDS-ACTION",
                "RSVP": "TRUE",
            },
        )


def event_attendees_from_component(component) -> list[tuple[str, str]]:
    attendees = component.get("attendee")
    if not attendees:
        return []
    if not isinstance(attendees, list):
        attendees = [attendees]
    return [identity for attendee in attendees if (identity := attendee_identity(attendee))]


def known_room_emails() -> set[str]:
    return {room["email"].lower() for room in load_rooms().values()}


def remove_known_room_attendees(component) -> None:
    remove_attendees_by_email(component, known_room_emails())


def add_room_to_component(component, room: dict[str, str]) -> None:
    remove_known_room_attendees(component)
    component.add(
        "attendee",
        f"mailto:{room['email'].lower()}",
        parameters={
            "CN": room["name"],
            "CUTYPE": "RESOURCE",
            "ROLE": "REQ-PARTICIPANT",
            "PARTSTAT": "NEEDS-ACTION",
            "RSVP": "TRUE",
        },
    )
    replace_component_property(component, "location", room["name"])


def update_calendar_event(
    principal,
    title: str | None,
    query: str | None,
    target_date: date,
    calendar_name: str | None,
    match_index: int | None,
    new_start: datetime | None,
    new_end: datetime | None,
    duration_minutes: int | None,
    new_title: str | None,
    description: str | None,
    clear_description: bool,
    location: str | None,
    clear_location: bool,
    add_attendee_names: list[str] | None,
    remove_attendee_names: list[str] | None,
    room_name: str | None,
    no_room: bool,
    dry_run: bool,
) -> None:
    tz = get_timezone()
    day_start = datetime.combine(target_date, time.min, tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    matches = find_raw_events_by_text(principal, day_start, day_end, calendar_name, title, query)
    selected_calendar_name, event = ensure_single_match(matches, match_index)
    component = event.icalendar_component

    if event_is_recurring(component):
        raise SystemExit("Recurring events are not supported by update yet.")

    old_start = normalize_dt(component.get("dtstart").dt, tz)
    old_end = normalize_dt(component.get("dtend").dt if component.get("dtend") else None, tz)
    if not old_start or not old_end:
        raise SystemExit("Cannot update event without DTSTART/DTEND.")

    if new_start and new_end and new_end <= new_start:
        raise SystemExit("--new-end must be later than --new-start.")
    if duration_minutes is not None and duration_minutes <= 0:
        raise SystemExit("--duration must be a positive number of minutes.")

    updated_start = new_start or old_start
    if new_end:
        updated_end = new_end
    elif duration_minutes is not None:
        updated_end = updated_start + timedelta(minutes=duration_minutes)
    elif new_start:
        updated_end = updated_start + (old_end - old_start)
    else:
        updated_end = old_end

    if updated_end <= updated_start:
        raise SystemExit("Updated end must be later than updated start.")
    if updated_start < datetime.now(tz):
        raise SystemExit("Refusing to move/update an event into the past.")

    if new_start or new_end or duration_minutes is not None:
        replace_component_property(component, "dtstart", updated_start)
        replace_component_property(component, "dtend", updated_end)
    if new_title:
        replace_component_property(component, "summary", new_title)
    if clear_description:
        remove_component_property(component, "description")
    elif description is not None:
        replace_component_property(component, "description", description)
    if clear_location:
        remove_component_property(component, "location")
    elif location is not None:
        replace_component_property(component, "location", location)

    if add_attendee_names:
        add_attendees_to_component(component, resolve_attendees(add_attendee_names))
    if remove_attendee_names:
        contacts = resolve_attendees(remove_attendee_names)
        remove_attendees_by_email(component, {item["email"].lower() for item in contacts})

    if no_room and room_name:
        raise SystemExit("Use either --room or --no-room, not both.")
    if no_room:
        remove_known_room_attendees(component)
        if not location:
            remove_component_property(component, "location")
    elif room_name:
        room = resolve_room(room_name)
        if room:
            add_room_to_component(component, room)

    replace_component_property(component, "last-modified", datetime.now(UTC))
    replace_component_property(component, "dtstamp", datetime.now(UTC))

    if dry_run:
        safe_print(event.icalendar_instance.to_ical().decode("utf-8", errors="replace"))
        return

    event.save(increase_seqno=True)
    safe_print(
        f"Updated event in {selected_calendar_name}: "
        f"{event_title(event)} ({updated_start:%Y-%m-%d %H:%M} - {updated_end:%H:%M})"
    )


def collect_contacts(
    principal,
    start_dt: datetime,
    end_dt: datetime,
    calendar_name: str | None,
) -> list[ContactInfo]:
    contacts: dict[str, ContactInfo] = {}

    for calendar in iter_calendars(principal, calendar_name):
        display_name = calendar_display_name(calendar)
        try:
            events = calendar.search(
                start=start_dt,
                end=end_dt,
                event=True,
                expand=True,
            )
        except TypeError:
            events = calendar.date_search(start=start_dt, end=end_dt)

        for event in events:
            try:
                component = event.icalendar_component
                dtstart = normalize_dt(component.get("dtstart").dt, get_timezone())
                if dtstart and not (start_dt <= dtstart < end_dt):
                    continue

                attendees = component.get("attendee")
                if not attendees:
                    continue
                if not isinstance(attendees, list):
                    attendees = [attendees]

                title = event_title(event)
                for attendee in attendees:
                    identity = attendee_identity(attendee)
                    if not identity:
                        continue

                    name, email = identity
                    key = email or name.lower()
                    contact = contacts.setdefault(
                        key,
                        ContactInfo(
                            name=name,
                            email=email,
                            calendars=set(),
                            recent_titles=[],
                        ),
                    )
                    contact.meetings += 1
                    contact.calendars.add(display_name)
                    if title not in contact.recent_titles:
                        contact.recent_titles.append(title)
                        contact.recent_titles = contact.recent_titles[:3]
            except Exception:
                continue

    return sorted(
        contacts.values(),
        key=lambda contact: (-contact.meetings, contact.name.lower()),
    )


def collect_room_signals(
    principal,
    start_dt: datetime,
    end_dt: datetime,
    calendar_name: str | None,
) -> tuple[dict[str, int], dict[str, tuple[str, int]]]:
    locations: dict[str, int] = {}
    resources: dict[str, tuple[str, int]] = {}
    words = room_signal_words()

    for calendar in iter_calendars(principal, calendar_name):
        try:
            events = calendar.search(
                start=start_dt,
                end=end_dt,
                event=True,
                expand=True,
            )
        except TypeError:
            events = calendar.date_search(start=start_dt, end=end_dt)

        for event in events:
            try:
                component = event.icalendar_component
                dtstart = normalize_dt(component.get("dtstart").dt, get_timezone())
                if dtstart and not (start_dt <= dtstart < end_dt):
                    continue

                location = component.get("location")
                if location:
                    key = str(location).strip()
                    locations[key] = locations.get(key, 0) + 1

                attendees = component.get("attendee")
                if not attendees:
                    continue
                if not isinstance(attendees, list):
                    attendees = [attendees]

                for attendee in attendees:
                    identity = attendee_identity(attendee)
                    if not identity:
                        continue
                    name, email = identity
                    params = getattr(attendee, "params", None)
                    cutype = str(params.get("CUTYPE", "")) if params else ""
                    haystack = normalize_lookup_text(f"{name} {email} {cutype}")
                    if cutype.upper() == "RESOURCE" or any(word in haystack for word in words):
                        current_name, count = resources.get(email, (name, 0))
                        resources[email] = (current_name or name, count + 1)
            except Exception:
                continue

    return locations, resources


def format_event(event: EventInfo) -> str:
    if event.start and event.end:
        return (
            f"{event.start:%Y-%m-%d %H:%M} - {event.end:%H:%M} | "
            f"{event.calendar_name} | {event.title}"
        )
    if event.start:
        return f"{event.start:%Y-%m-%d %H:%M} | {event.calendar_name} | {event.title}"
    return f"{event.calendar_name} | {event.title}"


def parse_local_datetime(value: str, tz: ZoneInfo) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=tz)


def build_ics(
    title: str,
    start_dt: datetime,
    end_dt: datetime,
    description: str | None,
    location: str | None,
    attendees: list[dict[str, str]] | None = None,
    room: dict[str, str] | None = None,
    organizer: dict[str, str] | None = None,
    telemost_required: bool = False,
) -> str:
    uid = f"{uuid.uuid4()}@codex-yandex-calendar"
    created = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dtstart = start_dt.strftime("%Y%m%dT%H%M%S")
    dtend = end_dt.strftime("%Y%m%dT%H%M%S")
    tzid = start_dt.tzinfo.key if hasattr(start_dt.tzinfo, "key") else "Europe/Moscow"

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Codex//Yandex Calendar Bridge//EN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{created}",
        f"DTSTART;TZID={tzid}:{dtstart}",
        f"DTEND;TZID={tzid}:{dtend}",
        f"SUMMARY:{title}",
    ]

    if organizer:
        lines.append(
            "ORGANIZER;"
            f"CN={organizer['name']}:"
            f"mailto:{organizer['email']}"
        )

    if description:
        lines.append(f"DESCRIPTION:{description.replace(chr(10), r'\\n')}")
    if telemost_required:
        lines.append("X-TELEMOST-REQUIRED:TRUE")
    event_location = location or (room["name"] if room else None)
    if event_location:
        lines.append(f"LOCATION:{event_location}")
    for attendee in attendees or []:
        lines.append(
            "ATTENDEE;"
            f"CN={attendee['name']};"
            "ROLE=REQ-PARTICIPANT;"
            "PARTSTAT=NEEDS-ACTION;"
            "RSVP=TRUE:"
            f"mailto:{attendee['email']}"
        )
    if room:
        lines.append(
            "ATTENDEE;"
            f"CN={room['name']};"
            "CUTYPE=RESOURCE;"
            "ROLE=REQ-PARTICIPANT;"
            "PARTSTAT=NEEDS-ACTION;"
            "RSVP=TRUE:"
            f"mailto:{room['email']}"
        )

    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(lines) + "\r\n"


def dry_run_create_without_caldav(args, tz: ZoneInfo) -> bool:
    if args.command != "create" or not args.dry_run:
        return False
    if args.attendee:
        return False
    if not args.no_room and not args.room:
        return False

    load_env_files()
    start_dt = parse_local_datetime(args.start, tz)
    end_dt = parse_local_datetime(args.end, tz)
    if end_dt <= start_dt:
        raise SystemExit("--end must be later than --start.")

    event_payload = build_ics(
        title=args.title,
        start_dt=start_dt,
        end_dt=end_dt,
        description=args.description,
        location=args.location,
        attendees=[],
        room=resolve_room(args.room),
        organizer=organizer_from_env(require_login=False),
        telemost_required=not args.no_telemost,
    )
    safe_print(event_payload)
    return True


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    tz = get_timezone()

    if dry_run_create_without_caldav(args, tz):
        return

    with build_client() as client:
        principal = client.principal()

        if args.command == "calendars":
            for calendar in iter_calendars(principal):
                safe_print(calendar_display_name(calendar))
            return

        if args.command == "resolve-contact":
            contact = resolve_attendees([args.name])[0]
            safe_print(f"{contact['name']} <{contact['email']}>")
            return

        if args.command == "create":
            start_dt = parse_local_datetime(args.start, tz)
            end_dt = parse_local_datetime(args.end, tz)
            if end_dt <= start_dt:
                raise SystemExit("--end must be later than --start.")
            attendees = resolve_attendees(args.attendee)
            room = resolve_room(args.room)
            if not room and not args.no_room:
                room = select_available_room(principal, start_dt, end_dt)
                if not room:
                    small_room = select_available_small_room_freebusy(principal, start_dt, end_dt)
                    if small_room:
                        raise SystemExit(
                            "Only a small one-person room is available: "
                            f"{small_room['name']} <{small_room['email']}>. "
                            f"Confirm explicitly with --room \"{small_room['name']}\" "
                            "or use --no-room."
                        )
                    raise SystemExit(
                        "No available room found by priority: "
                        + ", ".join(room_priority())
                        + ". Use --no-room to create without a room."
                    )
            calendar = get_calendar(principal, args.calendar)
            organizer = organizer_from_env()
            event_payload = build_ics(
                title=args.title,
                start_dt=start_dt,
                end_dt=end_dt,
                description=args.description,
                location=args.location,
                attendees=attendees,
                room=room,
                organizer=organizer,
                telemost_required=not args.no_telemost,
            )
            if args.dry_run:
                safe_print(event_payload)
                return

            calendar.save_event(event_payload)
            attendee_text = (
                " with "
                + ", ".join(f"{item['name']} <{item['email']}>" for item in attendees)
                if attendees
                else ""
            )
            room_text = f" in {room['name']} <{room['email']}>" if room else ""
            safe_print(
                f"Created event in {calendar_display_name(calendar)}: "
                f"{args.title} ({start_dt:%Y-%m-%d %H:%M} - {end_dt:%H:%M})"
                f"{attendee_text}"
                f"{room_text}"
            )
            saved_matches = find_raw_events_by_title(
                principal,
                datetime.combine(start_dt.date(), time.min, tzinfo=tz),
                datetime.combine(start_dt.date() + timedelta(days=1), time.min, tzinfo=tz),
                calendar_display_name(calendar),
                args.title,
            )
            for _, saved_event in saved_matches[:1]:
                saved_attendees = event_attendees(saved_event)
                if saved_attendees:
                    safe_print(
                        "Saved attendees: "
                        + ", ".join(f"{name} <{email}>" for name, email in saved_attendees)
                    )
                else:
                    safe_print("Saved attendees: none")
            return

        if args.command == "suggest":
            attendees = resolve_attendees(args.attendee)
            from_date, to_date, explicit_dates = parse_candidate_dates(
                args.date,
                args.from_date,
                args.to_date,
                tz,
            )
            suggest_meeting_slots(
                principal=principal,
                attendees=attendees,
                duration_minutes=args.duration,
                from_date=from_date,
                to_date=to_date,
                explicit_dates=explicit_dates,
                calendar_name=args.calendar,
                limit=args.limit,
                require_room=args.require_room,
            )
            return

        if args.command == "update":
            target_date = date.fromisoformat(args.date)
            new_start = parse_local_datetime(args.new_start, tz) if args.new_start else None
            new_end = parse_local_datetime(args.new_end, tz) if args.new_end else None
            update_calendar_event(
                principal=principal,
                title=args.title,
                query=args.query,
                target_date=target_date,
                calendar_name=args.calendar,
                match_index=args.match_index,
                new_start=new_start,
                new_end=new_end,
                duration_minutes=args.duration,
                new_title=args.new_title,
                description=args.description,
                clear_description=args.clear_description,
                location=args.location,
                clear_location=args.clear_location,
                add_attendee_names=args.add_attendee,
                remove_attendee_names=args.remove_attendee,
                room_name=args.room,
                no_room=args.no_room,
                dry_run=args.dry_run,
            )
            return

        if args.command == "delete":
            target_date = date.fromisoformat(args.date)
            start_dt = datetime.combine(target_date, time.min, tzinfo=tz)
            end_dt = start_dt + timedelta(days=1)
            matches = find_raw_events_by_title(
                principal,
                start_dt,
                end_dt,
                args.calendar,
                args.title,
            )

            if not matches:
                safe_print("No matching events found.")
                return

            for calendar_name, event in matches:
                event.delete()
                safe_print(f"Deleted from {calendar_name}: {args.title}")
            return

        if args.command == "contacts":
            today = datetime.now(tz).date()
            from_date = (
                date.fromisoformat(args.from_date)
                if args.from_date
                else today - timedelta(days=90)
            )
            to_date = date.fromisoformat(args.to_date) if args.to_date else today
            start_dt = datetime.combine(from_date, time.min, tzinfo=tz)
            end_dt = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=tz)
            contacts = collect_contacts(principal, start_dt, end_dt, args.calendar)

            if not contacts:
                safe_print("No attendee contacts found.")
                return

            safe_print(f"Period: {from_date:%Y-%m-%d} - {to_date:%Y-%m-%d}")
            for index, contact in enumerate(contacts[: max(args.limit, 1)], start=1):
                calendars = ", ".join(sorted(contact.calendars or []))
                titles = "; ".join(contact.recent_titles or [])
                safe_print(
                    f"{index}. {contact.name} <{contact.email}> | "
                    f"{contact.meetings} meetings | {calendars} | {titles}"
                )
            return

        if args.command == "rooms":
            today = datetime.now(tz).date()
            from_date = (
                date.fromisoformat(args.from_date)
                if args.from_date
                else today - timedelta(days=90)
            )
            to_date = date.fromisoformat(args.to_date) if args.to_date else today
            start_dt = datetime.combine(from_date, time.min, tzinfo=tz)
            end_dt = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=tz)
            locations, resources = collect_room_signals(
                principal,
                start_dt,
                end_dt,
                args.calendar,
            )

            safe_print(f"Period: {from_date:%Y-%m-%d} - {to_date:%Y-%m-%d}")
            safe_print("Locations:")
            for name, count in sorted(locations.items(), key=lambda item: (-item[1], item[0]))[: max(args.limit, 1)]:
                safe_print(f"{count} | {name}")

            safe_print("Likely resources:")
            for email, (name, count) in sorted(resources.items(), key=lambda item: (-item[1][1], item[1][0]))[: max(args.limit, 1)]:
                safe_print(f"{count} | {name} <{email}>")
            return

        if args.command == "room-freebusy":
            start_dt = parse_local_datetime(args.start, tz)
            end_dt = parse_local_datetime(args.end, tz)
            if end_dt <= start_dt:
                raise SystemExit("--end must be later than --start.")
            room = resolve_room(args.room)
            if not room:
                raise SystemExit("Room is required.")
            print_freebusy_response(principal, room, start_dt, end_dt)
            return

        if args.command == "inspect-telemost":
            today = datetime.now(tz).date()
            from_date = (
                date.fromisoformat(args.from_date)
                if args.from_date
                else today - timedelta(days=90)
            )
            to_date = date.fromisoformat(args.to_date) if args.to_date else today
            start_dt = datetime.combine(from_date, time.min, tzinfo=tz)
            end_dt = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=tz)
            inspect_telemost_fields(
                principal,
                start_dt,
                end_dt,
                args.calendar,
                max(args.limit, 1),
            )
            return

        if args.command == "today":
            target_date = datetime.now(tz).date()
            calendar_name = args.calendar
        else:
            target_date = date.fromisoformat(args.date)
            calendar_name = args.calendar

        start_dt = datetime.combine(target_date, time.min, tzinfo=tz)
        end_dt = start_dt + timedelta(days=1)
        events = collect_events(principal, start_dt, end_dt, calendar_name)

        if not events:
            safe_print("No events found.")
            return

        for event in events:
            safe_print(format_event(event))


if __name__ == "__main__":
    main()
