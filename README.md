# E-Ctrl Inspection Automation Demo

A local Streamlit proof of concept styled for E-Ctrl, using its yellow, black,
and white visual identity.

## What the demo shows

- Lets office staff or customers create a new appointment directly in the app.
- Geocodes Belgian addresses and adds online bookings to the planning queue.
- Provides a weekly capacity view, planning proposal, conflict list, and
  extra-inspector capacity simulation.
- Stores inspector qualifications, workdays, working hours, daily limits,
  preferred regions, and driving-time limits.
- Supports manually assigning and locking online appointments.
- Loads one day of 80 fake inspections assigned to 8 inspectors.
- Accepts an uploaded daily planning CSV with the same columns as the sample.
- Lets the user choose the inspection date and available inspectors.
- Prepares Dutch and French day-before customer reminders.
- Flags missing and invalid Belgian phone numbers.
- Automatically reassigns that day's inspections across the available
  inspectors with a Vehicle Routing Problem with Time Windows model.
- Builds a nearest-neighbor route per inspector from a fake Ghent office.
- Draws the full office-to-stops-to-office route over real Belgian roads with
  numbered stops. The demo office is Marktstraat 10, 8710 Wielsbeke.
- Applies the new inspector assignment and stop order to the planning.
- Prepares all valid day-before reminders in demo mode with one button.
- Logs inspector departure and inspection completion actions.
- Prepares completed inspections as a Yuki-compatible invoice CSV.
- Exports the complete notification audit log as CSV.
- Exports weekly proposals and daily planning as CSV.
- Keeps an audit history in `data/notification_log.db`.

The business flow is:

> Planning system or Excel exports daily inspections -> app prepares reminders
> -> inspector departure triggers customer message -> completed inspection
> creates invoice export for Yuki.

## Run locally

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\activate
```

Mac/Linux:

```bash
source .venv/bin/activate
```

Install and start:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Put the app online

### Fastest route: Streamlit Community Cloud

Use this for a shareable proof of concept:

1. Create a GitHub repository and push this project.
2. Sign in at <https://share.streamlit.io>.
3. Choose **Create app** and select the repository.
4. Set the entrypoint to `app.py`.
5. Choose an available URL such as `ectrl-demo.streamlit.app`.
6. Add Twilio credentials only through the Streamlit secrets settings, never
   commit them to GitHub.

Push the complete project, not only `app.py`. In particular, Streamlit Cloud
must receive `requirements.txt` in the repository root so it installs Plotly
and the other Python dependencies.

If the cloud app reports `ModuleNotFoundError: plotly`:

1. Confirm that GitHub contains `requirements.txt` beside `app.py`.
2. Confirm that the file contains `plotly>=5.0,<7`.
3. In Streamlit Cloud, open **Manage app**, choose **Reboot app**, and inspect
   the build log for the dependency installation.

Community Cloud rebuilds the app when the GitHub repository changes. Its local
filesystem is not suitable for permanent business records. The SQLite audit
log may be reset after a restart or redeployment.

### Production route: Docker on Azure

The included `Dockerfile` packages the app for a cloud container:

```bash
docker build -t ectrl-inspection-app .
docker run -p 8501:8501 ectrl-inspection-app
```

For a real E-Ctrl deployment:

- Run the image on Azure Container Apps, Azure App Service, or an equivalent
  managed container platform.
- Configure HTTPS and a custom domain such as `planning.e-ctrl.be`.
- Set `DATABASE_PATH` to a mounted persistent volume if SQLite is retained.
- Prefer PostgreSQL for multi-user use, reliable backups, and reporting.
- Store Twilio credentials in the cloud platform's secrets manager.
- Add Microsoft Entra ID or another login system before using customer data.
- Use a paid routing provider or hosted OSRM instance rather than relying on a
  public demo routing server.

Example persistent database setting:

```text
DATABASE_PATH=/mnt/ectrl-data/notification_log.db
```

The current version is deployable as a demo, but login, authorization,
database migration, backups, monitoring, privacy controls, and reliable
external integrations are still required before operational production use.

### Persistence warning for online bookings

New appointments and inspector settings are stored in SQLite. On Streamlit
Community Cloud, local files can be replaced when the app restarts or is
redeployed. Use a persistent PostgreSQL database before customers or office
staff rely on the online booking form in production.

### Planning rules

The day optimizer considers:

- Belgian road travel times and distances from OSRM;
- customer availability windows;
- configurable inspection duration;
- inspector qualifications;
- working hours and working days;
- maximum inspections and driving minutes;
- manually locked assignments;
- total route distance and workload balance.

If the constraints make a day impossible, the app reports that conflict rather
than silently creating an unrealistic route. The weekly planner should then be
used to move flexible work, add capacity, or widen customer availability.

## Demo-only boundaries

The appointments, customers, addresses, coordinates, inspectors, phone
numbers, prices, and Yuki references are fictional. The optimizer requests an
OSRM road-time and road-distance matrix using OpenStreetMap data. Google
OR-Tools then solves a Vehicle Routing Problem with Time Windows (VRPTW),
choosing both inspector assignments and stop order while respecting customer
windows and a 30-minute inspection duration. It minimizes road distance and
penalizes an excessively long individual route. If OSRM or OR-Tools is
unavailable, the app clearly labels its heuristic fallback. There is no login,
payment flow, live traffic data, Google Maps/Mapbox routing, or real Yuki API
integration.

SMS is disabled by default. Without all three Twilio environment variables,
departure actions are only written to SQLite and the app displays:

> DEMO MODE - no real SMS messages are sent.

To intentionally enable Twilio, copy `.env.example` to `.env` and provide
`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_FROM_NUMBER`. Use only
verified test recipients while demonstrating the app.

## Two-minute demo script

1. **Show daily planning**  
   Open the Planning tab and explain that the daily Excel or planning export is
   already loaded into one operational view.

2. **Show the scale**  
   Point to the KPI cards: 80 inspections, 8 inspectors, and 10 inspections per
   inspector. Mention the visible missing and invalid phone-number exceptions.

3. **Optimize routes**  
   Click **Optimize routes**. Open the Route optimization tab and select an
   inspector to show the nearest-neighbor stop order, map, and estimated
   round-trip distance.

4. **Prepare reminders**  
   Click **Prepare all day-before reminders**. Show that valid customers become
   `sent_demo`, while missing and invalid numbers remain clearly flagged. No
   real messages are sent.

5. **Inspector departing**  
   Open Inspector actions, choose an inspection, and click
   **Ik vertrek / Inspector departing**. Show the automatic customer message
   and its audit-log entry.

6. **Complete an inspection**  
   Click **Markeer keuring als uitgevoerd**. The inspection becomes completed
   and its invoice becomes `invoice_ready`.

7. **Export for Yuki**  
   Open Yuki export and download the invoice CSV. Finish in Audit log by
   downloading the notification log CSV and emphasizing that every action is
   traceable.
