# FE Integration Guide — Documents library core (BE-B)

Every response body below is pasted **verbatim** from `e2e/test_documents_library.py` running against a
real server (live captures in `e2e/_captures/documents_library/*.json`, one file per body cited).
IDs, timestamps and tokens are values from that ephemeral run: the **shapes are exact**, the values are
placeholders (tokens appear as `REDACTED_TOKEN`; emails are Resend sink addresses). Anything not exercised
live is marked **NOT LIVE** with where its shape comes from (§12 lists them all).

Base path `/api/v1`. Auth: Bearer access token + `X-Workspace-Id` header, **except** the grantee routes in §8
(Bearer only, no workspace header) and the public token routes. Success = `{"data": …, "meta": null}`;
errors = `{"error": {"code", "message", "field_errors"}}`.

> **Role rule (unchanged from BE-A, D-DOC-1).** Every `/documents*` route in §1–§7 and §9 is **founder +
> team_member only**, reads included. Any other role, **even one holding a grant**, gets
> `403 FORBIDDEN` there. A grant only opens the four `/documents/shared-with-me*` routes (§8).
> Live proof: a mentor holding a grant is refused on `/documents`:
> `granted_mentor_documents_403.json` — HTTP 403

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```
> and a granted account that is not a workspace member is refused the same way when it sends the workspace id:
> `granted_non_member_documents_403.json` — HTTP 403

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

## 0. What changed for the FE (read first)

| Area | Change | Why you care |
|---|---|---|
| `DELETE /documents/{id}` and `DELETE /documents/files/{id}` | **Soft delete.** Same `200 {"deleted": true}` plus `trashed_at`; the item moves to the Trash. | Add a confirm + a Trash view + Restore. Nothing is gone for 30 days. |
| Document and file rows | New fields `owner`, `tags`, `share_count`, `mime`, `trashed_at`, `purges_at` | Owner column, tag chips, "Private / Shared · n" chip. |
| `PUT /documents/{id}` | Still full replace + `version`; now writes a history row | Drives the version drawer. |
| `GET /documents`, `GET /documents/files` | New `q`, `tag` query params | Server-side search; drop the client-side filter. |
| `GET /shared/{token}` | Returns **nothing** for a trashed document (uniform 404) | The "link is no longer valid" state already exists. |
| Notifications | New copy, `data.href`, two new types | Bell can deep-link. |

**Field-nesting traps**
- `owner` is `{id, name}` (or `null` if the creator was deleted); file rows use the same shape for the uploader.
- File rows have **no `url`** (BE-A). They also carry `mime` (= `content_type`) and always `share_count: 0` (files cannot be shared yet).
- Names (`owner.name`, `shared_by.name`, comment authors) are the profile name, falling back to the **local part of the email** when the user has none (as in the captures); never null for a known user.
- A document row's `tags` is always an array (never null). `folder` is `null` for "uncategorised" — a path like `"Fundraising/Seed"` otherwise.
- `trashed_at` / `purges_at` are `null` on every live row; both are set on Trash rows.
- The grantee payload (§8) is **not** the owner summary: no `folder`, `tags`, `share_count`, `owner`.

## 1. Summary fields

`document_create.json` — HTTP 201

```json
{
  "data": {
    "id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
    "kind": "custom",
    "mime": "application/vnd.cofoundaz.document+json",
    "title": "Seed pitch narrative",
    "status": "draft",
    "ai_generated": false,
    "folder": "Fundraising/Seed",
    "tags": [
      "fundraising",
      "Pitch"
    ],
    "template_key": null,
    "version": 1,
    "owner": {
      "id": "1694eaad-0851-40a7-8c58-576c80cced79",
      "name": "Ada Founder"
    },
    "share_count": 0,
    "updated_at": "2026-10-25T11:02:18.165146+00:00",
    "trashed_at": null,
    "purges_at": null,
    "sections": [
      {
        "id": "b5ae0963-295b-4472-8fdb-095d0bdabf7d",
        "body": "Founders lose track of paperwork",
        "heading": "Problem"
      }
    ]
  },
  "meta": null
}
```

`share_count` = active token links + active grants (including grants still pending on an email that has no account yet). `mime` for documents is the constant `application/vnd.cofoundaz.document+json`; uploaded files use their real content type. File row (upload):

`file_upload.json` — HTTP 201

```json
{
  "data": {
    "id": "199e1cb9-db1a-4762-b437-aaf02127834b",
    "filename": "term-sheet.pdf",
    "content_type": "application/pdf",
    "mime": "application/pdf",
    "size_bytes": 19,
    "folder": "Fundraising",
    "tags": [],
    "owner": {
      "id": "1694eaad-0851-40a7-8c58-576c80cced79",
      "name": "Ada Founder"
    },
    "share_count": 0,
    "scan_status": "clean",
    "uploaded_at": "2026-10-25T11:02:18.441478+00:00",
    "trashed_at": null,
    "purges_at": null
  },
  "meta": null
}
```

## 2. Version history

A snapshot is written on create (v1), on every `PUT` save, on restore, and when the AI regenerates a business-plan section. **Documents that existed before this release have exactly one snapshot: their version at migration time**; older saves were overwritten and cannot be recovered (show the drawer with a single entry for them, note text `"Version at the time version history was introduced"`).

`PUT` (the same contract as before) — a second save by a team member returns:

`document_save_v2.json` — HTTP 200

```json
{
  "data": {
    "id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
    "kind": "custom",
    "mime": "application/vnd.cofoundaz.document+json",
    "title": "Seed pitch narrative",
    "status": "draft",
    "ai_generated": false,
    "folder": "Fundraising/Seed",
    "tags": [
      "fundraising",
      "Pitch"
    ],
    "template_key": null,
    "version": 2,
    "owner": {
      "id": "1694eaad-0851-40a7-8c58-576c80cced79",
      "name": "Ada Founder"
    },
    "share_count": 0,
    "updated_at": "2026-10-25T11:02:18.190775+00:00",
    "trashed_at": null,
    "purges_at": null,
    "sections": [
      {
        "id": "ad7b3bf3-a82a-41b8-b041-778c9234408f",
        "body": "Founders lose track of paperwork and deadlines",
        "heading": "Problem"
      }
    ]
  },
  "meta": null
}
```

A stale save still conflicts, and writes no history row:

`document_save_stale_409.json` — HTTP 409

```json
{
  "error": {
    "code": "DOCUMENT_VERSION_CONFLICT",
    "message": "This document was changed elsewhere. Reload and reapply your edits.",
    "field_errors": []
  }
}
```

`GET /documents/{id}/versions` — newest first, **no content** in the list (open one to read it). `author.name` can be `null` (e.g. AI-written versions have `author: null`):

`versions_list.json` — HTTP 200

```json
{
  "data": {
    "versions": [
      {
        "version": 3,
        "title": "Seed pitch (draft 3)",
        "status": "draft",
        "note": null,
        "author": {
          "id": "1694eaad-0851-40a7-8c58-576c80cced79",
          "name": "Ada Founder"
        },
        "created_at": "2026-10-25T11:02:18.214580+00:00",
        "is_current": true
      },
      {
        "version": 2,
        "title": "Seed pitch narrative",
        "status": "draft",
        "note": null,
        "author": {
          "id": "6c8367ea-6352-4e35-acca-82c17587dfc7",
          "name": null
        },
        "created_at": "2026-10-25T11:02:18.190775+00:00",
        "is_current": false
      },
      {
        "version": 1,
        "title": "Seed pitch narrative",
        "status": "draft",
        "note": "Created",
        "author": {
          "id": "1694eaad-0851-40a7-8c58-576c80cced79",
          "name": "Ada Founder"
        },
        "created_at": "2026-10-25T11:02:18.165146+00:00",
        "is_current": false
      }
    ],
    "current": 3
  },
  "meta": null
}
```

`GET /documents/{id}/versions/{n}` — adds `sections` (use it for Preview and for a text diff client-side):

`version_get_1.json` — HTTP 200

```json
{
  "data": {
    "version": 1,
    "title": "Seed pitch narrative",
    "status": "draft",
    "note": "Created",
    "author": {
      "id": "1694eaad-0851-40a7-8c58-576c80cced79",
      "name": "Ada Founder"
    },
    "created_at": "2026-10-25T11:02:18.165146+00:00",
    "is_current": false,
    "sections": [
      {
        "id": "b5ae0963-295b-4472-8fdb-095d0bdabf7d",
        "body": "Founders lose track of paperwork",
        "heading": "Problem"
      }
    ]
  },
  "meta": null
}
```

Unknown version → uniform 404:

`version_get_missing_404.json` — HTTP 404

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

`POST /documents/{id}/versions/{n}/restore` — makes that content current **as a new version** (history is never rewritten) and returns the full document (note `version` went up):

`version_restore.json` — HTTP 200

```json
{
  "data": {
    "id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
    "kind": "custom",
    "mime": "application/vnd.cofoundaz.document+json",
    "title": "Seed pitch narrative",
    "status": "draft",
    "ai_generated": false,
    "folder": "Fundraising/Seed",
    "tags": [
      "fundraising",
      "Pitch"
    ],
    "template_key": null,
    "version": 4,
    "owner": {
      "id": "1694eaad-0851-40a7-8c58-576c80cced79",
      "name": "Ada Founder"
    },
    "share_count": 0,
    "updated_at": "2026-10-25T11:02:18.258768+00:00",
    "trashed_at": null,
    "purges_at": null,
    "sections": [
      {
        "id": "b5ae0963-295b-4472-8fdb-095d0bdabf7d",
        "body": "Founders lose track of paperwork",
        "heading": "Problem"
      }
    ]
  },
  "meta": null
}
```

**UX instruction:** show the PRD confirm copy before restoring, and tell the user the restore creates a new version rather than discarding the later ones.

## 3. Rename / move / tag / status — `PATCH /documents/{id}`, `PATCH /documents/files/{id}`

Only fields you send change; `folder: null` moves the item out of its folder. **No new version** is created (the content is untouched). Document body: `{title?, folder?, tags?, status?}`; file body: `{filename?, folder?, tags?}`.

`document_patch.json` — HTTP 200

```json
{
  "data": {
    "id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
    "kind": "custom",
    "mime": "application/vnd.cofoundaz.document+json",
    "title": "Seed pitch narrative v2",
    "status": "final",
    "ai_generated": false,
    "folder": "Fundraising",
    "tags": [
      "Pitch",
      "seed"
    ],
    "template_key": null,
    "version": 4,
    "owner": {
      "id": "1694eaad-0851-40a7-8c58-576c80cced79",
      "name": "Ada Founder"
    },
    "share_count": 0,
    "updated_at": "2026-10-25T11:02:18.285772+00:00",
    "trashed_at": null,
    "purges_at": null,
    "sections": [
      {
        "id": "b5ae0963-295b-4472-8fdb-095d0bdabf7d",
        "body": "Founders lose track of paperwork",
        "heading": "Problem"
      }
    ]
  },
  "meta": null
}
```

Validation (blank title, name > 200 chars, > 10 tags, tag > 30 chars, folder segment empty / > 60 chars / deeper than 4 levels / path > 120):

`document_patch_422.json` — HTTP 422

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "A document needs a name.",
    "field_errors": [
      {
        "field": "title",
        "message": "A document needs a name."
      }
    ]
  }
}
```

File (note the stored content type never changes; a path in `filename` is stripped to its last segment — **verified in unit tests**, see §12):

`file_patch.json` — HTTP 200

```json
{
  "data": {
    "id": "199e1cb9-db1a-4762-b437-aaf02127834b",
    "filename": "Term sheet (signed).pdf",
    "content_type": "application/pdf",
    "mime": "application/pdf",
    "size_bytes": 19,
    "folder": "Contracts",
    "tags": [
      "legal"
    ],
    "owner": {
      "id": "1694eaad-0851-40a7-8c58-576c80cced79",
      "name": "Ada Founder"
    },
    "share_count": 0,
    "scan_status": "clean",
    "uploaded_at": "2026-10-25T11:02:18.441478+00:00",
    "trashed_at": null,
    "purges_at": null
  },
  "meta": null
}
```

Download = the BE-A signed link `GET /documents/files/{id}/download` (unchanged).

## 4. Inline viewer — `GET /documents/files/{id}/content`

Returns a 5-minute signed link that opens the file **inline**; `viewer` tells you which renderer to use (`pdf`, `image`, `text`, `markdown`, `csv`, `docx`, `xlsx`, `pptx`). Founder/team member only, `clean` files only (409 `FILE_NOT_CLEAN` otherwise), 404 for a trashed or foreign file.

`file_content_link.json` — HTTP 200

```json
{
  "data": {
    "url": "http://127.0.0.1:8031/api/v1/documents/files/content?token=REDACTED_TOKEN",
    "expires_at": "2026-10-25T11:07:18+00:00",
    "content_type": "application/pdf",
    "filename": "Term sheet (signed).pdf",
    "viewer": "pdf"
  },
  "meta": null
}
```

Fetching `url` (no auth header) returns the bytes with `Content-Disposition: inline`, `X-Content-Type-Options: nosniff` and a sandbox CSP (asserted live). With a remote storage backend (Cloudinary) the URL is the provider's signed URL instead: **NOT LIVE** (local storage only in the harness). Rendering docx/xlsx/pptx is the FE's job (PDF.js, an HTML conversion); the API serves the raw file.

## 5. Search and filters

`GET /documents?q=…&tag=…` (also `kind`, `folder`, `status` as before); `GET /documents/files?q=…&tag=…&folder=…`. Every whitespace-separated term in `q` must match (case-insensitive substring) in the title, the tags or — documents only — the body text; files match name and tags. Substring, so half-typed words work. The owner is **not** searched. Blank `q` = no filter. `tag` is an exact tag, case-insensitive. File **contents** are not searched (NOT built).

`documents_search.json` — HTTP 200

```json
{
  "data": {
    "documents": [
      {
        "id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
        "kind": "custom",
        "mime": "application/vnd.cofoundaz.document+json",
        "title": "Seed pitch narrative v2",
        "status": "final",
        "ai_generated": false,
        "folder": "Fundraising",
        "tags": [
          "Pitch",
          "seed"
        ],
        "template_key": null,
        "version": 4,
        "owner": {
          "id": "1694eaad-0851-40a7-8c58-576c80cced79",
          "name": "Ada Founder"
        },
        "share_count": 0,
        "updated_at": "2026-10-25T11:02:18.285772+00:00",
        "trashed_at": null,
        "purges_at": null
      }
    ]
  },
  "meta": null
}
```

`files_search.json` — HTTP 200

```json
{
  "data": {
    "files": [
      {
        "id": "199e1cb9-db1a-4762-b437-aaf02127834b",
        "filename": "Term sheet (signed).pdf",
        "content_type": "application/pdf",
        "mime": "application/pdf",
        "size_bytes": 19,
        "folder": "Contracts",
        "tags": [
          "legal"
        ],
        "owner": {
          "id": "1694eaad-0851-40a7-8c58-576c80cced79",
          "name": "Ada Founder"
        },
        "share_count": 0,
        "scan_status": "clean",
        "uploaded_at": "2026-10-25T11:02:18.441478+00:00",
        "trashed_at": null,
        "purges_at": null
      }
    ]
  },
  "meta": null
}
```

## 6. Folders

`folder` on items is the folder's full path (`"Legal/NDAs"`), so existing `?folder=` filters and fields are unchanged. Using a new path on an item creates the folder chain automatically. Routes: `GET /documents/folders`, `POST /documents/folders {name, parent_id?}`, `PATCH /documents/folders/{id} {name}` (renames the whole subtree **and every document/file inside it**), `DELETE /documents/folders/{id}` (empty folders only), `POST /documents/folders/standard` (Corporate, Financials, Legal, Fundraising, Marketing, Archive; idempotent). All folder mutations return the full tree.

Tree (counts exclude trashed items; `total_count` = the folder's subtree; `uncategorized` = items with no folder; `trash` = trashed items):

`folders_tree.json` — HTTP 200

```json
{
  "data": {
    "folders": [
      {
        "id": "444ba652-eed0-4b4e-9483-739e3262206e",
        "name": "Fundraising",
        "path": "Fundraising",
        "parent_id": null,
        "document_count": 1,
        "file_count": 0,
        "total_count": 1
      },
      {
        "id": "6c548203-d273-4d37-9b57-cf33561709ec",
        "name": "Seed",
        "path": "Fundraising/Seed",
        "parent_id": "444ba652-eed0-4b4e-9483-739e3262206e",
        "document_count": 0,
        "file_count": 0,
        "total_count": 0
      },
      {
        "id": "65b0702f-4418-46cd-9bea-8b021973915f",
        "name": "Legal",
        "path": "Legal",
        "parent_id": null,
        "document_count": 0,
        "file_count": 0,
        "total_count": 0
      }
    ],
    "uncategorized": 0,
    "trash": 0,
    "total": 1
  },
  "meta": null
}
```

Create / rename / duplicate / not-empty:

`folder_create.json` — HTTP 201

```json
{
  "data": {
    "folders": [
      {
        "id": "444ba652-eed0-4b4e-9483-739e3262206e",
        "name": "Fundraising",
        "path": "Fundraising",
        "parent_id": null,
        "document_count": 1,
        "file_count": 0,
        "total_count": 1
      },
      {
        "id": "6c548203-d273-4d37-9b57-cf33561709ec",
        "name": "Seed",
        "path": "Fundraising/Seed",
        "parent_id": "444ba652-eed0-4b4e-9483-739e3262206e",
        "document_count": 0,
        "file_count": 0,
        "total_count": 0
      },
      {
        "id": "65b0702f-4418-46cd-9bea-8b021973915f",
        "name": "Legal",
        "path": "Legal",
        "parent_id": null,
        "document_count": 0,
        "file_count": 0,
        "total_count": 0
      }
    ],
    "uncategorized": 0,
    "trash": 0,
    "total": 1
  },
  "meta": null
}
```

`folder_rename.json` — HTTP 200

```json
{
  "data": {
    "folders": [
      {
        "id": "65b0702f-4418-46cd-9bea-8b021973915f",
        "name": "Contracts",
        "path": "Contracts",
        "parent_id": null,
        "document_count": 0,
        "file_count": 0,
        "total_count": 0
      },
      {
        "id": "444ba652-eed0-4b4e-9483-739e3262206e",
        "name": "Fundraising",
        "path": "Fundraising",
        "parent_id": null,
        "document_count": 1,
        "file_count": 0,
        "total_count": 1
      },
      {
        "id": "6c548203-d273-4d37-9b57-cf33561709ec",
        "name": "Seed",
        "path": "Fundraising/Seed",
        "parent_id": "444ba652-eed0-4b4e-9483-739e3262206e",
        "document_count": 0,
        "file_count": 0,
        "total_count": 0
      }
    ],
    "uncategorized": 0,
    "trash": 0,
    "total": 1
  },
  "meta": null
}
```

`folder_create_duplicate_409.json` — HTTP 409

```json
{
  "error": {
    "code": "FOLDER_EXISTS",
    "message": "A folder with that name already exists here.",
    "field_errors": []
  }
}
```

`folder_delete_not_empty_409.json` — HTTP 409

```json
{
  "error": {
    "code": "FOLDER_NOT_EMPTY",
    "message": "This folder still has documents or files in it.",
    "field_errors": []
  }
}
```

`folders_standard.json` — HTTP 201

```json
{
  "data": {
    "folders": [
      {
        "id": "892c9d71-7d52-447a-a3ca-6da69c335451",
        "name": "Archive",
        "path": "Archive",
        "parent_id": null,
        "document_count": 0,
        "file_count": 0,
        "total_count": 0
      },
      {
        "id": "65b0702f-4418-46cd-9bea-8b021973915f",
        "name": "Contracts",
        "path": "Contracts",
        "parent_id": null,
        "document_count": 0,
        "file_count": 0,
        "total_count": 0
      },
      {
        "id": "2a2922a2-a75d-4236-9894-cfd9526701cb",
        "name": "Corporate",
        "path": "Corporate",
        "parent_id": null,
        "document_count": 0,
        "file_count": 0,
        "total_count": 0
      },
      {
        "id": "415e599f-0198-4786-8fb2-43a6e646b7a7",
        "name": "Financials",
        "path": "Financials",
        "parent_id": null,
        "document_count": 0,
        "file_count": 0,
        "total_count": 0
      },
      {
        "id": "444ba652-eed0-4b4e-9483-739e3262206e",
        "name": "Fundraising",
        "path": "Fundraising",
        "parent_id": null,
        "document_count": 1,
        "file_count": 0,
        "total_count": 1
      },
      {
        "id": "6c548203-d273-4d37-9b57-cf33561709ec",
        "name": "Seed",
        "path": "Fundraising/Seed",
        "parent_id": "444ba652-eed0-4b4e-9483-739e3262206e",
        "document_count": 0,
        "file_count": 0,
        "total_count": 0
      },
      {
        "id": "b7d7b820-d472-4b63-837a-fb28222c000e",
        "name": "Legal",
        "path": "Legal",
        "parent_id": null,
        "document_count": 0,
        "file_count": 0,
        "total_count": 0
      },
      {
        "id": "6c5b0534-cfc4-4f86-a212-a4e58ac25408",
        "name": "Marketing",
        "path": "Marketing",
        "parent_id": null,
        "document_count": 0,
        "file_count": 0,
        "total_count": 0
      }
    ],
    "uncategorized": 0,
    "trash": 0,
    "total": 1
  },
  "meta": null
}
```

## 7. Trash

`GET /documents/trash` → `{documents: […], files: […]}` (newest trashed first). `purges_at` = `trashed_at` + 30 days.

`document_trash.json` — HTTP 200

```json
{
  "data": {
    "deleted": true,
    "trashed_at": "2026-10-25T11:02:19.615240+00:00"
  },
  "meta": null
}
```

`trash_list.json` — HTTP 200

```json
{
  "data": {
    "documents": [
      {
        "id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
        "kind": "custom",
        "mime": "application/vnd.cofoundaz.document+json",
        "title": "Seed pitch narrative v2",
        "status": "draft",
        "ai_generated": false,
        "folder": "Fundraising/Seed",
        "tags": [
          "Pitch",
          "seed"
        ],
        "template_key": null,
        "version": 5,
        "owner": {
          "id": "1694eaad-0851-40a7-8c58-576c80cced79",
          "name": "Ada Founder"
        },
        "share_count": 3,
        "updated_at": "2026-10-25T11:02:19.611916+00:00",
        "trashed_at": "2026-10-25T11:02:19.615240+00:00",
        "purges_at": "2026-11-24T11:02:19.615240+00:00"
      }
    ],
    "files": []
  },
  "meta": null
}
```

`POST /documents/{id}/restore` (also `POST /documents/files/{id}/restore`) brings it back **with all its grants and token links working again**. `DELETE /documents/{id}/permanent` (and `/files/{id}/permanent`) empties one item now, only if it is already trashed:

`document_restore.json` — HTTP 200

```json
{
  "data": {
    "id": "a8b89e08-f669-403e-a9ce-63ab77a4ebd6",
    "kind": "custom",
    "mime": "application/vnd.cofoundaz.document+json",
    "title": "Live, not trashed",
    "status": "draft",
    "ai_generated": false,
    "folder": null,
    "tags": [],
    "template_key": null,
    "version": 1,
    "owner": {
      "id": "1694eaad-0851-40a7-8c58-576c80cced79",
      "name": "Ada Founder"
    },
    "share_count": 1,
    "updated_at": "2026-10-25T11:02:19.787351+00:00",
    "trashed_at": null,
    "purges_at": null,
    "sections": []
  },
  "meta": null
}
```

`document_permanent_not_in_trash_409.json` — HTTP 409

```json
{
  "error": {
    "code": "NOT_IN_TRASH",
    "message": "Move it to the trash first.",
    "field_errors": []
  }
}
```

Files:

`file_trash.json` — HTTP 200

```json
{
  "data": {
    "deleted": true,
    "trashed_at": "2026-10-25T11:02:19.853016+00:00"
  },
  "meta": null
}
```

`trash_list_with_file.json` — HTTP 200

```json
{
  "data": {
    "documents": [],
    "files": [
      {
        "id": "199e1cb9-db1a-4762-b437-aaf02127834b",
        "filename": "Term sheet (signed).pdf",
        "content_type": "application/pdf",
        "mime": "application/pdf",
        "size_bytes": 19,
        "folder": "Contracts",
        "tags": [
          "legal"
        ],
        "owner": {
          "id": "1694eaad-0851-40a7-8c58-576c80cced79",
          "name": "Ada Founder"
        },
        "share_count": 0,
        "scan_status": "clean",
        "uploaded_at": "2026-10-25T11:02:18.441478+00:00",
        "trashed_at": "2026-10-25T11:02:19.853016+00:00",
        "purges_at": "2026-11-24T11:02:19.853016+00:00"
      }
    ]
  },
  "meta": null
}
```

`file_restore.json` — HTTP 200

```json
{
  "data": {
    "id": "199e1cb9-db1a-4762-b437-aaf02127834b",
    "filename": "Term sheet (signed).pdf",
    "content_type": "application/pdf",
    "mime": "application/pdf",
    "size_bytes": 19,
    "folder": "Contracts",
    "tags": [
      "legal"
    ],
    "owner": {
      "id": "1694eaad-0851-40a7-8c58-576c80cced79",
      "name": "Ada Founder"
    },
    "share_count": 0,
    "scan_status": "clean",
    "uploaded_at": "2026-10-25T11:02:18.441478+00:00",
    "trashed_at": null,
    "purges_at": null
  },
  "meta": null
}
```

Rules the FE must surface: a trashed document disappears for **everyone** — grantees (§8) and anyone holding a token link get a 404 — until it is restored; a file that has any signature request **cannot be trashed** (409 `FILE_HAS_SIGNATURE_REQUESTS`, unchanged from BE-A); while an item is trashed, restoring it recreates its folder if that was deleted meanwhile. **The 30-day purge runs daily in the worker and was NOT waited out live** (unit-tested, §12): after purge, restore and permanent routes return 404.

Token link to a trashed document:

`shared_token_trashed_404.json` — HTTP 404

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

## 8. Per-person access (D-DOC-1): owner side

A grant gives one person **view** or **comment** on one rich document (files cannot be granted yet; "Can edit" is not offered — `permission: "edit"` is a 422). The grantee is either a **workspace member** (`user_id`) or an **email** (`email`, case-insensitive). The token link ("anyone with the link") is separate and unchanged.

`POST /documents/{id}/grants` body `{permission: "view"|"comment", user_id?}` **or** `{…, email?}` (exactly one). Founder/team member only; rate limited (30/hour).

By member:

`grant_create_member.json` — HTTP 201

```json
{
  "data": {
    "id": "ae4a0e49-cb9e-4f6a-ae0a-849c6796e48e",
    "document_id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
    "grantee": {
      "user_id": "ba27af70-124a-4361-addf-019441a5decf",
      "email": "delivered+mentor-20c440e5f20a@resend.dev",
      "name": "delivered+mentor-20c440e5f20a"
    },
    "permission": "view",
    "status": "active",
    "granted_by": "1694eaad-0851-40a7-8c58-576c80cced79",
    "created_at": "2026-10-25T11:02:18.500602+00:00",
    "revoked_at": null
  },
  "meta": null
}
```

By email for someone with no account yet. `status` is `"pending"` for **every grantee who is not a workspace member** — including an email that already matches a platform account — so the response cannot be used to probe which addresses have accounts. The person is told by email (best effort) and sees the document the first time they open Shared with me after verifying that email:

`grant_create_email_pending.json` — HTTP 201

```json
{
  "data": {
    "id": "5517d5b4-973d-4929-8eae-f0f24f33bd3b",
    "document_id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
    "grantee": {
      "user_id": null,
      "email": "delivered+grantee-24b280ca35dc@resend.dev",
      "name": null
    },
    "permission": "comment",
    "status": "pending",
    "granted_by": "6c8367ea-6352-4e35-acca-82c17587dfc7",
    "created_at": "2026-10-25T11:02:18.565766+00:00",
    "revoked_at": null
  },
  "meta": null
}
```

Errors: both/neither of `user_id`/`email` → 422; `user_id` not in the workspace → 422 ("Share by email instead"); a founder/team member as grantee → 422; an invalid email → 422; an existing live grant for that person → 409:

`grant_duplicate_409.json` — HTTP 409

```json
{
  "error": {
    "code": "GRANT_EXISTS",
    "message": "This person already has access to the document.",
    "field_errors": []
  }
}
```

`grant_full_access_member_422.json` — HTTP 422

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "That person is a founder or team member and already has full access.",
    "field_errors": []
  }
}
```

`GET /documents/{id}/grants` (live grants; `?include_revoked=true` adds history), `GET /documents/grants` (workspace-wide, for the "Shared with others" page), `PATCH /documents/{id}/grants/{grant_id} {permission}`, `DELETE /documents/{id}/grants/{grant_id}` (idempotent). These also work while the document is in the trash, so access can be revoked from the Trash.

`grants_list.json` — HTTP 200

```json
{
  "data": {
    "grants": [
      {
        "id": "5517d5b4-973d-4929-8eae-f0f24f33bd3b",
        "document_id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
        "grantee": {
          "user_id": null,
          "email": "delivered+grantee-24b280ca35dc@resend.dev",
          "name": null
        },
        "permission": "comment",
        "status": "pending",
        "granted_by": "6c8367ea-6352-4e35-acca-82c17587dfc7",
        "created_at": "2026-10-25T11:02:18.565766+00:00",
        "revoked_at": null
      },
      {
        "id": "ae4a0e49-cb9e-4f6a-ae0a-849c6796e48e",
        "document_id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
        "grantee": {
          "user_id": "ba27af70-124a-4361-addf-019441a5decf",
          "email": "delivered+mentor-20c440e5f20a@resend.dev",
          "name": "delivered+mentor-20c440e5f20a"
        },
        "permission": "view",
        "status": "active",
        "granted_by": "1694eaad-0851-40a7-8c58-576c80cced79",
        "created_at": "2026-10-25T11:02:18.500602+00:00",
        "revoked_at": null
      }
    ]
  },
  "meta": null
}
```

`grant_update_permission.json` — HTTP 200

```json
{
  "data": {
    "id": "ae4a0e49-cb9e-4f6a-ae0a-849c6796e48e",
    "document_id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
    "grantee": {
      "user_id": "ba27af70-124a-4361-addf-019441a5decf",
      "email": "delivered+mentor-20c440e5f20a@resend.dev",
      "name": "delivered+mentor-20c440e5f20a"
    },
    "permission": "comment",
    "status": "active",
    "granted_by": "1694eaad-0851-40a7-8c58-576c80cced79",
    "created_at": "2026-10-25T11:02:18.500602+00:00",
    "revoked_at": null
  },
  "meta": null
}
```

`grant_revoke.json` — HTTP 200

```json
{
  "data": {
    "revoked": true
  },
  "meta": null
}
```

`grants_list_with_revoked.json` — HTTP 200

```json
{
  "data": {
    "grants": [
      {
        "id": "995b4ed8-14d3-45f8-a50b-98d07635535e",
        "document_id": "a8b89e08-f669-403e-a9ce-63ab77a4ebd6",
        "grantee": {
          "user_id": "ba27af70-124a-4361-addf-019441a5decf",
          "email": "delivered+mentor-20c440e5f20a@resend.dev",
          "name": "delivered+mentor-20c440e5f20a"
        },
        "permission": "view",
        "status": "revoked",
        "granted_by": "1694eaad-0851-40a7-8c58-576c80cced79",
        "created_at": "2026-10-25T11:02:19.747527+00:00",
        "revoked_at": "2026-10-25T11:02:19.826016+00:00"
      }
    ]
  },
  "meta": null
}
```

Owner-side comments: `GET/POST /documents/{id}/comments` body `{body}` (1–5000 chars, not blank):

`document_comments_owner.json` — HTTP 200

```json
{
  "data": {
    "comments": [
      {
        "id": "b58db5c1-9c70-4b12-a484-84c39285a944",
        "entity_type": "document",
        "entity_id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
        "anchor": null,
        "author": {
          "id": "ba27af70-124a-4361-addf-019441a5decf",
          "name": null
        },
        "body": "Tighten the problem statement",
        "mention_user_ids": [],
        "created_at": "2026-10-25T11:02:18.785517+00:00",
        "edited_at": null,
        "resolved_at": null,
        "is_mine": false
      }
    ]
  },
  "meta": null
}
```

## 9. Shared with me (grantee side)

Available to **any signed-in, email-verified account, any role, no `X-Workspace-Id`**. Access exists only through a live grant bound to the caller; a revoked grant, someone else's grant, a document that was never granted, and a **trashed** document are all the same `404 NOT_FOUND` (never 403) so nothing can be probed. An unverified account gets `403 EMAIL_NOT_VERIFIED` (unit-tested).

Email grants **bind** to an account only when its email is **verified**: at verification, or on the next call to any route below (so "after sign-up/login" is covered). Live: the grant to a not-yet-existing address, then sign-up, verify, login, then this call:

`shared_with_me_after_signup_binding.json` — HTTP 200

```json
{
  "data": {
    "documents": [
      {
        "id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
        "title": "Seed pitch narrative v2",
        "kind": "custom",
        "status": "final",
        "version": 4,
        "updated_at": "2026-10-25T11:02:18.285772+00:00",
        "permission": "comment",
        "workspace": {
          "id": "cd502d97-1ba7-40cc-8663-f618febbc678",
          "name": "LibraryCo"
        },
        "shared_by": {
          "id": "6c8367ea-6352-4e35-acca-82c17587dfc7",
          "name": "delivered+team_member-3aa561ddd6ca"
        },
        "shared_at": "2026-10-25T11:02:18.565766+00:00"
      }
    ]
  },
  "meta": null
}
```

A member holding a view grant (list, then the document with content; no `folder`/`tags`):

`shared_with_me_list.json` — HTTP 200

```json
{
  "data": {
    "documents": [
      {
        "id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
        "title": "Seed pitch narrative v2",
        "kind": "custom",
        "status": "final",
        "version": 4,
        "updated_at": "2026-10-25T11:02:18.285772+00:00",
        "permission": "view",
        "workspace": {
          "id": "cd502d97-1ba7-40cc-8663-f618febbc678",
          "name": "LibraryCo"
        },
        "shared_by": {
          "id": "1694eaad-0851-40a7-8c58-576c80cced79",
          "name": "Ada Founder"
        },
        "shared_at": "2026-10-25T11:02:18.500602+00:00"
      }
    ]
  },
  "meta": null
}
```

`shared_with_me_get.json` — HTTP 200

```json
{
  "data": {
    "id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
    "title": "Seed pitch narrative v2",
    "kind": "custom",
    "status": "final",
    "version": 4,
    "updated_at": "2026-10-25T11:02:18.285772+00:00",
    "permission": "view",
    "workspace": {
      "id": "cd502d97-1ba7-40cc-8663-f618febbc678",
      "name": "LibraryCo"
    },
    "shared_by": {
      "id": "1694eaad-0851-40a7-8c58-576c80cced79",
      "name": "Ada Founder"
    },
    "shared_at": "2026-10-25T11:02:18.500602+00:00",
    "sections": [
      {
        "id": "b5ae0963-295b-4472-8fdb-095d0bdabf7d",
        "body": "Founders lose track of paperwork",
        "heading": "Problem"
      }
    ]
  },
  "meta": null
}
```

A member with no grant sees an empty list, and an ungranted id is a 404:

`shared_with_me_empty.json` — HTTP 200

```json
{
  "data": {
    "documents": []
  },
  "meta": null
}
```

`shared_with_me_get_not_granted_404.json` — HTTP 404

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

After revoke, and while trashed:

`shared_with_me_get_revoked_404.json` — HTTP 404

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

`shared_with_me_get_trashed_404.json` — HTTP 404

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Not found.",
    "field_errors": []
  }
}
```

Comments (`GET/POST /documents/shared-with-me/{id}/comments`): reading needs any live grant; posting needs `permission: "comment"` (else `403 FORBIDDEN`). Grantees see authors as `{name}` only (no internal ids) and no `mention_user_ids`:

`shared_comment_view_only_403.json` — HTTP 403

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "You don't have permission to do that.",
    "field_errors": []
  }
}
```

`shared_comment_create.json` — HTTP 201

```json
{
  "data": {
    "id": "b58db5c1-9c70-4b12-a484-84c39285a944",
    "entity_type": "document",
    "entity_id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
    "anchor": null,
    "body": "Tighten the problem statement",
    "created_at": "2026-10-25T11:02:18.785517+00:00",
    "edited_at": null,
    "resolved_at": null,
    "is_mine": true,
    "author": {
      "name": null
    }
  },
  "meta": null
}
```

**FE instructions:** show "Shared with me" to every role that has the Documents module in the nav, even though `/documents` itself is F/TM-only; for mentors/advisors/investors/accountants make it the Documents landing page; render a view-only grantee without the comment box. The document payload has no edit capability — render it read-only.

## 10. Notifications

New/changed (`GET /notifications` shapes are unchanged; `data` is the event payload, now with `href`). Mentor inbox (a member with a grant): new version of a granted document, and being granted access:

`mentor_notifications.json` — HTTP 200

```json
[
  {
    "type": "document.version.created",
    "title": "New version uploaded: Seed pitch narrative v2 (v5) by Ada Founder",
    "body": "Open document",
    "data": {
      "href": "/documents/c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
      "title": "Seed pitch narrative v2",
      "version": 5,
      "actor_id": "1694eaad-0851-40a7-8c58-576c80cced79",
      "owner_id": "1694eaad-0851-40a7-8c58-576c80cced79",
      "startup_id": "cd502d97-1ba7-40cc-8663-f618febbc678",
      "author_name": "Ada Founder",
      "document_id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6"
    }
  },
  {
    "type": "document.grant.created",
    "title": "Ada Founder shared Seed pitch narrative v2 with you",
    "body": "Open document",
    "data": {
      "href": "/documents/shared-with-me",
      "title": "Seed pitch narrative v2",
      "actor_id": "1694eaad-0851-40a7-8c58-576c80cced79",
      "grant_id": "ae4a0e49-cb9e-4f6a-ae0a-849c6796e48e",
      "permission": "view",
      "startup_id": "cd502d97-1ba7-40cc-8663-f618febbc678",
      "document_id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
      "sharer_name": "Ada Founder",
      "grantee_user_id": "ba27af70-124a-4361-addf-019441a5decf"
    }
  }
]
```

Founder inbox (team member saved a version; shares/signatures use the new copy "…shared a document: {title}", "{signer} signed {title}", "{title} is fully signed"):

`founder_notifications.json` — HTTP 200

```json
[
  {
    "type": "document.version.created",
    "title": "New version uploaded: Seed pitch narrative (v2) by delivered+team_member-3aa561ddd6ca",
    "body": "Open document",
    "data": {
      "href": "/documents/c1f3192a-aca9-4bcf-88ff-27e8ca511ed6",
      "title": "Seed pitch narrative",
      "version": 2,
      "actor_id": "6c8367ea-6352-4e35-acca-82c17587dfc7",
      "owner_id": "1694eaad-0851-40a7-8c58-576c80cced79",
      "startup_id": "cd502d97-1ba7-40cc-8663-f618febbc678",
      "author_name": "delivered+team_member-3aa561ddd6ca",
      "document_id": "c1f3192a-aca9-4bcf-88ff-27e8ca511ed6"
    }
  }
]
```

- `document.version.created` → owner (or the founders) and members holding a live grant, minus whoever saved. Not sent for the first version. Link to `data.href`.
- `document.grant.created` → the grantee **only if they are a member of the workspace** (an outside grantee has no workspace inbox; they get the email). Link `data.href` = `/documents/shared-with-me`.
- `document.signature.signed` / `.completed` and `document.shared` now link to `/documents/signatures` and `/documents/{id}`.

## 11. Error and edge summary

| Case | Response |
|---|---|
| Non-F/TM role on any owner route (even with a grant) | `403 FORBIDDEN` |
| Trashed/foreign document or file on any live route | `404 NOT_FOUND` |
| Permanent delete of a live item | `409 NOT_IN_TRASH` |
| Folder name exists / folder not empty | `409 FOLDER_EXISTS` / `409 FOLDER_NOT_EMPTY` |
| Grant exists for that person | `409 GRANT_EXISTS` |
| Trashing a file with a signature request | `409 FILE_HAS_SIGNATURE_REQUESTS` |
| Validation (names, tags, folder paths, grants, blank comment) | `422 VALIDATION_ERROR` |
| `PUT` with a stale `version` | `409 DOCUMENT_VERSION_CONFLICT` |

## 12. Verification table

| Behaviour | Status |
|---|---|
| Create/summary fields, save v2, stale 409, version list/get/restore/404 | ✅ live |
| PATCH document (+422), PATCH file, inline content link + fetch headers, search (docs and files), tag filter | ✅ live |
| Folders: tree, create, duplicate 409, rename (+items re-pointed), not-empty 409, standard | ✅ live |
| Trash: document + file trash/list/restore/permanent, not-in-trash 409, token link 404 while trashed | ✅ live |
| Grants: member, email-pending, duplicate 409, member-as-grantee 422, list, update, revoke, history | ✅ live |
| Shared with me: list, get, empty, not-granted 404, revoked 404, trashed 404, email binding after sign-up, comment 403/201, owner reads thread | ✅ live |
| Grantee + non-member refused on `/documents` | ✅ live |
| Notifications `document.grant.created`, `document.version.created` for a member grantee; founder `document.version.created` | ✅ live |
| **30-day purge job** (documents, files, objects, grants, versions, comments) and its daily scheduling | ⚠️ NOT LIVE (cannot wait 30 days): `tests/worker/test_documents_trash_purge.py` |
| Remote storage inline URL (Cloudinary) | ⚠️ NOT LIVE: provider-signed URL, shape = BE-A download link |
| `auth.user.verified` event binding (vs lazy bind on first call) | ⚠️ unit: `tests/api/test_documents_grants.py` (live run binds lazily) |
| Unverified account never binds; `EMAIL_NOT_VERIFIED` on shared-with-me | ⚠️ unit only |
| Filename path stripping, tag wildcard escaping, grants rate limit (30/hour) | ⚠️ unit only (rate limit: not exercised) |
| View-only grantee blocked on files; permission matrix for every role | ⚠️ unit: `tests/api/test_documents_access.py` (every route × every role) |
| Notification to the owner when someone else shares / signs (new copy) | ⚠️ unit: `tests/api/test_documents_notifications_be_b.py` |
