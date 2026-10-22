# FE integration guide: Learning Academy (Module 17, backend gaps 1, 2, 4 to 10, 13 to 17, 20, 23)

Everything under `/api/v1/learning/*` plus the public `GET /api/v1/verify/{code}`. It replaces
`fe-integration-guide-learning-recommendations.md` for the shapes below (that guide is kept for history).

**Where the bodies come from.** Every JSON body below is pasted from a real response captured from a running
server by the live journey `e2e/test_learning.py`; the files are committed in `e2e/_captures/learning/`
so any claim can be re-derived. Where a long body is shortened, the line above it says so and shortened text
carries the marker `[TRIMMED IN THIS GUIDE]`. Ids, codes and the user name in the captures belong to a throwaway
run (`Ada Founder` is a placeholder name, `CFZ-...` codes are throwaway). The last section marks what was and was
not verified live.

Base URL `/api/v1`. Every response is the envelope `{"data": ..., "meta": null}` or
`{"error": {"code", "message", "field_errors"}}`. Auth: a bearer token plus the `X-Workspace-Id` header, on
every route except `GET /verify/{code}`.

---

## 1. What the Academy is, in FE terms

A learning hub with **8 founder-stage courses** (3 lessons each), **6 stage paths** and **10 articles**, all
written by us and loaded from `content/academy/` into the database. Lessons are transcript (markdown text) for
now: **no lesson has a video yet** (`video` is `null`). Finishing every lesson of a course earns a
**certificate**: a real PDF, a short public credential ID (`CFZ-XXXX-XXXX-XXXX`), a public verify link and a prefilled
LinkedIn "add to profile" link.

**Who can use it:** founder and team member only (reads included). Every other role gets
`403 FORBIDDEN` on every `/learning/*` route. Progress, notes and certificates are **per person**:
a teammate never sees yours.

### UX instructions (the backend does these things; the user must be told)

1. **Draft marker (required).** Every course, path and article carries `"review_status": "draft"` until Adebayo
   reviews it. Show a visible "Draft" badge wherever you show one of them (card, course page, reader) while the
   value is `"draft"`. Hide the badge when it is `"reviewed"`.
2. **No fake video.** While `lesson.video` is `null`, show the transcript and **no player and no play button**.
   When it is an object, embed `video.embed_url` in an iframe (privacy-enhanced YouTube, `youtube-nocookie.com`).
   To resume, append `?start=<position_s>` to `embed_url`. Never build a player from any other field.
3. **The reason line can change.** The "Learn next" reason starts as a template sentence and is replaced by an
   AI sentence a few seconds later (`hero.reason_source` goes `template` to `ai`). It is also rewritten when the
   founder's stage changes or an Assessment/Validation gap closes. Render whatever string is there, never your own.
4. **Certificates are generated in the background.** Right after the last lesson the certificate exists but
   `pdf_ready` is `false`. Show "Preparing your PDF..." and poll (see section 6). Never show the raw certificate
   `id`; show `credential_id`.
5. **Lessons cannot be un-completed** (`completed: false` is a 422) and can be done in any order.
6. **The holder name on the certificate is frozen** at the moment it is issued (the learner's profile full name at
   that time). Changing the profile name later does not change it. If the profile has no name the certificate says
   `Cofoundaz member`: send people without a name to fill in their profile before they finish a course.
7. **The PDF folds unusual letters.** The PDF font cannot draw letters outside Western European Latin, so a name
   such as `Ọlá` prints as `Ola`. The JSON keeps the real spelling. Do not promise exact glyphs on the PDF.
8. **The verify page is public and must be built by the FE** at `/verify/[code]` (the QR code and LinkedIn
   link point there: `verify_url`). It calls `GET /api/v1/verify/{code}` with no token. Handle 404 (not found) and 429
   (rate limited, 30 per minute per visitor) with plain copy.
9. **Render markdown safely (security).** `transcript`, `description` and article `body` are **raw markdown text
   written by us, not HTML**. Render them with a sanitising markdown renderer with **raw HTML disabled** (no
   `dangerouslySetInnerHTML` on the raw string, no inline HTML pass-through). `holder_name`, `course_title`, titles and
   the note `body` are **plain text only**: render as text, never as HTML or markdown.
10. **LinkedIn:** open `linkedin_url` in a new tab (`target="_blank" rel="noopener"`). It is a link to LinkedIn's own
   add-certification form, prefilled; the user confirms it there. No LinkedIn app or login on our side.

---

## 2. Endpoints at a glance

| Method and path | Purpose | Success |
|---|---|---|
| `GET /learning/recommendations` | "Learn next": hero + reason, the stage shelf, continue watching | 200 |
| `GET /learning/courses` | catalog cards | 200 |
| `GET /learning/courses/{course_id}` | course page: description, outcomes, curriculum | 200 |
| `GET /learning/paths` | stage paths with ordered courses | 200 |
| `GET /learning/articles?q=&tag=` | article index with search and tag filter | 200 |
| `GET /learning/articles/{article_id}` | reader view with related items | 200 |
| `POST /learning/enrollments` | start a course | 201 first time, 200 repeat |
| `PATCH /learning/lessons/{lesson_id}/progress` | mark complete and/or save position | 200 |
| `GET /learning/lessons/{lesson_id}/notes` | read your private note | 200 |
| `PUT /learning/lessons/{lesson_id}/notes` | save your private note | 200 |
| `GET /learning/certificates` | your certificates | 200 |
| `GET /learning/certificates/{id}/pdf` | download your certificate PDF | 200 (`application/pdf`) |
| `GET /verify/{code}` | **public**, no login: check a credential | 200 |

---

## 3. Learn next: `GET /learning/recommendations`

The single source for the Recommended page. `hero` is the first item of `recommended`; its reason is per course
and per workspace. `recommended` excludes courses the caller completed. `continue_watching` is only courses that
were started (progress above 0, or a saved position) and not completed.

### First read, templated reason (validation-stage workspace with no interviews logged)

_Shortened for reading: the real response has 4 items under `data.recommended`; this shows the first 2. Every item has the same shape._

```json
{
  "data": {
    "stage": "validation",
    "recommendation_reason": "You have not logged a customer interview yet, and Talk to customers covers it.",
    "hero": {
      "course_id": "validation-talk-to-customers",
      "reason": "You have not logged a customer interview yet, and Talk to customers covers it.",
      "reason_source": "template",
      "matched_gaps": [
        "customers"
      ]
    },
    "recommended": [
      {
        "id": "validation-talk-to-customers",
        "title": "Talk to customers",
        "summary": "Recruit the right people, run an interview that gets honest answers, and turn notes into decisions.",
        "level": "beginner",
        "stage_tags": [
          "validation"
        ],
        "lesson_count": 3,
        "duration_min": 34,
        "review_status": "draft",
        "enrolled": false,
        "progress": 0,
        "completed": false,
        "last_lesson_id": null,
        "next_lesson_id": "validation-talk-to-customers-1",
        "position_s": 0
      },
      {
        "id": "build-scope-the-mvp",
        "title": "Scope the MVP",
        "summary": "Pick the one core flow, cut the rest, and test demand before you build too much.",
        "level": "intermediate",
        "stage_tags": [
          "validation",
          "build"
        ],
        "lesson_count": 3,
        "duration_min": 30,
        "review_status": "draft",
        "enrolled": false,
        "progress": 0,
        "completed": false,
        "last_lesson_id": null,
        "next_lesson_id": "build-scope-the-mvp-1",
        "position_s": 0
      }
    ],
    "continue_watching": []
  },
  "meta": null
}
```

### After the worker ran: the AI reason

`reason_source` is now `"ai"`. In this capture the model is the offline stub (see the verification table), so the
sentence carries a `[stub-llm]` tag that real model output will not have.

```json
{
  "course_id": "validation-talk-to-customers",
  "reason": "[stub-llm] reason",
  "reason_source": "ai",
  "matched_gaps": [
    "customers"
  ]
}
```

(The rest of that response has the same shape as above; only `recommendation_reason` and `hero.reason` changed.)

### After finishing a course

_Shortened for reading: the real response has 3 items under `data.recommended`; this shows the first 1. Every item has the same shape._

```json
{
  "data": {
    "stage": "validation",
    "recommendation_reason": "[stub-llm] reason",
    "hero": {
      "course_id": "validation-talk-to-customers",
      "reason": "[stub-llm] reason",
      "reason_source": "ai",
      "matched_gaps": [
        "customers"
      ]
    },
    "recommended": [
      {
        "id": "validation-talk-to-customers",
        "title": "Talk to customers",
        "summary": "Recruit the right people, run an interview that gets honest answers, and turn notes into decisions.",
        "level": "beginner",
        "stage_tags": [
          "validation"
        ],
        "lesson_count": 3,
        "duration_min": 34,
        "review_status": "draft",
        "enrolled": false,
        "progress": 0,
        "completed": false,
        "last_lesson_id": null,
        "next_lesson_id": "validation-talk-to-customers-1",
        "position_s": 0
      }
    ],
    "continue_watching": []
  },
  "meta": null
}
```

**Field notes**
- `hero` is `null` when the stage shelf is empty (every course for the stage is completed). Hide the hero block;
  `recommendation_reason` then holds a generic sentence.
- `recommendation_reason` always equals `hero.reason` when there is a hero (kept for older callers).
- `hero.matched_gaps` lists the gap tags the hero covers: `product market money pricing legal team` come from the
  Assessment, `customers demand` from the Validation hub (no interview / no experiment logged yet).
- `stage` is `null` for a workspace with no stage; the shelf is then beginner courses.
- Each course has the **same summary shape everywhere** (shelf, continue watching, `/courses`).

---

## 4. Courses

### `GET /learning/courses`

_Shortened for reading: the real response has 8 items under `data.courses`; this shows the first 2. Every item has the same shape._

```json
{
  "data": {
    "courses": [
      {
        "id": "idea-shape-the-problem",
        "title": "Shape the problem",
        "summary": "Turn a vague idea into a sharp problem statement and check that the pain is worth building for.",
        "level": "beginner",
        "stage_tags": [
          "idea",
          "validation"
        ],
        "lesson_count": 3,
        "duration_min": 30,
        "review_status": "draft",
        "enrolled": false,
        "progress": 0,
        "completed": false,
        "last_lesson_id": null,
        "next_lesson_id": "idea-shape-the-problem-1",
        "position_s": 0
      },
      {
        "id": "validation-talk-to-customers",
        "title": "Talk to customers",
        "summary": "Recruit the right people, run an interview that gets honest answers, and turn notes into decisions.",
        "level": "beginner",
        "stage_tags": [
          "validation"
        ],
        "lesson_count": 3,
        "duration_min": 34,
        "review_status": "draft",
        "enrolled": false,
        "progress": 0,
        "completed": false,
        "last_lesson_id": null,
        "next_lesson_id": "validation-talk-to-customers-1",
        "position_s": 0
      }
    ]
  },
  "meta": null
}
```

**Summary fields to know**
- `progress` is the caller's percent (0 to 100). `enrolled`/`completed` are the caller's.
- `last_lesson_id`: the lesson the caller last touched (completed or paused). `null` if none.
- `next_lesson_id`: where **Continue** opens: the last-touched lesson if it is not done, otherwise the first lesson
  not done. `null` once every lesson is done.
- `position_s`: the saved playback position **of `next_lesson_id`**, in seconds. `0` if none. It is not the
  position of `last_lesson_id`.
- `stage_tags` can hold several stages; show them all (or the caller's stage first).

### `GET /learning/courses/{course_id}`

The course page. Adds `description` (markdown), `outcomes` (3 strings) and the lessons. Lessons carry the full
`transcript` (markdown). Shown here with transcripts shortened.

```json
{
  "id": "build-scope-the-mvp",
  "title": "Scope the MVP",
  "summary": "Pick the one core flow, cut the rest, and test demand before you build too much.",
  "level": "intermediate",
  "stage_tags": [
    "validation",
    "build"
  ],
  "lesson_count": 3,
  "duration_min": 30,
  "review_status": "draft",
  "enrolled": false,
  "progress": 0,
  "completed": false,
  "last_lesson_id": null,
  "next_lesson_id": "build-scope-the-mvp-1",
  "position_s": 0,
  "description": "An MVP is the smallest thing that lets a real customer get the value you promise. It is not a bad version of t [TRIMMED IN THIS GUIDE]",
  "outcomes": [
    "Choose the single flow that delivers your promised value",
    "Cut features with a clear rule instead of a feeling",
    "Check demand with a small test before you commit to the full build"
  ],
  "lessons": [
    {
      "id": "build-scope-the-mvp-1",
      "title": "Pick the one core flow",
      "order": 1,
      "duration_min": 12,
      "video": null,
      "transcript": "Your product promises one outcome. The core flow is the shortest path a customer takes to reach it.\n\n## Write  [TRIMMED IN THIS GUIDE]",
      "completed": false,
      "position_s": 0
    },
    {
      "id": "build-scope-the-mvp-2",
      "title": "Cut everything else",
      "order": 2,
      "duration_min": 10,
      "video": null,
      "transcript": "Founders rarely run out of ideas. They run out of time and money. Cutting is the main skill of scoping.\n\n## Us [TRIMMED IN THIS GUIDE]",
      "completed": false,
      "position_s": 0
    },
    {
      "id": "build-scope-the-mvp-3",
      "title": "Test demand before you build",
      "order": 3,
      "duration_min": 8,
      "video": null,
      "transcript": "Before you commit months to building, run one cheap test to see whether people want the result you promise.\n\n# [TRIMMED IN THIS GUIDE]",
      "completed": false,
      "position_s": 0
    }
  ]
}
```

**Field notes**
- `lessons[].video` is `null` today; see UX instruction 2 for the object shape.
- `lessons[].order` starts at 1. `lessons[].position_s` is that lesson's saved position (0 if none).
- Unknown course: 404

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

### Enrol: `POST /learning/enrollments`

Body `{"course_id": "<id>"}`. **Start course** and **Continue** should call this (idempotent) before opening the
lesson. Enrolling also happens automatically on the first progress write.

First call (HTTP 201):

```json
{
  "data": {
    "course_id": "build-scope-the-mvp",
    "progress": 0,
    "completed_at": null
  },
  "meta": null
}
```

Repeat (HTTP 200, same body):

```json
{
  "data": {
    "course_id": "build-scope-the-mvp",
    "progress": 0,
    "completed_at": null
  },
  "meta": null
}
```

Unknown course (404):

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

Missing field (422):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "course_id",
        "message": "Field required"
      }
    ]
  }
}
```

---

## 5. Lessons: progress and notes

### `PATCH /learning/lessons/{lesson_id}/progress`

Body: `{"completed": true}`, `{"position_s": 95}`, or both. **At least one is required.** `position_s` is whole
seconds, 0 to 86400. `completed` can only be `true`. Lessons can be done in any order (D-ACA-4); **Mark complete**
is the only completion signal for now (a video-end signal can send the same call later).

Save a position only (course stays at 0%, no certificate):

```json
{
  "data": {
    "lesson_id": "build-scope-the-mvp-2",
    "course_id": "build-scope-the-mvp",
    "progress": 0,
    "completed_at": null,
    "position_s": 95,
    "certificate": null
  },
  "meta": null
}
```

Complete a lesson (the second of three, so `progress` is 33):

```json
{
  "data": {
    "lesson_id": "build-scope-the-mvp-2",
    "course_id": "build-scope-the-mvp",
    "progress": 33,
    "completed_at": null,
    "position_s": null,
    "certificate": null
  },
  "meta": null
}
```

Complete the last lesson (HTTP 200): `progress` 100, `completed_at` set and the **new** `certificate`. Note the
nesting: `progress`, `completed_at` and `position_s` are at the top of `data`; the certificate is nested under
`data.certificate`.

```json
{
  "data": {
    "lesson_id": "build-scope-the-mvp-3",
    "course_id": "build-scope-the-mvp",
    "progress": 100,
    "completed_at": "2026-10-22T13:45:07.805976+00:00",
    "position_s": 480,
    "certificate": {
      "id": "8434d809-122b-48a7-8ce0-cb33bd790577",
      "course_id": "build-scope-the-mvp",
      "course_title": "Scope the MVP",
      "holder_name": "Ada Founder",
      "credential_id": "CFZ-YHLS-DYNM-NZYA",
      "issuer": "Cofoundaz",
      "issued_at": "2026-10-22T13:45:07.810980+00:00",
      "verify_url": "http://localhost:3000/verify/CFZ-YHLS-DYNM-NZYA",
      "pdf_ready": false,
      "pdf_url": null,
      "linkedin_url": "https://www.linkedin.com/profile/add?startTask=CERTIFICATION_NAME&name=Scope%20the%20MVP&organizationName=Cofoundaz&issueYear=2026&issueMonth=10&certId=CFZ-YHLS-DYNM-NZYA&certUrl=http%3A%2F%2Flocalhost%3A3000%2Fverify%2FCFZ-YHLS-DYNM-NZYA"
    }
  },
  "meta": null
}
```

Repeating a completion changes nothing and returns `"certificate": null` (the first response is the only one that
carries the new certificate; list `GET /learning/certificates` for it later):

```json
{
  "data": {
    "lesson_id": "build-scope-the-mvp-3",
    "course_id": "build-scope-the-mvp",
    "progress": 100,
    "completed_at": "2026-10-22T13:45:07.805976+00:00",
    "position_s": null,
    "certificate": null
  },
  "meta": null
}
```

Empty body (422):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "",
        "message": "Value error, send completed: true, position_s, or both"
      }
    ]
  }
}
```

`{"completed": false}` is also a 422. Unknown lesson id: 404 (same body as the course 404 above).

### Private notes: `GET` and `PUT /learning/lessons/{lesson_id}/notes`

Notes belong to the caller **per lesson** and are never visible to anyone else, including the founder when the
author is a team member. They are not searchable and never sent to any AI. Body is `{"body": "<text up to 20000
characters>"}`. A lesson with no note returns an empty body and a `null` timestamp (not a 404):

```json
{
  "data": {
    "lesson_id": "build-scope-the-mvp-2",
    "body": "",
    "updated_at": null
  },
  "meta": null
}
```

Saved (PUT returns the same shape as GET):

```json
{
  "data": {
    "lesson_id": "build-scope-the-mvp-2",
    "body": "Ask the pilot customers about WhatsApp.",
    "updated_at": "2026-10-22T13:45:07.706660+00:00"
  },
  "meta": null
}
```

Too long (422):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Please check the highlighted fields.",
    "field_errors": [
      {
        "field": "",
        "message": "String should have at most 20000 characters"
      }
    ]
  }
}
```

Save on a debounce and show "Saved" from the response's `updated_at`.

---

## 6. Certificates

### `GET /learning/certificates`

Right after completion (PDF not yet rendered): `pdf_ready` is `false` and `pdf_url` is `null`.

```json
{
  "data": {
    "certificates": [
      {
        "id": "8434d809-122b-48a7-8ce0-cb33bd790577",
        "course_id": "build-scope-the-mvp",
        "course_title": "Scope the MVP",
        "holder_name": "Ada Founder",
        "credential_id": "CFZ-YHLS-DYNM-NZYA",
        "issuer": "Cofoundaz",
        "issued_at": "2026-10-22T13:45:07.810980+00:00",
        "verify_url": "http://localhost:3000/verify/CFZ-YHLS-DYNM-NZYA",
        "pdf_ready": false,
        "pdf_url": null,
        "linkedin_url": "https://www.linkedin.com/profile/add?startTask=CERTIFICATION_NAME&name=Scope%20the%20MVP&organizationName=Cofoundaz&issueYear=2026&issueMonth=10&certId=CFZ-YHLS-DYNM-NZYA&certUrl=http%3A%2F%2Flocalhost%3A3000%2Fverify%2FCFZ-YHLS-DYNM-NZYA"
      }
    ]
  },
  "meta": null
}
```

After the worker has rendered the PDF (usually a few seconds):

```json
{
  "data": {
    "certificates": [
      {
        "id": "8434d809-122b-48a7-8ce0-cb33bd790577",
        "course_id": "build-scope-the-mvp",
        "course_title": "Scope the MVP",
        "holder_name": "Ada Founder",
        "credential_id": "CFZ-YHLS-DYNM-NZYA",
        "issuer": "Cofoundaz",
        "issued_at": "2026-10-22T13:45:07.810980+00:00",
        "verify_url": "http://localhost:3000/verify/CFZ-YHLS-DYNM-NZYA",
        "pdf_ready": true,
        "pdf_url": "/api/v1/learning/certificates/8434d809-122b-48a7-8ce0-cb33bd790577/pdf",
        "linkedin_url": "https://www.linkedin.com/profile/add?startTask=CERTIFICATION_NAME&name=Scope%20the%20MVP&organizationName=Cofoundaz&issueYear=2026&issueMonth=10&certId=CFZ-YHLS-DYNM-NZYA&certUrl=http%3A%2F%2Flocalhost%3A3000%2Fverify%2FCFZ-YHLS-DYNM-NZYA"
      }
    ]
  },
  "meta": null
}
```

Field notes:
- `credential_id` is the short public code to show and copy. The old `credential_code` field no longer exists.
- `pdf_url` is a path under the API origin; request it **with your bearer token** (it is not a public link).
- `verify_url` is the public FE page the QR code points to. `linkedin_url` is ready to open.
- `issued_at` is UTC ISO 8601.

**Polling** while `pdf_ready` is `false`: call `GET /learning/certificates` every 3 seconds for up to 30 seconds,
then show "Still preparing. Check back soon." (the job retries on its own with backoff). Do not poll while the tab is hidden.

### `GET /learning/certificates/{id}/pdf`

Owner only. Returns `application/pdf` with these headers (captured; the file is committed as
`e2e/_captures/learning/certificate.pdf`):

```json
{
  "status": 200,
  "content-type": "application/pdf",
  "content-disposition": "attachment; filename=\"cofoundaz-certificate-CFZ-YHLS-DYNM-NZYA.pdf\"",
  "cache-control": "private, no-store",
  "bytes": 3857
}
```

Download with `fetch` plus the bearer token and save the blob; a plain `<a href>` cannot send the header.

Before the worker has rendered it (HTTP 409, retry as above):

```json
{
  "error": {
    "code": "CERTIFICATE_NOT_READY",
    "message": "Your certificate is still being prepared. Try again in a moment.",
    "field_errors": []
  }
}
```

A second founder, in their own workspace, asking for your certificate id (HTTP 404, the same body as an unknown
id, so ids cannot be probed):

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

Their workspace id with your token, when you are not a member of it (HTTP 403):

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

No token (HTTP 401):

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Not authenticated.",
    "field_errors": []
  }
}
```

### `GET /verify/{code}` (public, no token)

What the verify page shows. It returns the holder name, course, issue date, issuer and validity, and **nothing
else**. The code is case-insensitive.

```json
{
  "data": {
    "valid": true,
    "holder_name": "Ada Founder",
    "course_title": "Scope the MVP",
    "issued_on": "2026-10-22",
    "issuer": "Cofoundaz"
  },
  "meta": null
}
```

Unknown or malformed codes are the same 404 (so the page cannot tell "never existed" from "mistyped"):

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

Rate limited to 30 requests a minute per visitor (HTTP 429):

```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Too many requests. Slow down a moment.",
    "field_errors": []
  }
}
```

A **revoked** credential answers HTTP 200 with `{"data": {"valid": false}}` and no personal data. There is no
revoke feature in the app yet; this shape is from code and unit tests only (see the verification table).

---

## 7. Paths and articles

### `GET /learning/paths`

Paths are grouped by stage (one per stage). Each path lists its courses **in order** with the caller's progress;
`progress` of the path is the mean of its courses' progress. `next_lesson_id` per course is where **Continue**
opens. Page subtitle copy (PRD): "The curriculum for where you are, not where the internet thinks you are."
(FE-owned copy; the API does not return it.)

_Shortened for reading: the real response has 6 items under `data.paths`; this shows the first 1. Every item has the same shape._

```json
{
  "data": {
    "paths": [
      {
        "id": "path-idea-foundations",
        "title": "Idea foundations",
        "summary": "Sharpen the problem you want to solve, then check it with real people before you build anything.",
        "stage": "idea",
        "review_status": "draft",
        "course_ids": [
          "idea-shape-the-problem",
          "validation-talk-to-customers"
        ],
        "course_count": 2,
        "duration_min": 64,
        "progress": 0,
        "courses": [
          {
            "id": "idea-shape-the-problem",
            "title": "Shape the problem",
            "level": "beginner",
            "duration_min": 30,
            "progress": 0,
            "completed": false,
            "next_lesson_id": "idea-shape-the-problem-1"
          },
          {
            "id": "validation-talk-to-customers",
            "title": "Talk to customers",
            "level": "beginner",
            "duration_min": 34,
            "progress": 0,
            "completed": false,
            "next_lesson_id": "validation-talk-to-customers-1"
          }
        ]
      }
    ]
  },
  "meta": null
}
```

### `GET /learning/articles?q=&tag=`

`q` searches title, summary and body (case-insensitive, max 100 characters); `tag` is an exact tag (lowercase). Both
optional and combinable. The list has no body (use the reader route).

_Shortened for reading: the real response has 10 items under `data.articles`; this shows the first 2. Every item has the same shape._

```json
{
  "data": {
    "articles": [
      {
        "id": "article-interview-questions",
        "title": "Questions that get honest answers",
        "summary": "Seven questions that make people describe what really happened, plus the ones that waste your time.",
        "tags": [
          "validation",
          "customers"
        ],
        "read_min": 1,
        "review_status": "draft"
      },
      {
        "id": "article-pricing-basics",
        "title": "Pricing your first product in naira",
        "summary": "A short method to set a first price: start from the customer's alternative, then test with real money.",
        "tags": [
          "pricing",
          "validation",
          "money"
        ],
        "read_min": 1,
        "review_status": "draft"
      }
    ]
  },
  "meta": null
}
```

Search and tag together (`?q=runway&tag=money`):

```json
{
  "data": {
    "articles": [
      {
        "id": "article-read-your-runway",
        "title": "How to read your runway without a finance degree",
        "summary": "Three figures, one division and a weekly check, so cash never surprises you.",
        "tags": [
          "money",
          "runway",
          "growth"
        ],
        "read_min": 1,
        "review_status": "draft"
      }
    ]
  },
  "meta": null
}
```

### `GET /learning/articles/{article_id}`

The reader. `body` is markdown. `read_min` is computed from the real body. `related` holds up to 3 articles that
share a tag and up to 3 courses that share a tag with the course's stages or gaps. Shown with the body shortened.

```json
{
  "id": "article-pricing-basics",
  "title": "Pricing your first product in naira",
  "summary": "A short method to set a first price: start from the customer's alternative, then test with real money.",
  "tags": [
    "pricing",
    "validation",
    "money"
  ],
  "read_min": 1,
  "review_status": "draft",
  "body": "A first price is a hypothesis, not a promise. You will change it. The goal is to start close enough to learn q [TRIMMED IN THIS GUIDE]",
  "related": {
    "articles": [
      {
        "id": "article-interview-questions",
        "title": "Questions that get honest answers",
        "summary": "Seven questions that make people describe what really happened, plus the ones that waste your time.",
        "tags": [
          "validation",
          "customers"
        ],
        "read_min": 1,
        "review_status": "draft"
      },
      {
        "id": "article-painkiller-or-vitamin",
        "title": "Painkiller or vitamin: a quick test for your idea",
        "summary": "Painkillers solve urgent problems people already spend effort on. Vitamins are nice to have. Check which one y [TRIMMED IN THIS GUIDE]",
        "tags": [
          "idea",
          "validation",
          "product"
        ],
        "read_min": 1,
        "review_status": "draft"
      },
      {
        "id": "article-accepting-first-payments",
        "title": "Accepting your first payments without a big setup",
        "summary": "Start with simple, trusted ways to get paid and add complexity only when volume demands it.",
        "tags": [
          "payments",
          "launch",
          "money"
        ],
        "read_min": 1,
        "review_status": "draft"
      }
    ],
    "courses": [
      {
        "id": "validation-price-your-product",
        "title": "Price your product",
        "summary": "Choose a pricing model, set a first price in naira and test it with real money.",
        "level": "intermediate",
        "duration_min": 29
      },
      {
        "id": "idea-shape-the-problem",
        "title": "Shape the problem",
        "summary": "Turn a vague idea into a sharp problem statement and check that the pain is worth building for.",
        "level": "beginner",
        "duration_min": 30
      },
      {
        "id": "validation-talk-to-customers",
        "title": "Talk to customers",
        "summary": "Recruit the right people, run an interview that gets honest answers, and turn notes into decisions.",
        "level": "beginner",
        "duration_min": 34
      }
    ]
  }
}
```

Unknown article: 404 (same body as the course 404).

---

## 8. Notifications and search

**Notifications** (founder and team member, in-app only). `learning.course.completed` goes to the learner, with
title `Certificate earned: {course}` and body `Download it`: deep link to `/academy/certificates`. A second type,
`learning.course.recommended`, goes to the person who opens the recommendations after their stage changed and a
new hero course appears (title `New course recommended for your stage: {course}`, body `Start course`). The
completed-type item as captured from `GET /notifications`:

```json
{
  "id": "08205740-b2d8-4bef-b4a5-fded09cc01ae",
  "type": "learning.course.completed",
  "title": "Certificate earned: Scope the MVP \ud83c\udf93",
  "body": "Download it",
  "data": {
    "user_id": "228dfe20-6b6a-47fc-82e5-000288f8d8f5",
    "course_id": "build-scope-the-mvp",
    "startup_id": "b55fc383-3f95-4cb0-a4ee-81771190f126",
    "course_title": "Scope the MVP",
    "certificate_id": "8434d809-122b-48a7-8ce0-cb33bd790577"
  },
  "read": false,
  "created_at": "2026-10-22T13:45:07.787938+00:00"
}
```

`learning.course.recommended` was exercised in unit tests only (it needs a stage change); its shape is the same
`type` / `title` / `body` / `data` as above with `data` = `{startup_id, user_id, course_id, course_title, stage}`.

**Global search** (`GET /search?q=`) now returns `course` and `article` results for founder and team member
(never notes). Items of those types as captured:

```json
[
  {
    "type": "article",
    "group": "Learning Academy",
    "id": "article-read-your-runway",
    "title": "How to read your runway without a finance degree",
    "subtitle": "Article",
    "href": "/academy/articles/article-read-your-runway"
  }
]
```

Course results have `type: "course"`, `subtitle: "Course"` and `href: "/academy/courses/{id}"`.

---

## 9. State machines and cadence

| Thing | States | FE action |
|---|---|---|
| Hero reason | `template` then `ai` (`hero.reason_source`) | render the string as is; re-read recommendations once after ~5 s and once more after ~15 s while it is `template`, then stop |
| Certificate PDF | `pdf_ready` false then true | poll `GET /certificates` every 3 s up to 30 s; the PDF route answers 409 until ready |
| Course | `enrolled` false, started (`progress` > 0 or position saved), `completed` true | Start course, Continue (open `next_lesson_id` at `position_s`), Completed with the certificate |
| Lesson | not done then done | disable "Mark complete" once `completed` is true |

---

## 10. Error shapes

| Situation | Status | `error.code` | message |
|---|---|---|---|
| No token | 401 | see capture above | |
| Role is not founder or team member | 403 | `FORBIDDEN` | You don't have permission to do that. |
| Not a member of the workspace in `X-Workspace-Id` | 403 | `FORBIDDEN` | You don't have permission to do that. |
| Unknown course, lesson, article, certificate id, or any other person's certificate | 404 | `NOT_FOUND` | Not found. |
| Bad body (empty progress, note too long, missing `course_id`, `completed: false`) | 422 | `VALIDATION_ERROR` | Please check the highlighted fields. (`field_errors` as captured) |
| PDF asked for before it is rendered | 409 | `CERTIFICATE_NOT_READY` | Your certificate is still being prepared. Try again in a moment. |
| Verify route too often | 429 | `RATE_LIMITED` | Too many requests. Slow down a moment. |

---

## 11. Field-nesting traps

- `progress` is a **percent integer** in the course summary, in `POST /enrollments`, and at the top of the
  `PATCH /progress` response. It is never nested.
- In the `PATCH` response the new certificate is nested at `data.certificate`; it is `null` on any call that did
  not take the course to 100%, including repeats. Do not look for it there later; list certificates instead.
- `position_s` is on the **course summary** (the position of `next_lesson_id`), on each **lesson** (its own position),
  and on the **PATCH response** (the value you just sent, `null` if you sent none). Three places, three meanings.
- `video` is `null` or an object, never a string and never absent. `hero` is `null` or an object.
- `review_status` is on courses, paths and articles (list and detail), not on lessons.
- `credential_id` (certificates) versus the removed `credential_code`.
- `data.certificates[].pdf_url` is `null` until ready; do not construct the URL yourself.

---

## 12. Verification table

| Behaviour | Verified |
|---|---|
| Every endpoint above returns the bodies shown, with these status codes | **Live** (real uvicorn server, scratch Postgres, in-process worker drain, `e2e/test_learning.py`; captures committed) |
| Hero, matched gaps, template then `ai` reason | **Live** with the offline **stub** model. **Not verified live with the real model**: the real sentence text is unseen; the schema is strict-JSON valid (unit test) |
| Certificate PDF content and download | **Live** (a real PDF was fetched over HTTP and is committed as `certificate.pdf`; its layout was checked by rasterising it to an image; the QR code was **not** scanned with a phone) |
| Owner-only PDF (second founder gets 404 or 403, no token 401) | **Live** |
| Teammate and mentor cannot read the owner's PDF; notes isolated between users | **Unit and API tests only** (needs invited members, not driven live in this journey) |
| Public verify (valid, unknown, malformed, 429) | **Live** |
| Verify for a revoked credential | **Unit tests only** (no revoke feature yet) |
| LinkedIn link | URL shape **built to LinkedIn's documented add-to-profile parameters**; **the LinkedIn dialog was not opened** (no account in the build) |
| Notification `learning.course.completed` and the inbox row | **Live** (captured from `GET /notifications`) |
| Notification `learning.course.recommended` | **Unit tests only** |
| Global search returns courses and articles | **Live** |
| Role gate (mentor, consultant and others get 403 on the new routes) | **Unit tests only** for the new routes; the existing routes were walked live in the 2026-10-21 walk |
| Video object for a lesson with a YouTube video | **Unit tests only** (no video exists in the content yet) |
| Resume position across reload | **Live** (saved, then `next_lesson_id` and `position_s` read back) |
