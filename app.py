import json
import math
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

try:
    import plotly.graph_objects as go
except ModuleNotFoundError:
    go = None

try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
except ModuleNotFoundError:
    pywrapcp = None
    routing_enums_pb2 = None


APP_DIR = Path(__file__).resolve().parent
CSV_PATH = APP_DIR / "sample_data" / "inspections.csv"
DB_PATH = Path(
    os.getenv("DATABASE_PATH", str(APP_DIR / "data" / "notification_log.db"))
)
OFFICE = (50.9087459, 3.3691129)
OFFICE_ADDRESS = "Marktstraat 10, 8710 Wielsbeke"
BRAND_YELLOW = "#FFD400"
BRAND_BLACK = "#111111"
BRAND_WHITE = "#FFFFFF"
INSPECTION_SERVICE_MINUTES = 30
INSPECTION_TYPES = [
    "Electrical inspection",
    "EPC inspection",
    "Asbestos inspection",
    "Fire safety inspection",
    "Lifting equipment inspection",
]

REQUIRED_COLUMNS = [
    "inspection_id", "inspection_date", "time_window_start", "time_window_end",
    "customer_name", "customer_phone", "customer_email", "language",
    "site_address", "latitude", "longitude", "inspection_type", "inspector_id",
    "inspector_name", "status", "price_excl_vat", "vat_rate",
    "yuki_customer_reference",
]


def generate_demo_inspections():
    cities = [
        ("Gent", 51.0543, 3.7174, "9000"),
        ("Kortrijk", 50.8195, 3.2577, "8500"),
        ("Waregem", 50.8885, 3.4276, "8790"),
        ("Aalst", 50.9378, 4.0409, "9300"),
        ("Brugge", 51.2093, 3.2247, "8000"),
        ("Roeselare", 50.9465, 3.1227, "8800"),
        ("Antwerpen", 51.2194, 4.4025, "2000"),
        ("Leuven", 50.8798, 4.7005, "3000"),
        ("Brussel", 50.8503, 4.3517, "1000"),
        ("Namur", 50.4674, 4.8718, "5000"),
        ("Liège", 50.6326, 5.5797, "4000"),
    ]
    inspectors = [
        ("INS-01", "Emma De Vos"),
        ("INS-02", "Lucas Martens"),
        ("INS-03", "Noor Vermeulen"),
        ("INS-04", "Milan Jacobs"),
        ("INS-05", "Louise Lambert"),
        ("INS-06", "Arthur Dubois"),
        ("INS-07", "Julie Peeters"),
        ("INS-08", "Louis Claes"),
    ]
    inspection_types = [
        "Electrical inspection",
        "EPC inspection",
        "Asbestos inspection",
        "Fire safety inspection",
        "Lifting equipment inspection",
    ]
    streets = [
        "Kerkstraat", "Stationsstraat", "Industrieweg", "Schoolstraat",
        "Nieuwstraat", "Molenstraat", "Markt", "Havenlaan", "Parklaan",
        "Rue de la Station", "Rue du Commerce",
    ]
    first_names = [
        "Marie", "Sofie", "Pieter", "Jan", "Camille",
        "Luc", "An", "Thomas",
    ]
    last_names = [
        "Peeters", "Maes", "De Smet", "Jacobs", "Willems",
        "Dubois", "Martin", "Leclercq",
    ]
    missing_phones = {3, 17, 29, 44, 67, 75}
    invalid_phones = {8, 22, 36, 51, 64, 78}
    inspector_city_groups = [
        [0, 3],
        [1, 2],
        [4, 5],
        [6],
        [7, 8],
        [9],
        [10],
        [0, 2],
    ]
    rows = []
    for index in range(80):
        inspector_index = index // 10
        route_position = index % 10
        inspector_id, inspector_name = inspectors[inspector_index]
        city_group = inspector_city_groups[inspector_index]
        city, latitude, longitude, postal_code = cities[
            city_group[route_position % len(city_group)]
        ]
        language = "FR" if city in {"Namur", "Liège"} or index % 5 == 0 else "NL"
        customer_name = (
            f"{first_names[index % len(first_names)]} "
            f"{last_names[index % len(last_names)]}"
        )
        if index in missing_phones:
            phone = ""
        elif index in invalid_phones:
            phone = "12345"
        else:
            phone = f"047{index:07d}"
        start_hour = 8 + route_position
        rows.append(
            {
                "inspection_id": f"INSP-20260607-{index + 1:03d}",
                "inspection_date": "2026-06-07",
                "time_window_start": f"{start_hour:02d}:00",
                "time_window_end": f"{start_hour + 2:02d}:30",
                "customer_name": customer_name,
                "customer_phone": phone,
                "customer_email": f"customer{index + 1}@example.be",
                "language": language,
                "site_address": (
                    f"{streets[index % len(streets)]} {5 + index * 2}, "
                    f"{postal_code} {city}"
                ),
                "latitude": round(latitude + ((index % 7) - 3) * 0.006, 6),
                "longitude": round(longitude + ((index % 9) - 4) * 0.006, 6),
                "inspection_type": inspection_types[index % len(inspection_types)],
                "inspector_id": inspector_id,
                "inspector_name": inspector_name,
                "status": "planned",
                "price_excl_vat": f"{145 + (index % 5) * 35:.2f}",
                "vat_rate": "21",
                "yuki_customer_reference": f"YUKI-CUST-{1001 + index:04d}",
            }
        )
    return pd.DataFrame(rows)


def init_db(db_path=DB_PATH):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                inspection_id TEXT NOT NULL,
                customer_name TEXT,
                phone TEXT,
                message_type TEXT NOT NULL,
                message_text TEXT,
                status TEXT NOT NULL,
                error_reason TEXT
            )
            """
        )
        connection.execute(
            """
            DELETE FROM notification_log
            WHERE message_type = 'day_before_reminder'
              AND id NOT IN (
                  SELECT MIN(id)
                  FROM notification_log
                  WHERE message_type = 'day_before_reminder'
                  GROUP BY inspection_id
              )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS unique_initial_reminder
            ON notification_log (inspection_id, message_type)
            WHERE message_type = 'day_before_reminder'
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                inspection_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                inspection_date TEXT NOT NULL,
                time_window_start TEXT NOT NULL,
                time_window_end TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                customer_phone TEXT,
                customer_email TEXT,
                language TEXT NOT NULL,
                site_address TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                inspection_type TEXT NOT NULL,
                required_skill TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL DEFAULT 30,
                priority TEXT NOT NULL DEFAULT 'normal',
                deadline TEXT,
                flexible_date INTEGER NOT NULL DEFAULT 1,
                locked INTEGER NOT NULL DEFAULT 0,
                inspector_id TEXT,
                inspector_name TEXT,
                status TEXT NOT NULL DEFAULT 'unplanned',
                price_excl_vat REAL NOT NULL DEFAULT 0,
                vat_rate REAL NOT NULL DEFAULT 21,
                yuki_customer_reference TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS inspector_profiles (
                inspector_id TEXT PRIMARY KEY,
                inspector_name TEXT NOT NULL,
                skills TEXT NOT NULL,
                workdays TEXT NOT NULL DEFAULT '0,1,2,3,4',
                workday_start TEXT NOT NULL DEFAULT '06:00',
                workday_end TEXT NOT NULL DEFAULT '22:00',
                preferred_region TEXT,
                max_inspections INTEGER NOT NULL DEFAULT 12,
                max_drive_minutes INTEGER NOT NULL DEFAULT 480,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        connection.commit()


def seed_inspector_profiles(inspections, db_path=DB_PATH):
    catalog = (
        inspections[["inspector_id", "inspector_name"]]
        .drop_duplicates()
        .dropna()
    )
    catalog = catalog[
        catalog["inspector_id"].astype(str).str.strip().ne("")
        & catalog["inspector_name"].astype(str).str.strip().ne("")
    ]
    all_skills = ",".join(INSPECTION_TYPES)
    with sqlite3.connect(db_path) as connection:
        for _, row in catalog.iterrows():
            connection.execute(
                """
                INSERT OR IGNORE INTO inspector_profiles (
                    inspector_id, inspector_name, skills
                ) VALUES (?, ?, ?)
                """,
                (str(row["inspector_id"]), str(row["inspector_name"]), all_skills),
            )
        connection.commit()


def read_inspector_profiles(db_path=DB_PATH):
    with sqlite3.connect(db_path) as connection:
        return pd.read_sql_query(
            "SELECT * FROM inspector_profiles ORDER BY inspector_name", connection
        )


def save_inspector_profile(profile, db_path=DB_PATH):
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE inspector_profiles SET
                skills = ?, workdays = ?, workday_start = ?, workday_end = ?,
                preferred_region = ?, max_inspections = ?,
                max_drive_minutes = ?, active = ?
            WHERE inspector_id = ?
            """,
            (
                profile["skills"],
                profile["workdays"],
                profile["workday_start"],
                profile["workday_end"],
                profile["preferred_region"],
                int(profile["max_inspections"]),
                int(profile["max_drive_minutes"]),
                int(profile["active"]),
                profile["inspector_id"],
            ),
        )
        connection.commit()


def read_custom_appointments(db_path=DB_PATH):
    with sqlite3.connect(db_path) as connection:
        appointments = pd.read_sql_query(
            "SELECT * FROM appointments ORDER BY inspection_date, time_window_start",
            connection,
        )
    if appointments.empty:
        return appointments
    appointments["inspection_date"] = pd.to_datetime(
        appointments["inspection_date"], errors="coerce"
    ).dt.date
    return appointments


def save_appointment(appointment, db_path=DB_PATH):
    columns = [
        "inspection_id", "created_at", "inspection_date", "time_window_start",
        "time_window_end", "customer_name", "customer_phone", "customer_email",
        "language", "site_address", "latitude", "longitude", "inspection_type",
        "required_skill", "duration_minutes", "priority", "deadline",
        "flexible_date", "locked", "inspector_id", "inspector_name", "status",
        "price_excl_vat", "vat_rate", "yuki_customer_reference",
    ]
    placeholders = ",".join("?" for _ in columns)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"INSERT INTO appointments ({','.join(columns)}) VALUES ({placeholders})",
            tuple(appointment.get(column) for column in columns),
        )
        connection.commit()


def update_appointment_assignment(
    inspection_id, inspection_date, inspector_id, inspector_name, locked, db_path=DB_PATH
):
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE appointments SET
                inspection_date = ?, inspector_id = ?, inspector_name = ?,
                locked = ?, status = 'planned'
            WHERE inspection_id = ?
            """,
            (
                str(inspection_date),
                inspector_id,
                inspector_name,
                int(locked),
                inspection_id,
            ),
        )
        connection.commit()


def log_event(row, message_type, message_text, status, error_reason="", db_path=DB_PATH):
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO notification_log (
                timestamp, inspection_id, customer_name, phone, message_type,
                message_text, status, error_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                str(row["inspection_id"]),
                str(row["customer_name"]),
                str(row["customer_phone"]),
                message_type,
                message_text,
                status,
                error_reason,
            ),
        )
        connection.commit()


def read_logs(db_path=DB_PATH):
    with sqlite3.connect(db_path) as connection:
        return pd.read_sql_query(
            "SELECT * FROM notification_log ORDER BY id DESC", connection
        )


def prepare_all_reminders_demo(inspections, db_path=DB_PATH):
    prepared = 0
    skipped = 0
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as connection:
        for _, row in inspections.iterrows():
            status, error = notification_outcome(row)
            message = reminder_message(row) if status == "reminder_ready" else ""
            demo_status = "sent_demo" if status == "reminder_ready" else status
            connection.execute(
                """
                INSERT INTO notification_log (
                    timestamp, inspection_id, customer_name, phone, message_type,
                    message_text, status, error_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (inspection_id, message_type)
                WHERE message_type = 'day_before_reminder'
                DO UPDATE SET
                    timestamp = excluded.timestamp,
                    customer_name = excluded.customer_name,
                    phone = excluded.phone,
                    message_text = excluded.message_text,
                    status = excluded.status,
                    error_reason = excluded.error_reason
                """,
                (
                    timestamp,
                    str(row["inspection_id"]),
                    str(row["customer_name"]),
                    str(row["customer_phone"]),
                    "day_before_reminder",
                    message,
                    demo_status,
                    error,
                ),
            )
            if demo_status == "sent_demo":
                prepared += 1
            else:
                skipped += 1
        connection.commit()
    return prepared, skipped


def clean_phone(phone):
    if pd.isna(phone):
        return ""
    return re.sub(r"[\s()./-]", "", str(phone).strip())


def is_valid_belgian_phone(phone):
    value = clean_phone(phone)
    return bool(re.fullmatch(r"\+32\d{8,9}", value) or re.fullmatch(r"04\d{8}", value))


def notification_outcome(row):
    phone = clean_phone(row["customer_phone"])
    if not phone:
        return "missing_phone", "No customer phone number available"
    if not is_valid_belgian_phone(phone):
        return "invalid_phone", "Invalid Belgian phone number"
    return "reminder_ready", ""


@st.cache_data(ttl=86400, show_spinner=False)
def geocode_belgian_address(address):
    query = urllib.parse.urlencode(
        {"q": f"{address}, Belgium", "format": "jsonv2", "limit": 1}
    )
    request = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/search?{query}",
        headers={"User-Agent": "E-Ctrl-Planning-Demo/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            results = json.load(response)
        if not results:
            return None
        return {
            "latitude": float(results[0]["lat"]),
            "longitude": float(results[0]["lon"]),
            "display_name": results[0]["display_name"],
        }
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def enrich_planning_columns(data):
    enriched = data.copy()
    defaults = {
        "required_skill": enriched.get("inspection_type", ""),
        "duration_minutes": INSPECTION_SERVICE_MINUTES,
        "priority": "normal",
        "deadline": "",
        "flexible_date": 0,
        "locked": 0,
    }
    for column, value in defaults.items():
        if column not in enriched.columns:
            enriched[column] = value
    enriched["duration_minutes"] = pd.to_numeric(
        enriched["duration_minutes"], errors="coerce"
    ).fillna(INSPECTION_SERVICE_MINUTES).astype(int)
    enriched["locked"] = pd.to_numeric(
        enriched["locked"], errors="coerce"
    ).fillna(0).astype(int)
    enriched["flexible_date"] = pd.to_numeric(
        enriched["flexible_date"], errors="coerce"
    ).fillna(0).astype(int)
    return enriched


def reminder_message(row):
    values = row.to_dict()
    if str(row["language"]).upper() == "FR":
        return (
            "Bonjour {customer_name}, rappel: notre inspecteur passera demain pour "
            "votre {inspection_type} entre {time_window_start} et {time_window_end} "
            "à {site_address}. Merci de prévoir l'accès et les documents nécessaires."
        ).format(**values)
    return (
        "Beste {customer_name}, herinnering: morgen komt onze keurder langs voor uw "
        "{inspection_type} tussen {time_window_start} en {time_window_end} op "
        "{site_address}. Gelieve toegang en nodige documenten te voorzien."
    ).format(**values)


def departure_message(row):
    values = row.to_dict()
    if str(row["language"]).upper() == "FR":
        return (
            "Notre inspecteur {inspector_name} est en route vers {site_address}. "
            "Arrivée prévue dans le créneau convenu: "
            "{time_window_start}-{time_window_end}."
        ).format(**values)
    return (
        "Onze keurder {inspector_name} is onderweg naar {site_address}. Verwachte "
        "aankomst binnen het afgesproken tijdsvenster: "
        "{time_window_start}-{time_window_end}."
    ).format(**values)


def send_sms_twilio(to, body):
    from twilio.rest import Client

    client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    return client.messages.create(
        to=clean_phone(to), from_=os.environ["TWILIO_FROM_NUMBER"], body=body
    )


def haversine(point_a, point_b):
    lat1, lon1 = map(math.radians, point_a)
    lat2, lon2 = map(math.radians, point_b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(value))


def optimize_route(group):
    remaining = group.copy()
    current = OFFICE
    ordered = []
    total_distance = 0.0
    while not remaining.empty:
        distances = remaining.apply(
            lambda row: haversine(
                current, (float(row["latitude"]), float(row["longitude"]))
            ),
            axis=1,
        )
        next_index = distances.idxmin()
        next_row = remaining.loc[next_index]
        total_distance += float(distances.loc[next_index])
        ordered.append(next_row)
        current = (float(next_row["latitude"]), float(next_row["longitude"]))
        remaining = remaining.drop(next_index)
    total_distance += haversine(current, OFFICE)
    route = pd.DataFrame(ordered).reset_index(drop=True)
    route.insert(0, "stop", range(1, len(route) + 1))
    return route, total_distance


def minimum_cost_assignment(cost_matrix):
    task_count = len(cost_matrix)
    inspector_count = len(cost_matrix[0]) if task_count else 0
    if task_count > inspector_count:
        raise ValueError("Not enough inspectors for this time slot.")

    if inspector_count > 14:
        available = set(range(inspector_count))
        assignment = []
        for row in cost_matrix:
            selected = min(available, key=lambda index: row[index])
            assignment.append(selected)
            available.remove(selected)
        return tuple(assignment)

    @lru_cache(maxsize=None)
    def solve(task_index, used_mask):
        if task_index == task_count:
            return 0.0, ()
        best_cost = float("inf")
        best_assignment = ()
        for inspector_index in range(inspector_count):
            bit = 1 << inspector_index
            if used_mask & bit:
                continue
            remaining_cost, remaining_assignment = solve(
                task_index + 1, used_mask | bit
            )
            candidate_cost = (
                cost_matrix[task_index][inspector_index] + remaining_cost
            )
            if candidate_cost < best_cost:
                best_cost = candidate_cost
                best_assignment = (inspector_index, *remaining_assignment)
        return best_cost, best_assignment

    return solve(0, 0)[1]


def heuristic_assign_and_optimize_routes(data, inspectors):
    if data.empty:
        return pd.DataFrame(), pd.DataFrame()
    if not inspectors:
        raise ValueError("Select at least one available inspector.")

    assignments = data.copy().reset_index(drop=True)
    assignments["_start_minutes"] = assignments["time_window_start"].map(
        lambda value: int(str(value).split(":")[0]) * 60
        + int(str(value).split(":")[1])
    )
    assignments["_end_minutes"] = assignments["time_window_end"].map(
        lambda value: int(str(value).split(":")[0]) * 60
        + int(str(value).split(":")[1])
    )
    if (assignments["_end_minutes"] <= assignments["_start_minutes"]).any():
        raise ValueError("Every inspection must end after its start time.")

    inspector_count = min(len(inspectors), len(assignments))
    inspectors = inspectors[:inspector_count]
    schedules = [[] for _ in range(inspector_count)]
    current_locations = [OFFICE for _ in range(inspector_count)]
    available_from = [0 for _ in range(inspector_count)]

    for start_minutes, slot in assignments.sort_values(
        ["_start_minutes", "_end_minutes"]
    ).groupby("_start_minutes", sort=True):
        slot_indexes = slot.index.tolist()
        eligible = [
            index
            for index in range(inspector_count)
            if available_from[index] <= start_minutes
        ]
        if len(slot_indexes) > len(eligible):
            time_label = f"{start_minutes // 60:02d}:{start_minutes % 60:02d}"
            raise ValueError(
                f"Planning conflict at {time_label}: {len(slot_indexes)} inspections "
                f"but only {len(eligible)} inspectors are available."
            )

        cost_matrix = []
        for inspection_index in slot_indexes:
            row = assignments.loc[inspection_index]
            point = (float(row["latitude"]), float(row["longitude"]))
            cost_matrix.append(
                [
                    haversine(current_locations[inspector_index], point)
                    + len(schedules[inspector_index]) * 0.25
                    for inspector_index in eligible
                ]
            )
        local_assignment = minimum_cost_assignment(cost_matrix)
        best_assignment = tuple(
            eligible[local_index] for local_index in local_assignment
        )

        for inspection_index, inspector_index in zip(
            slot_indexes, best_assignment
        ):
            row = assignments.loc[inspection_index]
            schedules[inspector_index].append(inspection_index)
            current_locations[inspector_index] = (
                float(row["latitude"]),
                float(row["longitude"]),
            )
            available_from[inspector_index] = int(row["_end_minutes"])

    routes = []
    summary = []
    for inspector_index, indexes in enumerate(schedules):
        if not indexes:
            continue
        inspector_id, inspector_name = inspectors[inspector_index]
        group = assignments.iloc[indexes].copy()
        group["inspector_id"] = inspector_id
        group["inspector_name"] = inspector_name
        route = group.sort_values(
            ["_start_minutes", "_end_minutes"]
        ).reset_index(drop=True)
        route.insert(0, "stop", range(1, len(route) + 1))
        distance = 0.0
        current = OFFICE
        for _, row in route.iterrows():
            point = (float(row["latitude"]), float(row["longitude"]))
            distance += haversine(current, point)
            current = point
        distance += haversine(current, OFFICE)
        route["route_distance_km"] = round(distance, 1)
        route = route.drop(columns=["_start_minutes", "_end_minutes"])
        routes.append(route)
        summary.append(
            {
                "inspector_name": inspector_name,
                "stops": len(route),
                "estimated_round_trip_km": round(distance, 1),
            }
        )
    return pd.concat(routes, ignore_index=True), pd.DataFrame(summary)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_road_matrix(coordinates):
    coordinate_string = ";".join(
        f"{longitude:.6f},{latitude:.6f}"
        for latitude, longitude in coordinates
    )
    url = (
        "https://router.project-osrm.org/table/v1/driving/"
        f"{coordinate_string}?annotations=duration,distance"
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "E-Ctrl-Demo/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.load(response)
        if payload.get("code") != "Ok":
            raise ValueError(payload.get("message", "No road matrix found."))
        durations = payload["durations"]
        distances = payload["distances"]
        if any(value is None for row in durations for value in row):
            raise ValueError("Some locations cannot be reached by road.")
        return {
            "duration_minutes": [
                [max(0, round(value / 60)) for value in row]
                for row in durations
            ],
            "distance_meters": [
                [max(0, round(value)) for value in row] for row in distances
            ],
            "uses_roads": True,
            "error": "",
        }
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        duration_matrix = []
        distance_matrix = []
        for origin in coordinates:
            duration_row = []
            distance_row = []
            for destination in coordinates:
                straight_km = haversine(origin, destination)
                estimated_road_km = straight_km * 1.25
                distance_row.append(round(estimated_road_km * 1000))
                duration_row.append(round(estimated_road_km / 55 * 60))
            duration_matrix.append(duration_row)
            distance_matrix.append(distance_row)
        return {
            "duration_minutes": duration_matrix,
            "distance_meters": distance_matrix,
            "uses_roads": False,
            "error": str(exc),
        }


def ortools_assign_and_optimize_routes(data, inspectors):
    assignments = enrich_planning_columns(data).reset_index(drop=True)
    if assignments.empty:
        return pd.DataFrame(), pd.DataFrame()
    if not inspectors:
        raise ValueError("Select at least one available inspector.")

    assignments["_start_minutes"] = assignments["time_window_start"].map(
        lambda value: int(str(value).split(":")[0]) * 60
        + int(str(value).split(":")[1])
    )
    assignments["_end_minutes"] = assignments["time_window_end"].map(
        lambda value: int(str(value).split(":")[0]) * 60
        + int(str(value).split(":")[1])
    )
    latest_arrivals = assignments["_end_minutes"] - assignments["duration_minutes"]
    if (latest_arrivals < assignments["_start_minutes"]).any():
        raise ValueError(
            f"Each time window must allow at least "
            "enough time for the configured inspection duration."
        )

    coordinates = [
        OFFICE,
        *list(
            zip(
                assignments["latitude"].astype(float),
                assignments["longitude"].astype(float),
            )
        ),
    ]
    matrix = fetch_road_matrix(tuple(coordinates))
    duration_matrix = matrix["duration_minutes"]
    distance_matrix = matrix["distance_meters"]
    vehicle_count = min(len(inspectors), len(assignments))
    inspectors = inspectors[:vehicle_count]
    profiles = read_inspector_profiles()
    profile_map = profiles.set_index("inspector_id").to_dict("index")
    manager = pywrapcp.RoutingIndexManager(
        len(coordinates), vehicle_count, 0
    )
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return distance_matrix[from_node][to_node]

    distance_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(distance_callback_index)

    def time_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        service = (
            int(assignments.iloc[from_node - 1]["duration_minutes"])
            if from_node != 0
            else 0
        )
        return duration_matrix[from_node][to_node] + service

    time_callback_index = routing.RegisterTransitCallback(time_callback)
    routing.AddDimension(
        time_callback_index,
        12 * 60,
        24 * 60,
        False,
        "Time",
    )
    time_dimension = routing.GetDimensionOrDie("Time")
    for row_index, row in assignments.iterrows():
        node = row_index + 1
        index = manager.NodeToIndex(node)
        time_dimension.CumulVar(index).SetRange(
            int(row["_start_minutes"]),
            int(row["_end_minutes"] - row["duration_minutes"]),
        )

        allowed_vehicles = []
        required_skill = str(row.get("required_skill") or row["inspection_type"])
        for vehicle_id, (inspector_id, _) in enumerate(inspectors):
            profile = profile_map.get(str(inspector_id), {})
            skills = {
                skill.strip()
                for skill in str(profile.get("skills", "")).split(",")
                if skill.strip()
            }
            if not skills or required_skill in skills:
                allowed_vehicles.append(vehicle_id)
        if int(row.get("locked", 0)) and str(row.get("inspector_id", "")):
            allowed_vehicles = [
                vehicle_id
                for vehicle_id, (inspector_id, _) in enumerate(inspectors)
                if str(inspector_id) == str(row["inspector_id"])
            ]
        if not allowed_vehicles:
            raise ValueError(
                f"No selected inspector is qualified for {required_skill} "
                f"({row['inspection_id']})."
            )
        if len(allowed_vehicles) < vehicle_count:
            routing.VehicleVar(index).SetValues(
                [int(vehicle_id) for vehicle_id in allowed_vehicles]
            )

    day_start = min(5 * 60, int(assignments["_start_minutes"].min()))
    day_end = max(
        20 * 60,
        int(assignments["_end_minutes"].max()) + 2 * 60,
    )
    for vehicle_id in range(vehicle_count):
        inspector_id = str(inspectors[vehicle_id][0])
        profile = profile_map.get(inspector_id, {})
        profile_start = str(profile.get("workday_start", "06:00"))
        profile_end = str(profile.get("workday_end", "22:00"))
        vehicle_start = int(profile_start[:2]) * 60 + int(profile_start[3:])
        vehicle_end = int(profile_end[:2]) * 60 + int(profile_end[3:])
        time_dimension.CumulVar(routing.Start(vehicle_id)).SetRange(
            max(day_start, vehicle_start), min(day_end, vehicle_end)
        )
        time_dimension.CumulVar(routing.End(vehicle_id)).SetRange(
            max(day_start, vehicle_start), min(day_end, vehicle_end)
        )
        routing.AddVariableMinimizedByFinalizer(
            time_dimension.CumulVar(routing.Start(vehicle_id))
        )
        routing.AddVariableMinimizedByFinalizer(
            time_dimension.CumulVar(routing.End(vehicle_id))
        )

    def demand_callback(index):
        return 0 if manager.IndexToNode(index) == 0 else 1

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    capacities = [
        int(profile_map.get(str(inspector_id), {}).get("max_inspections", 12))
        for inspector_id, _ in inspectors
    ]
    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,
        capacities,
        True,
        "InspectionCount",
    )

    routing.SetFixedCostOfAllVehicles(1)
    routing.AddDimension(
        distance_callback_index,
        0,
        2_000_000,
        True,
        "Distance",
    )
    distance_dimension = routing.GetDimensionOrDie("Distance")
    distance_dimension.SetGlobalSpanCostCoefficient(15)

    def drive_time_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return duration_matrix[from_node][to_node]

    drive_time_callback_index = routing.RegisterTransitCallback(
        drive_time_callback
    )
    drive_capacities = [
        (
            int(
                profile_map.get(str(inspector_id), {}).get(
                    "max_drive_minutes", 480
                )
            )
            if matrix["uses_roads"]
            else 24 * 60
        )
        for inspector_id, _ in inspectors
    ]
    routing.AddDimensionWithVehicleCapacity(
        drive_time_callback_index,
        0,
        drive_capacities,
        True,
        "DriveTime",
    )

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_parameters.time_limit.seconds = 8
    solution = routing.SolveWithParameters(search_parameters)
    if solution is None:
        if not matrix["uses_roads"]:
            routes, summary = heuristic_assign_and_optimize_routes(
                assignments.drop(
                    columns=["_start_minutes", "_end_minutes"], errors="ignore"
                ),
                inspectors,
            )
            routes["scheduled_arrival"] = routes["time_window_start"]
            routes["scheduled_end"] = routes.apply(
                lambda row: (
                    datetime.strptime(row["time_window_start"], "%H:%M")
                    + timedelta(minutes=int(row["duration_minutes"]))
                ).strftime("%H:%M"),
                axis=1,
            )
            routes["optimization_mode"] = "Offline heuristic fallback"
            summary["optimization_mode"] = "Offline heuristic fallback"
            return routes, summary
        routing_mode = "road times" if matrix["uses_roads"] else "estimated times"
        raise ValueError(
            "No feasible planning was found with the selected inspectors, "
            f"time windows, 30-minute inspections, and {routing_mode}. "
            "Add inspectors or widen the appointment windows."
        )

    routes = []
    summary = []
    for vehicle_id, (inspector_id, inspector_name) in enumerate(inspectors):
        index = routing.Start(vehicle_id)
        route_rows = []
        route_distance = 0
        route_drive_minutes = 0
        while not routing.IsEnd(index):
            next_index = solution.Value(routing.NextVar(index))
            from_node = manager.IndexToNode(index)
            to_node = manager.IndexToNode(next_index)
            route_distance += distance_matrix[from_node][to_node]
            route_drive_minutes += duration_matrix[from_node][to_node]
            if to_node != 0:
                row = assignments.iloc[to_node - 1].copy()
                arrival = solution.Value(
                    time_dimension.CumulVar(next_index)
                )
                row["inspector_id"] = inspector_id
                row["inspector_name"] = inspector_name
                row["scheduled_arrival"] = (
                    f"{arrival // 60:02d}:{arrival % 60:02d}"
                )
                row["scheduled_end"] = (
                    f"{(arrival + int(row['duration_minutes'])) // 60:02d}:"
                    f"{(arrival + int(row['duration_minutes'])) % 60:02d}"
                )
                route_rows.append(row)
            index = next_index
        if not route_rows:
            continue
        route = pd.DataFrame(route_rows).reset_index(drop=True)
        route.insert(0, "stop", range(1, len(route) + 1))
        route["route_distance_km"] = round(route_distance / 1000, 1)
        route["route_drive_minutes"] = round(route_drive_minutes)
        route["optimization_mode"] = (
            "OR-Tools + OSRM"
            if matrix["uses_roads"]
            else "OR-Tools + estimated road times"
        )
        route = route.drop(
            columns=["_start_minutes", "_end_minutes"], errors="ignore"
        )
        routes.append(route)
        summary.append(
            {
                "inspector_name": inspector_name,
                "stops": len(route),
                "road_distance_km": round(route_distance / 1000, 1),
                "driving_minutes": round(route_drive_minutes),
                "optimization_mode": route["optimization_mode"].iloc[0],
            }
        )
    return pd.concat(routes, ignore_index=True), pd.DataFrame(summary)


def assign_and_optimize_routes(data, inspectors):
    if pywrapcp is None:
        routes, summary = heuristic_assign_and_optimize_routes(data, inspectors)
        routes["scheduled_arrival"] = routes["time_window_start"]
        routes["optimization_mode"] = "Heuristic fallback"
        summary["optimization_mode"] = "Heuristic fallback"
        return routes, summary
    return ortools_assign_and_optimize_routes(data, inspectors)


def optimize_all_routes(data):
    inspectors = list(
        data[["inspector_id", "inspector_name"]]
        .drop_duplicates()
        .sort_values("inspector_id")
        .itertuples(index=False, name=None)
    )
    return assign_and_optimize_routes(data, inspectors)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_road_route(coordinates):
    coordinate_string = ";".join(
        f"{longitude:.6f},{latitude:.6f}"
        for latitude, longitude in coordinates
    )
    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        f"{coordinate_string}?overview=full&geometries=geojson&steps=false"
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "E-Ctrl-Demo/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            payload = json.load(response)
        if payload.get("code") != "Ok" or not payload.get("routes"):
            raise ValueError(payload.get("message", "No road route found."))
        road_route = payload["routes"][0]
        geometry = road_route["geometry"]["coordinates"]
        return {
            "latitudes": [coordinate[1] for coordinate in geometry],
            "longitudes": [coordinate[0] for coordinate in geometry],
            "distance_km": road_route["distance"] / 1000,
            "duration_minutes": road_route["duration"] / 60,
            "uses_roads": True,
            "error": "",
        }
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {
            "latitudes": [coordinate[0] for coordinate in coordinates],
            "longitudes": [coordinate[1] for coordinate in coordinates],
            "distance_km": None,
            "duration_minutes": None,
            "uses_roads": False,
            "error": str(exc),
        }


def route_map_figure(route, inspector_name):
    if go is None:
        return None, {
            "latitudes": [],
            "longitudes": [],
            "distance_km": None,
            "duration_minutes": None,
            "uses_roads": False,
            "error": "Plotly is not installed.",
        }

    ordered_route = route.sort_values("stop")
    waypoints = [
        OFFICE,
        *list(
            zip(
                ordered_route["latitude"].astype(float),
                ordered_route["longitude"].astype(float),
            )
        ),
        OFFICE,
    ]
    road_route = fetch_road_route(tuple(waypoints))
    latitudes = road_route["latitudes"]
    longitudes = road_route["longitudes"]
    stop_latitudes = ordered_route["latitude"].astype(float).tolist()
    stop_longitudes = ordered_route["longitude"].astype(float).tolist()
    stop_numbers = ordered_route["stop"].astype(int).astype(str).tolist()
    stop_details = ordered_route[
        [
            "customer_name", "site_address", "time_window_start",
            "time_window_end", "inspection_type",
        ]
    ].values.tolist()

    latitude_span = max(latitudes) - min(latitudes)
    longitude_span = max(longitudes) - min(longitudes)
    largest_span = max(latitude_span, longitude_span, 0.01)
    zoom = max(6.0, min(12.5, math.log2(360 / largest_span) - 1.3))
    center = {
        "lat": (min(latitudes) + max(latitudes)) / 2,
        "lon": (min(longitudes) + max(longitudes)) / 2,
    }

    figure = go.Figure()
    figure.add_trace(
        go.Scattermapbox(
            lat=latitudes,
            lon=longitudes,
            mode="lines",
            line={"width": 9, "color": BRAND_BLACK},
            hoverinfo="skip",
            showlegend=False,
        )
    )
    figure.add_trace(
        go.Scattermapbox(
            lat=latitudes,
            lon=longitudes,
            mode="lines",
            line={"width": 5, "color": BRAND_YELLOW},
            hoverinfo="skip",
            name="Optimized route",
        )
    )
    figure.add_trace(
        go.Scattermapbox(
            lat=stop_latitudes,
            lon=stop_longitudes,
            mode="markers+text",
            marker={
                "size": 25,
                "color": BRAND_BLACK,
            },
            text=stop_numbers,
            textfont={"color": BRAND_YELLOW, "size": 12},
            textposition="middle center",
            customdata=stop_details,
            hovertemplate=(
                "<b>Stop %{text}: %{customdata[0]}</b><br>"
                "%{customdata[1]}<br>"
                "%{customdata[2]}-%{customdata[3]}<br>"
                "%{customdata[4]}<extra></extra>"
            ),
            name="Inspection stops",
        )
    )
    figure.add_trace(
        go.Scattermapbox(
            lat=[OFFICE[0]],
            lon=[OFFICE[1]],
            mode="markers+text",
            marker={"size": 30, "color": BRAND_YELLOW},
            text=["E-Ctrl"],
            textfont={"color": BRAND_BLACK, "size": 13},
            textposition="middle center",
            hovertemplate=(
                f"<b>E-Ctrl office</b><br>{OFFICE_ADDRESS}<extra></extra>"
            ),
            name="Start / finish",
        )
    )
    figure.update_layout(
        title=f"Optimized route - {inspector_name}",
        height=600,
        margin={"l": 0, "r": 0, "t": 50, "b": 0},
        paper_bgcolor=BRAND_WHITE,
        mapbox={
            "style": "open-street-map",
            "center": center,
            "zoom": zoom,
        },
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 0.01,
            "xanchor": "center",
            "x": 0.5,
            "bgcolor": "rgba(255,255,255,0.88)",
        },
        hoverlabel={"bgcolor": BRAND_BLACK, "font_color": BRAND_WHITE},
    )
    return figure, road_route


def apply_workflow_state(inspections, logs):
    data = inspections.copy()
    data["notification_status"] = data.apply(
        lambda row: notification_outcome(row)[0], axis=1
    )
    data["invoice_status"] = "not_ready"
    if logs.empty:
        return data

    for inspection_id, events in logs.groupby("inspection_id"):
        mask = data["inspection_id"].astype(str) == str(inspection_id)
        types = set(events["message_type"])
        reminder_events = events[events["message_type"] == "day_before_reminder"]
        if not reminder_events.empty:
            data.loc[mask, "notification_status"] = reminder_events.iloc[0]["status"]
        if "inspection_completed" in types:
            data.loc[mask, "status"] = "completed"
            data.loc[mask, "invoice_status"] = "invoice_ready"
        elif "inspector_departure" in types:
            data.loc[mask, "status"] = "departing"
        if "invoice_ready" in types:
            data.loc[mask, "invoice_status"] = "invoice_ready"
        if (
            (events["message_type"] == "invoice_ready")
            & (events["status"] == "invoice_exported")
        ).any():
            data.loc[mask, "invoice_status"] = "invoice_exported"
    return data


def status_badges():
    statuses = [
        "reminder_ready", "sent_demo", "missing_phone", "invalid_phone",
        "completed", "invoice_ready",
    ]
    return "".join(
        f'<span class="status-badge status-{status}">{status}</span>'
        for status in statuses
    )


def style_status_columns(frame):
    colors = {
        "reminder_ready": "background-color: #fff5b8; color: #111111",
        "sent_demo": "background-color: #111111; color: #ffd400",
        "missing_phone": "background-color: #eeeeee; color: #444444",
        "invalid_phone": "background-color: #ffe2e2; color: #8a1111",
        "completed": "background-color: #dff4e5; color: #145a2a",
        "invoice_ready": "background-color: #ffd400; color: #111111",
        "invoice_exported": "background-color: #dff4e5; color: #145a2a",
        "departing": "background-color: #fff0c7; color: #5f4300",
    }

    def color_value(value):
        return colors.get(str(value), "")

    columns = [
        column
        for column in ["status", "notification_status", "invoice_status"]
        if column in frame.columns
    ]
    return frame.style.map(color_value, subset=columns)


def prepare_initial_logs(inspections, logs):
    for _, row in inspections.iterrows():
        status, error = notification_outcome(row)
        message = reminder_message(row) if status == "reminder_ready" else ""
        with sqlite3.connect(DB_PATH) as connection:
            connection.execute(
                """
                INSERT INTO notification_log (
                    timestamp, inspection_id, customer_name, phone, message_type,
                    message_text, status, error_reason
                )
                SELECT ?, ?, ?, ?, ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM notification_log
                    WHERE inspection_id = ? AND message_type = 'day_before_reminder'
                )
                """,
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    str(row["inspection_id"]),
                    str(row["customer_name"]),
                    str(row["customer_phone"]),
                    "day_before_reminder",
                    message,
                    status,
                    error,
                    str(row["inspection_id"]),
                ),
            )
            connection.commit()


def invoice_export(data):
    invoices = data[data["invoice_status"].isin(["invoice_ready", "invoice_exported"])].copy()
    if invoices.empty:
        return pd.DataFrame()
    invoices["price_excl_vat"] = pd.to_numeric(invoices["price_excl_vat"])
    invoices["vat_rate"] = pd.to_numeric(invoices["vat_rate"])
    invoices["vat_amount"] = (
        invoices["price_excl_vat"] * invoices["vat_rate"] / 100
    ).round(2)
    invoices["total_incl_vat"] = (
        invoices["price_excl_vat"] + invoices["vat_amount"]
    ).round(2)
    invoices["invoice_reference"] = invoices["inspection_id"].map(lambda x: f"INV-{x}")
    invoices["description"] = invoices.apply(
        lambda row: (
            f"Inspection service - {row['inspection_type']} - "
            f"{row['site_address']} - {row['inspection_date']}"
        ),
        axis=1,
    )
    return invoices[
        [
            "invoice_reference", "inspection_id", "customer_name",
            "yuki_customer_reference", "inspection_type", "inspection_date",
            "price_excl_vat", "vat_rate", "vat_amount", "total_incl_vat",
            "description",
        ]
    ]


def next_appointment_id(db_path=DB_PATH):
    today = date.today().strftime("%Y%m%d")
    with sqlite3.connect(db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM appointments WHERE inspection_id LIKE ?",
            (f"WEB-{today}-%",),
        ).fetchone()[0]
    return f"WEB-{today}-{count + 1:04d}"


def build_week_proposal(appointments, week_start, profiles):
    week_end = week_start + timedelta(days=6)
    week = appointments[
        appointments["inspection_date"].between(week_start, week_end)
    ].copy()
    if week.empty:
        return week

    active_profiles = profiles[profiles["active"] == 1]
    capacity_by_day = {}
    for day_offset in range(5):
        planning_day = week_start + timedelta(days=day_offset)
        capacity_by_day[planning_day] = sum(
            int(row["max_inspections"])
            for _, row in active_profiles.iterrows()
            if str(planning_day.weekday()) in str(row["workdays"]).split(",")
        )
    assigned_counts = week.groupby("inspection_date").size().to_dict()
    for index, row in week.sort_values(
        ["priority", "deadline"], ascending=[True, True]
    ).iterrows():
        if not int(row.get("flexible_date", 0)) or int(row.get("locked", 0)):
            continue
        deadline = pd.to_datetime(row.get("deadline"), errors="coerce")
        deadline_date = deadline.date() if not pd.isna(deadline) else week_end
        candidates = [
            planning_day
            for planning_day, capacity in capacity_by_day.items()
            if planning_day <= deadline_date
            and assigned_counts.get(planning_day, 0) < capacity
        ]
        if not candidates:
            week.at[index, "planning_conflict"] = "No capacity before deadline"
            continue
        selected_day = min(
            candidates,
            key=lambda planning_day: (
                assigned_counts.get(planning_day, 0)
                / max(capacity_by_day[planning_day], 1),
                planning_day,
            ),
        )
        week.at[index, "inspection_date"] = selected_day
        assigned_counts[selected_day] = assigned_counts.get(selected_day, 0) + 1
    if "planning_conflict" not in week.columns:
        week["planning_conflict"] = ""
    else:
        week["planning_conflict"] = week["planning_conflict"].fillna("")
    return week.sort_values(
        ["inspection_date", "priority", "time_window_start"]
    )


def planning_conflicts(appointments, profiles):
    conflicts = []
    profile_map = profiles.set_index("inspector_id").to_dict("index")
    for _, row in appointments.iterrows():
        required_skill = str(row.get("required_skill") or row["inspection_type"])
        qualified = [
            profile
            for profile in profile_map.values()
            if int(profile["active"])
            and required_skill in str(profile["skills"]).split(",")
        ]
        if not qualified:
            conflicts.append(
                {
                    "inspection_id": row["inspection_id"],
                    "conflict": f"No active inspector qualified for {required_skill}",
                }
            )
        if int(row.get("locked", 0)) and not str(row.get("inspector_id", "")).strip():
            conflicts.append(
                {
                    "inspection_id": row["inspection_id"],
                    "conflict": "Appointment is locked without an inspector",
                }
            )
        deadline = pd.to_datetime(row.get("deadline"), errors="coerce")
        if not pd.isna(deadline) and row["inspection_date"] > deadline.date():
            conflicts.append(
                {
                    "inspection_id": row["inspection_id"],
                    "conflict": "Scheduled after deadline",
                }
            )
    return pd.DataFrame(conflicts)


def render_app():
    st.set_page_config(
        page_title="E-Ctrl Inspection Automation Demo",
        page_icon="E",
        layout="wide",
    )
    st.markdown(
        """
        <style>
        :root {
            --ectrl-yellow: #ffd400;
            --ectrl-black: #111111;
            --ectrl-white: #ffffff;
            --ectrl-soft: #f5f5f2;
        }
        .stApp {background: var(--ectrl-white); color: var(--ectrl-black);}
        .block-container {padding-top: 1.2rem; padding-bottom: 3rem;}
        h1, h2, h3, p, label {color: var(--ectrl-black);}
        [data-testid="stSidebar"] {
            background: var(--ectrl-black);
            border-right: 5px solid var(--ectrl-yellow);
        }
        [data-testid="stSidebar"] * {color: var(--ectrl-white);}
        [data-testid="stSidebar"] [data-testid="stAlert"] * {color: var(--ectrl-black);}
        [data-testid="stMetric"] {
            background: var(--ectrl-white);
            border: 1px solid #dedede;
            border-top: 5px solid var(--ectrl-yellow);
            box-shadow: 0 5px 16px rgba(0,0,0,.06);
            padding: 14px; border-radius: 8px;
        }
        [data-testid="stMetricLabel"], [data-testid="stMetricValue"] {
            color: var(--ectrl-black);
        }
        .stButton > button, .stDownloadButton > button {
            background: var(--ectrl-yellow);
            color: var(--ectrl-black);
            border: 2px solid var(--ectrl-black);
            border-radius: 6px;
            font-weight: 750;
        }
        .stButton > button:hover, .stDownloadButton > button:hover {
            background: var(--ectrl-black);
            color: var(--ectrl-yellow);
            border-color: var(--ectrl-black);
        }
        .ectrl-hero {
            background: var(--ectrl-black);
            color: var(--ectrl-white);
            border-left: 12px solid var(--ectrl-yellow);
            border-radius: 10px;
            padding: 28px 32px;
            margin-bottom: 18px;
        }
        .ectrl-logo {
            display: inline-flex;
            align-items: center;
            font-size: 2rem;
            font-weight: 800;
            letter-spacing: -2px;
            margin-bottom: 14px;
        }
        .ectrl-logo .e {color: var(--ectrl-yellow); margin-right: 3px;}
        .ectrl-logo .ctrl {
            color: var(--ectrl-white);
            border: 3px solid var(--ectrl-white);
            border-radius: 8px;
            padding: 0 7px 2px 5px;
        }
        .ectrl-hero h1 {
            color: var(--ectrl-white);
            font-size: 2.25rem;
            margin: 0 0 8px 0;
            line-height: 1.15;
        }
        .ectrl-hero p {color: #d8d8d8; margin: 0; font-size: 1.05rem;}
        .value-box {
            background: #fff9d6;
            border: 1px solid #efd257;
            border-radius: 8px;
            padding: 18px 22px;
            margin: 4px 0 18px;
        }
        .value-box strong {font-size: 1.05rem;}
        .value-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(130px, 1fr));
            gap: 10px;
            margin-top: 12px;
        }
        .value-item {
            background: var(--ectrl-white);
            border-left: 4px solid var(--ectrl-yellow);
            padding: 10px;
            font-weight: 650;
        }
        .scenario-box {
            background: var(--ectrl-soft);
            border-left: 5px solid var(--ectrl-black);
            padding: 13px 16px;
            border-radius: 5px;
            margin-bottom: 16px;
        }
        .status-badge {
            display: inline-block;
            padding: 5px 9px;
            border-radius: 999px;
            margin: 0 6px 8px 0;
            font-size: .78rem;
            font-weight: 750;
            border: 1px solid #c8c8c8;
        }
        .status-reminder_ready {background:#fff5b8; color:#111;}
        .status-sent_demo {background:#111; color:#ffd400;}
        .status-missing_phone {background:#eee; color:#444;}
        .status-invalid_phone {background:#ffe2e2; color:#8a1111;}
        .status-completed {background:#dff4e5; color:#145a2a;}
        .status-invoice_ready {background:#ffd400; color:#111;}
        @media (max-width: 900px) {
            .value-grid {grid-template-columns: 1fr 1fr;}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    load_dotenv()
    init_db()
    uploaded_plan = st.sidebar.file_uploader(
        "Upload day planning CSV",
        type=["csv"],
        help="Use the same columns as sample_data/inspections.csv.",
    )
    if uploaded_plan is not None:
        all_inspections = pd.read_csv(
            uploaded_plan, dtype={"customer_phone": str}
        ).fillna("")
    elif CSV_PATH.exists():
        all_inspections = pd.read_csv(
            CSV_PATH, dtype={"customer_phone": str}
        ).fillna("")
    else:
        all_inspections = generate_demo_inspections()
        st.sidebar.caption(
            "The bundled sample CSV was not found. Generated demo data is active."
        )
    custom_appointments = read_custom_appointments()
    if not custom_appointments.empty:
        all_inspections = all_inspections[
            ~all_inspections["inspection_id"].astype(str).isin(
                custom_appointments["inspection_id"].astype(str)
            )
        ]
        all_inspections = pd.concat(
            [all_inspections, custom_appointments],
            ignore_index=True,
            sort=False,
        )
    all_inspections = enrich_planning_columns(all_inspections).fillna("")
    missing = set(REQUIRED_COLUMNS) - set(all_inspections.columns)
    if missing:
        st.error(f"Missing CSV columns: {', '.join(sorted(missing))}")
        st.stop()

    all_inspections["inspection_date"] = pd.to_datetime(
        all_inspections["inspection_date"], errors="coerce"
    ).dt.date
    seed_inspector_profiles(all_inspections)
    valid_dates = sorted(all_inspections["inspection_date"].dropna().unique())
    if not valid_dates:
        st.error("The planning contains no valid inspection dates.")
        st.stop()
    selected_date = st.sidebar.date_input(
        "Inspection day",
        value=valid_dates[0],
        min_value=min(valid_dates),
        max_value=max(valid_dates),
    )
    inspections = all_inspections[
        all_inspections["inspection_date"] == selected_date
    ].copy()
    if inspections.empty:
        st.warning(f"No inspections found for {selected_date}.")
        st.stop()

    profiles = read_inspector_profiles()
    inspector_catalog = profiles[profiles["active"] == 1][
        ["inspector_id", "inspector_name"]
    ].sort_values("inspector_id")
    available_today_ids = {
        str(row["inspector_id"])
        for _, row in profiles.iterrows()
        if int(row["active"])
        and str(selected_date.weekday()) in str(row["workdays"]).split(",")
    }
    day_catalog = inspector_catalog[
        inspector_catalog["inspector_id"].astype(str).isin(available_today_ids)
    ]
    outside_standard_workday = day_catalog.empty and not inspector_catalog.empty
    if not day_catalog.empty:
        inspector_catalog = day_catalog
    selected_names = st.sidebar.multiselect(
        "Available inspectors",
        options=inspector_catalog["inspector_name"].tolist(),
        default=inspector_catalog["inspector_name"].tolist(),
    )
    selected_inspectors = list(
        inspector_catalog[
            inspector_catalog["inspector_name"].isin(selected_names)
        ].itertuples(index=False, name=None)
    )
    if not selected_inspectors:
        st.warning("Select at least one available inspector.")
        st.stop()

    logs = read_logs()
    prepare_initial_logs(inspections, logs)
    logs = read_logs()
    data = apply_workflow_state(inspections, logs)
    if "routes_optimized" not in st.session_state:
        st.session_state.routes_optimized = False
    if "optimized_routes" not in st.session_state:
        st.session_state.optimized_routes = None
    if "route_summary" not in st.session_state:
        st.session_state.route_summary = None
    selection_key = (
        str(selected_date),
        tuple(inspector_id for inspector_id, _ in selected_inspectors),
        tuple(inspections["inspection_id"].astype(str)),
    )
    if st.session_state.get("route_selection_key") != selection_key:
        try:
            optimized_routes, route_summary = assign_and_optimize_routes(
                apply_workflow_state(inspections, logs), selected_inspectors
            )
            st.session_state.routes_optimized = True
            st.session_state.optimized_routes = optimized_routes
            st.session_state.route_summary = route_summary
            st.session_state.planning_error = None
        except ValueError as exc:
            st.session_state.routes_optimized = False
            st.session_state.optimized_routes = None
            st.session_state.route_summary = None
            st.session_state.planning_error = str(exc)
        st.session_state.route_selection_key = selection_key

    twilio_keys = [
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"
    ]
    live_sms = all(os.getenv(key) for key in twilio_keys)

    with st.sidebar:
        st.markdown(
            '<div class="ectrl-logo"><span class="e">E-</span>'
            '<span class="ctrl">Ctrl</span></div>',
            unsafe_allow_html=True,
        )
        st.header("Demo workflow")
        if outside_standard_workday:
            st.warning(
                "This planning day falls outside the configured working days. "
                "Active inspectors are shown for scenario planning."
            )
        st.markdown(
            """
            1. Daily planning loaded
            2. Routes optimized for inspectors
            3. Day-before reminders prepared
            4. Inspector departure message sent
            5. Inspection completed
            6. Invoice prepared for Yuki
            """
        )
        st.divider()
        if live_sms:
            st.warning("LIVE SMS MODE - Twilio credentials detected.")
        else:
            st.info("DEMO MODE - no real SMS messages are sent.")
        st.caption("Proof of concept. No login, payments, or Yuki API connection.")

    st.markdown(
        """
        <div class="ectrl-hero">
            <div class="ectrl-logo">
                <span class="e">E-</span><span class="ctrl">Ctrl</span>
            </div>
            <h1>From planning to customer updates and Yuki-ready invoicing.</h1>
            <p>One local demo dashboard for planning, communication,
            inspection progress and invoice preparation.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div class="value-box">
            <strong>What E-Ctrl gains</strong>
            <div class="value-grid">
                <div class="value-item">Fewer customer calls</div>
                <div class="value-item">Automatic reminders</div>
                <div class="value-item">Better routes for inspectors</div>
                <div class="value-item">Faster invoicing</div>
                <div class="value-item">Less manual work in Yuki</div>
            </div>
        </div>
        <div class="scenario-box">
            <strong>Demo scenario</strong><br>
            {selected_date}: {len(inspections)} inspections and
            {len(selected_inspectors)} available inspectors.
            The optimizer assigns the inspections automatically.
        </div>
        """,
        unsafe_allow_html=True,
    )

    action_left, action_right = st.columns(2)
    if action_left.button(
        "Prepare all day-before reminders",
        use_container_width=True,
        type="primary",
    ):
        prepared, skipped = prepare_all_reminders_demo(inspections)
        st.session_state.action_message = (
            f"{prepared} reminders logged as sent_demo; "
            f"{skipped} records skipped because of missing or invalid phone numbers."
        )
        st.rerun()
    if action_right.button("Recalculate routes", use_container_width=True):
        try:
            optimized_routes, route_summary = assign_and_optimize_routes(
                data, selected_inspectors
            )
            st.session_state.optimized_routes = optimized_routes
            st.session_state.route_summary = route_summary
            st.session_state.routes_optimized = True
            st.session_state.route_selection_key = selection_key
            st.session_state.planning_error = None
            st.session_state.action_message = (
                f"{len(data)} inspections assigned across "
                f"{len(selected_inspectors)} inspectors and routes optimized."
            )
        except ValueError as exc:
            st.session_state.routes_optimized = False
            st.session_state.planning_error = str(exc)
        st.rerun()

    if "action_message" in st.session_state:
        st.success(st.session_state.pop("action_message"))
    if st.session_state.get("planning_error"):
        st.warning(
            "Route proposal unavailable: "
            f"{st.session_state.planning_error}"
        )

    st.markdown(status_badges(), unsafe_allow_html=True)

    logs = read_logs()
    data = apply_workflow_state(inspections, logs)
    planning_data = data.copy()
    if st.session_state.routes_optimized:
        route_order = st.session_state.optimized_routes[
            [
                "inspection_id", "inspector_id", "inspector_name", "stop",
                "scheduled_arrival", "optimization_mode",
            ]
        ].rename(columns={"stop": "route_stop"})
        planning_data = planning_data.drop(
            columns=["inspector_id", "inspector_name"]
        ).merge(route_order, on="inspection_id", how="left")
        planning_data = planning_data.sort_values(["inspector_name", "route_stop"])
        data = planning_data.drop(columns=["route_stop"]).copy()

    problem_phones = data["notification_status"].isin(["missing_phone", "invalid_phone"])
    metrics = [
        ("Total inspections", len(data)),
        ("Inspectors", len(selected_inspectors)),
        (
            "Reminders ready",
            data["notification_status"].isin(["reminder_ready", "sent_demo"]).sum(),
        ),
        ("Missing/invalid phones", problem_phones.sum()),
        ("Completed", (data["status"] == "completed").sum()),
        (
            "Invoices ready for Yuki",
            data["invoice_status"].isin(["invoice_ready", "invoice_exported"]).sum(),
        ),
    ]
    columns = st.columns(6)
    for column, (label, value) in zip(columns, metrics):
        column.metric(label, int(value))

    (
        new_appointment_tab,
        week_tab,
        planning_tab,
        routes_tab,
        team_tab,
        actions_tab,
        invoices_tab,
        log_tab,
    ) = st.tabs(
        [
            "New appointment",
            "Week planner",
            "Daily planning",
            "Route optimization",
            "Inspectors",
            "Inspector actions",
            "Yuki export",
            "Audit log",
        ]
    )

    with new_appointment_tab:
        st.subheader("Book a new inspection")
        st.caption(
            "This form can be used by office staff or embedded as the basis "
            "for a customer booking page."
        )
        with st.form("new_appointment_form", clear_on_submit=True):
            customer_col, contact_col = st.columns(2)
            customer_name = customer_col.text_input("Customer name *")
            customer_email = customer_col.text_input("Email")
            customer_phone = contact_col.text_input("Phone")
            language = contact_col.selectbox("Language", ["NL", "FR"])

            address = st.text_input(
                "Inspection address *",
                placeholder="Street, number, postal code, city",
            )
            type_col, duration_col, priority_col = st.columns(3)
            inspection_type = type_col.selectbox(
                "Inspection type", INSPECTION_TYPES
            )
            duration_minutes = duration_col.selectbox(
                "Expected duration", [30, 45, 60, 90, 120], index=0
            )
            priority = priority_col.selectbox(
                "Priority", ["normal", "high", "urgent"]
            )

            date_col, start_col, end_col = st.columns(3)
            requested_date = date_col.date_input(
                "Preferred date", value=date.today() + timedelta(days=7)
            )
            window_start = start_col.time_input(
                "Available from", value=datetime.strptime("08:00", "%H:%M").time()
            )
            window_end = end_col.time_input(
                "Available until", value=datetime.strptime("12:00", "%H:%M").time()
            )
            deadline = st.date_input(
                "Deadline", value=requested_date + timedelta(days=14)
            )
            flexible_date = st.checkbox(
                "Date may be moved within the planning week", value=True
            )
            price = st.number_input(
                "Price excl. VAT", min_value=0.0, value=195.0, step=5.0
            )
            submitted = st.form_submit_button(
                "Create appointment", use_container_width=True
            )

        if submitted:
            if not customer_name.strip() or not address.strip():
                st.error("Customer name and inspection address are required.")
            elif window_end <= window_start:
                st.error("The end of the availability window must be later.")
            else:
                with st.spinner("Looking up the Belgian address..."):
                    location = geocode_belgian_address(address)
                if location is None:
                    st.error(
                        "Address could not be found. Add the postal code and city "
                        "and try again."
                    )
                else:
                    inspection_id = next_appointment_id()
                    save_appointment(
                        {
                            "inspection_id": inspection_id,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "inspection_date": requested_date.isoformat(),
                            "time_window_start": window_start.strftime("%H:%M"),
                            "time_window_end": window_end.strftime("%H:%M"),
                            "customer_name": customer_name.strip(),
                            "customer_phone": customer_phone.strip(),
                            "customer_email": customer_email.strip(),
                            "language": language,
                            "site_address": location["display_name"],
                            "latitude": location["latitude"],
                            "longitude": location["longitude"],
                            "inspection_type": inspection_type,
                            "required_skill": inspection_type,
                            "duration_minutes": duration_minutes,
                            "priority": priority,
                            "deadline": deadline.isoformat(),
                            "flexible_date": int(flexible_date),
                            "locked": 0,
                            "inspector_id": "",
                            "inspector_name": "",
                            "status": "unplanned",
                            "price_excl_vat": price,
                            "vat_rate": 21,
                            "yuki_customer_reference": f"WEB-{inspection_id}",
                        }
                    )
                    st.success(
                        f"Appointment {inspection_id} created for "
                        f"{location['display_name']}."
                    )
                    st.rerun()

    with week_tab:
        st.subheader("Weekly capacity and planning proposal")
        default_monday = selected_date - timedelta(days=selected_date.weekday())
        week_start = st.date_input(
            "Week starting", value=default_monday, key="week_start"
        )
        week_end = week_start + timedelta(days=6)
        week_data = all_inspections[
            all_inspections["inspection_date"].between(week_start, week_end)
        ].copy()
        week_profiles = read_inspector_profiles()
        proposal = build_week_proposal(
            all_inspections, week_start, week_profiles
        )

        week_metrics = st.columns(5)
        week_metrics[0].metric("Appointments", len(week_data))
        week_metrics[1].metric(
            "Unplanned",
            int((week_data["status"].astype(str) == "unplanned").sum()),
        )
        week_metrics[2].metric(
            "Urgent",
            int((week_data["priority"].astype(str) == "urgent").sum()),
        )
        week_metrics[3].metric(
            "Locked",
            int(pd.to_numeric(week_data["locked"], errors="coerce").fillna(0).sum()),
        )
        weekly_capacity = sum(
            int(row["max_inspections"])
            * len(
                set(str(row["workdays"]).split(","))
                & {str(day) for day in range(5)}
            )
            for _, row in week_profiles[week_profiles["active"] == 1].iterrows()
        )
        week_metrics[4].metric("Weekly capacity", weekly_capacity)
        utilization = (
            len(week_data) / weekly_capacity * 100 if weekly_capacity else 0
        )
        st.progress(min(utilization / 100, 1.0))
        st.caption(f"Planned capacity utilization: {utilization:.1f}%")

        scenario_one, scenario_two = st.columns(2)
        extra_inspectors = scenario_one.number_input(
            "What if extra inspectors are available?",
            min_value=0,
            max_value=10,
            value=0,
        )
        average_daily_capacity = (
            round(
                week_profiles.loc[
                    week_profiles["active"] == 1, "max_inspections"
                ].mean()
            )
            if not week_profiles[week_profiles["active"] == 1].empty
            else 10
        )
        simulated_capacity = weekly_capacity + (
            extra_inspectors * average_daily_capacity * 5
        )
        scenario_two.metric(
            "Simulated weekly capacity",
            simulated_capacity,
            delta=simulated_capacity - weekly_capacity,
        )

        conflicts = planning_conflicts(week_data, week_profiles)
        if not conflicts.empty:
            st.warning(f"{len(conflicts)} planning conflicts need attention.")
            st.dataframe(conflicts, use_container_width=True, hide_index=True)

        st.markdown("**Automatic week proposal**")
        proposal_columns = [
            "inspection_date", "inspection_id", "priority", "customer_name",
            "site_address", "inspection_type", "duration_minutes", "status",
            "locked", "planning_conflict",
        ]
        st.dataframe(
            proposal[proposal_columns] if not proposal.empty else proposal,
            use_container_width=True,
            hide_index=True,
        )
        st.download_button(
            "Download week proposal CSV",
            data=proposal.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"ectrl_week_proposal_{week_start}.csv",
            mime="text/csv",
            use_container_width=True,
            disabled=proposal.empty,
        )
        if st.button(
            "Apply proposal to online appointments",
            use_container_width=True,
            disabled=proposal.empty,
        ):
            custom_ids = set(
                read_custom_appointments()["inspection_id"].astype(str)
            )
            for _, proposed in proposal.iterrows():
                if str(proposed["inspection_id"]) not in custom_ids:
                    continue
                update_appointment_assignment(
                    proposed["inspection_id"],
                    proposed["inspection_date"],
                    str(proposed.get("inspector_id", "")),
                    str(proposed.get("inspector_name", "")),
                    int(proposed.get("locked", 0)),
                )
            st.success("The week proposal was applied to online appointments.")
            st.rerun()

    with planning_tab:
        st.subheader("Planning dashboard")
        filter_value = st.selectbox(
            "Filter",
            [
                "all", "reminder_ready", "sent_demo", "missing_phone", "invalid_phone",
                "departing", "completed", "invoice_ready", "invoice_exported",
            ],
        )
        filtered = planning_data
        if filter_value in {
            "reminder_ready", "sent_demo", "missing_phone", "invalid_phone"
        }:
            filtered = planning_data[
                planning_data["notification_status"] == filter_value
            ]
        elif filter_value in {"departing", "completed"}:
            filtered = planning_data[planning_data["status"] == filter_value]
        elif filter_value in {"invoice_ready", "invoice_exported"}:
            filtered = planning_data[
                planning_data["invoice_status"] == filter_value
            ]
        shown_columns = [
            "inspection_id", "inspector_name", "customer_name", "site_address",
            "time_window_start", "time_window_end", "inspection_type",
            "customer_phone", "status", "notification_status", "invoice_status",
        ]
        if "route_stop" in filtered.columns:
            shown_columns.insert(2, "route_stop")
            shown_columns.insert(3, "scheduled_arrival")
        st.dataframe(
            style_status_columns(filtered[shown_columns]),
            use_container_width=True,
            hide_index=True,
        )
        st.download_button(
            "Download daily planning CSV",
            data=planning_data.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"ectrl_daily_planning_{selected_date}.csv",
            mime="text/csv",
            use_container_width=True,
        )
        custom_day = filtered[
            filtered["inspection_id"].astype(str).str.startswith("WEB-")
        ]
        if not custom_day.empty:
            st.markdown("**Manual control for online appointments**")
            managed_id = st.selectbox(
                "Appointment",
                custom_day["inspection_id"].tolist(),
                key="managed_appointment",
            )
            managed_row = custom_day[
                custom_day["inspection_id"] == managed_id
            ].iloc[0]
            manual_col, lock_col, save_col = st.columns([2, 1, 1])
            manual_name = manual_col.selectbox(
                "Assign inspector",
                ["Unassigned", *profiles["inspector_name"].tolist()],
                index=(
                    profiles["inspector_name"].tolist().index(
                        managed_row["inspector_name"]
                    )
                    + 1
                    if managed_row["inspector_name"]
                    in profiles["inspector_name"].tolist()
                    else 0
                ),
            )
            manual_lock = lock_col.checkbox(
                "Lock assignment", value=bool(int(managed_row["locked"]))
            )
            if save_col.button("Save assignment", use_container_width=True):
                if manual_name == "Unassigned":
                    inspector_id, inspector_name = "", ""
                else:
                    selected_profile = profiles[
                        profiles["inspector_name"] == manual_name
                    ].iloc[0]
                    inspector_id = selected_profile["inspector_id"]
                    inspector_name = selected_profile["inspector_name"]
                update_appointment_assignment(
                    managed_id,
                    managed_row["inspection_date"],
                    inspector_id,
                    inspector_name,
                    manual_lock,
                )
                st.success("Appointment assignment updated.")
                st.rerun()

    with routes_tab:
        st.subheader("Automatic assignment and route optimization")
        st.caption(
            "The optimizer first distributes the selected day's inspections "
            "geographically across the available inspectors. It then calculates "
            "a nearest-neighbor route from the Ghent office and back."
        )
        if st.session_state.routes_optimized:
            optimized_names = sorted(
                st.session_state.optimized_routes["inspector_name"].unique()
            )
            inspector_name = st.selectbox(
                "Inspector", optimized_names, key="route_inspector"
            )
            route = st.session_state.optimized_routes[
                st.session_state.optimized_routes["inspector_name"] == inspector_name
            ].copy()
            distance = float(route["route_distance_km"].iloc[0])
            st.success(
                f"Automatic assignment active: {len(route)} inspections assigned "
                f"to {inspector_name}."
            )
            mode = route["optimization_mode"].iloc[0]
            st.caption(
                f"Optimization: {mode}. Customer time windows and "
                f"{INSPECTION_SERVICE_MINUTES}-minute inspection duration are enforced."
            )
        else:
            st.info(
                "Choose the inspection day and available inspectors in the sidebar, "
                "then click Optimize routes. The app will assign every inspection "
                "and draw the routes."
            )
        if st.session_state.routes_optimized:
            map_figure, road_info = route_map_figure(route, inspector_name)
            st.dataframe(
                route[
                    [
                        "stop", "scheduled_arrival", "inspection_id",
                        "customer_name", "site_address", "time_window_start",
                        "time_window_end",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )
            metric_one, metric_two, metric_three = st.columns(3)
            if road_info["uses_roads"]:
                metric_one.metric(
                    "Road distance", f"{road_info['distance_km']:.1f} km"
                )
                metric_two.metric(
                    "Estimated driving time",
                    f"{round(road_info['duration_minutes'])} min",
                )
                metric_three.metric("Routing mode", "Belgian roads")
                st.caption(
                    f"Start and finish: E-Ctrl, {OFFICE_ADDRESS}. "
                    "Road geometry and estimates supplied by OSRM/OpenStreetMap."
                )
            else:
                metric_one.metric("Fallback distance", f"{distance:.1f} km")
                metric_two.metric("Estimated driving time", "Unavailable")
                metric_three.metric("Routing mode", "Straight line")
                st.warning(
                    "The road-routing service is currently unavailable. "
                    "The map temporarily shows straight lines."
                )
            st.plotly_chart(
                map_figure, use_container_width=True
            ) if map_figure is not None else st.map(
                pd.concat(
                    [
                        pd.DataFrame(
                            {"latitude": [OFFICE[0]], "longitude": [OFFICE[1]]}
                        ),
                        route[["latitude", "longitude"]].astype(float),
                    ],
                    ignore_index=True,
                ),
                latitude="latitude",
                longitude="longitude",
            )
            st.markdown("**All optimized routes**")
            st.dataframe(
                st.session_state.route_summary,
                use_container_width=True,
                hide_index=True,
            )

    with team_tab:
        st.subheader("Inspector availability and qualifications")
        team_profiles = read_inspector_profiles()
        selected_profile_name = st.selectbox(
            "Inspector",
            team_profiles["inspector_name"].tolist(),
            key="profile_inspector",
        )
        profile = team_profiles[
            team_profiles["inspector_name"] == selected_profile_name
        ].iloc[0]
        weekday_labels = {
            "Monday": "0",
            "Tuesday": "1",
            "Wednesday": "2",
            "Thursday": "3",
            "Friday": "4",
            "Saturday": "5",
        }
        current_workdays = {
            value
            for value in str(profile["workdays"]).split(",")
            if value
        }
        current_skills = {
            skill for skill in str(profile["skills"]).split(",") if skill
        }
        with st.form("inspector_profile_form"):
            skills = st.multiselect(
                "Qualifications",
                INSPECTION_TYPES,
                default=[
                    skill for skill in INSPECTION_TYPES if skill in current_skills
                ],
            )
            workday_names = st.multiselect(
                "Working days",
                list(weekday_labels),
                default=[
                    name
                    for name, value in weekday_labels.items()
                    if value in current_workdays
                ],
            )
            hour_one, hour_two = st.columns(2)
            workday_start = hour_one.time_input(
                "Workday starts",
                value=datetime.strptime(
                    str(profile["workday_start"]), "%H:%M"
                ).time(),
            )
            workday_end = hour_two.time_input(
                "Workday ends",
                value=datetime.strptime(
                    str(profile["workday_end"]), "%H:%M"
                ).time(),
            )
            preferred_region = st.text_input(
                "Preferred region/postcodes",
                value=str(profile["preferred_region"] or ""),
            )
            limit_one, limit_two = st.columns(2)
            max_inspections = limit_one.number_input(
                "Maximum inspections per day",
                min_value=1,
                max_value=20,
                value=int(profile["max_inspections"]),
            )
            max_drive_minutes = limit_two.number_input(
                "Maximum driving minutes per day",
                min_value=30,
                max_value=600,
                value=int(profile["max_drive_minutes"]),
                step=15,
            )
            active = st.checkbox("Active inspector", value=bool(profile["active"]))
            profile_saved = st.form_submit_button(
                "Save inspector profile", use_container_width=True
            )
        if profile_saved:
            if not skills or not workday_names or workday_end <= workday_start:
                st.error(
                    "Select at least one qualification and workday, with valid hours."
                )
            else:
                save_inspector_profile(
                    {
                        "inspector_id": profile["inspector_id"],
                        "skills": ",".join(skills),
                        "workdays": ",".join(
                            weekday_labels[name] for name in workday_names
                        ),
                        "workday_start": workday_start.strftime("%H:%M"),
                        "workday_end": workday_end.strftime("%H:%M"),
                        "preferred_region": preferred_region,
                        "max_inspections": max_inspections,
                        "max_drive_minutes": max_drive_minutes,
                        "active": active,
                    }
                )
                st.success("Inspector profile saved.")
                st.rerun()

        st.dataframe(
            team_profiles[
                [
                    "inspector_name", "skills", "workdays", "workday_start",
                    "workday_end", "preferred_region", "max_inspections",
                    "max_drive_minutes", "active",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    with actions_tab:
        st.subheader("Inspector workflow")
        inspection_id = st.selectbox(
            "Choose inspection",
            data["inspection_id"],
            format_func=lambda value: (
                f"{value} · "
                f"{data.loc[data['inspection_id'] == value, 'customer_name'].iloc[0]} · "
                f"{data.loc[data['inspection_id'] == value, 'status'].iloc[0]}"
            ),
        )
        row = data.loc[data["inspection_id"] == inspection_id].iloc[0]
        st.markdown(
            f"**{row['customer_name']}**  \n{row['site_address']}  \n"
            f"{row['time_window_start']}-{row['time_window_end']} · "
            f"{row['inspection_type']} · {row['inspector_name']}"
        )
        departure_col, complete_col = st.columns(2)
        if departure_col.button(
            "Ik vertrek / Inspector departing",
            use_container_width=True,
            disabled=row["status"] == "completed",
        ):
            message = departure_message(row)
            outcome, error = notification_outcome(row)
            event_status = "logged_demo"
            if live_sms and outcome == "reminder_ready":
                try:
                    send_sms_twilio(row["customer_phone"], message)
                    event_status = "sent_live"
                except Exception as exc:
                    event_status, error = "send_failed", str(exc)
            elif outcome != "reminder_ready":
                event_status = outcome
            log_event(row, "inspector_departure", message, event_status, error)
            if event_status == "logged_demo":
                st.success("Demo departure message logged")
            elif event_status == "sent_live":
                st.success("Departure SMS sent and logged")
            else:
                st.error(f"Departure recorded, but SMS unavailable: {error}")
            st.rerun()

        if complete_col.button(
            "Markeer keuring als uitgevoerd",
            use_container_width=True,
            disabled=row["status"] == "completed",
        ):
            log_event(row, "inspection_completed", "", "completed")
            log_event(
                row,
                "invoice_ready",
                f"Invoice prepared for {row['yuki_customer_reference']}",
                "invoice_ready",
            )
            st.success("Inspection completed and invoice prepared for Yuki")
            st.rerun()

        st.divider()
        st.markdown("**Prepared reminder**")
        outcome, error = notification_outcome(row)
        if outcome == "reminder_ready":
            st.code(reminder_message(row), language=None)
        else:
            st.warning(error)
        st.markdown("**Prepared departure message**")
        st.code(departure_message(row), language=None)

    with invoices_tab:
        st.subheader("Yuki invoice export demo")
        export = invoice_export(data)
        if export.empty:
            st.info("Complete an inspection to prepare its invoice export.")
        else:
            st.dataframe(export, use_container_width=True, hide_index=True)
            clicked = st.download_button(
                "Download Yuki invoice export CSV",
                data=export.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"yuki_invoice_export_{date.today().isoformat()}.csv",
                mime="text/csv",
                use_container_width=True,
            )
            if clicked:
                ready_ids = data.loc[
                    data["invoice_status"] == "invoice_ready", "inspection_id"
                ]
                for ready_id in ready_ids:
                    ready_row = data.loc[data["inspection_id"] == ready_id].iloc[0]
                    log_event(
                        ready_row, "invoice_ready", "Included in Yuki CSV export",
                        "invoice_exported",
                    )

    with log_tab:
        st.subheader("SQLite audit log")
        st.caption(f"Stored locally in {DB_PATH.relative_to(APP_DIR)}")
        current_logs = read_logs()
        st.download_button(
            "Download notification log CSV",
            data=current_logs.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"ectrl_notification_log_{date.today().isoformat()}.csv",
            mime="text/csv",
            use_container_width=True,
        )
        st.dataframe(current_logs, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    render_app()
