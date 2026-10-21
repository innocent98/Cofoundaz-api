# FE integration guide: Marketing PRD gaps, slice (a)

Gap rows 2, 3, 4 (BE part), 6, 7, 8, 13, 25, 26, 27 and 34 of `cofoundaz/docs/checklist/marketing-prd-gaps.md`.
Builds on `docs/fe-integration-guide-marketing*.md` (the original Marketing routes are unchanged unless
this guide says so). Every body below is pasted by script from a live run of
`e2e/test_marketing_prd_a.py` (captures in `e2e/_captures/marketing_prd_a/`); ids and timestamps are
throwaway test data. Slice (b) extends this file with the AI parts.

All routes: `Authorization: Bearer …` + `X-Workspace-Id`, base path `/api/v1/marketing`.
**Founders and team members only**; every other role gets `403 FORBIDDEN` (unit-tested for mentor,
consultant, investor, accountant and legal advisor on every new route; not captured live, see the table at the end).
D-MKT-8: there are no external integrations. "Published" is a status, nothing is posted to any platform.

---

## 1. Scheduled posts publish themselves (rows 3, 34)

A worker job runs every minute. A post with `status: "scheduled"` whose `scheduled_at` has passed
flips to `published` with `published_at` set to **the time it was due** (not the time the job ran).
Nothing is sent anywhere. Plan for a delay of up to a minute or two after `scheduled_at`.

**UX instruction:** do not say "Posted to Instagram". Say **"Marked published at {published_at}"**. A
post can be `scheduled` in the past for a minute or so; do not treat that as an error.

Before the tick (`post_scheduled_before_tick.json`, created with a `scheduled_at` already in the past):
```json
{
  "data": {
    "id": "3eb508f6-15b8-4959-a0a1-06a8bea034e5",
    "startup_id": "57829571-0f6c-4298-8e87-5cbc70631b58",
    "created_by": "dd5d48f6-1a40-4426-a84e-079dc98b68c1",
    "title": "Waitlist teaser",
    "channel": "organic_social",
    "status": "scheduled",
    "body": "Something is coming.",
    "media_ref": null,
    "media_id": "a095af7e-656a-4f1e-abed-378c2ec12c9f",
    "media": {
      "id": "a095af7e-656a-4f1e-abed-378c2ec12c9f",
      "filename": "hero.png",
      "content_type": "image/png",
      "size_bytes": 67,
      "url": "var/storage-e2e/marketing/57829571-0f6c-4298-8e87-5cbc70631b58/b6ecc7159b1240a8a225594a998f53b7.png",
      "created_at": "2026-10-20T18:14:36.812287Z"
    },
    "campaign_id": "54a87848-c841-45e3-9eee-1fe502b6f646",
    "scheduled_at": "2026-10-20T18:11:36.872292Z",
    "published_at": null,
    "created_at": "2026-10-20T18:14:36.875181Z",
    "updated_at": "2026-10-20T18:14:36.875181Z"
  },
  "meta": null
}
```
After the tick (`post_after_tick.json`): `status` is `published`, `published_at` equals `scheduled_at`:
```json
{
  "data": {
    "id": "3eb508f6-15b8-4959-a0a1-06a8bea034e5",
    "startup_id": "57829571-0f6c-4298-8e87-5cbc70631b58",
    "created_by": "dd5d48f6-1a40-4426-a84e-079dc98b68c1",
    "title": "Waitlist teaser",
    "channel": "organic_social",
    "status": "published",
    "body": "Something is coming.",
    "media_ref": null,
    "media_id": "a095af7e-656a-4f1e-abed-378c2ec12c9f",
    "media": {
      "id": "a095af7e-656a-4f1e-abed-378c2ec12c9f",
      "filename": "hero.png",
      "content_type": "image/png",
      "size_bytes": 67,
      "url": "var/storage-e2e/marketing/57829571-0f6c-4298-8e87-5cbc70631b58/b6ecc7159b1240a8a225594a998f53b7.png",
      "created_at": "2026-10-20T18:14:36.812287Z"
    },
    "campaign_id": "54a87848-c841-45e3-9eee-1fe502b6f646",
    "scheduled_at": "2026-10-20T18:11:36.872292Z",
    "published_at": "2026-10-20T18:11:36.872292Z",
    "created_at": "2026-10-20T18:14:36.875181Z",
    "updated_at": "2026-10-20T18:14:38.719632Z"
  },
  "meta": null
}
```
Refetch the calendar on focus or every 60 s while any `scheduled` post is due, so the chip updates.

### The notification (rows 3, 4, 34)
Everyone with marketing access is notified, **including the post's author** (the scheduler is the actor, not a
person). `notification_post_published.json`, one item of `GET /api/v1/notifications`:
```json
{
  "id": "c2c6c4a9-ead9-49b5-9e45-35ca6ab6908e",
  "type": "marketing.post.published",
  "title": "Scheduled post published: Waitlist teaser",
  "body": "See results",
  "data": {
    "href": "/marketing/calendar?entry=3eb508f6-15b8-4959-a0a1-06a8bea034e5",
    "title": "Waitlist teaser",
    "channel": "organic_social",
    "entry_id": "3eb508f6-15b8-4959-a0a1-06a8bea034e5",
    "startup_id": "57829571-0f6c-4298-8e87-5cbc70631b58",
    "was_scheduled": true
  },
  "read": false,
  "created_at": "2026-10-20T18:14:38.719632+00:00"
}
```
- `body` is the call to action: render a **"See results"** button.
- `data.href` is the deep link: `/marketing/calendar?entry={entry_id}`. Open the calendar and that post's drawer.
- `title` reads **"Scheduled post published: {title}"** when the post had a `scheduled_at`, and
  **"Post published: {title}"** when a never-scheduled draft was marked published by hand. Use `data.was_scheduled`
  if you build your own copy. (The "Post published:" variant is unit-tested, not captured: it needs a second user, because the person who clicks Mark published is not notified.)
- `marketing.campaign.launched` and `marketing.campaign.completed` now also carry `body: "See results"` and
  `data.href: "/marketing/campaigns/{campaign_id}"` (route is yours to build, see §3). Unit-tested, not captured live.
- Notification `type` starts with `marketing.`: file these under a Marketing category, not Missions.

---

## 2. Campaign links on posts and copy (row 8)

`campaign_id` (nullable) is now accepted on `POST` and `PATCH /calendar-entries` and on `POST /copy/generate`, and is
returned on posts and on copy generations. `PATCH` with `"campaign_id": null` unlinks. A campaign from another workspace
is `422 VALIDATION_ERROR` with `field_errors[0].field == "campaign_id"` (unit-tested).

Filters: `GET /calendar-entries?campaign_id={id}` and `GET /copy/generations?campaign_id={id}`.
`posts_filtered_by_campaign.json`:
```json
{
  "data": {
    "entries": [
      {
        "id": "3eb508f6-15b8-4959-a0a1-06a8bea034e5",
        "startup_id": "57829571-0f6c-4298-8e87-5cbc70631b58",
        "created_by": "dd5d48f6-1a40-4426-a84e-079dc98b68c1",
        "title": "Waitlist teaser",
        "channel": "organic_social",
        "status": "published",
        "body": "Something is coming.",
        "media_ref": null,
        "media_id": "a095af7e-656a-4f1e-abed-378c2ec12c9f",
        "media": {
          "id": "a095af7e-656a-4f1e-abed-378c2ec12c9f",
          "filename": "hero.png",
          "content_type": "image/png",
          "size_bytes": 67,
          "url": "var/storage-e2e/marketing/57829571-0f6c-4298-8e87-5cbc70631b58/b6ecc7159b1240a8a225594a998f53b7.png",
          "created_at": "2026-10-20T18:14:36.812287Z"
        },
        "campaign_id": "54a87848-c841-45e3-9eee-1fe502b6f646",
        "scheduled_at": "2026-10-20T18:11:36.872292Z",
        "published_at": "2026-10-20T18:11:36.872292Z",
        "created_at": "2026-10-20T18:14:36.875181Z",
        "updated_at": "2026-10-20T18:14:38.719632Z"
      }
    ]
  },
  "meta": null
}
```
**Nesting trap:** on a copy generation `campaign_id` is a top-level field of the generation, and also inside `inputs`
(the request as sent). Read the top-level one.

---

## 3. Campaign list stats and the performance endpoint (rows 6, 7)

`GET /campaigns` (and `GET /campaigns/{id}`, `POST`, `PATCH`) now fill `metrics`, which used to be `{}`. It is the
campaign's all-time totals from metric points recorded **against that campaign**; a campaign with no data has zeros.
Show **CTR** = `metrics.click_through_rate` and **Conv** = `metrics.conversion_rate` (both percentages, 1 decimal), and
the period from the existing `period_start` / `period_end` (either can be `null`). `campaigns_list_with_stats.json`:
```json
{
  "data": {
    "campaigns": [
      {
        "id": "54a87848-c841-45e3-9eee-1fe502b6f646",
        "startup_id": "57829571-0f6c-4298-8e87-5cbc70631b58",
        "name": "Waitlist push",
        "objective": "launch",
        "budget": 50000,
        "channel_mix": {
          "email": 60.0,
          "search": 40.0
        },
        "status": "draft",
        "metrics": {
          "impressions": 2000,
          "clicks": 100,
          "conversions": 10,
          "spend": 20000,
          "click_through_rate": 5.0,
          "conversion_rate": 10.0
        },
        "segment_ids": [],
        "period_start": "2026-10-20",
        "period_end": "2026-11-03",
        "launched_at": null,
        "completed_at": null,
        "created_at": "2026-10-20T18:14:36.793414Z",
        "updated_at": "2026-10-20T18:14:36.793414Z"
      }
    ]
  },
  "meta": null
}
```
Note `channel_mix` values are floats (`60.0`). `spend` is in the same minor unit you recorded it in (the page already
treats it as kobo).

### `GET /campaigns/{id}/performance?range=` for the detail charts
`range` is `7d`, `30d`, `90d` or `all` (default `all`). `campaign_performance.json`:
```json
{
  "data": {
    "campaign_id": "54a87848-c841-45e3-9eee-1fe502b6f646",
    "name": "Waitlist push",
    "range": "all",
    "totals": {
      "impressions": 2000,
      "clicks": 100,
      "conversions": 10,
      "spend": 20000,
      "click_through_rate": 5.0,
      "conversion_rate": 10.0
    },
    "series": [
      {
        "date": "2026-10-20",
        "impressions": 2000,
        "clicks": 100,
        "conversions": 10,
        "spend": 20000
      }
    ],
    "by_channel": [
      {
        "channel": "email",
        "share_percent": 60.0,
        "impressions": 1200,
        "clicks": 60,
        "conversions": 6,
        "spend": 12000,
        "estimated": true
      },
      {
        "channel": "search",
        "share_percent": 40.0,
        "impressions": 800,
        "clicks": 40,
        "conversions": 4,
        "spend": 8000,
        "estimated": true
      }
    ]
  },
  "meta": null
}
```
- `series` has **one point per day that has data**, ascending. Fill the missing days with zeros yourself.
- `by_channel` is the campaign's `channel_mix` applied to its totals. Campaign-scoped numbers carry no channel, so this is
  **always an estimate** (`estimated: true`): label the chart "Estimated split by channel". It is empty when the campaign has no `channel_mix`.
- Another workspace's campaign id is `404`. A bad range (`campaign_performance_bad_range.json`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "range must be one of 7d, 30d, 90d.",
    "field_errors": [
      {
        "field": "range",
        "message": "range must be one of 7d, 30d, 90d."
      }
    ]
  }
}
```
(The message lists only 7d/30d/90d; `all` is also accepted here.)

---

## 4. Recording, listing and deleting metrics; one scope per number (rows 26, 27)

### One scope per metric point (D-MKT-7)
A point may have a `channel` **or** a `campaign_id` **or** neither (whole workspace), never both, so one event can't be counted
twice in the funnel. Make the Record metric form a scope picker (Workspace / Channel / Campaign), not two independent
selects. `metric_two_scopes_rejected.json`, `422`:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "points.0",
        "message": "Value error, Record a metric against a channel or a campaign, not both."
      }
    ]
  }
}
```
Note the `field` is `points.0` (the index of the offending point) and the message starts with `Value error, `.
`POST /metrics` (`metrics_ingested.json`) is unchanged otherwise:
```json
{
  "data": {
    "created": 8
  },
  "meta": null
}
```

### `GET /metrics?range=30d&limit=100&offset=0` for a "Recorded entries" table
`range` 7d/30d/90d (default 30d), `limit` 1 to 500 (default 100), `offset` from 0. Newest day first. `metrics_list_page.json`
(`limit=3`; `total` is the count for the whole range):
```json
{
  "data": {
    "metrics": [
      {
        "id": "14c80ebe-848d-4eb6-acfb-b59a6fb555c7",
        "ts": "2026-10-20",
        "metric": "visits",
        "value": 999,
        "channel": null,
        "campaign_id": null,
        "campaign_name": null,
        "created_at": "2026-10-20T18:14:39.358900Z"
      },
      {
        "id": "2740e09f-aefa-43bb-9254-99e8ef008412",
        "ts": "2026-10-20",
        "metric": "visits",
        "value": 300,
        "channel": null,
        "campaign_id": null,
        "campaign_name": null,
        "created_at": "2026-10-20T18:14:39.358900Z"
      },
      {
        "id": "5d6be119-2540-448e-880a-a1c3fdd2563d",
        "ts": "2026-10-20",
        "metric": "conversions",
        "value": 5,
        "channel": "email",
        "campaign_id": null,
        "campaign_name": null,
        "created_at": "2026-10-20T18:14:39.358900Z"
      }
    ],
    "total": 8,
    "limit": 3,
    "offset": 0
  },
  "meta": null
}
```
`campaign_name` is null unless the point is campaign-scoped. Rows recorded in one batch share a `created_at`, so their order within a day is not meaningful.

### `DELETE /metrics/{id}`: `metric_deleted.json`
```json
{
  "data": {
    "deleted": true
  },
  "meta": null
}
```
A second delete or another workspace's id is `404`. **UX:** confirm before deleting; the funnel, CAC, campaign stats and the Dashboard
"Campaign CTR" KPI all change.

### CAC by channel is now partly an estimate (row 26)
`GET /analytics` `cac_by_channel` rows gained `attributed_spend`, `attributed_conversions` and `estimated`. Campaign-scoped
spend and conversions are split across channels by the campaign's `channel_mix` and added in. `spend` / `conversions` / `cac` are the
**totals including the attributed part**. `analytics_with_estimated_cac.json`:
```json
{
  "data": {
    "range": "30d",
    "traffic_by_week": [
      {
        "week_start": "2026-10-19",
        "visits": 300
      }
    ],
    "cac_by_channel": [
      {
        "channel": "search",
        "spend": 8000,
        "conversions": 4,
        "cac": 2000,
        "attributed_spend": 8000,
        "attributed_conversions": 4,
        "estimated": true
      },
      {
        "channel": "email",
        "spend": 17000,
        "conversions": 11,
        "cac": 1545,
        "attributed_spend": 12000,
        "attributed_conversions": 6,
        "estimated": true
      }
    ],
    "funnel": {
      "impressions": 2000,
      "clicks": 100,
      "conversions": 15,
      "click_through_rate": 5.0,
      "conversion_rate": 15.0
    },
    "leaderboard": [
      {
        "campaign_id": "54a87848-c841-45e3-9eee-1fe502b6f646",
        "name": "Waitlist push",
        "clicks": 100,
        "conversions": 10,
        "spend": 20000,
        "cac": 2000
      }
    ]
  },
  "meta": null
}
```
**UX instruction:** when `estimated` is true, show an "Estimated" tag with a tooltip ("includes campaign spend split by the
campaign's channel mix"). Rows are in channel order, not sorted by CAC. Channel-only data is `estimated: false` with zero `attributed_*` (unit-tested).

---

## 5. CSV export (row 25, interim until Reports exists)

`GET /analytics/export?range=30d` returns **`text/csv`, not the JSON envelope**, as an attachment. Download it with `fetch` +
`blob()` using the bearer header (a plain `<a href>` cannot send it). One row per recorded metric point in the range, oldest first.
Columns `date,scope,channel,campaign,metric,value`, `scope` is `channel`, `campaign` or `workspace`. A cell that would start a
spreadsheet formula (`=`, `+`, `-`, `@`) gets a leading `'`. Errors (bad range) are the usual JSON `422`. Max 50,000 rows.
`analytics_export.json` (status, headers and the body string):
```json
{
  "status": 200,
  "content-type": "text/csv; charset=utf-8",
  "content-disposition": "attachment; filename=\"marketing-metrics-30d.csv\"",
  "body": "date,scope,channel,campaign,metric,value\n2026-10-20,workspace,,,visits,300\n2026-10-20,channel,email,,conversions,5\n2026-10-20,campaign,,Waitlist push,conversions,10\n2026-10-20,channel,email,,spend,5000\n2026-10-20,campaign,,Waitlist push,impressions,2000\n2026-10-20,campaign,,Waitlist push,clicks,100\n2026-10-20,campaign,,Waitlist push,spend,20000\n"
}
```

---

## 6. Post media (rows 2, 13)

Images for the entry drawer's media slot. **Stored apart from Documents**: they never appear in `/documents/files` and the
routes are marketing-only (a mentor cannot list, fetch or delete them). Allowed: PNG, JPEG, GIF, WebP, **max 5 MB**, **200 per workspace**.
The server checks the file's real bytes match the declared type.

`POST /media` (multipart, field `file`) → `201`, `media_uploaded.json`:
```json
{
  "data": {
    "id": "a095af7e-656a-4f1e-abed-378c2ec12c9f",
    "filename": "hero.png",
    "content_type": "image/png",
    "size_bytes": 67,
    "url": "var/storage-e2e/marketing/57829571-0f6c-4298-8e87-5cbc70631b58/b6ecc7159b1240a8a225594a998f53b7.png",
    "created_at": "2026-10-20T18:14:36.812287Z"
  },
  "meta": null
}
```
**`url` is storage-dependent.** This capture ran on the local-disk backend, so it is a relative path. Deployed environments use
Cloudinary and return an `https://res.cloudinary.com/…` URL; that shape was **not exercised live** (needs a Cloudinary account). Use `url` as the `<img src>` as given.

Rejections (`422`): bytes that aren't the declared image (`media_rejected_not_an_image.json`) and a disallowed type such as SVG (`media_rejected_type.json`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "That file is not a valid image of the type it claims to be.",
    "field_errors": [
      {
        "field": "file",
        "message": "That file is not a valid image of the type it claims to be."
      }
    ]
  }
}
```
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Upload a PNG, JPEG, GIF or WebP image.",
    "field_errors": []
  }
}
```
Over 5 MB and over the 200 cap are `422` with `field_errors[0].field == "file"` (unit-tested, not captured).

`GET /media` → `{"data": {"media": [...]}}` newest first (`media_list.json`):
```json
{
  "data": {
    "media": [
      {
        "id": "a095af7e-656a-4f1e-abed-378c2ec12c9f",
        "filename": "hero.png",
        "content_type": "image/png",
        "size_bytes": 67,
        "url": "var/storage-e2e/marketing/57829571-0f6c-4298-8e87-5cbc70631b58/b6ecc7159b1240a8a225594a998f53b7.png",
        "created_at": "2026-10-20T18:14:36.812287Z"
      }
    ]
  },
  "meta": null
}
```
`DELETE /media/{id}` → `{"data": {"deleted": true}}`; posts that used it keep working with no image.

### Attach to a post
Send `media_id` on `POST` / `PATCH /calendar-entries` (`null` clears). Posts return **both** `media_id` and a `media` object
(or `null`); the post bodies in §1 show it. A `media_id` from another workspace is `422` (`field: "media_id"`).
After deleting the media, the post reads (`post_after_media_deleted.json`):
```json
{
  "data": {
    "id": "3eb508f6-15b8-4959-a0a1-06a8bea034e5",
    "startup_id": "57829571-0f6c-4298-8e87-5cbc70631b58",
    "created_by": "dd5d48f6-1a40-4426-a84e-079dc98b68c1",
    "title": "Waitlist teaser",
    "channel": "organic_social",
    "status": "published",
    "body": "Something is coming.",
    "media_ref": null,
    "media_id": null,
    "media": null,
    "campaign_id": "54a87848-c841-45e3-9eee-1fe502b6f646",
    "scheduled_at": "2026-10-20T18:11:36.872292Z",
    "published_at": "2026-10-20T18:11:36.872292Z",
    "created_at": "2026-10-20T18:14:36.875181Z",
    "updated_at": "2026-10-20T18:14:38.719632Z"
  },
  "meta": null
}
```
The old free-text `media_ref` field still exists and is untouched; use `media_id`.

**Security note for you:** the upload is the only way marketing images enter the system. Do not offer it to roles that don't have
Marketing, and do not put these URLs anywhere outside Marketing screens.

---

## Verification table

| Behaviour | Status |
|---|---|
| Scheduled post flips to published, `published_at` = due time, author notified, `href` / CTA | **verified live** (§1) |
| Campaign link on posts + filter | **verified live** (§2); campaign filter on copy generations and cross-workspace 422s: unit tests only |
| Campaign list stats, performance series and estimated channel split | **verified live** (§3) |
| One-scope rule, list, pagination, delete, 404 on repeat | **verified live** (§4) |
| Estimated CAC by channel | **verified live** (§4); channel-only `estimated:false`: unit |
| CSV export | **verified live** (§5); formula-injection prefix: unit |
| Media upload / list / attach / detach / delete, rejections, not in Documents list | **verified live** (§6) |
| Media on Cloudinary (https URL) | **not exercised** (no Cloudinary in the harness) |
| 5 MB and 200-image caps | unit tests only |
| 403 for mentor, consultant, investor, accountant, legal advisor on every new route | unit tests only (a second invited user is not part of the live journey) |
| "Post published:" copy for a manual publish; campaign launched/completed CTA | unit tests only |
| Scheduler skips deleted workspaces, claims once per (post, time), ignores a late edit | unit tests only |
