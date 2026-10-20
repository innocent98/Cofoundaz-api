# FE integration guide: Marketing PRD gaps (slices a and b)

Gap rows 2, 3, 4 (BE part), 6, 7, 8, 13, 25, 26, 27 and 34 of `cofoundaz/docs/checklist/marketing-prd-gaps.md`.
Builds on `docs/fe-integration-guide-marketing*.md` (the original Marketing routes are unchanged unless
this guide says so). Every body below is pasted by script from a live run of
`e2e/test_marketing_prd_a.py` (captures in `e2e/_captures/marketing_prd_a/`); ids and timestamps are
throwaway test data. Slice (b) is §7 onwards (the AI half) and has its own captures.

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

---

# Slice (b): the AI half

Gap rows 5, 12, 16, 17, 18, 20, 21, 22 and 24. The captures are in `e2e/_captures/marketing_prd_b/`.
**Read this first:** the live journey ran with the **stub model** (`LLM_PROVIDER=stub`), so AI text below reads
`[stub-llm] ...` and the stub returns one variant instead of three. It proves the routes, jobs, stored shapes and the
notification. It does **not** prove the quality of real-model text, and no real-model capture was taken for this slice
(the build session has no model key). Treat every AI string as an example of *shape only*.

## 7. AI context and placeholders (row 16): nothing for the FE to build

Every marketing prompt (copy, refine, Plan my first week, channel split, fit notes, content gaps) now also receives the
Business Builder problem / solution / value proposition, personas, the saved positioning statement, audience segments and the
channels' status and latest fit level. The prompts forbid bracket placeholders such as `[link]`; if an answer contains one, the
job asks the model once more, and keeps the retry (or the first answer if the daily budget is spent). Consequences for you:
- Generations can take roughly twice as long when a retry happens. Keep the poll cadence you already use (every 2 s, give up around 90 s).
- A founder with an empty Business Builder gets the same generic-but-finished copy as before. Consider nudging them to fill it in.
- The channel split is told each channel's fit level, so it should stop putting budget on a channel the notes call low priority.

## 8. Copy: platform and character limits (row 18, D-MKT-3)

`GET /copy/platforms` is the fixed table. `copy_platforms.json`:
```json
{
  "data": {
    "platforms": [
      {
        "key": "x",
        "label": "X",
        "limit": 280,
        "kind": "hard"
      },
      {
        "key": "instagram",
        "label": "Instagram caption",
        "limit": 2200,
        "kind": "hard"
      },
      {
        "key": "linkedin",
        "label": "LinkedIn post",
        "limit": 3000,
        "kind": "hard"
      },
      {
        "key": "facebook",
        "label": "Facebook post",
        "limit": 500,
        "kind": "recommended"
      },
      {
        "key": "whatsapp",
        "label": "WhatsApp message",
        "limit": 1024,
        "kind": "hard"
      },
      {
        "key": "google_ads_headline",
        "label": "Google Ads headline",
        "limit": 30,
        "kind": "hard"
      },
      {
        "key": "google_ads_description",
        "label": "Google Ads description",
        "limit": 90,
        "kind": "hard"
      },
      {
        "key": "email_subject",
        "label": "Email subject line",
        "limit": 60,
        "kind": "recommended"
      }
    ]
  },
  "meta": null
}
```
`kind: "hard"` means the platform rejects longer text; `"recommended"` means longer works but is cut off or performs worse.
`POST /copy/generate` takes an optional `platform` (one of those keys; anything else is `422`, `copy_unknown_platform_422.json`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "platform",
        "message": "Input should be 'x', 'instagram', 'linkedin', 'facebook', 'whatsapp', 'google_ads_headline', 'google_ads_description' or 'email_subject'"
      }
    ]
  }
}
```
The finished generation carries the limit and per-variant lengths in `output`. `copy_generation_ready_with_platform.json`:
```json
{
  "data": {
    "id": "f5c33759-cba9-40fe-9b89-aebc7eadfd42",
    "startup_id": "b9b42c95-d001-4e99-a824-9bbea9353147",
    "campaign_id": null,
    "kind": "copy",
    "status": "ready",
    "inputs": {
      "cta": null,
      "tone": "bold",
      "channel": "organic_social",
      "platform": "x",
      "asset_type": "ad",
      "campaign_id": null,
      "key_message": "Settle the same day",
      "audience_segment_id": null
    },
    "output": {
      "limit": 280,
      "lengths": [
        19
      ],
      "platform": "x",
      "variants": [
        "[stub-llm] variants"
      ],
      "over_limit": []
    },
    "error": null,
    "created_at": "2026-10-20T18:32:44.192329Z",
    "updated_at": "2026-10-20T18:32:44.219108Z"
  },
  "meta": null
}
```
- Show `lengths[i] / limit` under each variant; turn it red when `i` is in `over_limit` (or `lengths[i] > limit`).
- The model is told the limit and asked again once if a variant is over; a variant can still come back over, so **do not rely on it being under**.
- `limit` / `platform` are `null` when no platform was chosen. Variants are still a list of strings; `output.variants` has not changed shape.
- `channel` (the category) and `platform` are separate fields. Choose the platform per variant set, not per campaign.

## 9. Copy: Refine (row 17)

`POST /copy/generations/{id}/refine`, body `{"variant_index": 0..2, "instruction": "Make it shorter"}` (instruction 1 to 500
characters, not blank) → `202` `{id, status:"generating"}` (`refine_accepted.json`):
```json
{
  "data": {
    "id": "763c29fc-e304-4dcf-8863-5683bf922625",
    "status": "generating"
  },
  "meta": null
}
```
It creates a **new** generation (same `kind: "copy"`, same polling as any copy generation) and never changes the parent. It keeps the
parent's inputs, so the **original `key_message` is preserved** (history titles stay meaningful), and the link to the parent is
`inputs.refine`. `refine_generation_ready.json`:
```json
{
  "data": {
    "id": "763c29fc-e304-4dcf-8863-5683bf922625",
    "startup_id": "b9b42c95-d001-4e99-a824-9bbea9353147",
    "campaign_id": null,
    "kind": "copy",
    "status": "ready",
    "inputs": {
      "cta": null,
      "tone": "bold",
      "refine": {
        "instruction": "Make it shorter",
        "variant_index": 0,
        "parent_generation_id": "f5c33759-cba9-40fe-9b89-aebc7eadfd42"
      },
      "channel": "organic_social",
      "platform": "x",
      "asset_type": "ad",
      "campaign_id": null,
      "key_message": "Settle the same day",
      "audience_segment_id": null
    },
    "output": {
      "limit": 280,
      "lengths": [
        19
      ],
      "platform": "x",
      "variants": [
        "[stub-llm] variants"
      ],
      "over_limit": []
    },
    "error": null,
    "created_at": "2026-10-20T18:32:44.249288Z",
    "updated_at": "2026-10-20T18:32:44.265681Z"
  },
  "meta": null
}
```
It appears in `GET /copy/generations` history like any other; use `inputs.refine` to show it as "Refinement of …" or to group it under its parent.
Errors: parent still `generating` or `failed` → `422` with `field: "generation_id"` (`refine_not_ready_422.json`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Only a finished generation can be refined.",
    "field_errors": [
      {
        "field": "generation_id",
        "message": "Only a finished generation can be refined."
      }
    ]
  }
}
```
A `variant_index` the parent doesn't have → `422` `field: "variant_index"`; another workspace's id, or a generation that isn't a copy → `404` (both unit-tested, not captured).
If the parent disappears while the job is queued the new generation ends `failed` with `error: "parent_missing"` (unit-tested).
The parent's limits do not carry over to the instruction: a refine of an `x` generation is re-limited to 280 because `platform` is copied into `inputs`.

## 10. Channels: fit level, 30-day stats, and auto-status (rows 20, 21, D-MKT-4)

### Fit level
Fit-note generation now returns `output.levels` (`{channel: "high"|"medium"|"low"}`) next to `output.notes`
(`{channel: string}`, unchanged), and each channel row has `fit_level` (`null` until notes were generated, or if the model returned no valid level).
The note text no longer starts with the level word: render your own badge ("High fit") from `fit_level`. `fit_notes_ready.json`:
```json
{
  "data": {
    "id": "83511b3e-c815-48fb-b6d6-94807c6e23f7",
    "startup_id": "b9b42c95-d001-4e99-a824-9bbea9353147",
    "campaign_id": null,
    "kind": "channel_fit",
    "status": "ready",
    "inputs": {},
    "output": {
      "notes": {
        "email": "[stub-llm] note",
        "events": "[stub-llm] note",
        "search": "[stub-llm] note",
        "referral": "[stub-llm] note",
        "content_seo": "[stub-llm] note",
        "paid_social": "[stub-llm] note",
        "partnerships": "[stub-llm] note",
        "organic_social": "[stub-llm] note"
      },
      "levels": {
        "email": "high",
        "events": "high",
        "search": "high",
        "referral": "high",
        "content_seo": "high",
        "paid_social": "high",
        "partnerships": "high",
        "organic_social": "high"
      }
    },
    "error": null,
    "created_at": "2026-10-20T18:32:44.360076Z",
    "updated_at": "2026-10-20T18:32:44.388033Z"
  },
  "meta": null
}
```
(The stub picks the first enum value, so every level reads `high` here.)

### `GET /channels` now has `stats_30d` per channel
Last 30 days of numbers recorded **against that channel** (channel-scoped points only; campaign-scoped numbers are not included).
`has_data: false` means nothing was recorded: show "No tracked results yet". `PATCH /channels/{key}` returns the same object.
`channels_after_fit_notes.json` (full response, 8 rows; `email` had 40 clicks and 900 spend recorded):
```json
{
  "data": [
    {
      "id": "942d8d63-4f08-4f12-abb6-2140fa981fb8",
      "key": "organic_social",
      "status": "not_started",
      "notes": null,
      "ai_fit_note": "[stub-llm] note",
      "fit_level": "high",
      "fit_note_generated_at": "2026-10-20T18:32:44.397023Z",
      "stats_30d": {
        "visits": 0,
        "impressions": 0,
        "clicks": 0,
        "conversions": 0,
        "spend": 0,
        "has_data": false
      }
    },
    {
      "id": "02811d19-5ee5-4ecd-b61e-3f7f982c17c8",
      "key": "paid_social",
      "status": "not_started",
      "notes": null,
      "ai_fit_note": "[stub-llm] note",
      "fit_level": "high",
      "fit_note_generated_at": "2026-10-20T18:32:44.397023Z",
      "stats_30d": {
        "visits": 0,
        "impressions": 0,
        "clicks": 0,
        "conversions": 0,
        "spend": 0,
        "has_data": false
      }
    },
    {
      "id": "45643e87-5ca1-4c83-bfba-647b5ade4e1c",
      "key": "search",
      "status": "not_started",
      "notes": null,
      "ai_fit_note": "[stub-llm] note",
      "fit_level": "high",
      "fit_note_generated_at": "2026-10-20T18:32:44.397023Z",
      "stats_30d": {
        "visits": 0,
        "impressions": 0,
        "clicks": 0,
        "conversions": 0,
        "spend": 0,
        "has_data": false
      }
    },
    {
      "id": "9c7698ed-63b7-4bd4-80f4-954aa7aabc2f",
      "key": "email",
      "status": "not_started",
      "notes": null,
      "ai_fit_note": "[stub-llm] note",
      "fit_level": "high",
      "fit_note_generated_at": "2026-10-20T18:32:44.397023Z",
      "stats_30d": {
        "visits": 0,
        "impressions": 0,
        "clicks": 40,
        "conversions": 0,
        "spend": 900,
        "has_data": true
      }
    },
    {
      "id": "56db6253-463b-4e28-9c14-3ce78e7d5659",
      "key": "content_seo",
      "status": "not_started",
      "notes": null,
      "ai_fit_note": "[stub-llm] note",
      "fit_level": "high",
      "fit_note_generated_at": "2026-10-20T18:32:44.397023Z",
      "stats_30d": {
        "visits": 0,
        "impressions": 0,
        "clicks": 0,
        "conversions": 0,
        "spend": 0,
        "has_data": false
      }
    },
    {
      "id": "1f8c250c-4b4a-499f-9288-0aa3a6c2adb6",
      "key": "partnerships",
      "status": "not_started",
      "notes": null,
      "ai_fit_note": "[stub-llm] note",
      "fit_level": "high",
      "fit_note_generated_at": "2026-10-20T18:32:44.397023Z",
      "stats_30d": {
        "visits": 0,
        "impressions": 0,
        "clicks": 0,
        "conversions": 0,
        "spend": 0,
        "has_data": false
      }
    },
    {
      "id": "0262b867-5610-4e55-b862-1d1a9bada548",
      "key": "events",
      "status": "not_started",
      "notes": null,
      "ai_fit_note": "[stub-llm] note",
      "fit_level": "high",
      "fit_note_generated_at": "2026-10-20T18:32:44.397023Z",
      "stats_30d": {
        "visits": 0,
        "impressions": 0,
        "clicks": 0,
        "conversions": 0,
        "spend": 0,
        "has_data": false
      }
    },
    {
      "id": "8b25352d-20d0-427d-834e-2aece0b2de1f",
      "key": "referral",
      "status": "not_started",
      "notes": null,
      "ai_fit_note": "[stub-llm] note",
      "fit_level": "high",
      "fit_note_generated_at": "2026-10-20T18:32:44.397023Z",
      "stats_30d": {
        "visits": 0,
        "impressions": 0,
        "clicks": 0,
        "conversions": 0,
        "spend": 0,
        "has_data": false
      }
    }
  ],
  "meta": null
}
```
**Nesting trap:** `stats_30d` is on the channel row; the same numbers by channel on Analytics live in `cac_by_channel` and mean something different (they include campaign spend, estimated).

### Launching a campaign starts its channels (D-MKT-4)
When a campaign goes `draft` to `active`, every channel with a `channel_mix` share above 0 that is **Not started** becomes **Testing**.
A status the founder set by hand (Active, Paused, Testing) is never changed, 0% channels are not touched, and pause/resume does not repeat it.
Refetch `/channels` and the overview after a launch. `channel_statuses_after_launch.json` (mix was email 60, search 40, events 0;
`search` had been set to active by hand first):
```json
{
  "organic_social": "not_started",
  "paid_social": "not_started",
  "search": "active",
  "email": "testing",
  "content_seo": "not_started",
  "partnerships": "not_started",
  "events": "not_started",
  "referral": "not_started"
}
```
**UX instruction:** tell the founder when a launch changed channels: "Email is now Testing."

## 11. Weekly campaign readout (row 5)

Once per ISO week, from 08:00 workspace time, every **active** campaign that recorded numbers in the last 14 days gets an AI readout
(a campaign with no data gets none and costs no tokens). The model sees numbers only (objective, plan, last 7 days vs the 7 before, all time); never the campaign name or any founder text.
`GET /campaigns/{id}/readouts` returns up to 12, newest first. `campaign_readouts.json` (stub text):
```json
{
  "data": {
    "readouts": [
      {
        "id": "50d8a1e9-ddcc-47a2-8f02-ea3222a914b8",
        "campaign_id": "56e3de5a-c3d3-411f-aecf-afa46b32c369",
        "week_start": "2026-10-19",
        "status": "ready",
        "output": {
          "wins": [
            "[stub-llm] wins"
          ],
          "watch": [
            "[stub-llm] watch"
          ],
          "summary": "[stub-llm] summary",
          "headline": "[stub-llm] headline",
          "next_step": "[stub-llm] next_step"
        },
        "error": null,
        "created_at": "2026-10-20T18:32:44.580169Z"
      }
    ]
  },
  "meta": null
}
```
- `output`: `headline`, `summary`, `wins` (max 3), `watch` (max 3), `next_step`. Render as the "AI weekly readout" card on the campaign detail page; show the newest `ready` one and `week_start` as "Week of …".
- `status` is `generating`, `ready` or `failed` (`error: "over_budget"` when the workspace's AI budget was spent that day; show nothing, or "Readout unavailable this week"). A `generating` row is normally seconds old.
- An empty list means no readout yet (new campaign, no data, or before Monday 08:00): show an empty state, not an error.
- The notification (`marketing.campaign.readout.ready`, founders and team members, nobody else) reads "Campaign performance summary ready: {name}", body **"See results"**, `data.href` = `/marketing/campaigns/{campaign_id}`, plus `data.readout_id`. `notification_readout_ready.json`:
```json
{
  "id": "484189f8-1023-46eb-a679-0ccffc693e24",
  "type": "marketing.campaign.readout.ready",
  "title": "Campaign performance summary ready: Waitlist push",
  "body": "See results",
  "data": {
    "href": "/marketing/campaigns/56e3de5a-c3d3-411f-aecf-afa46b32c369",
    "name": "Waitlist push",
    "readout_id": "50d8a1e9-ddcc-47a2-8f02-ea3222a914b8",
    "startup_id": "b9b42c95-d001-4e99-a824-9bbea9353147",
    "campaign_id": "56e3de5a-c3d3-411f-aecf-afa46b32c369"
  },
  "read": false,
  "created_at": "2026-10-20T18:32:44.580169+00:00"
}
```
- Another workspace's campaign is `404`.

## 12. Keywords: honest estimates (row 22)

Volume, difficulty and rank are typed by hand (there is no SEO data provider). Keyword rows now say so: `source` is always `"manual"` and
`metrics_updated_at` is when volume / difficulty / rank were last *changed* (`null` if never entered; renaming or re-saving the same value does not move it).
Show "Estimated by you · updated {date}". `keyword_with_manual_metrics.json`:
```json
{
  "data": {
    "id": "43507cc8-6c05-404d-b935-9fd0f5063f9f",
    "keyword": "same day settlement",
    "volume": "1.2k",
    "difficulty": null,
    "current_rank": null,
    "target_page": null,
    "source": "manual",
    "metrics_updated_at": "2026-10-20T18:32:44.658702Z",
    "created_at": "2026-10-20T18:32:44.655671Z",
    "updated_at": "2026-10-20T18:32:44.655671Z"
  },
  "meta": null
}
```
`keyword_without_metrics.json`:
```json
{
  "data": {
    "id": "b033248b-032d-4d5f-8180-8b5a58506b4c",
    "keyword": "payments",
    "volume": null,
    "difficulty": null,
    "current_rank": null,
    "target_page": null,
    "source": "manual",
    "metrics_updated_at": null,
    "created_at": "2026-10-20T18:32:44.666395Z",
    "updated_at": "2026-10-20T18:32:44.666395Z"
  },
  "meta": null
}
```

## 13. Segment definitions (row 24, D-MKT-5)

`definition` is now validated: only these attributes, each shown as a chip. `GET /segments/attributes` (`segment_attributes.json`):
```json
{
  "data": {
    "text": [
      "location",
      "business_type",
      "company_size",
      "income_band"
    ],
    "channel_acquired": [
      "organic_social",
      "paid_social",
      "search",
      "email",
      "content_seo",
      "partnerships",
      "events",
      "referral"
    ],
    "behaviour_tags": {
      "max_tags": 10,
      "max_length": 40
    }
  },
  "meta": null
}
```
- `text` attributes: free text up to 100 characters. `channel_acquired` must be one of the channel keys. `behaviour_tags`: up to 10 tags, 40 characters each.
- The server trims, drops blanks, and de-duplicates tags, so read the response back rather than trusting what you sent. `segment_created_cleaned.json` (sent `" Lagos "` and a duplicate tag):
```json
{
  "data": {
    "id": "faef4d50-0dac-4269-a128-c10db6dd56b4",
    "startup_id": "b9b42c95-d001-4e99-a824-9bbea9353147",
    "name": "Lagos merchants",
    "definition": {
      "location": "Lagos",
      "behaviour_tags": [
        "trial",
        "mobile first"
      ],
      "channel_acquired": "email"
    },
    "est_size": 1200,
    "persona_id": null,
    "created_at": "2026-10-20T18:32:44.684265Z",
    "updated_at": "2026-10-20T18:32:44.684265Z"
  },
  "meta": null
}
```
- Anything else is `422` with `field: "definition"` (`segment_definition_422.json`; only the first problem is reported):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "definition",
        "message": "Value error, Unknown attribute 'favourite_colour'. Use one of: location, business_type, company_size, income_band, channel_acquired, behaviour_tags."
      }
    ]
  }
}
```
- Segments saved before this change keep whatever `definition` they had (no migration). Render unknown keys as plain chips and do not fail on them; the next save through the form will be validated.
- `est_size` is still a founder's estimate (no user data source): label it "Estimated".

## 14. One channel per post (row 12, D-MKT-2)

Decision: a post has exactly one `channel`; cross-posting means duplicating the entry. Nothing changed in the API; sending a list is `422`
(`post_two_channels_422.json`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "channel",
        "message": "Input should be 'organic_social', 'paid_social', 'search', 'email', 'content_seo', 'partnerships', 'events' or 'referral'"
      }
    ]
  }
}
```

---

## Verification table

| Behaviour | Status |
|---|---|
| **Slice (a)** | |
| Scheduled post flips to published, `published_at` = due time, author notified, `href` / CTA | **verified live** (§1) |
| Campaign link on posts + filter | **verified live** (§2); campaign filter on copy generations and cross-workspace 422s: unit tests only |
| Campaign list stats, performance series and estimated channel split | **verified live** (§3) |
| One-scope rule, list, pagination, delete, 404 on repeat | **verified live** (§4) |
| Estimated CAC by channel | **verified live** (§4); channel-only `estimated:false`: unit |
| CSV export | **verified live** (§5); formula-injection prefix: unit |
| Media upload / list / attach / detach / delete, rejections, not in Documents list | **verified live** (§6) |
| Media on Cloudinary (https URL) | **not exercised** (no Cloudinary in the harness) |
| 5 MB and 200-image caps | unit tests only |
| 403 for mentor, consultant, investor, accountant, legal advisor on every new route (a and b) | unit tests only (a second invited user is not part of the live journey) |
| "Post published:" copy for a manual publish; campaign launched/completed CTA | unit tests only |
| Scheduler skips deleted workspaces, claims once per (post, time), ignores a late edit | unit tests only |
| **Slice (b)** | |
| Platform table, copy with platform, `lengths` / `over_limit` fields, unknown platform 422 | **verified live** (stub model) (§8) |
| Over-limit and placeholder retry, context in every prompt, limit stated in the prompt | unit tests only (they capture the prompts); **no real-model run** |
| Refine: new linked generation, key message kept, not-ready 422 | **verified live** (stub model) (§9); bad index, foreign id, vanished parent: unit |
| Fit levels, `fit_level` on channels, `stats_30d` | **verified live** (stub model) (§10); unknown level dropped / bare note kept: unit |
| Channel auto-status on launch | **verified live** (§10); never-downgrade, 0% share, pause/resume: unit |
| Weekly readout job, list, notification (founders + team only), once per week | **verified live** (stub model) (§11); over-budget, foreign payload, non-active campaign: unit |
| Readout text quality / real numbers in prose | **not exercised**: needs the real model |
| Keyword `source` / `metrics_updated_at` | **verified live** (§12); "only on change" rule: unit |
| Segment definition validation and cleaning, attributes route | **verified live** (§13); each bad shape: unit |
| One channel per post | **verified live** (§14) |
