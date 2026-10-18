# Events

Events are tenant-scoped. Only organization owners and administrators can manage
organization-wide events. Church administrators can manage events, occurrences,
registrations, attendance, statistics, and audit history only for their
assigned church (and its campuses).
Public visitors can view a published event and register with contact details;
authenticated members are associated with their member profile and must belong
to the event's church, campus, or selected organization churches.

Organization administrators assign active members as church event
administrators from **Administration → Églises → Administrateurs événements**.
The assignment grants no access to organization-wide events or other churches.

## API

Admin endpoints are rooted at `/api/events/`:

- `GET/POST /` — list and create events; list filters include status, scope,
  category, search text, and occurrence date range.
- `GET/PATCH /{event_id}/`, `POST /{event_id}/status/`, and
  `POST /{event_id}/series-update/` — read/update event details, transition
  status, or explicitly confirm updates to future occurrences.
- `GET/POST /{event_id}/occurrences/`,
  `POST /{event_id}/occurrences/generate/`, and
  `PATCH /{event_id}/occurrences/{occurrence_id}/` — manage individual sessions
  and generate at most 50 recurring sessions per request.
- `/public/{event_id}/` and
  `/public/{event_id}/occurrences/{occurrence_id}/registrations/` — public
  published-event details and registration.
- Occurrence registration, attendance, scan, check-in, correction, statistics,
  and audit endpoints are also available under `/api/events/`.

QR tokens are stored as hashes. A scan only validates an attendee; a separate
authorized confirmation records attendance. Waitlist offers expire after 24
hours. Run `python manage.py process_event_waitlists` periodically to expire
offers and advance each queue. No scheduler or notification transport is
configured by this app.

Confirmed registrations include a random five-digit fallback code, stored as
a hash and unique within its occurrence. Manual scan and confirmation require
the target occurrence and are rate-limited. The public registration screen
offers a downloadable PDF receipt containing the event/session information,
QR code, and fallback code.

## Setup and verification

Apply schema changes with `python manage.py migrate`. Run the focused suite with
`python manage.py test apps.events.tests.test_api`.

Capacity changes are transactionally locked where supported. SQLite does not
provide the same row-locking guarantees as PostgreSQL; production concurrency
validation should use PostgreSQL.
