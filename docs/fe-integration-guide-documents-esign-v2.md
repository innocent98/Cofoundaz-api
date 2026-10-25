# FE integration guide — Documents e-sign v2 and link shares (BE-D)

Covers gap-list rows 15, 16, 17, 31, 37. Supersedes the e-sign and share parts of
`fe-integration-guide-documents-esignature.md` and `fe-integration-guide-documents-sharing.md` where they differ
(those two are still right for everything not listed here).

**Every body below is pasted verbatim from a live run** of `e2e/test_documents_esign_v2.py` against a running server
(captures in `e2e/_captures/documents_esign_v2/`, one file per body). Raw tokens in links were replaced with
`REDACTED_TOKEN` before the capture was written. Emails like `delivered+...@resend.dev` are the test sink, not real
people. The last section lists what was *not* exercised live.

Base URL `/api/v1`. Envelope: success `{"data": ..., "meta": null}`, failure `{"error": {"code", "message", "field_errors"}}`.
Auth: Documents routes need a bearer token + `X-Workspace-Id` and a **founder or team member** role (anyone else: 403, see
`mentor_request_detail_403` below). The `/sign/{token}` and `/shared/{token}` routes are public: the token is the credential.

## 1. What it is, in the FE's terms

A founder sends a **PDF** to one or more signers. Each signer opens an emailed link (no account), types their name and signs.
When the last one signs, the server builds an **executed PDF** (the original pages unchanged, then a signature page with every
typed signature and an audit certificate: signers, emails, viewed/signed times, IP, browser, the PDF's SHA-256, and a legal
caveat), stores it as a private file in the library and emails it, attached, to **every signer and to the sender**.

New in v2: a **signing order** switch, a **message** to the signers, a **Viewed** state, a full **request detail** (sent date, expiry,
per-signer email/status/times/IP/browser), **copy a signer's link again**, the executed PDF, and **"anyone with the link"** shares
with a **custom expiry** for documents *and* uploaded files.

**Field placement on the PDF is not built** (decision for this slice, see the SOP): signatures are stamped on the page appended to the
PDF, not at a position on a page. The UI should not offer "drag a signature field".

## 2. Which files can be signed

Only a **clean PDF** that can be parsed. Everything else is refused *when the request is created*, so the founder learns before anyone is emailed.

| File | Result |
|---|---|
| PDF upload or a rich document exported as PDF (`POST /documents/{id}/export {"format":"pdf"}`) | signable |
| DOCX upload or a document exported as DOCX | `422 PDF_REQUIRED`: "Export the document to PDF first." |
| Any other type (image, xlsx, csv, md, txt) | `422 PDF_REQUIRED` |
| A PDF that is encrypted, damaged, 0 pages or > 300 pages | `422 PDF_UNREADABLE` |
| A file whose scan is not `clean` | `409 FILE_NOT_CLEAN` (unchanged) |

```json
{
  "status": 422,
  "body": {
    "error": {
      "code": "PDF_REQUIRED",
      "message": "Only PDF files can be sent for signature. Export the document to PDF first.",
      "field_errors": []
    }
  }
}
```

```json
{
  "status": 422,
  "body": {
    "error": {
      "code": "PDF_UNREADABLE",
      "message": "This PDF can't be signed: it is encrypted, damaged or too long. Upload a plain PDF.",
      "field_errors": []
    }
  }
}
```

**UX:** in the send dialog, list only PDFs (or show other types disabled with "Export to PDF first" and a button to `POST /documents/{id}/export`).

## 3. Send a request: `POST /documents/files/{file_id}/signature-requests`

Body (all optional except `signers`): `signers[]` (`email` required, `name`), `title`, `expires_in_days` (1 to 365, default 14; `null` = never expires; anything else is a 422), `sequential` (default false),
`message` (≤ 2000 chars; blank = default sentence). At most 20 signers. Limit 30/hour per caller.

Verified sequential example (first signer released, second waiting; `signer_links` lines up with `signers`, **`null` = not released yet**):

```json
{
  "status": 201,
  "body": {
    "data": {
      "id": "2b81176a-edce-4db2-9f1f-bae2a087aa3b",
      "title": "Founders agreement",
      "status": "awaiting",
      "file_id": "b151cc1c-a92e-49d8-9c3e-ac10856bfa9f",
      "filename": "founders-agreement.pdf",
      "signed_count": 0,
      "total": 2,
      "sequential": true,
      "message": "Hi both, please sign before Friday.\nThanks, Ada",
      "sent_at": "2026-10-25T17:18:02.933745+00:00",
      "expires_at": "2026-11-08T17:18:02.941259+00:00",
      "completed_at": null,
      "created_at": "2026-10-25T17:18:02.933745+00:00",
      "document_hash": "a1f361e64b738f2c2946da5dadaab49a4d5adb6fc53255ff6bc4c2fac4ecf627",
      "executed": null,
      "legal_caveat": "These are simple electronic signatures: each signer typed their name to adopt it as their signature. Cofoundaz records the signer's email, IP address, browser, the time and the document's SHA-256 hash, but does not issue a certificate-based or qualified signature and is not a trust service provider. Whether a typed-name signature is enforceable depends on the document and the jurisdiction; take legal advice for high-stakes agreements.",
      "signers": [
        {
          "id": "96a4d6d5-8160-47c7-9932-55478f326a27",
          "email": "delivered+signer-one-c685d7cb984b@resend.dev",
          "name": "Ada Investor",
          "position": 0,
          "status": "pending",
          "notified_at": "2026-10-25T17:18:02.941259+00:00",
          "viewed_at": null,
          "signed_at": null,
          "signed_name": null,
          "signed_ip": null,
          "signed_user_agent": null,
          "link_copies": [
            {
              "at": "2026-10-25T17:18:02.950734+00:00",
              "via": "created",
              "name": "Ada Founder",
              "email": "delivered+e2e-101791a33a21@resend.dev",
              "user_id": "74d8ddad-0d8b-404d-ac03-5f23280cf490"
            }
          ]
        },
        {
          "id": "b6ffa449-261f-40e3-ab4e-a92ea14ddb16",
          "email": "delivered+signer-two-df114f6f8386@resend.dev",
          "name": "Bello Legal",
          "position": 1,
          "status": "waiting",
          "notified_at": null,
          "viewed_at": null,
          "signed_at": null,
          "signed_name": null,
          "signed_ip": null,
          "signed_user_agent": null,
          "link_copies": []
        }
      ],
      "signer_links": [
        "http://localhost:3000/sign/REDACTED_TOKEN",
        null
      ]
    },
    "meta": null
  }
}
```

What the first signer's email looks like (the second signer got nothing yet):

```json
{
  "to": "delivered+signer-one-c685d7cb984b@resend.dev",
  "subject": "Signature requested: Founders agreement",
  "html": "<p>Hi both, please sign before Friday.<br>Thanks, Ada</p><p><a href=\"http://localhost:3000/sign/REDACTED_TOKEN\">Review and sign</a></p><p style='font-size:12px;color:#666'>These are simple electronic signatures: each signer typed their name to adopt it as their signature. Cofoundaz records the signer&#x27;s email, IP address, browser, the time and the document&#x27;s SHA-256 hash, but does not issue a certificate-based or qualified signature and is not a trust service provider. Whether a typed-name signature is enforceable depends on the document and the jurisdiction; take legal advice for high-stakes agreements.</p>",
  "sent_at": "2026-10-25T17:18:02.947850+00:00"
}
```

Parallel (default) with the default message, signing an exported PDF:

```json
{
  "status": 201,
  "body": {
    "data": {
      "id": "1a82277d-d3e8-4c64-a34d-566e4361001f",
      "title": "One-Pager.pdf",
      "status": "awaiting",
      "file_id": "fec4505b-defd-48fd-a32e-ae294a15733b",
      "filename": "One-Pager.pdf",
      "signed_count": 0,
      "total": 1,
      "sequential": false,
      "message": "Ada Founder has sent you One-Pager.pdf to sign via Cofoundaz.",
      "sent_at": "2026-10-25T17:18:04.603750+00:00",
      "expires_at": "2026-11-08T17:18:04.609156+00:00",
      "completed_at": null,
      "created_at": "2026-10-25T17:18:04.603750+00:00",
      "document_hash": "a19e96d6cd23969f1420d14fee4c370b4b5c1aebec0dfed8e8bcce6fc72d89c2",
      "executed": null,
      "legal_caveat": "These are simple electronic signatures: each signer typed their name to adopt it as their signature. Cofoundaz records the signer's email, IP address, browser, the time and the document's SHA-256 hash, but does not issue a certificate-based or qualified signature and is not a trust service provider. Whether a typed-name signature is enforceable depends on the document and the jurisdiction; take legal advice for high-stakes agreements.",
      "signers": [
        {
          "id": "d0bb5d4a-8350-4d0c-bff2-75d0e111b72c",
          "email": "delivered+signer-three-99a4b1d0989f@resend.dev",
          "name": "Cleo",
          "position": 0,
          "status": "pending",
          "notified_at": "2026-10-25T17:18:04.609156+00:00",
          "viewed_at": null,
          "signed_at": null,
          "signed_name": null,
          "signed_ip": null,
          "signed_user_agent": null,
          "link_copies": [
            {
              "at": "2026-10-25T17:18:04.614000+00:00",
              "via": "created",
              "name": "Ada Founder",
              "email": "delivered+e2e-101791a33a21@resend.dev",
              "user_id": "74d8ddad-0d8b-404d-ac03-5f23280cf490"
            }
          ]
        }
      ],
      "signer_links": [
        "http://localhost:3000/sign/REDACTED_TOKEN"
      ]
    },
    "meta": null
  }
}
```

Out-of-range expiry (`0`, negative, or above 365):

```json
{
  "status": 422,
  "body": {
    "error": {
      "code": "VALIDATION_ERROR",
      "message": "Please check the highlighted fields.",
      "field_errors": [
        {
          "field": "expires_in_days",
          "message": "Input should be less than or equal to 365"
        }
      ]
    }
  }
}
```

Validation error shape for a bad signer email (one plain address only; CR/LF, commas, angle brackets are rejected):

```json
{
  "status": 422,
  "body": {
    "error": {
      "code": "VALIDATION_ERROR",
      "message": "Please check the highlighted fields.",
      "field_errors": [
        {
          "field": "signers.0.email",
          "message": "Value error, Enter a valid email address."
        }
      ]
    }
  }
}
```

### The message
- `message` is stored as plain text and shown to the signer on the sign page and in the email body. Control characters are stripped (a newline is kept).
- When blank, the server stores and returns the default: `"{Founder name} has sent you {title} to sign via Cofoundaz."`.
- It never reaches an email header. Render it as **text** (not HTML) everywhere.

## 4. Request detail and list (editors): `GET /documents/signature-requests/{id}`, `GET /documents/signature-requests`

Both return the same object. After the first signer opened their link (state **viewed**) and the second is **waiting**:

```json
{
  "status": 200,
  "body": {
    "data": {
      "id": "2b81176a-edce-4db2-9f1f-bae2a087aa3b",
      "title": "Founders agreement",
      "status": "awaiting",
      "file_id": "b151cc1c-a92e-49d8-9c3e-ac10856bfa9f",
      "filename": "founders-agreement.pdf",
      "signed_count": 0,
      "total": 2,
      "sequential": true,
      "message": "Hi both, please sign before Friday.\nThanks, Ada",
      "sent_at": "2026-10-25T17:18:02.933745+00:00",
      "expires_at": "2026-11-08T17:18:02.941259+00:00",
      "completed_at": null,
      "created_at": "2026-10-25T17:18:02.933745+00:00",
      "document_hash": "a1f361e64b738f2c2946da5dadaab49a4d5adb6fc53255ff6bc4c2fac4ecf627",
      "executed": null,
      "legal_caveat": "These are simple electronic signatures: each signer typed their name to adopt it as their signature. Cofoundaz records the signer's email, IP address, browser, the time and the document's SHA-256 hash, but does not issue a certificate-based or qualified signature and is not a trust service provider. Whether a typed-name signature is enforceable depends on the document and the jurisdiction; take legal advice for high-stakes agreements.",
      "signers": [
        {
          "id": "96a4d6d5-8160-47c7-9932-55478f326a27",
          "email": "delivered+signer-one-c685d7cb984b@resend.dev",
          "name": "Ada Investor",
          "position": 0,
          "status": "viewed",
          "notified_at": "2026-10-25T17:18:02.941259+00:00",
          "viewed_at": "2026-10-25T17:18:02.996890+00:00",
          "signed_at": null,
          "signed_name": null,
          "signed_ip": null,
          "signed_user_agent": null,
          "link_copies": [
            {
              "at": "2026-10-25T17:18:02.950734+00:00",
              "via": "created",
              "name": "Ada Founder",
              "email": "delivered+e2e-101791a33a21@resend.dev",
              "user_id": "74d8ddad-0d8b-404d-ac03-5f23280cf490"
            }
          ]
        },
        {
          "id": "b6ffa449-261f-40e3-ab4e-a92ea14ddb16",
          "email": "delivered+signer-two-df114f6f8386@resend.dev",
          "name": "Bello Legal",
          "position": 1,
          "status": "waiting",
          "notified_at": null,
          "viewed_at": null,
          "signed_at": null,
          "signed_name": null,
          "signed_ip": null,
          "signed_user_agent": null,
          "link_copies": []
        }
      ]
    },
    "meta": null
  }
}
```

Signer `status` values: `waiting` (sequential, not their turn; no link exists), `pending` (link sent, not opened), `viewed` (opened, not signed), `signed`.
Request `status`: `awaiting`, `complete`, `cancelled`, `expired` (derived). `executed` is `null` until the request completes; `{"state": "legacy", ...}` for a request that completed before e-sign v2 (no executed copy exists or will be built; hide the download).

The list (`{"data": {"requests": [...]}}`):

```json
{
  "status": 200,
  "body": {
    "data": {
      "requests": [
        {
          "id": "1a82277d-d3e8-4c64-a34d-566e4361001f",
          "title": "One-Pager.pdf",
          "status": "complete",
          "file_id": "fec4505b-defd-48fd-a32e-ae294a15733b",
          "filename": "One-Pager.pdf",
          "signed_count": 1,
          "total": 1,
          "sequential": false,
          "message": "Ada Founder has sent you One-Pager.pdf to sign via Cofoundaz.",
          "sent_at": "2026-10-25T17:18:04.603750+00:00",
          "expires_at": "2026-11-08T17:18:04.609156+00:00",
          "completed_at": "2026-10-25T17:18:04.644824+00:00",
          "created_at": "2026-10-25T17:18:04.603750+00:00",
          "document_hash": null,
          "executed": {
            "state": "legacy",
            "file_id": null,
            "filename": null,
            "size_bytes": null
          },
          "legal_caveat": "These are simple electronic signatures: each signer typed their name to adopt it as their signature. Cofoundaz records the signer's email, IP address, browser, the time and the document's SHA-256 hash, but does not issue a certificate-based or qualified signature and is not a trust service provider. Whether a typed-name signature is enforceable depends on the document and the jurisdiction; take legal advice for high-stakes agreements.",
          "signers": [
            {
              "id": "d0bb5d4a-8350-4d0c-bff2-75d0e111b72c",
              "email": "delivered+signer-three-99a4b1d0989f@resend.dev",
              "name": "Cleo",
              "position": 0,
              "status": "signed",
              "notified_at": "2026-10-25T17:18:04.625607+00:00",
              "viewed_at": "2026-10-25T17:18:04.644824+00:00",
              "signed_at": "2026-10-25T17:18:04.644824+00:00",
              "signed_name": "Cleo",
              "signed_ip": "127.0.0.1",
              "signed_user_agent": "python-httpx/0.28.1",
              "link_copies": [
                {
                  "at": "2026-10-25T17:18:04.614000+00:00",
                  "via": "created",
                  "name": "Ada Founder",
                  "email": "delivered+e2e-101791a33a21@resend.dev",
                  "user_id": "74d8ddad-0d8b-404d-ac03-5f23280cf490"
                },
                {
                  "at": "2026-10-25T17:18:04.637541+00:00",
                  "via": "copied",
                  "name": "Ada Founder",
                  "email": "delivered+e2e-101791a33a21@resend.dev",
                  "user_id": "74d8ddad-0d8b-404d-ac03-5f23280cf490"
                }
              ]
            }
          ]
        },
        {
          "id": "2b81176a-edce-4db2-9f1f-bae2a087aa3b",
          "title": "Founders agreement",
          "status": "complete",
          "file_id": "b151cc1c-a92e-49d8-9c3e-ac10856bfa9f",
          "filename": "founders-agreement.pdf",
          "signed_count": 2,
          "total": 2,
          "sequential": true,
          "message": "Hi both, please sign before Friday.\nThanks, Ada",
          "sent_at": "2026-10-25T17:18:02.933745+00:00",
          "expires_at": "2026-11-08T17:18:02.941259+00:00",
          "completed_at": "2026-10-25T17:18:03.068049+00:00",
          "created_at": "2026-10-25T17:18:02.933745+00:00",
          "document_hash": "a1f361e64b738f2c2946da5dadaab49a4d5adb6fc53255ff6bc4c2fac4ecf627",
          "executed": {
            "state": "ready",
            "file_id": "597e5401-6cde-4846-8730-a461a9934868",
            "filename": "founders-agreement - signed.pdf",
            "size_bytes": 4669,
            "sha256": "29b4b7b106221ada2844adef83cf6d9b9501b9221e9e29814a44685d1e2ad1bb"
          },
          "legal_caveat": "These are simple electronic signatures: each signer typed their name to adopt it as their signature. Cofoundaz records the signer's email, IP address, browser, the time and the document's SHA-256 hash, but does not issue a certificate-based or qualified signature and is not a trust service provider. Whether a typed-name signature is enforceable depends on the document and the jurisdiction; take legal advice for high-stakes agreements.",
          "signers": [
            {
              "id": "96a4d6d5-8160-47c7-9932-55478f326a27",
              "email": "delivered+signer-one-c685d7cb984b@resend.dev",
              "name": "Ada Investor",
              "position": 0,
              "status": "signed",
              "notified_at": "2026-10-25T17:18:02.941259+00:00",
              "viewed_at": "2026-10-25T17:18:02.996890+00:00",
              "signed_at": "2026-10-25T17:18:03.035412+00:00",
              "signed_name": "Ada Investor",
              "signed_ip": "127.0.0.1",
              "signed_user_agent": "e2e-browser/1.0 (signer one)",
              "link_copies": [
                {
                  "at": "2026-10-25T17:18:02.950734+00:00",
                  "via": "created",
                  "name": "Ada Founder",
                  "email": "delivered+e2e-101791a33a21@resend.dev",
                  "user_id": "74d8ddad-0d8b-404d-ac03-5f23280cf490"
                },
                {
                  "at": "2026-10-25T17:18:03.020131+00:00",
                  "via": "copied",
                  "name": "Ada Founder",
                  "email": "delivered+e2e-101791a33a21@resend.dev",
                  "user_id": "74d8ddad-0d8b-404d-ac03-5f23280cf490"
                }
              ]
            },
            {
              "id": "b6ffa449-261f-40e3-ab4e-a92ea14ddb16",
              "email": "delivered+signer-two-df114f6f8386@resend.dev",
              "name": "Bello Legal",
              "position": 1,
              "status": "signed",
              "notified_at": "2026-10-25T17:18:03.047259+00:00",
              "viewed_at": "2026-10-25T17:18:03.068049+00:00",
              "signed_at": "2026-10-25T17:18:03.068049+00:00",
              "signed_name": "Bello Legal",
              "signed_ip": "127.0.0.1",
              "signed_user_agent": "e2e-browser/1.0 (signer two)",
              "link_copies": []
            }
          ]
        }
      ]
    },
    "meta": null
  }
}
```

### Copy a signer's link again: `GET /documents/signature-requests/{id}/signers/{signer_id}/link` (editors)

Returns the signer's current link. Nothing changes; the link in the signer's inbox keeps working. Limit 60/hour.

```json
{
  "status": 200,
  "body": {
    "data": {
      "signer_id": "96a4d6d5-8160-47c7-9932-55478f326a27",
      "link": "http://localhost:3000/sign/REDACTED_TOKEN"
    },
    "meta": null
  }
}
```

**Link audit (new).** An editor holding a live signer link could sign as that person, so every time a link is handed to a workspace member it is recorded:
once per released signer in the create response (`via: "created"`) and on every call of this route (`via: "copied"`). `signers[].link_copies` (editor objects, see the detail above)
lists `{user_id, name, email, at, via}`, and the audit certificate prints "Link handed out" for each signer, so the record shows the link left the signer's inbox.
**UX:** show it next to the signer ("Link copied by Ada on 25 Oct 17:42") and warn before copying: "Anyone with this link can sign as {signer}. The copy is recorded."

A signer who got their link before this release (a random token) cannot be re-copied: `409 LINK_NOT_RECOVERABLE`; send a reminder (new link, copyable afterwards).

Refused with `409` when the signer already signed, the request is complete/cancelled/expired (`SIGNATURE_NOT_ACTIVE`), or the signer is waiting their turn:

```json
{
  "status": 409,
  "body": {
    "error": {
      "code": "SIGNER_NOT_RELEASED",
      "message": "This signer's turn hasn't come yet: their link is released when the signer before them signs.",
      "field_errors": []
    }
  }
}
```

`POST /documents/signature-requests/{id}/remind` now emails **released, unsigned** signers a *new* link and invalidates their old one (a copied older link
stops working: copy again afterwards). A waiting sequential signer is skipped. Verified response:

```json
{
  "status": 200,
  "body": {
    "data": {
      "reminded": 1
    },
    "meta": null
  }
}
```

`POST .../cancel` is unchanged.

### Roles
A mentor (or any non-F/TM role) gets 403 on all of the above:

```json
{
  "status": 403,
  "body": {
    "error": {
      "code": "FORBIDDEN",
      "message": "You don't have permission to do that.",
      "field_errors": []
    }
  }
}
```

## 5. The public signer pages

### `GET /sign/{token}` — opening the link marks the signer **Viewed** (first open only)

```json
{
  "status": 200,
  "body": {
    "data": {
      "request": {
        "title": "Founders agreement",
        "status": "awaiting",
        "message": "Hi both, please sign before Friday.\nThanks, Ada",
        "sent_by": "Ada Founder",
        "sequential": true,
        "expires_at": "2026-11-08T17:18:02.941259+00:00"
      },
      "file": {
        "filename": "founders-agreement.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1371,
        "download_url": "http://127.0.0.1:8610/api/v1/sign/REDACTED_TOKEN/file"
      },
      "signer": {
        "email": "delivered+signer-one-c685d7cb984b@resend.dev",
        "name": "Ada Investor"
      },
      "legal_caveat": "These are simple electronic signatures: each signer typed their name to adopt it as their signature. Cofoundaz records the signer's email, IP address, browser, the time and the document's SHA-256 hash, but does not issue a certificate-based or qualified signature and is not a trust service provider. Whether a typed-name signature is enforceable depends on the document and the jurisdiction; take legal advice for high-stakes agreements."
    },
    "meta": null
  }
}
```

Notes: `request.message`, `request.sent_by`, `request.sequential`, `request.expires_at` and `legal_caveat` are new. Show the caveat text on the sign page next to the consent
checkbox. The page still gets no co-signer list. `file.download_url` serves the PDF only while the token is active (`GET /sign/{token}/file`).

A token that is unknown, already used, expired, cancelled, for a request that completed, for a signer whose turn has not come, or for a file that is no longer clean is the same
`404`: render one "This link is no longer valid" page.

```json
{
  "status": 404,
  "body": {
    "error": {
      "code": "NOT_FOUND",
      "message": "Not found.",
      "field_errors": []
    }
  }
}
```

### `POST /sign/{token}` — sign (`{"typed_name": "..."}`)

The first signer of a sequential request; the response has no roster. At this moment the server releases and emails signer two:

```json
{
  "status": 200,
  "body": {
    "data": {
      "id": "2b81176a-edce-4db2-9f1f-bae2a087aa3b",
      "title": "Founders agreement",
      "status": "awaiting",
      "file_id": "b151cc1c-a92e-49d8-9c3e-ac10856bfa9f",
      "filename": "founders-agreement.pdf",
      "signed_count": 1,
      "total": 2,
      "sequential": true,
      "message": "Hi both, please sign before Friday.\nThanks, Ada",
      "sent_at": "2026-10-25T17:18:02.933745+00:00",
      "expires_at": "2026-11-08T17:18:02.941259+00:00",
      "completed_at": null,
      "created_at": "2026-10-25T17:18:02.933745+00:00"
    },
    "meta": null
  }
}
```

The last signature completes the request. The response now also has `executed_url` (the signer can download the executed PDF from it once it is ready):

```json
{
  "status": 200,
  "body": {
    "data": {
      "id": "2b81176a-edce-4db2-9f1f-bae2a087aa3b",
      "title": "Founders agreement",
      "status": "complete",
      "file_id": "b151cc1c-a92e-49d8-9c3e-ac10856bfa9f",
      "filename": "founders-agreement.pdf",
      "signed_count": 2,
      "total": 2,
      "sequential": true,
      "message": "Hi both, please sign before Friday.\nThanks, Ada",
      "sent_at": "2026-10-25T17:18:02.933745+00:00",
      "expires_at": "2026-11-08T17:18:02.941259+00:00",
      "completed_at": "2026-10-25T17:18:03.068049+00:00",
      "created_at": "2026-10-25T17:18:02.933745+00:00",
      "executed_url": "http://127.0.0.1:8610/api/v1/sign/REDACTED_TOKEN/executed"
    },
    "meta": null
  }
}
```

### `GET /sign/{token}/executed` — the signer's copy of the executed PDF
Works with the signer's **own** link, only after the request completed, for 90 days after completion; every other state is `404`. Right after completion the
PDF is still being built (a few seconds): `409 EXECUTED_NOT_READY`.

```json
{
  "status": 409,
  "body": {
    "error": {
      "code": "EXECUTED_NOT_READY",
      "message": "The executed copy isn't ready yet. Try again shortly.",
      "field_errors": []
    }
  }
}
```

Success is a file download (`200`, `application/pdf`):

```json
{
  "status": 200,
  "content_type": "application/pdf",
  "content_disposition": "attachment; filename=\"founders-agreement - signed.pdf\"; filename*=UTF-8''founders-agreement%20-%20signed.pdf"
}
```

## 6. Completion: the executed PDF

Timeline after the last signature: the request is `complete` with `executed.state: "pending"` → the worker builds the PDF (seconds) → `executed.state: "ready"` with the file id, and
the parties are emailed.

Right after completion:

```json
{
  "status": 200,
  "body": {
    "data": {
      "id": "2b81176a-edce-4db2-9f1f-bae2a087aa3b",
      "title": "Founders agreement",
      "status": "complete",
      "file_id": "b151cc1c-a92e-49d8-9c3e-ac10856bfa9f",
      "filename": "founders-agreement.pdf",
      "signed_count": 2,
      "total": 2,
      "sequential": true,
      "message": "Hi both, please sign before Friday.\nThanks, Ada",
      "sent_at": "2026-10-25T17:18:02.933745+00:00",
      "expires_at": "2026-11-08T17:18:02.941259+00:00",
      "completed_at": "2026-10-25T17:18:03.068049+00:00",
      "created_at": "2026-10-25T17:18:02.933745+00:00",
      "document_hash": "a1f361e64b738f2c2946da5dadaab49a4d5adb6fc53255ff6bc4c2fac4ecf627",
      "executed": {
        "state": "pending",
        "file_id": null,
        "filename": null,
        "size_bytes": null
      },
      "legal_caveat": "These are simple electronic signatures: each signer typed their name to adopt it as their signature. Cofoundaz records the signer's email, IP address, browser, the time and the document's SHA-256 hash, but does not issue a certificate-based or qualified signature and is not a trust service provider. Whether a typed-name signature is enforceable depends on the document and the jurisdiction; take legal advice for high-stakes agreements.",
      "signers": [
        {
          "id": "96a4d6d5-8160-47c7-9932-55478f326a27",
          "email": "delivered+signer-one-c685d7cb984b@resend.dev",
          "name": "Ada Investor",
          "position": 0,
          "status": "signed",
          "notified_at": "2026-10-25T17:18:02.941259+00:00",
          "viewed_at": "2026-10-25T17:18:02.996890+00:00",
          "signed_at": "2026-10-25T17:18:03.035412+00:00",
          "signed_name": "Ada Investor",
          "signed_ip": "127.0.0.1",
          "signed_user_agent": "e2e-browser/1.0 (signer one)",
          "link_copies": [
            {
              "at": "2026-10-25T17:18:02.950734+00:00",
              "via": "created",
              "name": "Ada Founder",
              "email": "delivered+e2e-101791a33a21@resend.dev",
              "user_id": "74d8ddad-0d8b-404d-ac03-5f23280cf490"
            },
            {
              "at": "2026-10-25T17:18:03.020131+00:00",
              "via": "copied",
              "name": "Ada Founder",
              "email": "delivered+e2e-101791a33a21@resend.dev",
              "user_id": "74d8ddad-0d8b-404d-ac03-5f23280cf490"
            }
          ]
        },
        {
          "id": "b6ffa449-261f-40e3-ab4e-a92ea14ddb16",
          "email": "delivered+signer-two-df114f6f8386@resend.dev",
          "name": "Bello Legal",
          "position": 1,
          "status": "signed",
          "notified_at": "2026-10-25T17:18:03.047259+00:00",
          "viewed_at": "2026-10-25T17:18:03.068049+00:00",
          "signed_at": "2026-10-25T17:18:03.068049+00:00",
          "signed_name": "Bello Legal",
          "signed_ip": "127.0.0.1",
          "signed_user_agent": "e2e-browser/1.0 (signer two)",
          "link_copies": []
        }
      ]
    },
    "meta": null
  }
}
```

A request that completed before this release:

```json
{
  "status": 200,
  "body": {
    "data": {
      "id": "1a82277d-d3e8-4c64-a34d-566e4361001f",
      "title": "One-Pager.pdf",
      "status": "complete",
      "file_id": "fec4505b-defd-48fd-a32e-ae294a15733b",
      "filename": "One-Pager.pdf",
      "signed_count": 1,
      "total": 1,
      "sequential": false,
      "message": "Ada Founder has sent you One-Pager.pdf to sign via Cofoundaz.",
      "sent_at": "2026-10-25T17:18:04.603750+00:00",
      "expires_at": "2026-11-08T17:18:04.609156+00:00",
      "completed_at": "2026-10-25T17:18:04.644824+00:00",
      "created_at": "2026-10-25T17:18:04.603750+00:00",
      "document_hash": null,
      "executed": {
        "state": "legacy",
        "file_id": null,
        "filename": null,
        "size_bytes": null
      },
      "legal_caveat": "These are simple electronic signatures: each signer typed their name to adopt it as their signature. Cofoundaz records the signer's email, IP address, browser, the time and the document's SHA-256 hash, but does not issue a certificate-based or qualified signature and is not a trust service provider. Whether a typed-name signature is enforceable depends on the document and the jurisdiction; take legal advice for high-stakes agreements.",
      "signers": [
        {
          "id": "d0bb5d4a-8350-4d0c-bff2-75d0e111b72c",
          "email": "delivered+signer-three-99a4b1d0989f@resend.dev",
          "name": "Cleo",
          "position": 0,
          "status": "signed",
          "notified_at": "2026-10-25T17:18:04.625607+00:00",
          "viewed_at": "2026-10-25T17:18:04.644824+00:00",
          "signed_at": "2026-10-25T17:18:04.644824+00:00",
          "signed_name": "Cleo",
          "signed_ip": "127.0.0.1",
          "signed_user_agent": "python-httpx/0.28.1",
          "link_copies": [
            {
              "at": "2026-10-25T17:18:04.614000+00:00",
              "via": "created",
              "name": "Ada Founder",
              "email": "delivered+e2e-101791a33a21@resend.dev",
              "user_id": "74d8ddad-0d8b-404d-ac03-5f23280cf490"
            },
            {
              "at": "2026-10-25T17:18:04.637541+00:00",
              "via": "copied",
              "name": "Ada Founder",
              "email": "delivered+e2e-101791a33a21@resend.dev",
              "user_id": "74d8ddad-0d8b-404d-ac03-5f23280cf490"
            }
          ]
        }
      ]
    },
    "meta": null
  }
}
```

A few seconds later:

```json
{
  "status": 200,
  "body": {
    "data": {
      "id": "2b81176a-edce-4db2-9f1f-bae2a087aa3b",
      "title": "Founders agreement",
      "status": "complete",
      "file_id": "b151cc1c-a92e-49d8-9c3e-ac10856bfa9f",
      "filename": "founders-agreement.pdf",
      "signed_count": 2,
      "total": 2,
      "sequential": true,
      "message": "Hi both, please sign before Friday.\nThanks, Ada",
      "sent_at": "2026-10-25T17:18:02.933745+00:00",
      "expires_at": "2026-11-08T17:18:02.941259+00:00",
      "completed_at": "2026-10-25T17:18:03.068049+00:00",
      "created_at": "2026-10-25T17:18:02.933745+00:00",
      "document_hash": "a1f361e64b738f2c2946da5dadaab49a4d5adb6fc53255ff6bc4c2fac4ecf627",
      "executed": {
        "state": "ready",
        "file_id": "597e5401-6cde-4846-8730-a461a9934868",
        "filename": "founders-agreement - signed.pdf",
        "size_bytes": 4669,
        "sha256": "29b4b7b106221ada2844adef83cf6d9b9501b9221e9e29814a44685d1e2ad1bb"
      },
      "legal_caveat": "These are simple electronic signatures: each signer typed their name to adopt it as their signature. Cofoundaz records the signer's email, IP address, browser, the time and the document's SHA-256 hash, but does not issue a certificate-based or qualified signature and is not a trust service provider. Whether a typed-name signature is enforceable depends on the document and the jurisdiction; take legal advice for high-stakes agreements.",
      "signers": [
        {
          "id": "96a4d6d5-8160-47c7-9932-55478f326a27",
          "email": "delivered+signer-one-c685d7cb984b@resend.dev",
          "name": "Ada Investor",
          "position": 0,
          "status": "signed",
          "notified_at": "2026-10-25T17:18:02.941259+00:00",
          "viewed_at": "2026-10-25T17:18:02.996890+00:00",
          "signed_at": "2026-10-25T17:18:03.035412+00:00",
          "signed_name": "Ada Investor",
          "signed_ip": "127.0.0.1",
          "signed_user_agent": "e2e-browser/1.0 (signer one)",
          "link_copies": [
            {
              "at": "2026-10-25T17:18:02.950734+00:00",
              "via": "created",
              "name": "Ada Founder",
              "email": "delivered+e2e-101791a33a21@resend.dev",
              "user_id": "74d8ddad-0d8b-404d-ac03-5f23280cf490"
            },
            {
              "at": "2026-10-25T17:18:03.020131+00:00",
              "via": "copied",
              "name": "Ada Founder",
              "email": "delivered+e2e-101791a33a21@resend.dev",
              "user_id": "74d8ddad-0d8b-404d-ac03-5f23280cf490"
            }
          ]
        },
        {
          "id": "b6ffa449-261f-40e3-ab4e-a92ea14ddb16",
          "email": "delivered+signer-two-df114f6f8386@resend.dev",
          "name": "Bello Legal",
          "position": 1,
          "status": "signed",
          "notified_at": "2026-10-25T17:18:03.047259+00:00",
          "viewed_at": "2026-10-25T17:18:03.068049+00:00",
          "signed_at": "2026-10-25T17:18:03.068049+00:00",
          "signed_name": "Bello Legal",
          "signed_ip": "127.0.0.1",
          "signed_user_agent": "e2e-browser/1.0 (signer two)",
          "link_copies": []
        }
      ]
    },
    "meta": null
  }
}
```

`executed.sha256` is the hash of the executed file; `document_hash` is the hash of the original PDF that was sent (also printed on the certificate).

### Download for editors
`GET /documents/signature-requests/{id}/executed` returns a 5-minute signed link (same shape as `GET /documents/files/{id}/download`); `409 EXECUTED_NOT_READY` before it is built:

```json
{
  "status": 409,
  "body": {
    "error": {
      "code": "EXECUTED_NOT_READY",
      "message": "The executed copy isn't ready yet. Try again shortly.",
      "field_errors": []
    }
  }
}
```

```json
{
  "status": 200,
  "body": {
    "data": {
      "url": "http://127.0.0.1:8610/api/v1/documents/files/download?token=REDACTED_TOKEN",
      "expires_at": "2026-10-25T17:23:04+00:00",
      "content_type": "application/pdf",
      "filename": "founders-agreement - signed.pdf"
    },
    "meta": null
  }
}
```

The executed copy is also an ordinary private file in the library (`origin: "executed"`, name `<original> - signed.pdf`, same folder as the original):

```json
{
  "id": "597e5401-6cde-4846-8730-a461a9934868",
  "filename": "founders-agreement - signed.pdf",
  "content_type": "application/pdf",
  "mime": "application/pdf",
  "type": "file",
  "origin": "executed",
  "source_document_id": null,
  "source_version": null,
  "size_bytes": 4669,
  "folder": null,
  "tags": [],
  "owner": {
    "id": "74d8ddad-0d8b-404d-ac03-5f23280cf490",
    "name": "Ada Founder"
  },
  "share_count": 0,
  "scan_status": "clean",
  "uploaded_at": "2026-10-25T17:18:04.462897+00:00",
  "trashed_at": null,
  "purges_at": null
}
```

It, and the PDF that was signed, **cannot be deleted or trashed** while the signature record exists:

```json
{
  "status": 409,
  "body": {
    "error": {
      "code": "FILE_HAS_SIGNATURE_REQUESTS",
      "message": "This file has signature requests, so it can't be deleted.",
      "field_errors": []
    }
  }
}
```

### What is inside the PDF (text extracted from the live executed file)

```json
{
  "pages": 3,
  "first_page_text": "Founders agreement (e2e)",
  "certificate_text": "Cofoundaz signature certificate\nCertificate page 2\nThese are simple electronic signatures: each signer typed their name to adopt it as their signature. Cofoundaz records the signer's\nemail, IP address, browser, the time and the document's SHA-256 hash, but does not issue a certificate-based or qualified signature\nand is not a trust service provider. Whether a typed-name signature is enforceable depends on the document and the jurisdiction;\ntake legal advice for high-stakes agreements.\n"
}
```

### The email to each party
Subject `Signed: {title}`, the executed PDF attached (`<original> - signed.pdf`). Sent to every signer and to the sender; a failed address is retried without re-sending to the ones already done.

```json
{
  "to": "delivered+signer-one-c685d7cb984b@resend.dev",
  "subject": "Signed: Founders agreement",
  "html": "<div style='font-family:system-ui,sans-serif;max-width:560px'><h2>Founders agreement is fully signed</h2><p>Everyone has signed. The executed PDF, with its signature page and audit certificate, is attached. Keep it for your records.</p><ul><li>Ada Investor (delivered+signer-one-c685d7cb984b@resend.dev)</li><li>Bello Legal (delivered+signer-two-df114f6f8386@resend.dev)</li></ul><p style='font-size:12px;color:#666'>These are simple electronic signatures: each signer typed their name to adopt it as their signature. Cofoundaz records the signer&#x27;s email, IP address, browser, the time and the document&#x27;s SHA-256 hash, but does not issue a certificate-based or qualified signature and is not a trust service provider. Whether a typed-name signature is enforceable depends on the document and the jurisdiction; take legal advice for high-stakes agreements.</p></div>",
  "sent_at": "2026-10-25T17:18:04.500505+00:00",
  "attachments": [
    "founders-agreement_-_signed.pdf"
  ]
}
```

**Polling:** after the last signature show "Signed. Preparing the executed copy…" and poll `GET /documents/signature-requests/{id}` every 3 seconds, up to 60 seconds, until
`executed.state === "ready"`; after that, stop polling and show "Taking longer than usual — we'll email it to everyone" (the email still arrives when the worker catches up).

## 7. Link shares (documents and files)

`POST /documents/{document_id}/shares` and the new `POST /documents/files/{file_id}/shares`. Body: `email` (optional: **omit it for "anyone with the link"**; nothing is emailed),
`access_level` (`view` | `comment`; always `view` for files), and the expiry: `expires_at` (ISO date-time, must be in the future and ≤ 365 days ahead) **or** `expires_in_days`
(default **30**; send `null` for a link that never expires). `expires_at` wins if both are sent. Limit 60/hour.

### File, anyone with the link, custom expiry
Past date first (the validation error):

```json
{
  "status": 422,
  "body": {
    "error": {
      "code": "VALIDATION_ERROR",
      "message": "The expiry date must be in the future.",
      "field_errors": [
        {
          "field": "expires_at",
          "message": "The expiry date must be in the future."
        }
      ]
    }
  }
}
```

Create:

```json
{
  "status": 201,
  "body": {
    "data": {
      "id": "130bc553-b54e-44ae-8807-efe8b49ef592",
      "kind": "file",
      "email": null,
      "anyone_with_link": true,
      "access_level": "view",
      "expires_at": "2026-11-04T17:18:02+00:00",
      "revoked_at": null,
      "last_viewed_at": null,
      "created_at": "2026-10-25T17:18:02.670085+00:00",
      "status": "active",
      "link": "http://localhost:3000/shared/REDACTED_TOKEN"
    },
    "meta": null
  }
}
```

**The link is returned on create and again on every list** — to editors only. Copy-again = `GET .../shares`:

```json
{
  "status": 200,
  "body": {
    "data": {
      "shares": [
        {
          "id": "130bc553-b54e-44ae-8807-efe8b49ef592",
          "kind": "file",
          "email": null,
          "anyone_with_link": true,
          "access_level": "view",
          "expires_at": "2026-11-04T17:18:02+00:00",
          "revoked_at": null,
          "last_viewed_at": null,
          "created_at": "2026-10-25T17:18:02.670085+00:00",
          "status": "active",
          "link": "http://localhost:3000/shared/REDACTED_TOKEN"
        }
      ]
    },
    "meta": null
  }
}
```

`link` is `null` for a share that is revoked/expired, and for shares created *before* this release (their random token was never recoverable): for those, create a new link.

Change the expiry (`PATCH .../shares/{share_id}`, body `{"expires_at": "<iso>"}`; `null` = never; a revoked share → 404):

```json
{
  "status": 200,
  "body": {
    "data": {
      "id": "130bc553-b54e-44ae-8807-efe8b49ef592",
      "kind": "file",
      "email": null,
      "anyone_with_link": true,
      "access_level": "view",
      "expires_at": "2026-11-14T17:18:02+00:00",
      "revoked_at": null,
      "last_viewed_at": "2026-10-25T17:18:02.708224+00:00",
      "created_at": "2026-10-25T17:18:02.670085+00:00",
      "status": "active",
      "link": "http://localhost:3000/shared/REDACTED_TOKEN"
    },
    "meta": null
  }
}
```

Revoke: `DELETE .../shares/{share_id}` → `{"data": {"revoked": true}, "meta": null}`.

### What the link holder gets
`GET /shared/{token}` is now `kind`-tagged. For a file:

```json
{
  "status": 200,
  "body": {
    "data": {
      "kind": "file",
      "file": {
        "filename": "founders-agreement.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1371,
        "viewer": "pdf",
        "download_url": "http://127.0.0.1:8610/api/v1/shared/REDACTED_TOKEN/file"
      },
      "access_level": "view",
      "expires_at": "2026-11-04T17:18:02+00:00"
    },
    "meta": null
  }
}
```

`download_url` serves the file (`GET /shared/{token}/file`, add `?inline=true` to display in a viewer). Headers of the real download:

```json
{
  "status": 200,
  "content_type": "application/pdf",
  "content_disposition": "attachment; filename=\"founders-agreement.pdf\"; filename*=UTF-8''founders-agreement.pdf"
}
```

After revoke (or expiry, trash, or the file no longer being clean) all of it is the same 404:

```json
{
  "status": 404,
  "body": {
    "error": {
      "code": "NOT_FOUND",
      "message": "Not found.",
      "field_errors": []
    }
  }
}
```

### Document, anyone with the link, never expires

```json
{
  "status": 201,
  "body": {
    "data": {
      "id": "1b5e5e46-04b4-47a7-aa27-ce17fc0cc1e2",
      "kind": "document",
      "email": null,
      "anyone_with_link": true,
      "access_level": "view",
      "expires_at": null,
      "revoked_at": null,
      "last_viewed_at": null,
      "created_at": "2026-10-25T17:18:02.786802+00:00",
      "status": "active",
      "link": "http://localhost:3000/shared/REDACTED_TOKEN"
    },
    "meta": null
  }
}
```

`GET /shared/{token}` for a document now also has `"kind": "document"`; the rest of the body is unchanged.

### The library row
Files now carry a real `share_count` (live links only):

```json
{
  "id": "b151cc1c-a92e-49d8-9c3e-ac10856bfa9f",
  "filename": "founders-agreement.pdf",
  "content_type": "application/pdf",
  "mime": "application/pdf",
  "type": "file",
  "origin": "upload",
  "source_document_id": null,
  "source_version": null,
  "size_bytes": 1371,
  "folder": null,
  "tags": [],
  "owner": {
    "id": "74d8ddad-0d8b-404d-ac03-5f23280cf490",
    "name": "Ada Founder"
  },
  "share_count": 1,
  "scan_status": "clean",
  "uploaded_at": "2026-10-25T17:18:02.637155+00:00",
  "trashed_at": null,
  "purges_at": null
}
```

## 8. Error reference

| HTTP | `code` | When |
|---|---|---|
| 422 | `PDF_REQUIRED` | file is not a PDF |
| 422 | `PDF_UNREADABLE` | PDF encrypted / damaged / 0 or > 300 pages |
| 422 | `VALIDATION_ERROR` | bad signer email, message > 2000, expiry in the past or > 365 days (`field_errors[].field` = `expires_at`) |
| 409 | `FILE_NOT_CLEAN` | file not scanned clean (sign or share) |
| 409 | `FILE_UNAVAILABLE` | the stored PDF could not be read (retry) |
| 409 | `SIGNATURE_NOT_ACTIVE` | remind / copy link on a complete, cancelled or expired request, or a signer who already signed |
| 409 | `LINK_NOT_RECOVERABLE` | copy link for a signer whose link predates e-sign v2 (send a reminder) |
| 409 | `SIGNER_NOT_RELEASED` | copy link for a sequential signer whose turn has not come |
| 409 | `EXECUTED_NOT_READY` | executed PDF not built yet (editor route and signer route) |
| 409 | `FILE_HAS_SIGNATURE_REQUESTS` | delete/trash the signed or executed file |
| 403 | `FORBIDDEN` | any non-founder / non-team-member role |
| 404 | `NOT_FOUND` | unknown/foreign ids; every dead public token |
| 429 | rate limit | public routes 30/min (read) and 10/min (sign) per caller, shared across tokens |

## 9. Field-nesting traps

- `signer_links` is a **list aligned with `signers`** and can contain `null` (sequential, not released). It is only in the **create** response.
- Per-signer `status`, `viewed_at`, `signed_ip`, `signed_user_agent` exist only under `signers[]` of the **editor** objects. The public `POST /sign/{token}` response has no `signers` and no `document_hash`.
- `executed` is `null` (not complete) vs `state: "legacy"` (old request) vs an object with `state: "pending"` (file ids are `null`) vs `state: "ready"` (adds `sha256`). Note `pending` has no `sha256` key at all.
- `message` is on the request (editor) **and** `request.message` on the public view; `sent_by` exists only on the public view.
- Share objects: `email` is `null` for anyone-with-link and `anyone_with_link` is the boolean to branch on; `document_id`/`file_id` appear only in the workspace list `GET /documents/shares` (with `link` there too).
- `expires_at` is the instant, always UTC with offset; the share/request is dead at that instant.

## 10. UX consequences the FE must surface

1. **Remind rotates links.** Tell the founder: "Sending a reminder replaces the signer's link; the old one stops working."
2. **Sequential:** show waiting signers as "Waiting for {previous signer}" and hide their copy-link button (the API answers 409).
3. **"Anyone with the link" is public:** keep it off by default and show a warning when it is on ("Anyone who has this link can view this file until {expiry}"); show the expiry date and offer revoke.
4. **Legal caveat:** these are simple typed-name signatures, not certificate-based ones. Show `legal_caveat` (returned by the API) on the sign page and in the request detail; it is also printed on the certificate.
5. **No field placement:** signatures appear on an appended page, not on the page the signer reviewed. Say so in the send dialog if the founder expects a position.
6. **DOCX cannot be signed:** guide the founder to "Export as PDF" first.
7. **Executed copy is delayed by seconds** (section 6): never show a dead download button; show the preparing state.
8. **The signed and executed files are locked:** the Delete action must explain the 409 ("part of a signature record").
9. **Rotating the server's `SECRET_KEY` invalidates every copied link** (signer links and v2 share links); the API owner knows this, the UI need not.

## 11. Verification table

| Behaviour | Verified |
|---|---|
| Sequential create (first released, second `null`, one email) | live (`request_create_sequential`, `email_to_first_signer`) |
| Viewed state after opening the link | live (`request_detail_after_first_view`) |
| Next signer released and emailed on signature | live (`sign_first`; the mail count was asserted) |
| Replay of a used link is 404 | live |
| Request detail with signer times, IP, browser | live (`request_detail_complete_executed_ready`) |
| Copy link again, and the not-released 409 | live |
| Mentor refused | live |
| Executed PDF built by the worker, emailed to both signers and the sender with the PDF attached | live (worker drained in-process; mail dir read back) |
| Executed PDF contents (signature names, emails, IP, UA, hash, caveat) | live (`executed_pdf_facts`) |
| Editor download link and signer download | live |
| Delete of signed / executed file is 409 | live |
| DOCX → `PDF_REQUIRED`, broken PDF → `PDF_UNREADABLE`, bad signer email 422 | live |
| Export to PDF is signable; default message | live (`request_create_parallel_default_message_from_export`) |
| File share: create, list (copy again), change expiry, public open and download, revoke → 404 | live |
| Expiry in the past → 422 | live; **> 365 days** and **inline=true** are unit-tested only |
| Document link share (no email, never expires) | live |
| Remind rotating links | live (`remind`) response only; the old link failing is unit-tested |
| `EXECUTED_NOT_READY` (editor and signer) | live |
| 90-day limit on the signer's executed link | **unit-tested only** (a clock cannot be waited out) |
| Cloudinary-backed storage (private download + `read_private`) | **not exercised live**: local storage only; unit-tested with a faked provider |
| Real mail delivery (SMTP/Resend) of the executed copy | **not exercised**: file mail backend only |
| Link audit entries (create + copy) and the certificate line | live (detail captures) and unit-tested (certificate text) |
| `expires_in_days` bounds 422; legacy executed state | live |
| Two last signers submitting at the same instant, double submit | **unit-tested only** (lock order asserted on the SQL, stale-roster simulation); not raced live |
| `LINK_NOT_RECOVERABLE` | unit-tested only (needs a pre-v2 row) |
| Rate limits on the new routes | **not exercised live** (the e2e server runs with the limiter relaxed); configured with `shared_limit` and covered by the route matrix test only |
| Pre-v2 shares/requests after the migration | unit-tested (migration test); not run against real data |
